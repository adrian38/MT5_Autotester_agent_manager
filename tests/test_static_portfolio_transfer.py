from __future__ import annotations

import unittest
from pathlib import Path

from lxml import html


class PortfolioImportScreenTests(unittest.TestCase):
    """Importar existe en los tres ámbitos y hereda el transporte de exportar."""

    PAGES = ("portfolios", "portfolios_monthly", "portfolios_grid")

    def static(self, name: str) -> str:
        return (
            Path(__file__).parents[1] / "mt5_manager" / "static" / name
        ).read_text(encoding="utf-8")

    def test_every_scope_offers_the_import_button(self) -> None:
        for name in self.PAGES:
            page, script = self.static(f"{name}.html"), self.static(f"{name}.js")
            with self.subTest(page=name):
                self.assertIn('id="portfolio-import"', page)
                self.assertIn('src="/portfolio_transfer.js"', page)
                self.assertIn("pickPortfolioImportSource(", script)
                self.assertIn("describePortfolioImport(data)", script)
                # El velo cubre la reconstruccion, nunca el selector de origen.
                self.assertLess(
                    script.index("pickPortfolioImportSource("),
                    script.index("portfolioImportProgress(label)"),
                )
                self.assertIn("portfolioImportProgress(label)", script)

    def test_the_import_mirrors_the_export_transport(self) -> None:
        # Con `export_mode=folder` el manager abre su selector nativo; con
        # `download` el ZIP viaja desde el navegador. Si la importación solo
        # cubriera uno, quedaría inservible en el otro despliegue.
        transfer = self.static("portfolio_transfer.js")
        self.assertIn("exportMode === 'download'", transfer)
        self.assertIn("'choose-import-folder'", transfer)
        self.assertIn("readAsDataURL", transfer)


class ExclusionReasonScreenTests(unittest.TestCase):
    """Las tres pantallas piden el motivo y reparten la cuarentena en tres tablas.

    Los tres ámbitos tienen interfaz separada a propósito, así que sin una prueba
    que los recorra a los tres una mejora se queda en la pantalla donde nació.
    """

    PAGES = ("portfolios", "portfolios_monthly", "portfolios_grid")

    def static(self, name: str) -> str:
        return (
            Path(__file__).parents[1] / "mt5_manager" / "static" / name
        ).read_text(encoding="utf-8")

    def test_every_scope_has_the_two_verdict_tables(self) -> None:
        for name in self.PAGES:
            page = self.static(f"{name}.html")
            with self.subTest(page=name):
                self.assertIn('id="quarantine-rows"', page)
                self.assertIn('id="quarantine-degradation-rows"', page)
                self.assertIn('id="quarantine-ohlc-rows"', page)
                self.assertIn('src="/exclusion_reason.js"', page)

    def test_the_two_verdict_panels_share_a_row_of_equal_columns(self) -> None:
        # Sueltos caían en las columnas 1fr/1.3fr del inventario y el de la
        # izquierda salía estrecho, partiendo la fecha en dos líneas.
        styles = self.static("styles.css")
        self.assertIn(".portfolio-inventory-verdicts{grid-column:1/-1", styles)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", styles)
        for name in self.PAGES:
            page = html.fromstring(self.static(f"{name}.html"))
            with self.subTest(page=name):
                wrappers = page.xpath('//div[@class="portfolio-inventory-verdicts"]')
                self.assertEqual(len(wrappers), 1)
                self.assertEqual(
                    [child.get("class") for child in wrappers[0]],
                    ["inventory-panel", "inventory-panel"],
                )

    def test_the_radio_of_each_option_escapes_the_global_input_rule(self) -> None:
        # `input,select{width:100%;padding:10px;border:...}` alcanza también a
        # los radios: cada uno era una caja ancha con el punto centrado dentro,
        # así que caía en una x distinta en cada fila según su texto.
        styles = self.static("styles.css")
        self.assertIn(".reason-option input{width:auto", styles)
        self.assertIn("padding:0;border:0", styles)

    def test_the_toast_wraps_long_windows_paths(self) -> None:
        # Los avisos llevan rutas de Windows, que no tienen espacios: sin
        # permitir el corte dentro de la palabra el texto se salía del recuadro.
        styles = self.static("styles.css")
        self.assertIn("overflow-wrap:anywhere", styles)
        self.assertIn("max-width:min(420px,calc(100vw - 48px))", styles)

    def test_the_panel_note_lives_inside_the_panel_padding(self) -> None:
        # El panel no tiene padding propio: lo pone .panel-title. Sin esto la
        # nota salía pegada al borde y desalineada con el título.
        self.assertIn(".quarantine-verdict-note{margin:0;padding:11px 15px 0", self.static("styles.css"))

    def test_every_scope_asks_for_the_reason_and_sends_its_code(self) -> None:
        for name in self.PAGES:
            script = self.static(f"{name}.js")
            with self.subTest(script=name):
                # UBS normal añade la acción por set de la ventana de símbolo;
                # las otras pantallas conservan las dos exclusiones históricas.
                expected = 3 if name == "portfolios" else 2
                self.assertEqual(script.count("reason_code: reasonCode"), expected)
                self.assertEqual(script.count("await askExclusionReason("), expected)
                self.assertIn("renderQuarantineTables(quarantine);", script)

    def test_the_three_reason_codes_match_the_python_side(self) -> None:
        script = self.static("exclusion_reason.js")
        from mt5_manager import candidate_verdict

        for code in candidate_verdict.REASON_CODES:
            self.assertIn(f"code: '{code}'", script)

    def test_the_table_button_offers_the_three_reasons_and_the_pool(self) -> None:
        # El botón de la tabla no es «Reintegrar» a secas: mueve la estrategia
        # entre los tres motivos y el pool, que son estados de la misma cosa.
        script = self.static("exclusion_reason.js")
        self.assertIn("code: 'pool'", script)
        self.assertIn("options: [...EXCLUSION_REASONS, POOL_TARGET]", script)
        # Excluir NO ofrece el pool: no es un motivo de exclusión.
        self.assertIn("options: EXCLUSION_REASONS,", script)
        self.assertIn('onclick="requalifyStrategy(', script)

    def test_every_scope_sends_the_requalification_to_its_own_endpoint(self) -> None:
        for name in self.PAGES:
            script = self.static(f"{name}.js")
            with self.subTest(script=name):
                self.assertIn("async function requalifyStrategy(", script)
                self.assertIn("postManager('requalify'", script)
                self.assertIn("reason_code: target", script)
                self.assertNotIn("postManager('release'", script)

    def test_choosing_the_current_state_reports_that_nothing_changed(self) -> None:
        helper = self.static("exclusion_reason.js")
        self.assertIn("function quarantineTargetIsTheSame(", helper)
        for name in self.PAGES:
            script = self.static(f"{name}.js")
            with self.subTest(script=name):
                expected = 2 if name == "portfolios" else 1
                self.assertEqual(script.count("quarantineTargetIsTheSame("), expected)
                self.assertNotIn("target === currentCode) return;", script)
                self.assertNotIn("target === row.reason_code) return;", script)


if __name__ == "__main__":
    unittest.main()
