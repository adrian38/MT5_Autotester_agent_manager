"""Lo que la pantalla de patrimonio promete y no se puede ejecutar aquí.

No hay runtime de JavaScript en este equipo, así que los criterios que viven
en `patrimonio.js` y `patrimonio.css` se comprueban sobre su texto. No es una
prueba de estilo: cada uno es una decisión que ya se tomó al revés una vez y
volvería sola en la siguiente edición.

El fichero se pide **por su nombre de URL** (`static_source`), que es lo que
el navegador recibe, y nunca construyendo su ruta: el reparto de `static/` en
carpetas dejó sin resolver a media docena de guardas que lo hacían.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tools.package_layout import static_source

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SCRIPT = static_source("patrimonio.js")
SHEET = static_source("patrimonio.css")
PAGE = static_source("patrimonio.html")


class WealthScreenTests(unittest.TestCase):
    def test_a_portfolio_is_named_by_its_alias_and_nothing_else(self) -> None:
        """El número era un dato por el que nadie busca, repetido en todo.

        Salía en cada fila de la matriz, en cada apunte de la tabla de
        ingresos y en las diez líneas de «quién lo movió» de cada mes. Vuelve
        sólo cuando no hay alias ni nombre, porque entonces es lo único que
        distingue una fila de otra: de las diez cuentas del equipo, el `name`
        que manda el nodo viene vacío en las diez (2026-11-16).
        """
        self.assertNotIn("(#${id})", SCRIPT)
        self.assertIn("aliasOf(row.node_id, id) || portfolio.name", SCRIPT)
        self.assertIn("(id ? `#${id}` : 'sin nombre')", SCRIPT)

    def test_nothing_is_painted_until_the_aliases_are_in(self) -> None:
        """El telón tapa `main`, espera a los alias y no es eterno.

        Son las tres partes del mismo criterio: enseñar el patrimonio antes
        que los alias dejaba las dos tablas y el detalle diciendo «sin
        nombre» y, al llegar la otra lectura, un salto; tapar la ventana
        entera dejaba «Actualizar» fuera de alcance justo cuando lo que
        falla es la lectura; y esperar sin tope dejaba la pantalla en blanco
        si un agente no contesta.
        """
        self.assertIn('id="wealth-loading"', PAGE)
        self.assertIn("if (entering) await atMost(aliases, WAIT_FOR_ALIASES);", SCRIPT)
        self.assertIn("const WAIT_FOR_ALIASES = 12000;", SCRIPT)
        self.assertIn(".wealth-loading{position:absolute", SHEET)

    def test_an_alias_that_arrives_after_the_curtain_repaints(self) -> None:
        """La espera decide cuándo se enseña, no con qué nombres se queda.

        El 2026-11-16, en una entrada normal, los tres agentes contestaron a
        los 0,07 s, 4,2 s y 8,06 s: el último entró 60 ms después del corte,
        que entonces eran 8 s, y sus cinco portafolios se quedaron llamándose
        `#186` el resto de la sesión con la respuesta ya en memoria. Se
        perdía porque los tres se juntaban en un `Promise.all` que la espera
        descartaba entera. Ahora cada agente repinta al llegar.
        """
        self.assertIn("if (changed && painted === data) renderByAlias(data);", SCRIPT)
        self.assertIn(".then(changed => aliasesArrived(changed, data))", SCRIPT)
        self.assertNotIn("await loadAliases(data) &&", SCRIPT)

    def test_an_agent_that_does_not_answer_is_asked_again(self) -> None:
        """Contestar 502 por estar ocupado no es contestar «sin alias».

        Lo primero se reintenta —antes no volvía hasta que alguien pulsaba
        «Actualizar»— y lo segundo es la respuesta del agente, que se acata.
        """
        self.assertIn("const ALIAS_RETRIES = [3000, 9000];", SCRIPT)
        self.assertIn("if (changed !== null) return changed;", SCRIPT)

    def test_the_curtain_waits_for_the_first_round_and_not_for_the_retries(self) -> None:
        """Esperar al reintento es tener la pantalla tapada por nada.

        El agente que contesta 502 ya dijo lo que tenía que decir; su
        reintento llega tres segundos después y repinta solo. Medido con la
        pantalla servida: con un agente ocupado el telón se levanta en la
        primera vuelta y el alias entra después, sin tapar nada.
        """
        self.assertIn("return first;", SCRIPT)
        self.assertNotIn("return chain;", SCRIPT)
        self.assertIn(
            "const first = loadNodeAliases(nodeId, ids)"
            ".then(changed => aliasesArrived(changed, data));",
            SCRIPT,
        )

    def test_the_daily_sondeo_has_no_door_on_this_screen(self) -> None:
        """El sondeo diario no se abre desde aquí, ni se carga aquí.

        Son dos citas distintas por las dos cosas que importan: **qué cuentas**
        se leen —las de los portafolios activos en el diario, las que suman en
        el patrimonio en ésta— y **qué se extrae** —los últimos días frente al
        informe completo de toda la vida de la cuenta—. Tenerlas a un clic una
        de otra las hacía pasar por dos caras del mismo sondeo.

        Cargar `live_account_schedule.js` aquí no era sólo el botón: metía su
        diálogo entero en esta pantalla y montaba el suyo en la cabecera. Así
        que lo que se comprueba es que esta pantalla no lo carga y que en el
        script compartido ya no queda el enganche para alojarlo.
        """
        schedule = static_source("live_account_schedule.js")
        self.assertNotIn("data-opens-account-cron", schedule)
        self.assertNotIn("data-opens-account-cron", SCRIPT)
        self.assertNotIn("/live_account_schedule.js", PAGE)
        self.assertNotIn("account-cron", SCRIPT)
        self.assertEqual(1, PAGE.count('<button type="button" id="wealth-open-cron"'))

    def test_the_dialog_closes_with_the_shared_action_bar(self) -> None:
        """El `<footer>` salía sin estilo: sin margen y pegado al borde.

        `live_audit_schedule.css` es la única hoja de diálogos que carga esta
        pantalla, y ahí la barra de botones es `.cron-actions`; `dialog footer`
        sólo está en `accounts.css`, que aquí no se carga. El diálogo del
        patrimonio es el mismo diálogo que los otros dos y cierra igual.
        """
        self.assertIn('<div class="cron-actions">', SCRIPT)
        self.assertNotIn("<footer>", SCRIPT)
        self.assertIn(".cron-dialog .cron-actions{", static_source("live_audit_schedule.css"))

    def test_the_dialog_text_is_not_markdown(self) -> None:
        """Los `**` del resumen salían impresos: va a un `textContent`."""
        self.assertIn("cronField('#wealth-cron-description').textContent", SCRIPT)
        from mt5_manager.live_account.live_account_wealth_schedule import (
            DEFAULT_WEALTH_SCHEDULER_SETTINGS, schedule_description,
        )
        self.assertNotIn("**", schedule_description(DEFAULT_WEALTH_SCHEDULER_SETTINGS))

    def test_a_partial_month_shows_its_figures_marked_instead_of_a_dash(self) -> None:
        """Una cuenta de diez sin releer dejaba en «—» cuatro meses enteros.

        Las cifras de esos meses son la suma de las cuentas que sí están
        releídas: un mínimo cierto. Se enseñan, pero marcadas y con el título
        diciendo cuántas cuentas las sostienen, porque leerlas como el total
        del mes sería la mentira que el «—» evitaba.
        """
        self.assertIn('class="partial" title="Suma ${month.cash_accounts} de ', SCRIPT)
        self.assertIn("const flow = missing.length ? 'partial' : '';", SCRIPT)
        self.assertIn(".wealth-table td.partial{", SHEET)
        self.assertIn(".wealth-tip li.partial strong{", SHEET)

    def test_the_portfolio_matrix_is_not_cut_at_four_rows(self) -> None:
        """Comparar la quinta cuenta con la primera no puede exigir memoria."""
        self.assertIn('id="wealth-contributors-wrap"', PAGE)
        self.assertIn("#wealth-contributors-wrap{max-height:none}", SHEET)

    def test_the_first_card_is_dated_and_never_says_today(self) -> None:
        """«Patrimonio hoy» daba por leído esta mañana un saldo semanal.

        La relectura completa es la cita de esta pantalla y en este equipo
        corre los domingos, así que la tarjeta lleva su fecha. Y la lleva de
        **esa** cita: la del sondeo diario de la portada sería otra lectura,
        de otras cuentas, puesta encima de estas cifras.
        """
        self.assertNotIn("'Patrimonio hoy'", SCRIPT)
        self.assertIn(
            "moment ? `Patrimonio al ${moment}` : 'Patrimonio sin releer'", SCRIPT,
        )
        self.assertIn("const read = data.full_read || {};", SCRIPT)

    def test_the_date_carries_the_time_because_a_day_admits_several_reads(self) -> None:
        """«Releer ahora» se pulsa varias veces el mismo día.

        Con la fecha sola, la segunda relectura pintaba la misma cabecera que
        la primera y el saldo cambiaba debajo sin que nada dijera por qué.
        """
        self.assertIn("hour: '2-digit', minute: '2-digit',", SCRIPT)
        self.assertNotIn("toLocaleDateString('es-ES', {", SCRIPT)

    def test_the_screen_never_enumerates_the_accounts_left_out(self) -> None:
        """La casilla «Sumar» ya decide el alcance antes del sondeo.

        Nombrar aquí las desmarcadas o las que no tienen credenciales pedía
        explicar cuentas que no forman parte de este cálculo.
        """
        self.assertNotIn("not_summed", SCRIPT)
        self.assertNotIn("sin credenciales", SCRIPT)
        self.assertNotIn("no suman aquí", SCRIPT)
        self.assertNotIn("fuera`", SCRIPT)

    def test_the_two_detail_panels_are_stacked_at_the_end(self) -> None:
        """En media pantalla la tabla sacaba diez columnas en un palmo.

        Los dos van al final y a todo lo ancho, uno debajo del otro, que es
        como se piden las columnas de «Mes a mes» sin recorrerla de lado. La
        rejilla de dos columnas ya no existe: `main` apila por sí solo.
        """
        self.assertNotIn("wealth-row", PAGE)
        self.assertNotIn(".wealth-row{", SHEET)
        self.assertLess(PAGE.index('id="wealth-contributors"'), PAGE.index('id="wealth-months"'))
        self.assertLess(PAGE.index('id="wealth-cash"'), PAGE.index('id="wealth-flow-chart"'))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
