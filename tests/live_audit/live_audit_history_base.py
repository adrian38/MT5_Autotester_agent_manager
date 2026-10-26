"""Las operaciones y el detalle de comparacion que comparten las dos mitades.

La serie se archiva en `test_live_audit_history` y se sirve en
`test_live_audit_history_routes`; ambas necesitan fabricar el mismo
detalle de comparacion que escribe el nodo.
"""
from __future__ import annotations


def trade(symbol: str, close: str, profit: float, volume: float = 0.01) -> dict[str, object]:
    return {
        "symbol": symbol, "close_time": close, "open_time": close,
        "profit": profit, "volume": volume,
    }


def detail(strategy: str, trades: list[dict[str, object]], symbol: str = "EURUSD") -> dict[str, object]:
    return {
        "operation_comparisons": [
            {"strategy": strategy, "strategy_set": f"{strategy}.set",
             "tester": {**row, "strategy": strategy}, "real": row}
            for row in trades
        ],
        "strategy_summary": [{"strategy": strategy, "strategy_set": f"{strategy}.set"}],
        "methodology": {"ignorado": True},
    }
