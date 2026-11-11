"""A qué lote se presenta la cuenta real de la portada, y quién lo decide.

La tarjeta compara dos cosas que no tienen por qué estar al mismo tamaño: el
dinero que la cuenta movió y la media que promete el backtest del portafolio.
Si no se llevan al mismo lote, la comparación mide volumen y no comportamiento.

Antes esto era un mapa cableado por cuenta y símbolo. Ahora lo elige el usuario
en la simulación de lotes de Análisis —la misma casilla que decide qué curva se
mueve ahí— y aquí se traduce a números:

| Casilla (`view_lot_overrides.$mode`) | Qué se presenta |
| --- | --- |
| Lote demo / tester (`tester`) | Cada cierre real se escala al lote que el tester corrió, y la promesa se mide a ese mismo lote |
| Lote cuenta real (`autolot`) | Los cierres no se tocan —ya son lo que la cuenta ejecutó— y la promesa se mide al lote **ejecutado**, no al declarado |
| Ninguna | Nada: el dinero observado contra la promesa al lote declarado |

**Sólo alcanza a los portafolios que lo marcan.** Una cuenta puede servir a
varios usos (`live_account_inventory`), así que el mapa es la unión de lo que
cada uso pide y un símbolo que nadie reclama se queda como se ejecutó.

## Los lotes no se teclean: se miden

Salen de la última auditoría archivada del uso, que es donde consta lo que cada
lado ejecutó de verdad. Medido el 2026-10-24 sobre `audit-67-muzhv8jq-1` de
AXI: BTCUSD 0,03 contra 0,10, ETHUSD 0,10 contra 0,10, USDJPY y XAUUSD 0,01
contra 0,01. Sin auditoría archivada, el lote del tester es el del miembro del
portafolio —coinciden en las cuatro— y el ejecutado no se inventa.

## El puente con los cierres es el miembro, no el texto del símbolo

Un cierre del informe nativo no dice de qué estrategia es —el magic del
terminal real no identifica al miembro (`ai_context/live_audit_autolot.md`)—,
así que lo único que los une es el símbolo. Y **las dos partes lo escriben
distinto**: el informe nativo de la cuenta imprime `BTCUSD` y la auditoría
`BTCUSD.sa`, que es como lo nombra el bróker. Comprobado el 2026-10-24 sobre el
informe de `audit-67-muzhv8jq-1`: 48 cierres, todos sin sufijo.

Por eso el símbolo lo pone **el miembro del portafolio** —`BTCUSD`, la misma
grafía que el informe— y la auditoría sólo pone el número. Recortar el sufijo a
ojo no vale: en RoboForex hay símbolos que empiezan por punto (`.DE40Cash`) y
el recorte los dejaría vacíos.

**Un símbolo no se puede presentar a dos lotes.** Si dos estrategias del mismo
símbolo corrieron lotes distintos, o dos usos de la misma cuenta piden lotes
distintos para él, ese símbolo se queda sin escalar **y sus miembros se quedan
sin reescalar la promesa**: mover una mitad sin la otra es justo lo que esto
existe para evitar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PRESENTATION_VERSION = "lot-notes-v2"
"""Qué criterio escribió los avisos y los lotes guardados de una tarjeta.

La portada no recalcula: pinta lo que `_rebuild` dejó escrito, y lo escrito
sobrevive al reinicio. Sin esto, cambiar qué se avisa deja la nota vieja en
pantalla hasta que al uso le toque reconstruirse por edad —un día entero—, y
el arreglo parece no haber hecho nada. Subir el número lo caduca al vuelo,
igual que `live_account_expectation.CRITERION_VERSION` caduca la medida.
"""

TESTER_LEADS = "tester"
REAL_LEADS = "autolot"
"""Los dos valores de `view_lot_overrides.$mode`.

