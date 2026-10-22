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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

STATIC = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"

# El criterio portado. Cada cadena tiene que estar en el .js, y lo comprueba
# `test_the_ported_rule_is_the_one_the_script_runs`.
RESTART = "#restart-manager"
BACK_LINK = 'a[href="/"]'
REFRESH = "#refresh, #reload, #portfolio-refresh, #universe-refresh"
REFRESH_STATE = "#refresh-state, #catalog-state"

CRON = "Auditoría programada"
BELL = "🔔"

ACCOUNT_CRON = "Sondeo de cuentas"

# Cada script que se monta solo, con lo que inserta antes del ancla. El orden
# en que corren es el de sus etiquetas `<script>`, así que el que va primero
# acaba más lejos del ancla: por eso se leen del HTML y no de esta lista.
MOUNTS = {
    "live_account_schedule.js": (ACCOUNT_CRON,),
    "live_audit_schedule.js": (BELL,),
}

RESTART_MOUNT = "manager_restart.js"
"""El unico que **anade al final** en vez de colarse delante del ancla.

Monta `#restart-manager` donde la pantalla no lo trae escrito, y por eso se
carga el primero: los otros dos se colocan justo delante de ese boton.
"""
RESTART_LABEL = "Reiniciar manager"
BACK = "Volver al generador"
AUDITOR_BACK = "Volver al auditor"
"""La vuelta lleva al generador, que es de donde se entra a estas pantallas.

La raiz es la portada de cuentas desde el 2026-10-?? y «Volver al manager»
dejaba al usuario en otra pantalla distinta de la que venia.
"""


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
    names = [*MOUNTS, RESTART_MOUNT]
    found = [(source.index(f"/{name}"), name) for name in names if f"/{name}" in source]
    return [name for _position, name in sorted(found)]


