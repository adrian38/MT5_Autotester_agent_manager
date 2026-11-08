"""Partir la pasada OHLC de un set manual en los dos tramos IS y OOS."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from portfolio_manager.mt5_report import StrategyReport, Trade
from portfolio_manager.ubs_portfolio import (
    load_robust_sets_from_rows,
    period_report_from_strategy_report,
    split_report_at_year,
)


def _trade(close: datetime, profit: float) -> Trade:
    return Trade(
        ticket=str(int(close.timestamp())), trade_type="buy", open_time=close,
        open_price=1.0, size=0.01, close_time=close, close_price=1.0,
        profit_loss=profit, comment="",
    )


def _single_pass_report(path: Path) -> StrategyReport:
    """La pasada OHLC completa de un set manual: un informe, 2020 a 2026."""
    return StrategyReport(
        path=path, name=path.stem, expert="manual", symbol="XAUUSD.sa",
        timeframe="D1", period_start="06.01.2020", period_end="12.01.2026",
        initial_deposit=1000.0,
        metrics={
            "Beneficio Neto": "100.00", "Beneficio Bruto": "140.00",
            "Perdidas Brutas": "40.00", "Factor de Beneficio": "3.50",
            "Reduccion maxima del balance": "25.00",
            "Reduccion maxima de la equidad": "30.00",
        },
        monthly={}, raw_deals=[], image_paths={}, set_path=None,
        trades=[
            _trade(datetime(2021, 3, 4), 60.0), _trade(datetime(2024, 11, 8), -20.0),
            _trade(datetime(2025, 2, 1), 80.0), _trade(datetime(2026, 1, 9), -20.0),
        ],
    )


class SinglePassSplitTests(unittest.TestCase):
    """Partir la pasada OHLC completa de un set que nunca pasó el embudo."""

    def test_the_two_halves_rebuild_the_whole_pass(self) -> None:
        """El corte es por fecha de cierre sobre el mismo listado, así que no
        inventa nada. Medido el 2026-11-08 sobre `strategy1_Daily` y
        `9637014_bk_tsla_vol_d1atr` de AXI: beneficio, valle, punto y
        operaciones coinciden al céntimo con el informe entero."""
        whole = _single_pass_report(Path("manual_000110_strategy1_Daily.htm"))
        entire = period_report_from_strategy_report(whole, "2020_2026")

        head, tail = split_report_at_year(whole)
        is_period = period_report_from_strategy_report(head, "2020_2024")
        oos_period = period_report_from_strategy_report(tail, "2025_2026")

        self.assertEqual(is_period.trades + oos_period.trades, entire.trades)
        self.assertAlmostEqual(
            is_period.net_profit_001 + oos_period.net_profit_001,
            entire.net_profit_001,
        )
        self.assertAlmostEqual(is_period.net_profit_001, 40.0)
        self.assertAlmostEqual(oos_period.net_profit_001, 60.0)

    def test_each_half_recomputes_its_totals_but_keeps_the_risk(self) -> None:
        """Con el neto del HTML, cada tramo diría el beneficio de los dos. Las
        reducciones máximas sí se conservan: son las de la pasada entera, así
        que el tramo hereda un riesgo que nunca se queda corto."""
        head, tail = split_report_at_year(_single_pass_report(Path("x.htm")))

        self.assertNotIn("Beneficio Neto", head.metrics)
        self.assertNotIn("Factor de Beneficio", tail.metrics)
        self.assertEqual(head.metrics["Reduccion maxima de la equidad"], "30.00")
        self.assertEqual(head.period_start, "04.03.2021")
        self.assertEqual(head.period_end, "08.11.2024")
        self.assertEqual(tail.period_start, "01.02.2025")

    def test_a_pass_on_one_side_of_the_cut_is_refused(self) -> None:
        whole = _single_pass_report(Path("x.htm"))
        whole.trades = [_trade(datetime(2023, 5, 5), 10.0)]

        with self.assertRaisesRegex(ValueError, "2025"):
            split_report_at_year(whole)

    def test_the_loader_builds_the_strategy_from_the_single_pass(self) -> None:
        """Sin el par IS/OOS la fila se cargaba como «faltan reportes»."""
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "manual_000110_strategy1_Daily.htm"
            report_path.touch()
            row = {
                "set_path": str(Path(temp_dir) / "strategy1_Daily.set"),
                "candidate_id": "manual", "target_symbol": "XAUUSD.sa", "period": "D1",
                "is_report_path": "", "oos_report_path": "",
                "single_pass_report_path": str(report_path),
            }

            loaded, warnings = load_robust_sets_from_rows(
                [row], [], parse=lambda path: _single_pass_report(Path(path)),
            )

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].symbol, "XAUUSD")
        self.assertEqual(loaded[0].trades_2020_2026, 4)
        self.assertAlmostEqual(loaded[0].net_profit_2020_2026_001, 100.0)
        self.assertEqual(loaded[0].is_report_path, str(report_path))
        self.assertEqual(warnings, [])

    def test_a_row_without_the_key_is_untouched(self) -> None:
        loaded, warnings = load_robust_sets_from_rows(
            [{"set_path": "x.set", "is_report_path": "", "oos_report_path": ""}],
            [], parse=lambda path: _single_pass_report(Path(path)),
        )

        self.assertEqual(loaded, [])
        self.assertTrue(any("faltan reportes" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()

