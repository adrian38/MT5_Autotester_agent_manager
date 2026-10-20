"""La barra superior tiene el mismo orden en todas las pantallas.

No hay runtime de JavaScript en este equipo, así que el criterio que vive en
`live_audit_schedule.js` se porta aquí y se le exige que cada expresión portada
esté **literalmente** en el fichero: un port que se queda viejo mide otra cosa
y pasa igual.

Lo que lo motivó (2026-10-20): el ancla era
`querySelector('#restart-manager, a[href="/"]')`, que devuelve el primero del
**documento** y no el primero de la lista. En Generación el enlace de vuelta va
antes que «Reiniciar manager», así que la cita y la campana se colaban en mitad
de la fila mientras en la portada salían bien. Un orden que depende del orden
del HTML de cada página no es un orden.
"""
from __future__ import annotations

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATIC = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"

# El criterio portado. Cada cadena tiene que estar en el .js, y lo comprueba
# `test_the_ported_rule_is_the_one_the_script_runs`.
RESTART = "#restart-manager"
BACK_LINK = 'a[href="/"]'
REFRESH = "#refresh, #reload, #portfolio-refresh, #universe-refresh"
REFRESH_STATE = "#refresh-state, #catalog-state"

CRON = "Auditoría programada"
BELL = "🔔"

ACCOUNT_CRON = "Sondeo de cuentas"
ACCOUNT_NOW = "Sondear ahora"

# Cada script que se monta solo, con lo que inserta antes del ancla. El orden
# en que corren es el de sus etiquetas `<script>`, así que el que va primero
# acaba más lejos del ancla: por eso se leen del HTML y no de esta lista.
MOUNTS = {
    "live_account_schedule.js": (ACCOUNT_CRON, ACCOUNT_NOW),
    "live_audit_schedule.js": (CRON, BELL),
}


VOID_TAGS = frozenset({"br", "hr", "img", "input", "link", "meta", "source"})