def rendered_order(page: str) -> list[str]:
    """La fila tal y como queda después de que los scripts la recoloquen."""
    children = header_actions(page)
    source = (STATIC / page).read_text(encoding="utf-8")
    for name in _mount_order(source):
        if name == RESTART_MOUNT:
            if _first(children, RESTART) is None:
                children.append({"id": "restart-manager", "href": "", "text": RESTART_LABEL})
            continue
        anchor = _first(children, RESTART) or _first(children, BACK_LINK)
        index = children.index(anchor) if anchor else len(children)
        labels = MOUNTS[name]
        if name == "live_audit_schedule.js" and "data-live-audit-schedule" in source:
            labels = (CRON, *labels)
        children[index:index] = [
            {"id": "", "href": "", "text": label} for label in labels
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
        "accounts.html": [
            "Actualizando…", "Actualizar", "Portafolios activos", "Generación",
            ACCOUNT_CRON, BELL, RESTART_LABEL,
        ],
        "correlation.html": ["Cargando…", "Actualizar", BACK, BELL, RESTART_LABEL],
        "dashboard.html": [
            "Actualizando…", "Actualizar", "Cuentas en real", "Generación",
            "Experimenta", "Correlación", CRON, BELL, RESTART_LABEL,
        ],
        "experiment.html": ["Cargando…", "Actualizar", BACK, BELL, RESTART_LABEL],
        "index.html": [
            "Actualizando…", "Actualizar", "Cuentas en real", "Portafolios activos",
            "Experimenta", "Correlación", BELL, RESTART_LABEL,
        ],
        "live_audit.html": ["⚙ Cuenta final", BACK, BELL, RESTART_LABEL],
        "live_audit_analysis.html": [
            "Lotes por estrategia", "Atribuir reales sin pareja",
            "Ver detalle de operaciones", AUDITOR_BACK, BELL, RESTART_LABEL,
        ],
        "live_audit_result.html": [AUDITOR_BACK, BELL, RESTART_LABEL],
        # El análisis de una comprobación manual se abre desde el detalle del
        # portafolio, así que vuelve allí; el enlace al generador es la salida
        # a la raíz que toda pantalla tiene que ofrecer.
        "manual_check_analysis.html": [
            "Actualizar", "Volver al portafolio", BACK, BELL, RESTART_LABEL,
        ],
        # El histórico OHLC guardado se abre desde el detalle del portafolio y
        # vuelve allí, con la misma salida al generador.
        "portfolio_history.html": [
            "Actualizar", "Volver al portafolio", BACK, BELL, RESTART_LABEL,
        ],
        "manual_portfolios.html": ["Crear portafolio", BACK, BELL, RESTART_LABEL],
        "portfolio_manual.html": ["Portafolios manuales", BACK, BELL, RESTART_LABEL],
        "portfolios.html": ["Actualizar", BACK, BELL, RESTART_LABEL],
        "portfolios_grid.html": ["Actualizar", BACK, BELL, RESTART_LABEL],
        "portfolios_monthly.html": ["Actualizar", BACK, BELL, RESTART_LABEL],
        "universe.html": ["Actualizar", BACK, BELL, RESTART_LABEL],
    }

    def test_every_screen_that_loads_the_script_is_declared_here(self) -> None:
        loaders = {
            page.name for page in STATIC.glob("*.html")
            if "live_audit_schedule.js" in page.read_text(encoding="utf-8")
        }
        self.assertEqual(loaders, set(self.EXPECTED))

    def test_the_bell_takes_its_stylesheet_with_it(self) -> None:
        """La campana no es la misma campana sin su hoja de estilos.

        El script monta el panel con `document.body.appendChild`, y es
        `live_audit_schedule.css` quien lo saca del flujo con `position:
        fixed`. Siete pantallas cargaban el script sin la hoja (correlación,
        experimenta, los dos análisis de auditoría, los dos de portafolios
        manuales y universo): el botón salía sin estilo y, al pulsarlo, el
        panel se abría al final del documento —transparente y fuera de la
        vista—, así que parecía que el clic no hacía nada.
        """
        missing = sorted(
            page for page in self.EXPECTED
            if "live_audit_schedule.css" not in (STATIC / page).read_text(encoding="utf-8")
        )
        self.assertFalse(
            missing,
            "Estas pantallas montan la campana sin su hoja de estilos; añadir "
            f'<link rel="stylesheet" href="/live_audit_schedule.css">: {missing}',
        )

    def test_each_screen_has_only_its_contextual_controls(self) -> None:
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                self.assertEqual(rendered_order(page), expected)

    def test_only_active_portfolios_has_the_audit_schedule(self) -> None:
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                self.assertEqual(CRON in expected, page == "dashboard.html")
                self.assertIn(BELL, expected)

    def test_updating_is_always_the_first_pair(self) -> None:
        # Donde hay boton de actualizar manda la fila; las pantallas que no lo
        # tienen empiezan por su control propio, no por la vuelta.
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                if "Actualizar" not in expected:
                    continue
                self.assertIn(expected[0], {"Actualizando…", "Cargando…", "Actualizar"})
                # Como mucho detras de su propio rotulo de estado.
                self.assertLessEqual(expected.index("Actualizar"), 1)

    def test_the_bell_and_the_restart_reach_every_screen(self) -> None:
        # Peticion del usuario (2026-10-21): la campana y el reinicio no son de
        # una pantalla, son del manager entero. El reinicio cierra la fila
        # siempre, y la campana va justo delante.
        pages = {page.name for page in STATIC.glob("*.html")}
        self.assertEqual(set(self.EXPECTED), pages)
        for page, expected in self.EXPECTED.items():
            with self.subTest(page=page):
                self.assertEqual(expected[-1], RESTART_LABEL)
                self.assertEqual(expected[-2], BELL)

    def test_the_restart_mount_appends_instead_of_jumping_the_queue(self) -> None:
        script = (STATIC / RESTART_MOUNT).read_text(encoding="utf-8")
        for expression in (
            "document.querySelector('#restart-manager')",
            "actions.appendChild(button)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, script)
        # Lo cargan todas menos las tres que ya traen el boton escrito.
        written = {"accounts.html", "dashboard.html", "index.html"}
        for page in STATIC.glob("*.html"):
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual(f"/{RESTART_MOUNT}" in source, page.name not in written)
                if page.name in written:
                    continue
                self.assertLess(
                    source.index(f"/{RESTART_MOUNT}"), source.index("/live_audit_schedule.js"),
                    "la campana se coloca delante de `#restart-manager`: si el boton se "
                    "monta despues, la campana se queda en mitad de la fila",
                )

    def test_the_account_cron_mounts_one_header_button(self) -> None:
        script = (STATIC / "live_account_schedule.js").read_text(encoding="utf-8")
        for expression in (
            f"actions.querySelector('{RESTART}')",
            f"actions.querySelector('{BACK_LINK}')",
            "actions.insertBefore(open, anchor || null)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, script)
        self.assertNotIn("actions.insertBefore(launch, anchor || null)", script)
        self.assertIn('id="account-snapshot-now"', script)
        self.assertEqual(MOUNTS["live_account_schedule.js"], (ACCOUNT_CRON,))

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
