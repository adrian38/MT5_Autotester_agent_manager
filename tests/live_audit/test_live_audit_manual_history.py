"""El histórico de un portafolio manual también cuenta como histórico.

«Contra el histórico» contestaba «SIN HISTÓRICO» en las 27 filas de
`Christmas_Axi`, y el rótulo decía la verdad a medias: no hay backtest **de ese
candidato en la memoria UBS**, porque un portafolio manual no sale del pool y
sus miembros son `manual:N`. Pero el histórico existe: lo genera el botón
«Generar histórico OHLC» de la pantalla del portafolio manual, que deja el
informe en `<proyecto>/reports/` con el nombre del set. Las 32 estrategias de
ese portafolio lo tenían.

Lo avisó el usuario —«fíjate que sí tienen histórico»— después de que yo diera
por bueno el rótulo.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import live_audit_degradation as degradation


def trade(profit: float, day: int) -> SimpleNamespace:
    return SimpleNamespace(
        profit_loss=profit, close_time=datetime(2026, 1, day, tzinfo=timezone.utc),
    )


def report(profits: list[float], deposit: float = 1000.0) -> SimpleNamespace:
    return SimpleNamespace(
        trades=[trade(value, index + 1) for index, value in enumerate(profits)],
        initial_deposit=deposit, path=r"F:\reports\ubs-tsla.nas-h1-tsv01.htm",
        period_start=datetime(2021, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class HistoryFromReportTests(unittest.TestCase):
    def test_it_reads_what_the_memory_would_have_stored(self) -> None:
        history = degradation.history_from_report(report([3.0, -1.0, 2.0, 4.0]))
        self.assertEqual(history.trades, 4)
        self.assertEqual(history.net_profit, 8.0)
        self.assertEqual(history.average_trade, 2.0)
        self.assertAlmostEqual(history.profit_factor, 9.0)
        # Cinco años de backtest.
        self.assertAlmostEqual(history.active_months, 60.0, places=0)

    def test_the_sqn_comes_out_of_the_operations(self) -> None:
        # La memoria lo guarda hecho; un informe OHLC no. Es la misma cifra:
        # media / desviación * raíz(n).
        history = degradation.history_from_report(report([3.0, -1.0, 2.0, 4.0]))
        self.assertAlmostEqual(history.sqn, 2.0 / 2.1602468994692865 * 2, places=6)
        self.assertIsNotNone(history.trade_deviation)

    def test_the_drawdown_is_the_valley_in_closing_order(self) -> None:
        # El orden importa y no es el del informe: una curva recorrida en otro
        # orden da otro valle.
        self.assertAlmostEqual(
            degradation.history_from_report(report([5.0, -3.0, -4.0, 9.0])).drawdown, 7.0,
        )

    def test_a_report_without_operations_is_not_a_history(self) -> None:
        self.assertIsNone(degradation.history_from_report(report([])))

    def test_one_single_operation_has_no_dispersion(self) -> None:
        history = degradation.history_from_report(report([3.0]))
        self.assertEqual(history.trades, 1)
        self.assertIsNone(history.sqn)


class Source:
    """Lo único que `read_histories` usa de un `PortfolioSource`."""

    def __init__(self, memory_sources=()) -> None:
        self.project = Path(r"F:\proyecto")
        self.memory_sources = list(memory_sources)


class ReadHistoriesTests(unittest.TestCase):
    def _read(self, candidate_ids, set_names, found=None):
        with patch.object(
            degradation.portfolio_manual_reports, "report_for_set",
            side_effect=lambda _project, name: Path(f"F:/reports/{name}.htm") if name else None,
        ), patch.object(degradation, "cached_report", return_value=report([3.0, -1.0, 2.0, 4.0])):
            return degradation.read_histories(Source(found or ()), candidate_ids, set_names)

    def test_a_manual_member_gets_its_history_from_its_report(self) -> None:
        # `manual` no es el scope de ninguna memoria, así que el bucle de
        # arriba no contesta nada y antes la fila moría ahí.
        histories = self._read(["manual:10"], {"manual:10": "ubs-tsla.nas-h1-tsv01.set"})
        self.assertEqual(list(histories), ["manual:10"])
        self.assertEqual(histories["manual:10"].trades, 4)

    def test_without_a_set_name_there_is_nothing_to_look_for(self) -> None:
        self.assertEqual(self._read(["manual:10"], {}), {})
        self.assertEqual(self._read(["manual:10"], None), {})

    def test_a_report_that_is_not_there_is_simply_without_history(self) -> None:
        with patch.object(
            degradation.portfolio_manual_reports, "report_for_set", return_value=None,
        ):
            self.assertEqual(
                degradation.read_histories(Source(), ["manual:10"], {"manual:10": "x.set"}), {},
            )

    def test_an_unreadable_report_does_not_bring_down_the_screen(self) -> None:
        with patch.object(
            degradation.portfolio_manual_reports, "report_for_set",
            return_value=Path("F:/reports/x.htm"),
        ), patch.object(degradation, "cached_report", side_effect=OSError("ilegible")):
            self.assertEqual(
                degradation.read_histories(Source(), ["manual:10"], {"manual:10": "x.set"}), {},
            )


class PoolStillWinsTests(unittest.TestCase):
    def test_a_candidate_the_memory_answered_is_not_overwritten(self) -> None:
        # El informe homónimo es el recurso de quien no está en el pool. Un
        # candidato se sigue midiendo contra lo que la memoria guarda de él.
        stored = degradation.StrategyHistory(
            trades=99, net_profit=1.0, average_trade=0.01, sqn=1.0,
            active_months=None, drawdown=None, profit_factor=None,
        )
        with patch.object(degradation, "_read_memory", return_value={"AXI/STANDARD:7": stored}), \
                patch.object(
                    degradation.portfolio_manual_reports, "report_for_set",
                    return_value=Path("F:/reports/x.htm"),
                ), patch.object(degradation, "cached_report", return_value=report([3.0, 4.0])):
            source = Source([("AXI/STANDARD", Path("F:/memoria.sqlite"))])
            histories = degradation.read_histories(
                source, ["AXI/STANDARD:7"], {"AXI/STANDARD:7": "x.set"},
            )
        self.assertEqual(histories["AXI/STANDARD:7"].trades, 99)


if __name__ == "__main__":
    unittest.main()
