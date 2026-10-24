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
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from portfolio_manager.mt5_report import parse_report

from .live_account_series import movements


SERIES_VERSION = "merge-window-v2"
"""Con qué criterio se armó la serie guardada, normalice o no esa cuenta.

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


def movements_from_html(
    contents: bytes, symbol_lots: dict[str, float] | None = None,
) -> list[tuple[datetime, float]]:
    """Los cierres de la cuenta que trae ese informe, ordenados y en UTC."""
    if not contents:
        raise ValueError("El informe nativo de la cuenta real está vacío")
    with tempfile.TemporaryDirectory(prefix="mt5-account-") as folder:
        path = Path(folder) / "real_account_report.html"
        path.write_bytes(contents)
        report = parse_report(path)
    return movements(
        normalized_deal(deal, symbol_lots or {}) for deal in report.raw_deals
    )
