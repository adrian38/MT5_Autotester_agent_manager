"""Contrato de la pantalla de Análisis: escalado al lote real y cuadre.

Salió de `test_live_audit_configuration_screen.py`, que se pasaba de las 600
líneas. Aquí viven las comprobaciones de lo que esa pantalla decide —a qué lote
se lleva la demo, qué entra en cada curva y dónde acaba cada real—; en el otro
quedan la configuración del uso y la pantalla de resultado.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lxml import html


class LiveAuditAnalysisScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        cls.analysis_page_text = (static_dir / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.analysis_script = (static_dir / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.reproduction_script = (static_dir / "live_audit_reproduction.js").read_text(
            encoding="utf-8",
        )
        cls.analysis_css = (static_dir / "live_audit_analysis.css").read_text(encoding="utf-8")
        cls.analysis_page = html.fromstring(cls.analysis_page_text)

    def test_the_demo_curve_is_scaled_to_the_lot_the_real_account_executed(self) -> None:
        # La cuenta real lleva la batuta. Escalar al lote que el autolote
        # calcula mete en la comparación la desviación que la auditoría está
        # midiendo: en la auditoría 148 de RoboForex, 65 de 94 operaciones
        # ejecutaron un lote distinto del calculado.
        self.assertIn("function scaleTarget(row, timeline)", self.analysis_script)
        self.assertIn("row?.real || row?.real_position_still_open", self.reproduction_script)
        self.assertIn("return {volume: executed, source: 'real'}", self.reproduction_script)
        self.assertIn("trade.scale_volume", self.analysis_script)
        self.assertIn("applyLots({$mode: 'autolot'})", self.analysis_script)
        self.assertIn("La curva Demo está escalada al lote de la cuenta real", self.analysis_script)
        self.assertIn("La cuenta real lleva la batuta", self.analysis_script)
        self.assertIn("Escalar demo al lote real", self.analysis_page_text)

    def test_which_column_leads_is_a_choice_and_either_one_can_be_unmarked(self) -> None:
        # Las dos casillas de la cabecera deciden qué lotes hacen el escalado.
        # La marcada no se toca: se mueve la otra curva. Son casillas y no
        # botones de radio porque «ninguna» tiene que poder alcanzarse con el
        # ratón, y sin marca es como se ve lo que cada parte ejecutó.
        self.assertIn('id="lots-lead-demo"', self.analysis_page_text)
        self.assertIn('id="lots-lead-real"', self.analysis_page_text)
        self.assertIn('type="checkbox"', self.analysis_page_text)
        self.assertNotIn('type="radio"', self.analysis_page_text)
        self.assertIn(
            "const LEAD_BOXES = {tester: '#lots-lead-demo', autolot: '#lots-lead-real'}",
            self.analysis_script,
        )
        # Una excluye a la otra, y el diálogo vuelve a abrirse con lo elegido.
        self.assertIn("if (!event.target.checked) return;", self.analysis_script)
        self.assertIn(
            "leadBox(mode).checked = lotOverrides.$mode === mode;", self.analysis_script,
        )
        # Sin casilla marcada no se escribe `$mode`, y sin `$mode` no se mueve
        # ninguna curva: cada parte se dibuja con el lote que ejecutó.
        self.assertIn(
            "applyLots(mode ? {...readDialogLots(), $mode: mode} : readDialogLots())",
            self.analysis_script,
        )
        self.assertIn("const SCALED_SIDES = {autolot: 'demo', tester: 'real'}", self.reproduction_script)
        self.assertIn(
            "return SCALED_SIDES[overrides?.$mode] || null;", self.reproduction_script,
        )

    def test_with_the_tester_leading_the_real_curve_drops_to_the_backtest_lot(self) -> None:
        # El sentido contrario al del autolote, y el único lote que puede
        # copiar: el de su pareja del backtest. Sin pareja no hay de dónde
        # copiarlo —una atribuida a mano, o una suelta— y conserva el suyo,
        # que es lo que la cuenta ejecutó de verdad.
        self.assertIn("function pairedReal(row)", self.reproduction_script)
        self.assertIn("const lot = Number(row?.tester?.volume);", self.reproduction_script)
        self.assertIn(
            "{...row.real, scale_volume: lot, scale_source: 'tester'}", self.reproduction_script,
        )
        self.assertIn(": {...row.real};", self.reproduction_script)
        self.assertIn(
            "group.real.push(LiveAuditReproduction.pairedReal(row))", self.analysis_script,
        )
        # Y la escala la elige el modo, no el lado: antes `sideSeries` sólo
        # sabía escalar la demo.
        self.assertIn("const automatic = scaledSide(overrides) === side", self.reproduction_script)
        self.assertIn("La curva Real está escalada al lote del tester.", self.analysis_script)
        self.assertIn("ESCALADA AL LOTE DEL TESTER", self.analysis_script)
        self.assertIn(
            "Los importes de la cuenta real están escalados al lote del tester",
            self.analysis_script,
        )

    def test_the_front_page_scales_the_same_way_as_the_analysis_screen(self) -> None:
        # La portada dibuja la misma comparación desde el mismo perfil: si no
        # apunta el lote de la pareja, con el tester al mando enseñaría la
        # curva real en bruto y Análisis la escalada.
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        metrics = (static_dir / "dashboard_metrics.js").read_text(encoding="utf-8")
        self.assertIn("group.real.push(reproduction.pairedReal(row))", metrics)

    def test_an_unpaired_demo_uses_the_nearest_observed_real_lot(self) -> None:
        # Busca sólo dentro de la misma estrategia y por apertura. Sin ninguna
        # real observada usa el autolote/tramo de esa apertura y, si tampoco lo
        # hay, conserva el lote del tester.
        self.assertIn("function realLotTimeline(rows)", self.reproduction_script)
        self.assertIn("timeline.get(identity).push({time, volume})", self.reproduction_script)
        self.assertIn("timeline?.get(rowIdentity(row))", self.reproduction_script)
        self.assertIn("source: 'nearby_real'", self.reproduction_script)
        self.assertIn("{volume: null, source: 'own'}", self.reproduction_script)
        self.assertIn("EXPECTED_SOURCES[row?.limits?.volume_expected_source]",
                      self.reproduction_script)
        self.assertIn("row.limits.volume_expected_real", self.reproduction_script)
        direct = self.reproduction_script.index("source: 'real'")
        nearby = self.reproduction_script.index("source: 'nearby_real'")
        expected = self.reproduction_script.index("const source = EXPECTED_SOURCES[")
        own = self.reproduction_script.index("source: 'own'")
        self.assertLess(direct, nearby)
        self.assertLess(nearby, expected)
        self.assertLess(expected, own)
        self.assertIn("LiveAuditReproduction.realLotTimeline(rows)", self.analysis_script)
        self.assertIn("function lotList(lots)", self.analysis_script)
        self.assertIn("function scaledBySource(group, source)", self.analysis_script)

    def test_a_pair_whose_real_is_still_open_leaves_both_curves(self) -> None:
        # El tester cerró esa operación sólo porque se acabó el rango del
        # backtest, así que su PnL mide el corte y no la estrategia. Mientras
        # estuvo dentro, la curva Demo de la auditoría 148 arrastraba -124,88 €
        # que en real no se habían materializado.
        self.assertIn("function splitPending(series)", self.analysis_script)
        self.assertIn("point.pending_real", self.analysis_script)
        self.assertIn("pending_real: Boolean(row.real_position_still_open)", self.analysis_script)
        self.assertIn("splitPending(series).settled.reduce", self.analysis_script)
        self.assertIn("sólo cerró porque se acabó el\n// rango del backtest", self.analysis_script)
        # No se dibujan en ningún sitio: el gráfico sólo tiene Demo y Real, y
        # lo excluido se informa con texto en el resumen y en la cabecera.
        self.assertIn("chartLegend([['demo', 'Demo / Tester'], ['real', 'Real']])", self.analysis_script)
        self.assertNotIn("audit-analysis-line pending", self.analysis_script)
        self.assertNotIn(".audit-analysis-line.pending", self.analysis_css)
        self.assertIn("ninguna de las dos se dibuja", self.analysis_page_text)

    def test_a_real_position_still_open_counts_as_paired_and_not_as_absent(self) -> None:
        # Una real sin cerrar no tiene PnL, así que no entra en la curva; pero
        # el auditor sí la emparejó. Contarla sólo por `real.close_time` hacía
        # que la pantalla dijese «0 REAL EMPAREJADAS» de una estrategia que el
        # veredicto alineaba al segundo, y se leía como que no había operado.
        self.assertIn("row.real_position_still_open) group.openReal.push", self.analysis_script)
        self.assertIn("function openRealMark(group)", self.analysis_script)
        self.assertIn("EMPAREJADA${open === 1 ? '' : 'S'} SIN CERRAR", self.analysis_script)
        self.assertIn("${open} SIN CERRAR, FUERA", self.analysis_script)
        self.assertIn("ninguna de las dos se dibuja", self.analysis_script)
        self.assertIn("Fuera: sin cerrar en real", self.analysis_script)

    def test_a_strategy_that_does_not_reproduce_in_real_is_flagged(self) -> None:
        # `.USTECHCash` H1 de la auditoría 148 hace +11,38 € en el tester y
        # -0,46 € en real: el cambio de signo no admite grado y tiene que verse
        # sin abrir estrategia por estrategia.
        self.assertTrue(self.analysis_page.xpath('//tbody[@id="reproduction-body"]'))
        self.assertTrue(self.analysis_page.xpath('//span[@id="reproduction-count"]'))
        self.assertIn("¿Se está comportando en real como en el tester?", self.analysis_page_text)
        self.assertIn("function reproductionVerdict(numbers)", self.analysis_script)
        self.assertIn("if (demo >= 0 && real < 0) {", self.reproduction_script)
        self.assertIn("'NO REPRODUCE'", self.reproduction_script)
        # Sólo avisa si el real empeora, igual que `adverse_shortfall_only` del
        # motor: que la cuenta gane más que el tester no es un defecto.
        self.assertIn("'MEJOR QUE EL TESTER', 'good'", self.reproduction_script)
        # La magnitud NO es la del perfil: `pnl_deviation_warning_pct` mide la
        # desviación de un relleno, no el deterioro agregado de la estrategia.
        # Perder algo al pasar a real es lo normal y está cuantificado fuera.
        self.assertIn("const EXPECTED_SHORTFALL_PCT = 20;", self.reproduction_script)
        self.assertIn("const BROKEN_SHORTFALL_PCT = 60;", self.reproduction_script)
        self.assertNotIn("tolerances?.pnl_pct", self.analysis_script)
        self.assertIn("no un umbral canónico", self.analysis_script)
        # Lo que no se compara se dice: faltantes y pendientes no están en los
        # importes, así que la fila avisa de que la comparación es parcial.
        self.assertIn("Comparación parcial", self.analysis_script)
        # Con pocas parejas no se calla el veredicto: es una comparación de las
        # mismas operaciones, no un contraste contra una distribución. Lo que se
        # dice es hasta dónde vale, y va pegado al veredicto, no en otra columna.
        self.assertIn("function fewPairsChip(counts)", self.analysis_script)
        self.assertIn("POCAS OPERACIONES", self.analysis_script)
        self.assertIn("vale para mirarlo, no para concluir", self.analysis_script)
        # El corte sale de la propia banda: con n parejas una operación mueve
        # 1/n del total, y por debajo de cinco eso supera el 20 % tolerado.
        # El corte vive en el módulo, que es quien ordena la tabla con él: la
        # pantalla lo reexporta en vez de volver a derivarlo.
        self.assertIn(
            "const FEW_PAIRS = Math.ceil(100 / EXPECTED_SHORTFALL_PCT);",
            self.reproduction_script,
        )
        self.assertIn(
            "const REPRODUCTION_FEW_PAIRS = LiveAuditReproduction.FEW_PAIRS;",
            self.analysis_script,
        )
        self.assertIn("Math.max(Math.abs(demo), 1)", self.reproduction_script)
        self.assertIn("function differenceNote(numbers)", self.analysis_script)
        self.assertIn("renderReproduction();", self.analysis_script)

    def test_a_real_account_that_executed_nothing_never_comes_out_green(self) -> None:
        # Medido sobre las ocho auditorías de los tres brókers: de las 26 filas
        # marcadas, 15 eran esto, y había otras cinco **en verde** por el mismo
        # motivo con el signo cambiado. `EURUSD` de ICTrading: el tester cerró
        # doce operaciones, la cuenta no ejecutó ninguna, y como el tester
        # perdía 42,27 € la pantalla decía «MEJOR QUE EL TESTER».
        #
        # Que no se ejecutara nada es un hecho, no un déficit: el signo del
        # tester no lo gradúa y el porcentaje (±100 % por construcción) no
        # informa, así que lo que se enseña es el recuento.
        self.assertIn("function noFills(testerTrades)", self.reproduction_script)
        self.assertIn("if (!pairs) return noFills(testerTrades);", self.reproduction_script)
        self.assertIn("no ejecutó ninguna de las ${testerTrades} operaciones", self.reproduction_script)
        self.assertIn("pairs: realTrades.length, testerTrades: demoTrades.length,", self.reproduction_script)
        # Y la salvedad de muestra deja de apagarse justo ahí: sin parejas la
        # evidencia son las operaciones del tester sin ejecutar. Seis concluyen
        # —el caso que el usuario revisó—, una sola no.
        self.assertIn("return values.pairs || values.testerTrades || 0;", self.reproduction_script)
        self.assertIn("const few = LiveAuditReproduction.fewTrades(numbers);", self.analysis_script)
        self.assertIn("operaciones`} del tester sin ejecutar", self.analysis_script)

    def test_a_verdict_is_not_graded_on_amounts_that_cannot_carry_it(self) -> None:
        # Cuatro de las 26 filas marcadas eran rojas por uno o tres dólares:
        # `NAS100.fs` con el tester en 1,36 US$ y el real en 0,37 US$ salía
        # «73 %, NO REPRODUCE». Por debajo del importe mínimo no se gradúa.
        self.assertIn("const MEANINGFUL_AMOUNT = 5;", self.reproduction_script)
        self.assertIn(
            "if (Math.max(Math.abs(demo), Math.abs(real)) < MEANINGFUL_AMOUNT) {",
            self.reproduction_script,
        )
        self.assertIn("'POCO IMPORTE', 'info'", self.reproduction_script)
        # Con los signos cruzados el cociente no mide nada: el tester a -0,62 €
        # contra un real de 9,19 € daba «-981 %». Ahí lo dice el veredicto con
        # palabras y la fila no enseña porcentaje.
        self.assertIn("function crossedSigns(demo, real)", self.reproduction_script)
        self.assertIn(
            "if ((demo >= 0) !== (real >= 0)) return crossedSigns(demo, real);",
            self.reproduction_script,
        )
        self.assertIn("El resultado cambia de signo: el porcentaje no lo mide.", self.analysis_script)
        # Y el porcentaje deja de ordenar la tabla él solo: una fila de
        # céntimos daba 2.000 % y encabezaba por delante de un déficit del 44 %.
        self.assertIn("function compare(left, right)", self.reproduction_script)
        self.assertIn("SEVERITY[verdict(right)[0]]", self.reproduction_script)
        # POCO IMPORTE va por encima de REPRODUCE: no es un aprobado, es que no
        # se puede decir, y eso se mira antes que lo que ya se sabe que va bien.
        self.assertIn("'NO REPRODUCE': 4, AVISO: 3, 'POCO IMPORTE': 2,", self.reproduction_script)
        self.assertIn(
            "REPRODUCE: 1, 'MEJOR QUE EL TESTER': 1, 'SIN DATOS': 0,",
            self.reproduction_script,
        )
        self.assertIn("LiveAuditReproduction.compare(", self.analysis_script)
        # Y dentro de un mismo veredicto, primero el que se sostiene: «no
        # reproduce» con seis operaciones de respaldo va antes que «no
        # reproduce» con una, que es lo que se mira después y no al revés.
        self.assertIn(
            "const confidence = Number(fewTrades(left)) - Number(fewTrades(right));",
            self.reproduction_script,
        )
        self.assertIn("return confidence || right.pct - left.pct;", self.reproduction_script)

    def test_the_percentage_is_worded_for_the_sign_it_has_in_front(self) -> None:
        # «Se deja el 122 % del tester» era imposible de leer: no se puede
        # ceder más del 100 % de nada. Lo que pasaba en esa fila es que la
        # pérdida se había doblado, y eso es lo que tiene que decir.
        self.assertIn("function keptNote(demo, real)", self.reproduction_script)
        self.assertIn("conserva el ${Math.round(real / demo * 100)} % de lo que ganó el tester", self.reproduction_script)
        self.assertIn("veces lo que perdía el tester", self.reproduction_script)
        self.assertNotIn("se deja más del", self.reproduction_script)

    def test_each_table_says_which_period_it_is_talking_about(self) -> None:
        # Las tres tablas contestan la misma pregunta sobre rangos
        # distintos: la de arriba sobre el periodo auditado y las dos de
        # abajo sobre toda la serie. Sin el rango escrito, una captura de
        # cualquiera de ellas no se puede volver a situar.
        self.assertIn('id="reproduction-period"', self.analysis_page_text)
        self.assertIn("function spanLabel(since, until)", self.analysis_script)
        self.assertIn(
            "spanLabel(currentResult.period_start, currentResult.period_end)",
            self.analysis_script,
        )
        self.assertIn(
            "spanLabel(accumulated.since, accumulated.until)", self.analysis_script,
        )
        self.assertIn(
            "spanLabel(series?.covered_since, series?.covered_until)",
            (Path(__file__).parents[1] / "mt5_manager" / "static"
             / "live_audit_history.js").read_text(encoding="utf-8"),
        )

    def test_every_closed_real_ends_up_accounted_or_said(self) -> None:
        # El objetivo es que cuadre: cada real cerrada acaba emparejada,
        # atribuida a una estrategia, o en la tabla del final con su motivo.
        self.assertTrue(self.analysis_page.xpath('//tbody[@id="unaccounted-body"]'))
        self.assertIn("Reales que no cuentan en ninguna estrategia", self.analysis_page_text)
        self.assertIn("function attributionOf(row)", self.analysis_script)
        self.assertIn("PENDIENTE DE INVESTIGACIÓN", self.analysis_script)
        self.assertIn("FUERA DEL PORTAFOLIO", self.analysis_script)
        self.assertIn("function renderUnaccounted()", self.analysis_script)
        self.assertIn("renderUnaccounted();", self.analysis_script)
        # Lo atribuido suma en el real de su estrategia; lo excluido sale de la
        # curva; lo que nadie explicó sigue en la curva pero no en una fila.
        self.assertIn(".attributed.push(row.real)", self.analysis_script)
        self.assertIn("LiveAuditReproduction.attributedOwner(row)", self.analysis_script)
        self.assertIn(
            "if (!groups.has(owner.strategy)) groups.set(owner.strategy",
            self.analysis_script,
        )
        self.assertIn("function groupRealTrades(group)", self.analysis_script)
        self.assertIn("attributionOf(row).state !== 'excluded'", self.analysis_script)

    def test_reproduction_says_whether_the_backtest_had_the_tick_quality(self) -> None:
        # Decidir si se retira una estrategia por su déficit frente al tester
        # exige saber si ese tester corrió con historial completo: con 66 % de
        # calidad, el déficit puede ser del dato y no de la estrategia.
        self.assertIn("function strategyQuality(result)", self.analysis_script)
        self.assertIn("function qualityChip(group)", self.analysis_script)
        self.assertIn("CALIDAD TICK ", self.analysis_script)
        self.assertIn("SIN CALIDAD TICK", self.analysis_script)
        self.assertIn("quality: quality.get(key) || null", self.analysis_script)
        self.assertIn("${qualityChip(group)}", self.analysis_script)
        self.assertIn("su veredicto no sirve para decidir si se retiran", self.analysis_script)

    def test_the_reals_discarded_by_lot_are_said_instead_of_disappearing(self) -> None:
        # El filtro de banda de lote descarta cierres reales antes de comparar.
        # En el uso 105 de AXI eran 10 de 35 —nueve Tesla+ a 1.0 y un BTCUSD—
        # que no salían en ninguna tabla: ni emparejadas, ni sin pareja, ni
        # ajenas. Son dinero que la cuenta movió y el Excel tiene que poder
        # reclamarlas.
        self.assertIn("function discardedRows(result)", self.analysis_script)
        self.assertIn("discarded_real_operations", self.analysis_script)
        self.assertIn("function unaccountedRows(result)", self.analysis_script)
        self.assertIn("DESCARTADA POR LOTE", self.analysis_script)
        self.assertIn("function discardedHelp(row)", self.analysis_script)
        # Entran en el cuadre y en la atribución, pero no en la curva Real
        # mientras nadie las reclame: el filtro ya las dio por ajenas.
        self.assertIn(
            "row.status !== 'discarded' || attributionOf(row).state === 'attributed'",
            self.analysis_script,
        )
        self.assertIn("las descartó el filtro de lote antes de comparar", self.analysis_script)
        self.assertIn("ese filtro es una conjetura", self.analysis_page_text)

    def test_without_a_comparison_the_balance_panel_says_so_instead_of_squaring(self) -> None:
        # Una lista vacía porque no se comparó nada daba «TODO CUADRADO» y
        # «No queda ninguna real sin explicar»: la misma mentira que pintaba de
        # verde el resultado de un uso sin medir.
        self.assertIn("function renderUncomparedUnaccounted()", self.analysis_script)
        self.assertIn("if (!currentResult?.comparison_detail) return renderUncomparedUnaccounted();", self.analysis_script)
        self.assertIn("SIN COMPARACIÓN", self.analysis_script)
        self.assertIn("no significa que todas las reales estén explicadas", self.analysis_script)

    def test_an_empty_table_row_is_not_a_blank_block_under_the_header(self) -> None:
        # `.audit-analysis-empty` reserva 280 px para el hueco de una gráfica.
        # En un `td` eso abre un vacío enorme y, con `display: grid`, saca a la
        # celda de la tabla.
        css = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "live_audit_analysis.css"
        ).read_text(encoding="utf-8")
        self.assertIn("td.audit-analysis-empty { display: table-cell; min-height: 0;", css)

    def test_uploading_the_excel_saves_it_and_repaints_with_the_new_verdict(self) -> None:
        # El veredicto lo calcula el manager al leer, así que subir el Excel no
        # basta: hay que guardarlo en el uso y volver a pedir el análisis. Va en
        # una sola llamada para que no quede guardado sin repintar.
        self.assertTrue(self.analysis_page.xpath('//input[@id="attribution-file"]'))
        self.assertIn("live-audit-attribution-import", self.analysis_script)
        self.assertIn("/attribution`", self.analysis_script)
        self.assertIn("Recalculando la auditoría…", self.analysis_script)
        self.assertIn("render(result);", self.analysis_script)
        self.assertIn("await loadDegradation();", self.analysis_script)
        # El `input` es hijo del `label`, así que el rótulo tiene que vivir en
        # su propio `span`: escribir en el `textContent` del `label` borraba el
        # `input` y la subida sólo funcionaba una vez.
        self.assertTrue(self.analysis_page.xpath('//label[@id="attribution-upload"]/span[@id="attribution-label"]'))
        self.assertTrue(self.analysis_page.xpath('//label[@id="attribution-upload"]/input[@id="attribution-file"]'))
        upload = self.analysis_script.split("async function uploadAttribution(file)", 1)[1]
        self.assertIn("const label = document.querySelector('#attribution-label')", upload)
        self.assertNotIn("const label = document.querySelector('#attribution-upload')", upload)
        # Vaciar el `value` deja volver a elegir el mismo fichero corregido.
        self.assertIn("document.querySelector('#attribution-file').value = ''", self.analysis_script)

    def test_the_excel_pair_is_not_reduced_to_a_strategy_attribution(self) -> None:
        self.assertIn("parsed.manual_pair_rows", self.analysis_script)
        self.assertIn("manual_pairs: manualPairsFromImport(importedPairs)", self.analysis_script)
        self.assertIn(
            "[tester.strategy, tester.symbol, tester.side, tester.open_time, tester.close_time]",
            self.analysis_script,
        )
        self.assertIn("row.status !== 'missing'", self.analysis_script)

    def test_an_uploaded_excel_can_be_taken_back(self) -> None:
        # Si se acumula, tiene que poder deshacerse: cada regla recuerda de qué
        # fichero salió, así que se retira uno sin perder los demás.
        self.assertTrue(self.analysis_page.xpath('//div[@id="attribution-sources"]'))
        # La `panel-card` no tiene relleno: lo pone cada hijo. Esta fila se lo
        # había dejado, así que «EXCEL SUBIDOS» arrancaba veinte píxeles a la
        # izquierda del título y de la nota entre los que vive. Medido sobre la
        # auditoría 170: la etiqueta en x=52 y el título en x=72.
        #
        # Y el de abajo iguala el margen de arriba: lo que sigue a la fila es
        # el borde superior de la nota, no un hueco, así que sin relleno los
        # chips quedaban pegados al filete. Medido a 1830px: 15px arriba
        # contra 1px abajo; con el relleno, 15 y 15.
        self.assertIn(
            "gap: 8px; margin-top: 14px; padding: 0 20px 14px; }",
            (Path(__file__).parents[1] / "mt5_manager" / "static"
             / "live_audit_analysis.css").read_text(encoding="utf-8"),
        )
        self.assertIn("function attributionSources()", self.analysis_script)
        self.assertIn("data-remove-source", self.analysis_script)
        self.assertIn("data-clear-attribution", self.analysis_script)
        self.assertIn("{remove_source: remove.dataset.removeSource}", self.analysis_script)
        self.assertIn("{clear: true}", self.analysis_script)
        # Quitar y subir hacen el mismo viaje: guardar y repintar con lo nuevo.
        self.assertIn("changeAttribution(change)", self.analysis_script)
        self.assertIn("window.confirm(question)", self.analysis_script)
        # Lo guardado antes de que existiera el campo no puede quedar atrapado.
        self.assertIn("Subidas anteriores", self.analysis_script)

    def test_a_crossing_the_backtest_itself_repeats_is_not_sold_as_degradation(self) -> None:
        # `AXI/STANDARD:10751` cruzó −2,12 σ con nueve operaciones y 90 de los
        # 1.355 tramos de nueve de su propio backtest fueron iguales o peores.
        # La fila tiene que contar las dos cosas: que cruzó, y por qué eso no
        # basta para retirarla. Esconder cualquiera de las dos es el error.
        self.assertIn("function evidenceNote(row)", self.analysis_script)
        self.assertIn("row.verdict === 'warning' && row.evidence?.crossed", self.analysis_script)
        self.assertIn("'AVISO FUERTE'", self.analysis_script)
        self.assertIn("tramos consecutivos", self.analysis_script)
        self.assertIn("del propio backtest fueron iguales o peores", self.analysis_script)
        self.assertIn("Mirando la serie en cada auditoría", self.analysis_script)
        self.assertIn("estrategias que se comparan a la vez", self.analysis_script)
        self.assertIn("Por eso se queda en aviso y no en DEGRADA", self.analysis_script)
        self.assertIn("Las dos pruebas lo confirman", self.analysis_script)
        self.assertIn("menos del 0,1 %", self.analysis_script)
        # Y la pantalla explica el criterio, no solo el caso.
        self.assertIn("Cruzar −2 σ abre el expediente, no lo cierra.", self.analysis_page_text)
        self.assertIn("raro entre los tramos consecutivos del propio backtest", self.analysis_page_text)

    def test_the_row_shows_the_recent_window_next_to_the_accumulated_one(self) -> None:
        # La serie acumulada diluye: un año bueno tapa un mes roto, y cada
        # semana que pasa lo tapa más. La fila enseña las dos cifras y dice
        # cuál de las dos ha decidido.
        self.assertIn("function recentNote(row)", self.analysis_script)
        self.assertIn("últimos 6 meses", self.analysis_script)
        self.assertIn("row.decided === 'recent'", self.analysis_script)
        self.assertIn("la serie entera lo diluye", self.analysis_script)

    def test_a_strategy_that_stopped_trading_is_a_finding_of_its_own(self) -> None:
        # Distinguir +0,77 € de 0 por operación pide ~600 operaciones; que el
        # recuento caiga a la mitad se ve en dos meses. Y es el mismo truco:
        # lo decide el propio backtest, no un Poisson que los recuentos de
        # este pipeline no cumplen.
        self.assertIn("function rateNote(row)", self.analysis_script)
        self.assertIn("Opera mucho menos de lo que solía", self.analysis_script)
        self.assertIn("esperadas por su", self.analysis_script)
        self.assertIn("tramos de esa duración del propio backtest", self.analysis_script)
        self.assertIn("row.rate?.flagged", self.analysis_script)


if __name__ == "__main__":
    unittest.main()
