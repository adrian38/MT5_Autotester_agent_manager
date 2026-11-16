"""El cambio de divisa, refrescado por el sondeo desde la referencia del BCE.

Hasta el 2026-11-16 el cambio se escribía a mano en el diálogo del sondeo
diario y se quedaba ahí: el guardado el 2026-11-10 seguía sumando seis días
después, y nada en la pantalla distinguía «el cambio de hoy» de «el cambio que
alguien tecleó la semana pasada». Son seis cuentas en dólares de catorce, así
que el patrimonio entero colgaba de ese número.

Ahora lo refresca el sondeo. La fuente es el feed diario del Banco Central
Europeo (`eurofxref-daily.xml`): sin clave, sin terceros y con su propia fecha
dentro, que es la que se guarda. El manager ya sale a Internet para los avisos
push, así que no hace falta nada nuevo en la máquina.

Tres decisiones que no son obvias:

- **Un fallo conserva el cambio anterior.** Sin red o con el feed caído se
  sigue sumando con el número viejo —que lleva su fecha— y entra un aviso en la
  campana. Borrarlo habría convertido un corte de red en un patrimonio
  incompleto, y el código ya sabe no sumar lo que no puede convertir.
- **Se mira una vez al día.** El `tick` del sondeo pasa cada pocos segundos;
  lo que dispara el refresco es que un nodo **haya entregado** un sondeo, y aun
  así sólo la primera vez de cada día UTC. El BCE publica una vez al día, hacia
  las 16:00 CET y sólo en días hábiles, de modo que un sábado devuelve el del
  viernes: `rates_updated_at` lo dice en vez de aparentar que es de hoy.
- **No pisa lo que no sabe.** Los cambios se funden sobre los que había, así
  que una divisa que el BCE no publica conserva el valor escrito a mano en vez
  de desaparecer.

No ejecuta nada del lado del agente: el sondeo que lo dispara sí, pero la
descarga y el guardado son del manager.
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from typing import Any, Iterable

from . import live_account_inventory
from .live_account_schedule import normalize_currency

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
"""La referencia diaria del BCE, en euros por unidad de cada divisa."""

TIMEOUT = 20.0
NOTICE_KIND = "exchange_rates_stale"


def parse_eurofxref(payload: bytes) -> tuple[str, dict[str, float]]:
    """`(fecha que trae el feed, {divisa: unidades por 1 EUR})`.

    El XML anida tres `Cube`: el de fuera no lleva nada, el de en medio la
    fecha y los de dentro una divisa cada uno. Se recorre por el final del
    nombre de la etiqueta en vez de declarar el espacio de nombres: el BCE lo
    ha cambiado antes, y un espacio de nombres distinto devolvería cero
    divisas **sin error**, que es la forma de fallar que no se ve.
    """
    root = ElementTree.fromstring(payload)
    published = ""
    per_eur: dict[str, float] = {"EUR": 1.0}
    for node in root.iter():
        if not node.tag.endswith("Cube"):
            continue
        published = str(node.get("time") or published)
        currency, rate = node.get("currency"), node.get("rate")
        if not currency or rate is None:
            continue
        try:
            number = float(rate)
            code = normalize_currency(currency)
        except ValueError:
            continue
        if number > 0:
            per_eur[code] = number
    if not published or len(per_eur) < 2:
        raise ValueError("el feed del BCE no trae ninguna fecha con cambios")
    return published, per_eur


def fetch_eurofxref(timeout: float = TIMEOUT) -> tuple[str, dict[str, float]]:
    """Descarga y lee el feed. Cualquier fallo sube tal cual: quien llama avisa."""
    request = urllib.request.Request(
        ECB_DAILY_URL, headers={"User-Agent": "MT5-Manager", "Accept": "application/xml"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse_eurofxref(response.read())


def rates_towards(
    display: str, wanted: Iterable[str], per_eur: dict[str, float],
) -> dict[str, float]:
    """De «unidades por euro» a lo que guarda `exchange_rates`.

    `convert` multiplica el importe por este número, así que lo que se guarda
    es **cuánto vale una unidad de la divisa en la moneda de los totales**: con
    el euro delante y 1,087 dólares por euro, el dólar entra como 0,92. La
    moneda de los totales no tiene por qué ser el euro, y por eso se divide en
    vez de invertir: la referencia es en euros y el cruce sale de las dos.
    """
    target = per_eur.get(normalize_currency(display))
    if not target or target <= 0:
        return {}
    converted: dict[str, float] = {}
    for code in wanted:
        source = per_eur.get(code)
        if code != display and source and source > 0:
            converted[code] = target / source
    return converted


def currencies_in_use(server: Any) -> set[str]:
    """Las divisas de las cuentas reales, que son las que hay que convertir.

    Se leen del archivo y no de la configuración: una cuenta nueva en una
    divisa que nadie tecleó nunca entra sola en el refresco, en vez de esperar
    a que alguien abra el diálogo y la guarde a mano.
    """
    found: set[str] = set()
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        for account in live_account_inventory.node_accounts(server, node_id):
            stored = server.live_accounts.read(node_id, account["account_key"])
            code = str((stored or {}).get("currency") or "").strip().upper()
            if code:
                found.add(code)
    return found


def _announce_failure(server: Any, display: str, wanted: set[str], reason: str) -> None:
    previous = str(server.live_account_scheduler_settings.get("rates_updated_at") or "")
    server.live_audit_notifications.add(
        "warning", "El cambio de divisa no se pudo refrescar",
        f"El sondeo terminó, pero la referencia diaria del BCE no contestó: {reason}. "
        f"Se sigue sumando con el cambio anterior, que es "
        f"{'del ' + previous if previous else 'de fecha desconocida'}. Afecta a "
        f"{', '.join(sorted(wanted))} → {display}.",
        kind=NOTICE_KIND, link="/accounts.html", link_text="Sondeo de cuentas",
    )


def refresh_after_snapshot(server: Any, now: datetime | None = None) -> dict[str, Any]:
    """Refresca el cambio tras un sondeo entregado. Una vez por día UTC.

    Devuelve lo que hizo para que la prueba —y el log— puedan distinguir «no
    tocaba», «no cambió» y «se escribió», que desde fuera se parecen todas a
    que no pasó nada.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = moment.date().isoformat()
    if getattr(server, "live_account_rates_day", "") == day:
        return {"skipped": "ya se miró hoy"}
    # Se marca antes de salir a la red: un feed caído no puede hacer que cada
    # vuelta del `tick` —son segundos— repita la descarga y el aviso.
    server.live_account_rates_day = day
    settings = server.live_account_scheduler_settings
    display = normalize_currency(settings.get("display_currency") or "EUR")
    stored = dict(settings.get("exchange_rates") or {})
    wanted = (currencies_in_use(server) | set(stored)) - {display}
    if not wanted:
        return {"skipped": "no hay ninguna divisa que convertir"}
    try:
        published, per_eur = fetch_eurofxref()
        fresh = rates_towards(display, wanted, per_eur)
    except Exception as exc:  # red, XML, HTTP: para quien suma son lo mismo
        _announce_failure(server, display, wanted, str(exc) or exc.__class__.__name__)
        return {"error": str(exc)}
    if not fresh:
        _announce_failure(
            server, display, wanted, "no publica ninguna de esas divisas",
        )
        return {"error": "el feed no cubre ninguna de las divisas en uso"}
    # Lo que decide si hay algo que guardar es **la fecha del feed**, no
    # comparar los números: el cambio calculado es 0,919963… y el guardado el
    # 0,92 que alguien tecleó, así que comparar `float` daría «ha cambiado»
    # todos los días y reescribiría el fichero para nada. La excepción es una
    # divisa nueva, que con la misma fecha sí hay que rellenar.
    if str(settings.get("rates_updated_at") or "") == published and wanted <= set(stored):
        return {"unchanged": published}
    server.update_live_account_scheduler(
        {"exchange_rates": {**stored, **fresh}, "rates_updated_at": published},
    )
    return {"updated": sorted(fresh), "published": published}