`autolot` se llama así por historia: fue el único sentido que existió cuando se
guardó por primera vez, y hay perfiles escritos con ese nombre
(`live_audit_settings_schema.normalize_view_lot_overrides`).
"""


@dataclass(frozen=True)
class UseLots:
    """A qué lote presenta un uso: por símbolo y por miembro."""

    symbols: dict[str, float] = field(default_factory=dict)
    members: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def mode_of(profile: dict[str, Any]) -> str:
    """La casilla marcada en la simulación de lotes de ese uso."""
    mode = str(((profile or {}).get("view_lot_overrides") or {}).get("$mode") or "")
    return mode if mode in {TESTER_LEADS, REAL_LEADS} else ""


def _lot(value: Any) -> float:
    try:
        lot = round(float(value), 8)
    except (TypeError, ValueError):
        return 0.0
    return lot if lot > 0 else 0.0


def observed_lots(detail: dict[str, Any]) -> dict[str, dict[str, set[float]]]:
    """Los lotes que la auditoría vio a cada lado, por estrategia.

    Se mira también la real que seguía abierta: está emparejada y su volumen es
    tan ejecutado como el de una cerrada.
    """
    found: dict[str, dict[str, set[float]]] = {}
    for row in detail.get("operation_comparisons") or []:
        if not isinstance(row, dict):
            continue
        tester = row.get("tester") or {}
        strategy = str(row.get("strategy") or tester.get("strategy") or "")
        if not strategy:
            continue
        entry = found.setdefault(strategy, {"tester": set(), "real": set()})
        if _lot(tester.get("volume")):
            entry["tester"].add(_lot(tester.get("volume")))
        real = row.get("real") or row.get("real_position_still_open") or {}
        if _lot(real.get("volume")):
            entry["real"].add(_lot(real.get("volume")))
    return found


def _member_lot(member: dict[str, Any], mode: str, lots: dict[str, set[float]]) -> float:
    """El lote al que ese miembro se presenta, o cero si no se puede saber.

    Con varios lotes observados no hay uno: una estrategia que cambió de
    tamaño dentro del periodo no se puede presentar a un número solo.
    """
    observed = lots.get("tester" if mode == TESTER_LEADS else "real") or set()
    if len(observed) == 1:
        return next(iter(observed))
    if observed or mode == REAL_LEADS:
        # Sin auditoría no se sabe qué ejecutó la cuenta y no se inventa; el
        # lote del backtest, en cambio, lo declara el propio portafolio.
        return 0.0
    return _lot(member.get("lot"))


def _missing_lot_warning(missing: int, autolot: bool) -> list[str]:
    """Una línea por las estrategias sin lote único, y ninguna con autolote.

    Con autolote el lote lo decide el saldo trato a trato, así que **ninguna**
    estrategia tiene un tamaño único que observar: el aviso saldría para todas,
    siempre, y no hay nada que corregir. Sin autolote sí es una anomalía, pero
    se dice una vez: once frases con el `candidate_id` crudo tapan la nota que
    de verdad explica a qué lote está la tarjeta.
    """
    if not missing or autolot:
        return []
    if missing == 1:
        return ["Una estrategia sin un lote único que presentar: se deja como se ejecutó."]
    return [
        f"{missing} estrategias sin un lote único que presentar: se dejan "
        "como se ejecutaron.",
    ]


def use_lots(
    mode: str, members: list[dict[str, Any]], detail: dict[str, Any],
    autolot: bool = False,
) -> UseLots:
    """Lo que un uso pide presentar, ya resuelto a números."""
    if mode not in {TESTER_LEADS, REAL_LEADS}:
        return UseLots()
    observed = observed_lots(detail)
    found: dict[str, float] = {}
    symbols: dict[str, float] = {}
    conflicts: set[str] = set()
    missing = 0
    for member in members:
        candidate = str(member.get("candidate_id") or "").strip()
        symbol = str(member.get("symbol") or "").strip().upper()
        lot = _member_lot(member, mode, observed.get(candidate) or {})
        if not candidate or not lot:
            missing += 1
            continue
        found[candidate] = lot
        if mode != TESTER_LEADS or not symbol:
            continue
        if symbols.setdefault(symbol, lot) != lot:
            conflicts.add(symbol)
    return _without_conflicts(
        symbols, found, members, conflicts, _missing_lot_warning(missing, autolot),
    )


def _without_conflicts(
    symbols: dict[str, float], members_lots: dict[str, float],
    members: list[dict[str, Any]], conflicts: set[str], warnings: list[str],
) -> UseLots:
    """Un símbolo en disputa sale entero: sus cierres y sus miembros.

    Escalar la promesa de un miembro cuyo símbolo no se puede escalar separaría
    las dos mitades de la comparación, que es peor que no escalar ninguna.
    """
    for symbol in sorted(conflicts):
        symbols.pop(symbol, None)
        warnings.append(
            f"{symbol}: dos estrategias lo operan a lotes distintos, así que se "
            "deja como se ejecutó.",
        )
    disputed = {
        str(member.get("candidate_id") or "")
        for member in members
        if str(member.get("symbol") or "").strip().upper() in conflicts
    }
    return UseLots(
        symbols=symbols,
        members={
            candidate: lot for candidate, lot in members_lots.items()
            if candidate not in disputed
        },
        warnings=tuple(warnings),
    )


def latest_detail(server: Any, node_id: str, audit_id: str) -> dict[str, Any]:
    """El detalle de la última ejecución archivada de ese uso, o nada.

    Se lee del archivo del manager y no del agente: esto corre dentro del
    sondeo de cuentas, que ya tiene bastante con bajar el informe.
    """
    archive = getattr(server, "live_audit_archive", None)
    if archive is None:
        return {}
    entries = [
        row for row in archive.index(node_id)
        if str(row.get("audit_key") or "") == str(audit_id)
    ]
    if not entries:
        return {}
    entries.sort(key=lambda row: str(row.get("completed_at") or row.get("executed_at") or ""))
    return archive.detail(node_id, entries[-1])


def account_symbols(server: Any, node_id: str, account: dict[str, Any]) -> dict[str, float]:
    """El lote al que se presenta cada símbolo de una cuenta.

    Sale de las promesas ya guardadas —cada una dejó escrito el suyo— porque
    resolverlo aquí exigiría abrir la memoria UBS del agente, que es justo lo
    que `refresh_expectations` evita hacer en cada vuelta.
    """
    found: dict[str, float] = {}
    disputed: set[str] = set()
    for portfolio in account.get("portfolios") or []:
        stored = server.live_accounts.read_expectation(
            node_id, portfolio.get("portfolio_id") or 0,
        )
        for symbol, value in (stored.get("presentation_lots") or {}).items():
            lot = _lot(value)
            if lot and found.setdefault(str(symbol).upper(), lot) != lot:
                disputed.add(str(symbol).upper())
    for symbol in disputed:
        found.pop(symbol, None)
    return found
