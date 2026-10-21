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

import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from portfolio_manager.mt5_report import parse_report

from .live_account_series import movements


ACCOUNT_LOT_NORMALIZATION = {
    "10010194@Axi-US50-Demo": {"BTCUSD": 0.01, "ETHUSD": 0.01},
}
"""Lotes base de presentación por cuenta y símbolo.

Esta cuenta ejecuta BTCUSD y ETHUSD a 0,10, pero su histórico se compara a
0,01 por operación. Sólo esos dos símbolos se llevan a la misma base; saldo,
equidad, número de operaciones y el resto de cuentas permanecen observados.
"""

NORMALIZATION_VERSION = "account-lot-normalization-v1"


def normalization_version(account_key: str) -> str:
    return NORMALIZATION_VERSION if account_key in ACCOUNT_LOT_NORMALIZATION else ""


def ingestion_key(snapshot_id: str, account_keys: Iterable[str]) -> str:
    """La misma captura se relee una vez cuando cambia su criterio por lote."""
    versions = sorted({normalization_version(str(key)) for key in account_keys} - {""})
    return ":".join([snapshot_id, *versions])


def normalized_deal(deal: Any, account_key: str) -> Any:
    """Escala el resultado al lote base, como el auditor escala cada pareja."""
    target = ACCOUNT_LOT_NORMALIZATION.get(account_key, {}).get(
        str(getattr(deal, "symbol", "") or "").upper(),
    )
    volume = float(getattr(deal, "volume", 0.0) or 0.0)
    if target is None or volume <= 0:
        return deal
    factor = target / volume
    return replace(
        deal,
        commission=float(getattr(deal, "commission", 0.0) or 0.0) * factor,
        swap=float(getattr(deal, "swap", 0.0) or 0.0) * factor,
        profit=float(getattr(deal, "profit", 0.0) or 0.0) * factor,
    )


def movements_from_html(
    contents: bytes, account_key: str = "",
) -> list[tuple[datetime, float]]:
    """Los cierres de la cuenta que trae ese informe, ordenados y en UTC."""
    if not contents:
        raise ValueError("El informe nativo de la cuenta real está vacío")
    with tempfile.TemporaryDirectory(prefix="mt5-account-") as folder:
        path = Path(folder) / "real_account_report.html"
        path.write_bytes(contents)
        report = parse_report(path)
    return movements(normalized_deal(deal, account_key) for deal in report.raw_deals)