class _HeaderActions(HTMLParser):
    """Los hijos directos del **primer** `.header-actions`, en orden.

    El primero es el de la cabecera, que es justo el que coge el script con
    `document.querySelector`. `portfolios_grid.html` tiene otro más abajo, en
    el panel de resultados, y seguir leyendo colaba sus dos botones de guardar
    en la fila de arriba.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.done = False
        self.children: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done or tag in VOID_TAGS:
            return
        values = {key: value or "" for key, value in attrs}
        if self.depth == 0:
            if "header-actions" in values.get("class", "").split():
                self.depth = 1
            return
        if self.depth == 1:
            self.children.append({
                "id": values.get("id", ""), "href": values.get("href", ""), "text": "",
            })
        self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.done or self.depth == 0 or tag in VOID_TAGS:
            return
        self.depth -= 1
        if self.depth == 0:
            self.done = True

    def handle_data(self, data: str) -> None:
        if not self.done and self.depth >= 2 and self.children and data.strip():
            self.children[-1]["text"] += data.strip()


def header_actions(page: str) -> list[dict[str, str]]:
    parser = _HeaderActions()
    parser.feed((STATIC / page).read_text(encoding="utf-8"))
    return parser.children


def _match(child: dict[str, str], selector: str) -> bool:
    if selector == BACK_LINK:
        return child["href"] == "/"
    return any(child["id"] == name.strip().lstrip("#") for name in selector.split(","))


def _first(children: list[dict[str, str]], selector: str) -> dict[str, str] | None:
    return next((child for child in children if _match(child, selector)), None)


def _mount_order(source: str) -> list[str]:
    """Los scripts que esta página monta, en el orden en que corren."""
    found = [(source.index(f"/{name}"), name) for name in MOUNTS if f"/{name}" in source]
    return [name for _position, name in sorted(found)]


def rendered_order(page: str) -> list[str]:
    """La fila tal y como queda después de que los scripts la recoloquen."""
    children = header_actions(page)
    source = (STATIC / page).read_text(encoding="utf-8")
    for name in _mount_order(source):
        anchor = _first(children, RESTART) or _first(children, BACK_LINK)
        index = children.index(anchor) if anchor else len(children)
        children[index:index] = [
            {"id": "", "href": "", "text": label} for label in MOUNTS[name]
        ]
    if "live_audit_schedule.js" not in source:
        return [child["text"] or child["id"] for child in children]
    refresh = _first(children, REFRESH)
    if refresh is not None:
        children.remove(refresh)
        children.insert(0, refresh)
        state = _first(children, REFRESH_STATE)
        if state is not None:
            children.remove(state)
            children.insert(0, state)
    return [child["text"] or child["id"] for child in children]


class HeaderActionsOrderTests(unittest.TestCase):
    EXPECTED = {
        # Las tres pantallas que montan los dos crones. El de cuentas se carga
        # antes, así que queda por fuera y la campana sigue pegada a la salida.
        "accounts.html": [
            "Actualizando…", "Actualizar", "Portafolios activos", "Generación",
            ACCOUNT_CRON, ACCOUNT_NOW, CRON, BELL,
        ],
        "dashboard.html": [
            "Actualizando…", "Actualizar", "Cuentas en real", "Generación",
            "Experimenta", "Correlación", ACCOUNT_CRON, ACCOUNT_NOW, CRON, BELL,
            "Reiniciar manager",
        ],
        "index.html": [
            "Actualizando…", "Actualizar", "Cuentas en real", "Portafolios activos",
            "Experimenta", "Correlación", ACCOUNT_CRON, ACCOUNT_NOW, CRON, BELL,
            "Reiniciar manager",
        ],
        "live_audit.html": ["⚙ Cuenta final", CRON, BELL, "Volver al manager"],
        "portfolios.html": ["Actualizar", CRON, BELL, "Volver al manager"],
        "portfolios_grid.html": ["Actualizar", CRON, BELL, "Volver al manager"],
        "portfolios_monthly.html": ["Actualizar", CRON, BELL, "Volver al manager"],
    }

    def test_every_screen_that_loads_the_script_is_declared_here(self) -> None:
        loaders = {
            page.name for page in STATIC.glob("*.html")
            if "live_audit_schedule.js" in page.read_text(encoding="utf-8")
        }
        self.assertEqual(loaders, set(self.EXPECTED))

    def test_the_cron_and_the_bell_always_close_the_row(self) -> None:
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                self.assertEqual(rendered_order(page), expected)

    def test_the_last_one_is_the_restart_or_the_way_back(self) -> None:
        # La regla en una frase: la campana va pegada a la salida de la
        # pantalla, sea «Reiniciar manager» o el enlace de vuelta. La portada
        # de cuentas no tiene salida —es la raíz—, y entonces cierra ella.
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                tail = expected[-2:] if expected[-1] == BELL else expected[-3:-1]
                self.assertEqual(tail, [CRON, BELL])
                if expected[-1] != BELL:
                    self.assertIn(expected[-1], {"Reiniciar manager", "Volver al manager"})

    def test_updating_is_always_the_first_pair(self) -> None:
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                self.assertIn(expected[0], {"Actualizando…", "Actualizar", "⚙ Cuenta final"})

    def test_the_account_cron_mounts_with_the_same_anchor(self) -> None:
        """El segundo script tiene que anclar igual que el primero.

        Si no, los dos pares de botones aterrizan en sitios distintos según la
        pantalla, que es exactamente el fallo que esta guarda existe para no
        repetir.
        """
        script = (STATIC / "live_account_schedule.js").read_text(encoding="utf-8")
        for expression in (
            f"actions.querySelector('{RESTART}')",
            f"actions.querySelector('{BACK_LINK}')",
            "actions.insertBefore(open, anchor || null)",
            "actions.insertBefore(launch, anchor || null)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, script)
        # Y en ese orden, que es el que decide cual queda a la izquierda. Sin
        # esto el modelo de arriba mediria un orden que el script no hace.
        self.assertLess(
            script.index("actions.insertBefore(open, anchor || null)"),
            script.index("actions.insertBefore(launch, anchor || null)"),
        )
        self.assertEqual(MOUNTS["live_account_schedule.js"], (ACCOUNT_CRON, ACCOUNT_NOW))

    def test_the_ported_rule_is_the_one_the_script_runs(self) -> None:
        script = (STATIC / "live_audit_schedule.js").read_text(encoding="utf-8")
        for expression in (
            f"actions.querySelector('{RESTART}')",
            f"actions.querySelector('{BACK_LINK}')",
            f"actions.querySelector('{REFRESH}')",
            f"actions.querySelector('{REFRESH_STATE}')",
            "actions.insertBefore(open, anchor || null)",
            "actions.insertBefore(bell, anchor || null)",
            "actions.insertBefore(refresh, actions.firstElementChild)",
            "actions.insertBefore(state, refresh)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, script)

    def test_the_two_selectors_are_asked_separately(self) -> None:
        # El fallo exacto que esto impide repetir: un único `querySelector` con
        # los dos selectores devuelve el primero del documento, no el preferido.
        script = (STATIC / "live_audit_schedule.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, re.compile(r"querySelector\('#restart-manager\s*,"))


if __name__ == "__main__":
    unittest.main()
