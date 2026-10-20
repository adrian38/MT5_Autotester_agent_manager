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
from datetime import datetime
from pathlib import Path

from portfolio_manager.mt5_report import parse_report

from .live_account_series import movements


def movements_from_html(contents: bytes) -> list[tuple[datetime, float]]:
    """Los cierres de la cuenta que trae ese informe, ordenados y en UTC."""
    if not contents:
        raise ValueError("El informe nativo de la cuenta real está vacío")
    with tempfile.TemporaryDirectory(prefix="mt5-account-") as folder:
        path = Path(folder) / "real_account_report.html"
        path.write_bytes(contents)
        report = parse_report(path)
    return movements(report.raw_deals)
