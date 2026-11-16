"""Lo que la pantalla de patrimonio promete y no se puede ejecutar aquí.

No hay runtime de JavaScript en este equipo, así que los tres criterios que
viven en `patrimonio.js` y `patrimonio.css` se comprueban sobre su texto. No
es una prueba de estilo: las tres son decisiones que ya se tomaron al revés
una vez y volverían solas en la siguiente edición.

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
        self.assertIn("if (first) await atMost(loadAliases(data), WAIT_FOR_ALIASES);", SCRIPT)
        self.assertIn("const WAIT_FOR_ALIASES = 8000;", SCRIPT)
        self.assertIn(".wealth-loading{position:absolute", SHEET)

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
