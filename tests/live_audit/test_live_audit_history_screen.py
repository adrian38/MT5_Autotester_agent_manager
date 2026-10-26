"""La pantalla de la serie: que la tabla del histórico diga lo mismo que la
del periodo.

Vive aparte de `test_live_audit_history`, que mide el archivo y la
acumulación: esto no toca datos, sólo lee el HTML y los `.js`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class HistoryScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from mt5_manager.manager_handler import STATIC_FILES

        cls.static_files = STATIC_FILES
        static = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"
        cls.page = (static / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.script = (static / "live_audit_history.js").read_text(encoding="utf-8")
        cls.analysis = (static / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.verdicts = (static / "live_audit_history_verdicts.js").read_text(encoding="utf-8")
        cls.card = (static / "dashboard_metrics.js").read_text(encoding="utf-8")

    def test_the_second_table_lives_next_to_the_first_one(self) -> None:
        self.assertIn("live_audit_history.js", self.static_files)
        self.assertIn('<script src="/live_audit_history.js"></script>', self.page)
        self.assertIn('id="history-body"', self.page)
        self.assertIn('id="history-count"', self.page)
        # La del periodo se queda como estaba.
        self.assertIn('id="reproduction-body"', self.page)

    def test_the_historic_table_uses_the_same_verdict_as_the_period_one(self) -> None:
        # El veredicto sigue siendo el de la tabla del periodo; lo pide el
        # módulo que calcula, no la pantalla.
        self.assertIn("reproduction.verdict(numbers)", self.verdicts)
        self.assertIn("reproduction.numbers(group, lots)", self.verdicts)
        self.assertIn("reproduction.compare(left.numbers, right.numbers)", self.verdicts)
        self.assertIn("/history", self.script)

    def test_few_operations_uses_the_same_visible_badge_as_the_period(self) -> None:
        badge = '<span class="degradation-status info">POCAS OPERACIONES</span>'
        self.assertIn(badge, self.analysis)
        self.assertIn(badge, self.script)

    def test_the_table_and_the_card_share_one_single_calculation(self) -> None:
        """Dos cálculos daban dos respuestas: la tarjeta marcaba «NO
        REPRODUCE» tres EURUSD del #173 que la tabla daba por buenos.

        Ahora la pantalla y la tarjeta sólo pintan lo que devuelve
        `LiveAuditHistoryVerdicts`, y ninguna de las dos suma nada.
        """
        self.assertIn("live_audit_history_verdicts.js", self.static_files)
        self.assertIn(
            '<script src="/live_audit_history_verdicts.js"></script>', self.page,
        )
        self.assertIn("LiveAuditHistoryVerdicts.ranked(series.executions || []", self.script)
        self.assertIn("root.LiveAuditHistoryVerdicts.flagged(executions, {", self.card)
        # Y no queda rastro de la segunda copia en ninguna de las dos.
        for source in (self.script, self.card):
            for gone in ("mergeGroups", "mergeHistoryGroups", "LiveAuditReproduction.numbers("):
                self.assertNotIn(gone, source)

    def test_the_historic_table_honours_the_lead_box_but_not_the_typed_lots(self) -> None:
        # La casilla dice de qué lado se copia el lote, y cada operación copia
        # el de su propia auditoría (`scale_volume`). Sin ella la demo se
        # quedaba a 0,01 contra una real a 0,10 y el cociente medía tamaño.
        self.assertIn("function executedLots(overrides)", self.verdicts)
        self.assertIn("return mode ? {$mode: mode} : {};", self.verdicts)
        self.assertNotIn("const EXECUTED_LOTS", self.verdicts)
        self.assertIn("const lots = executedLots(overrides);", self.verdicts)
        # Y las dos pantallas entregan la misma casilla al cálculo.
        self.assertIn("overrides: typeof lotOverrides === 'object' ? lotOverrides : {},", self.script)
        self.assertIn("result, historyOf(entry), overrides,",
                      (Path(__file__).resolve().parents[2] / "mt5_manager" / "static"
                       / "dashboard.js").read_text(encoding="utf-8"))
        # Y alguien tiene que avisarla: carga antes que los lotes guardados.
        self.assertIn("function renderHistoryTable()", self.analysis)
        self.assertIn("window.LiveAuditHistory.render()", self.analysis)
        self.assertIn("renderHistoryTable();", self.analysis)

    def test_an_unpaired_demo_can_use_a_real_lot_from_another_audit(self) -> None:
        # La serie se junta antes de resolver cercanía: si un periodo no tiene
        # pareja pero el anterior sí, ésa sigue siendo una referencia real de
        # la misma estrategia y no se pierde al cruzar la frontera semanal.
        self.assertIn(
            "const timeline = reproduction.realLotTimeline((executions || []).flatMap(",
            self.verdicts,
        )
        self.assertIn("groupsOf(execution.comparison_detail || {}, timeline)", self.verdicts)

    def test_an_attributed_real_creates_its_group_without_tester_rows(self) -> None:
        self.assertIn("reproduction.attributedOwner(row)", self.verdicts)
        self.assertIn(
            "if (!groups.has(owner.strategy)) groups.set(owner.strategy",
            self.verdicts,
        )
        self.assertIn("identities.push(`${owner.strategy}|${owner.label}`)", self.verdicts)

    def test_it_says_which_portfolios_it_inherits_from_and_with_what_limit(self) -> None:
        self.assertIn("inherited_portfolios", self.script)
        self.assertIn("hereda el histórico de", self.script)
        self.assertIn("sólo de las estrategias que conserva", self.script)

    def test_the_table_only_shows_the_strategies_the_portfolio_keeps(self) -> None:
        # El servidor ya recorta; la pantalla no puede volver a abrir la mano
        # con las ejecuciones propias, que era lo que hacía antes.
        self.assertIn("function retained()", self.script)
        self.assertIn("if (retained?.size && !retained.has(identity(group))) return;", self.verdicts)
        self.assertNotIn("execution.own", self.script)

    def test_the_degradation_table_announces_that_it_accumulates(self) -> None:
        self.assertIn("renderAccumulatedNote", self.analysis)
        self.assertIn("acumulado ${escapeHtml(atHistoryLot", self.analysis)
        self.assertIn("todas las auditorías archivadas", self.page)
        # El desglose por estrategia sigue siendo el de la auditoría actual.
        self.assertIn("Análisis por estrategia", self.page)


if __name__ == "__main__":
    unittest.main()
