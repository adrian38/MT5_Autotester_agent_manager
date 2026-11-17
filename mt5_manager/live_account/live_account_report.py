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

from .live_account_mt4_report import is_mt4_report, raw_deals_from_mt4
from .live_account_series import as_utc, cash_movements, movements, net_movements


SERIES_VERSION = "balance-series-v6-cash-movements"
"""Con qué criterio se armó la serie guardada, normalice o no esa cuenta.

La v6 guarda los **apuntes de saldo**: ingresos, retiradas, traspasos entre
cuentas y abonos del bróker, que hasta ahora se parseaban y se tiraban. Sube la
versión porque un sondeo incremental sólo trae su ventana y los apuntes de una
cuenta están repartidos por toda su vida: sin releerla entera, la pantalla de
patrimonio tendría los depósitos de esta semana y ninguno de los anteriores,
que es peor que no tener ninguno. Hasta que cada cuenta vuelva a leerse
completa, `cash_is_complete` dice que no se sabe y la pantalla se calla en vez
de enseñar un cero.

La v5 guarda también la columna **Profit** separada del efecto neto en saldo.
Antes sólo se conservaba `Profit + comisión + swap`, por lo que el resumen no
podía enseñar la misma cifra que la columna Profit del informe de MT5. La
versión obliga a releer toda la historia para no fingir que el neto antiguo es
Profit en los meses que todavía no hayan vuelto a aparecer en un sondeo.

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


CASH_VERSION_PREFIX = "balance-series-v6"
"""Desde qué versión la serie guardada incluye los apuntes de saldo."""


def cash_is_complete(stored: dict[str, Any]) -> bool:
    """Si los apuntes guardados son los de **toda** la vida de la cuenta.

    La versión sólo se reescribe cuando el sondeo relee la serie entera
    (`live_account_ingest._merge_account`), así que es exactamente la pregunta
    que hay que hacer: una cuenta que todavía va por la v5 tiene los apuntes
    que trajo la última ventana —o ninguno— y sumar eso daría un «aportado»
    inventado. La ausencia se dice; no se rellena con un cero.
    """
    return str(stored.get("normalization_version") or "").startswith(CASH_VERSION_PREFIX)


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
) -> tuple[
    list[tuple[datetime, float]], list[tuple[datetime, float]],
    list[tuple[str, float]], list[tuple[datetime, float, str, str]],
]:
    """Cierres netos, su Profit, el saldo diario y los apuntes de saldo.

    Van juntos porque salen de la misma tabla y el informe de una cuenta con
    años de vida no es barato de parsear: leerlo dos veces, una por cada cosa,
    sería pagar dos veces lo mismo.

    Los dos últimos salen de los cierres **sin normalizar**: el saldo y un
    ingreso son dinero del bróker, y llevarlos a otro lote daría un tamaño de
    cuenta y un aportado que no existieron.
    """
    if not contents:
        raise ValueError("El informe nativo de la cuenta real está vacío")
    if is_mt4_report(contents):
        raw_deals = raw_deals_from_mt4(contents)
    else:
        with tempfile.TemporaryDirectory(prefix="mt5-account-") as folder:
            path = Path(folder) / "real_account_report.html"
            path.write_bytes(contents)
            raw_deals = parse_report(path).raw_deals
    normalized = [
        normalized_deal(deal, symbol_lots or {}) for deal in raw_deals
    ]
    return (
        net_movements(normalized), movements(normalized),
        daily_balances(raw_deals), cash_movements(raw_deals),
    )


def movements_from_html(
    contents: bytes, symbol_lots: dict[str, float] | None = None,
) -> list[tuple[datetime, float]]:
    """Los cierres de la cuenta que trae ese informe, ordenados y en UTC."""
    return account_from_html(contents, symbol_lots)[1]
