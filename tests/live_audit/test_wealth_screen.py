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

    def test_the_daily_sondeo_has_a_single_door_on_this_screen(self) -> None:
        """La palabra «sondeo» salía tres veces para dos citas.

        Dos de ellas eran **el mismo diálogo**: «Moneda y sondeo diario» en la
        barra y «Sondeo de cuentas» dentro del desplegable «Menú», que
        `live_account_schedule.js` monta en la cabecera de toda pantalla que
        lo cargue. Con dos nombres distintos, que es peor que con el mismo:
        parecían dos citas. Ahora la pantalla declara su sitio y el de la
        cabecera no se monta; la cita diaria se abre desde dentro del diálogo
        de la del patrimonio, que es donde se explica qué las separa.
        """
        schedule = static_source("live_account_schedule.js")
        self.assertIn(
            "const own = document.querySelector('[data-opens-account-cron]');", schedule
        )
        self.assertIn("const open = own || document.createElement('button');", schedule)
        self.assertIn("data-opens-account-cron", SCRIPT)
        self.assertNotIn("wealth-open-accounts-cron", PAGE)
        self.assertNotIn("wealth-open-accounts-cron", SCRIPT)
        self.assertEqual(1, PAGE.count('<button type="button" id="wealth-open-cron"'))

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
