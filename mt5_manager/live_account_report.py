"""Del informe nativo de MT5 a los movimientos de la cuenta.

Una sola función, y en su propio módulo porque es la frontera entre el formato
del broker y nuestra aritmética: lo de arriba es HTML de MT5 y lo de abajo son
pares `(instante, importe)`.

El mismo gesto que hace `live_audit_autolot.balance_history_from_html` para el
saldo del autolote: escribir el HTML en un temporal porque `parse_report`
trabaja con rutas. No se importa de allí porque aquello es sobre lotes y esto
sobre el resultado de la cuenta, y la única cosa que compartirían es el
`tempfile`.
"""
from __future__ import annotations

import hashlib
import math
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from portfolio_manager.mt5_report import parse_report

from .live_account_series import as_utc, movements


SERIES_VERSION = "balance-series-v4"
"""Con qué criterio se armó la serie guardada, normalice o no esa cuenta.

La v4 no cambia ni un cierre: añade el **saldo diario**, que sale de la misma
tabla y antes se tiraba. Sube igual porque un sondeo incremental sólo trae su
ventana, así que sin forzar la lectura de toda la vida el saldo de septiembre
no llegaría nunca y la promesa de una cartera con autolote sólo tendría con
qué medir los últimos días. Es exactamente para lo que esta constante existe.

La v3 es el arreglo del parser: hasta la v2, una **posición abierta** del
informe nativo entraba en la serie como un cierre, con un importe sacado de su
comentario (`mt5_report._parse_raw_deals`). Toda serie construida antes lleva
esos cierres inventados y hay que releerla entera; es exactamente para lo que
esta constante existe.

Lo pone **toda** cuenta, y por eso no puede quedar vacío: `_period` pide toda
la vida cuando no coincide, y esa es la única forma de reparar una serie que se
construyó mal. La anterior no tenía versión propia —sólo la de normalización, y
sólo para las cuentas del mapa—, así que el arreglo del solapamiento de
`LiveAccountArchive._inside` no habría alcanzado a las demás: seguirían con sus
movimientos duplicados hasta que alguien borrase el fichero a mano.
"""


def series_version(symbol_lots: dict[str, float] | None) -> str:
    """Con qué criterio está guardada la serie de esa cuenta.

    Es una sola cadena porque es un solo candado: en cuanto cambia el criterio
    —o el lote al que se presenta cualquiera de sus símbolos— la serie se
    vuelve a leer entera. Antes el segundo término era el nombre de un mapa
    cableado; ahora lo elige el usuario en la simulación de lotes, así que lo
    que identifica al criterio es **su contenido** y no una etiqueta que
    alguien tendría que acordarse de subir.
    """
    if not symbol_lots:
        return SERIES_VERSION
    stamp = ";".join(
        f"{symbol}={lot:.8f}" for symbol, lot in sorted(symbol_lots.items())
    )
    digest = hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12]
    return f"{SERIES_VERSION}+lots-{digest}"


def ingestion_key(snapshot_id: str, versions: Iterable[str]) -> str:
    """La misma captura se relee una vez cuando cambia su criterio por lote."""
    return ":".join([snapshot_id, *sorted({str(value) for value in versions} - {""})])


def normalized_deal(deal: Any, symbol_lots: dict[str, float]) -> Any:
    """Escala el resultado al lote de presentación, como el auditor su pareja.

    El volumen se conserva observado a propósito: es lo que la cuenta ejecutó,
    y el único reescalado es el del dinero.
    """
    target = (symbol_lots or {}).get(str(getattr(deal, "symbol", "") or "").upper())
    volume = float(getattr(deal, "volume", 0.0) or 0.0)
    if not target or volume <= 0:
        return deal
    factor = target / volume
    return replace(
        deal,
        commission=float(getattr(deal, "commission", 0.0) or 0.0) * factor,
        swap=float(getattr(deal, "swap", 0.0) or 0.0) * factor,
        profit=float(getattr(deal, "profit", 0.0) or 0.0) * factor,
    )


def daily_balances(raw_deals: Iterable[Any]) -> list[tuple[str, float]]:
    """El saldo con el que cerró cada día UTC, según la tabla de la cuenta.

    El mismo dato que `live_audit_autolot.balance_history_from_html` usa para
    el autolote, y con el mismo descarte de los ceros: MT5 imprime saldo 0 en
    las filas que no mueven la cuenta. Aquí se queda **el último de cada día**
    porque es lo que la promesa necesita —el tamaño que tenía la cuenta ese
    día— y guardar uno por operación multiplicaría el fichero sin añadir nada.

    El saldo **no se reescala** con los lotes de presentación. Es el dinero que
    el bróker tiene de verdad, que es lo que leen los EAs con autolote;
    llevarlo a otro lote daría un tamaño de cuenta que no existió.
    """
    found: dict[str, tuple[str, float]] = {}
    for deal in raw_deals:
        moment = getattr(deal, "timestamp", None)
        if not isinstance(moment, datetime):
            continue
        try:
            balance = float(getattr(deal, "balance", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(balance) or balance == 0.0:
            continue
        stamp = as_utc(moment).isoformat()
        day = stamp[:10]
        if day not in found or found[day][0] <= stamp:
            found[day] = (stamp, balance)
    return [(day, found[day][1]) for day in sorted(found)]


def account_from_html(
    contents: bytes, symbol_lots: dict[str, float] | None = None,
) -> tuple[list[tuple[datetime, float]], list[tuple[str, float]]]:
    """Los cierres y el saldo diario de ese informe, con una sola lectura.

    Van juntos porque salen de la misma tabla y el informe de una cuenta con
    años de vida no es barato de parsear: leerlo dos veces, una por cada cosa,
    sería pagar dos veces lo mismo.
    """
    if not contents:
        raise ValueError("El informe nativo de la cuenta real está vacío")
    with tempfile.TemporaryDirectory(prefix="mt5-account-") as folder:
        path = Path(folder) / "real_account_report.html"
        path.write_bytes(contents)
        report = parse_report(path)
    return (
        movements(
            normalized_deal(deal, symbol_lots or {}) for deal in report.raw_deals
        ),
        daily_balances(report.raw_deals),
    )


def movements_from_html(
    contents: bytes, symbol_lots: dict[str, float] | None = None,
) -> list[tuple[datetime, float]]:
    """Los cierres de la cuenta que trae ese informe, ordenados y en UTC."""
    return account_from_html(contents, symbol_lots)[0]
