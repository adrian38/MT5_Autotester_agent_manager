"""Importar un portafolio desde su exportación.

El caso real: se exporta, se borra del manager, y meses después hace falta que
sus sets sigan contando como usados para que la siguiente generación no los
repita. Lo que se comprueba aquí es que la fila importada es la misma que la de
un guardado normal — no una copia degradada del texto del resumen — y que sus
sets vuelven a bloquear el pool.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import portfolio_import
from mt5_manager.portfolio_service import (
    PortfolioCoordinator,
    PortfolioSource,
    _imported_target_month,
    build_import_proposals,
    save_proposal,
)
from portfolio_manager.ubs_portfolio import (
    PeriodReport,
    RobustStrategySet,
    build_robust_strategy_set,
)

SUMMARY = """Portafolio: A/M/C | Base Moderado | 2 sets | 09.08.2026 13:01
Alias: Londres estable
Tipo: bundle   Capital: 10,000
DD valle objetivo: 300.00
DD puntual objetivo: 300.00
DD valle usado: 254.31
DD puntual usado: 120.00
Net profit total 2020-2026: 4,120.55

Sets exportados: copia exacta del .set original probado.
No se modifica Risk, LotPerBalance_step, grid ni ningun otro parametro del EA.
UNID. y LOTE son la asignacion informativa calculada por el portafolio.

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
Agresivo     ICTRADING    EURUSD       H1          3    0.03   alpha.set
Agresivo     ICTRADING    GBPUSD       H1          2    0.02   beta.set
Moderado     ICTRADING    EURUSD       H1          2    0.02   alpha.set
Moderado     ICTRADING    GBPUSD       H1          1    0.01   beta.set
Conservador  ICTRADING    EURUSD       H1          1    0.01   alpha.set
Conservador  ICTRADING    GBPUSD       H1          1    0.01   beta.set
"""

IMPROVEMENT_SUMMARY = """Portafolio: Mejora del portafolio #73 | modo Moderado
Tipo: balanced   Capital: 10,000
Mejora origen: 73
Mejora modo: balanced
Mejora prioridad: stress
Mejora incorporaciones: 1
DD valle objetivo: 300.00
DD puntual objetivo: 300.00

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
Moderado     ICTRADING    EURUSD       H1          2    0.02   alpha.set
Moderado     ICTRADING    GBPUSD       H1          1    0.01   beta.set
"""

CHAINED_IMPROVEMENT_SUMMARY = """Portafolio: Mejora del portafolio #36 | modo Moderado
Tipo: balanced   Capital: 10,000
Portafolio UID: 33333333-3333-4333-8333-333333333333
Mejora etiqueta: Mejora del portafolio #36 | modo Moderado
Mejora origen: 36
Mejora modo: balanced
Mejora origen UID: 22222222-2222-4222-8222-222222222222
Mejora raiz: 14
Mejora raiz UID: 11111111-1111-4111-8111-111111111111
Mejora nivel: 2
Mejora linaje JSON: [{"portfolio_id":14,"portfolio_uid":"11111111-1111-4111-8111-111111111111","label":"Portafolio #14","mode":"balanced"},{"portfolio_id":36,"portfolio_uid":"22222222-2222-4222-8222-222222222222","label":"Mejora del portafolio #14 | modo Moderado","mode":"balanced"}]
Mejora snapshot JSON: {"id":36,"portfolio_uid":"22222222-2222-4222-8222-222222222222","portfolio_type":"balanced","label":"Mejora del portafolio #14 | modo Moderado","total_net_profit":100,"actual_valley_dd":12,"active_strategies":1,"total_units":2,"total_lot":0.02,"members":[]}
Mejora prioridad: balanced
Mejora incorporaciones: 1
DD valle objetivo: 300.00
DD puntual objetivo: 300.00

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
Moderado     ICTRADING    EURUSD       H1          2    0.02   alpha.set
Moderado     ICTRADING    GBPUSD       H1          1    0.01   beta.set
"""


# Lo que exporta de verdad una mejora: `save_proposal` guarda sus miembros con
# `variant_key` vacio —la variante es la fila entera, no una de tres— y la
# columna PERFIL sale en blanco. Tomado de PORTAFOLIO_120/121 de RoboForex, que
# no se podian importar. El modo solo esta en la cabecera.
BLANK_PROFILE_IMPROVEMENT_SUMMARY = """Portafolio: Mejora del portafolio #104 | modo Agresivo
Tipo: aggressive   Capital: 10,000
Portafolio UID: 44444444-4444-4444-8444-444444444444
Mejora etiqueta: Mejora del portafolio #104 | modo Agresivo
Mejora origen: 104
Mejora modo: aggressive
Mejora origen UID: 11111111-1111-4111-8111-111111111111
Mejora raiz: 104
Mejora nivel: 1
Mejora prioridad: balanced
Mejora incorporaciones: 1
DD valle objetivo: 300.00
DD puntual objetivo: 300.00

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
             ICTRADING    EURUSD       H1          3    0.03   alpha.set
             ICTRADING    GBPUSD       H1          2    0.02   beta.set
"""

def period(symbol: str, name: str, start: int, end: int, *, net: float = 100.0) -> PeriodReport:
    return PeriodReport(
        period_name=name, start_year=start, end_year=end, symbol=symbol, timeframe="H1",
        pnl_curve_001=[0.0, net], net_profit_001=net, valley_dd_001=10.0, point_dd_001=4.0,
        profit_factor=2.0, return_dd_ratio=net / 10.0, trades=120,
        balance_dd_metric_001=6.0, equity_dd_metric_001=8.0,
    )


def strategy(set_path: str, symbol: str, candidate: int, net: float) -> RobustStrategySet:
    return build_robust_strategy_set(
        set_id=set_path, candidate_id=f"ICTRADING/STANDARD:{candidate}", symbol=symbol,
        timeframe="H1", strategy_family="", robustness_status="accepted", already_used=False,
        report_2020_2024=period(symbol, "2020_2024", 2020, 2024, net=net),
        report_2025_2026=period(symbol, "2025_2026", 2025, 2026, net=net / 2),
        set_path=set_path, is_report_path=f"{set_path}.is.html", oos_report_path=f"{set_path}.oos.html",
    )


class ImportRoundTripTestCase(unittest.TestCase):
    def _source(self, project: Path):
        (project / "outputs").mkdir(parents=True, exist_ok=True)
        (project / "assets").mkdir(exist_ok=True)
        (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
        return PortfolioSource({
            "portfolio_project_dir": str(project),
            "portfolio_broker": "ICTRADING",
            "portfolio_account_type": "STANDARD",
        })

    def _candidates(self, project: Path) -> list[dict]:
        return [
            {
                "candidate_id": f"ICTRADING/STANDARD:{index}",
                "set_path": str(project / name), "source_memory_path": str(project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"),
                "account_type": "ICTRADING/STANDARD", "source_candidate_id": index,
                "target_symbol": symbol, "symbol": symbol, "period": "H1",
                "is_report_path": "", "oos_report_path": "",
            }
            for index, (name, symbol) in enumerate((("alpha.set", "EURUSD"), ("beta.set", "GBPUSD")), start=1)
        ]
