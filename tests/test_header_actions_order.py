"""El orden de la barra de botones de cada cabecera.

«Actualizar» abre la barra en todas las pantallas y «Reiniciar manager» la
cierra, con la campana justo a su izquierda. La campana la monta
`live_audit_schedule.js`, así que aquí se comprueban las dos mitades: que el
script la coloca delante del botón de reinicio y manda «Actualizar» al
principio, y que el HTML de cada pantalla ya trae ese orden, para que no
dependa de que el script llegue a tiempo.
"""
from __future__ import annotations

import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATIC = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"

# Cada pantalla le puso un id distinto al mismo botón; son los que busca
# `live_audit_schedule.js` para recolocarlo.
REFRESH_IDS = ("refresh", "reload", "portfolio-refresh", "universe-refresh")
# El rótulo de estado va pegado a su botón, así que puede abrir la barra él.
STATE_IDS = ("refresh-state", "catalog-state")


class _HeaderActions(HTMLParser):
    """Los hijos directos de la primera barra `header-actions` de la página.

    La primera es la de la cabecera: `portfolios_grid.html` reutiliza la clase
    más abajo para los botones de guardado de una tarjeta, y ésa no es la barra
    de la que habla esta prueba.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.children: list[tuple[str, str]] = []
        self._depth = 0
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if self._depth:
            if self._depth == 1:
                self.children.append((tag, values.get("id", "")))
            self._depth += 1
        elif not self._done and "header-actions" in values.get("class", "").split():
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        self._depth -= 1
        if not self._depth:
            self._done = True


def header_actions(page: Path) -> list[str]:
    parser = _HeaderActions()
    parser.feed(page.read_text(encoding="utf-8"))
    return [identifier for _tag, identifier in parser.children]


class HeaderActionsOrderTests(unittest.TestCase):
    """«Actualizar» el primero, reinicio el último, campana en medio."""

    def test_the_refresh_button_opens_every_header(self) -> None:
        checked = 0
        for page in sorted(STATIC.glob("*.html")):
            identifiers = header_actions(page)
            if not any(identifier in REFRESH_IDS for identifier in identifiers):
                continue
            checked += 1
            front = [identifier for identifier in identifiers[:2]]
            self.assertTrue(
                front[0] in REFRESH_IDS
                or (front[0] in STATE_IDS and front[1] in REFRESH_IDS),
                msg=f"{page.name} no abre con «Actualizar»: {identifiers}",
            )
        # Si un día se renombran todos los ids, la prueba no puede quedarse
        # vacía y en verde.
        self.assertGreaterEqual(checked, 8)

    def test_restarting_the_manager_closes_every_header_that_offers_it(self) -> None:
        checked = 0
        for page in sorted(STATIC.glob("*.html")):
            identifiers = header_actions(page)
            if "restart-manager" not in identifiers:
                continue
            checked += 1
            self.assertEqual(
                identifiers[-1], "restart-manager",
                msg=f"{page.name} no cierra con el reinicio: {identifiers}",
            )
        self.assertGreaterEqual(checked, 2)

    def test_the_bell_is_mounted_in_front_of_the_restart_button(self) -> None:
        script = (STATIC / "live_audit_schedule.js").read_text(encoding="utf-8")
        for identifier in REFRESH_IDS:
            self.assertIn(f"#{identifier}", script)
        self.assertIn("'#restart-manager, a[href=\"/\"]'", script)
        self.assertIn("actions.insertBefore(bell, anchor || null)", script)
        self.assertIn("actions.insertBefore(refresh, actions.firstElementChild)", script)


if __name__ == "__main__":
    unittest.main()
