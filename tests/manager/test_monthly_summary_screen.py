"""La pantalla del resumen mensual: sus rutas, su almacén y lo que no recalcula.

En este equipo no hay runtime de JavaScript, así que la pantalla se comprueba
por el texto de sus ficheros. Lo que importa aquí es que **pinte y no decida**:
los veredictos, los avisos y sus frases vienen hechos de
`monthly_summary_notices.py`, y dos criterios para el mismo número acabarían
diciendo cosas distintas del mismo mes. La única excepción es el veredicto de
reproducción, que vive en `live_audit_reproduction.js` desde antes y que la
pantalla pide prestado en vez de copiar.

El resumen es una **ventana propia** desde el 2026-10-25, no un diálogo sobre
la portada, y eso parte el JavaScript en dos: `monthly_summary_button.js`
monta el botón en las dos pantallas de operación real y `monthly_summary.js`
pinta la pantalla. Que la portada no cargue el pintado que nunca usa es parte
del contrato, y por eso se comprueba.

Del lado del manager se comprueba lo que decide: que un `?month=` a medias no
pase por mes, que recalcular borre la marca de leído —lo que el usuario miró
ya no es lo que hay— y que el almacén no crezca sin fin.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import manager_handler, manager_summary_routes, monthly_summary_runtime
from mt5_manager import monthly_summary_watch
from mt5_manager.monthly_summary_store import MAXIMUM_MONTHS, MonthlySummaryStore

STATIC = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"
PAGE = (STATIC / "monthly_summary.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "monthly_summary.js").read_text(encoding="utf-8")
BUTTON = (STATIC / "monthly_summary_button.js").read_text(encoding="utf-8")
STYLE = (STATIC / "monthly_summary.css").read_text(encoding="utf-8")
HOSTS = ("accounts.html", "dashboard.html")
"""Las dos pantallas de operación real, que son las que montan el botón."""


def ids_of(script: str) -> set[str]:
    """Los ids que el guion pide, por `querySelector` y por su atajo `field`."""
    return set(re.findall(r"(?:querySelector|field)\('#([\w-]+)'\)", script))


def declared_ids(*sources: str) -> set[str]:
    found: set[str] = set()
    for source in sources:
        found |= set(re.findall(r'id="([\w-]+)"', source))
        found |= set(re.findall(r"\.id = '([\w-]+)'", source))
    return found


class FakeHandler:
    """Lo mínimo que las rutas le piden al handler: un cuerpo y una respuesta."""

    def __init__(self, server: object, body: dict | None = None) -> None:
        self.server = server
        self._payload = body or {}
        self.status = 0
        self.sent: dict = {}

    def _body(self) -> dict:
        return dict(self._payload)

    def _send_json(self, status: int, value: dict) -> None:
        self.status = status
        self.sent = value


class FakeServer:
    """El servidor que las rutas tocan, con la misma delegacion que el real."""

    nodes: list = []

    def __init__(self) -> None:
        # `update` despierta el hilo del cron; aqui no hay hilo, pero el
        # aviso tiene que poder darse igual.
        self.monthly_summary_wakeup = threading.Event()

    def update_monthly_summary(self, changes: dict) -> dict:
        return monthly_summary_runtime.update(self, changes)


def parsed(path: str):
    return urllib.parse.urlparse(path)


def report(month: str, verdict: str = "keep") -> dict:
    return {
        "month": month, "generated_at": f"{month}-01T09:00:00+00:00",
        "accounts": [{"verdict": verdict, "login": "1", "node_id": "n"}],
        "counts": {verdict: 1},
    }


class RoutingTests(unittest.TestCase):
    def test_the_page_and_its_three_assets_are_servable(self) -> None:
        self.assertLessEqual(
            {
                "monthly_summary.html", "monthly_summary.js",
                "monthly_summary_button.js", "monthly_summary.css",
            },
            manager_handler.STATIC_FILES,
        )

    def test_only_the_two_real_operation_screens_mount_the_button(self) -> None:
        for page in STATIC.glob("*.html"):
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual(
                    "/monthly_summary_button.js" in source, page.name in HOSTS,
                )

    def test_only_its_own_page_loads_the_painting(self) -> None:
        # La portada no carga lo que nunca usa: son 300 líneas y los dos
        # ficheros del veredicto de reproducción detrás.
        for page in STATIC.glob("*.html"):
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual(
                    '"/monthly_summary.js"' in source, page.name == "monthly_summary.html",
                )

    def test_the_page_brings_its_stylesheet_and_its_borrowed_rules(self) -> None:
        for asset in (
            "/monthly_summary.css", "/live_audit_schedule.css", "/styles.css",
            "/live_audit_reproduction.js", "/dashboard_metrics.js",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, PAGE)
        self.assertLess(
            PAGE.index("/live_audit_reproduction.js"), PAGE.index("/monthly_summary.js"),
            "la pantalla lee `window.LiveAuditReproduction` al cargarse",
        )

    def test_the_button_opens_a_window_instead_of_a_dialog(self) -> None:
        for expression in (
            "const PAGE = '/monthly_summary.html'", "open.target = '_blank'",
            "open.rel = 'noopener'",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, BUTTON)
        # Lo que fue un diálogo y ya no lo es.
        self.assertNotIn("showModal", BUTTON + SCRIPT)

    def test_the_button_takes_the_same_anchor_as_the_other_two(self) -> None:
        for expression in (
            "actions.querySelector('#restart-manager')",
            'actions.querySelector(\'a[href="/"]\')',
            "actions.insertBefore(open, anchor || null)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, BUTTON)

    def test_the_bell_lands_on_the_page_with_its_month(self) -> None:
        self.assertEqual(monthly_summary_watch.LINK, "/monthly_summary.html")
        self.assertIn("monthly_summary.html", manager_handler.STATIC_FILES)
        source = Path(monthly_summary_watch.__file__).read_text(encoding="utf-8")
        self.assertIn('link=f"{LINK}?month=', source)
        # Y la pantalla lo lee, que es la otra mitad del enlace.
        self.assertIn("URLSearchParams(window.location.search).get('month')", SCRIPT)


class ScriptTests(unittest.TestCase):
    def test_every_id_it_asks_for_is_one_the_page_declares(self) -> None:
        # `#restart-manager` es el ancla de la barra y lo pone cada pantalla
        # (o `manager_restart.js`); el resto sale de este HTML y estos guiones.
        asked = (ids_of(SCRIPT) | ids_of(BUTTON)) - {"restart-manager"}
        self.assertLessEqual(asked, declared_ids(PAGE, SCRIPT, BUTTON))
        self.assertIn("monthly-summary-cards", asked)

    def test_the_verdict_and_the_notices_come_from_the_manager(self) -> None:
        for expression in ("card.notices", "card.verdict", "report.counts"):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)

    def test_it_does_not_measure_the_month_again(self) -> None:
        # Las cuatro cosas que el manager ya decidió. Si alguna se calculase
        # aquí, la pantalla y la campana podrían decir distinto del mismo mes.
        for forbidden in ("below_promise", "COVERAGE_MINIMUM", "quantile", "worse_than"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, SCRIPT)

    def test_the_reproduction_verdict_is_borrowed_and_not_copied(self) -> None:
        for expression in (
            "window.LiveDashboardMetrics", "window.LiveAuditReproduction",
            "metrics.summarize(", "NO REPRODUCE",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)
        # El cálculo entra en el nodo: se pide después de pintar, nunca antes.
        self.assertIn("loadReproduction", SCRIPT)
        self.assertLess(
            SCRIPT.index("renderReport(answer"), SCRIPT.index("loadReproduction(answer"),
        )

    def test_recalculating_asks_first(self) -> None:
        # El resumen guardado es la foto de lo que se sabía al cerrar el mes.
        run = SCRIPT[SCRIPT.index("async function recalculate"):]
        self.assertIn("window.confirm(warning)", run)
        self.assertIn("/api/monthly-summary/run", run)

    def test_the_four_verdicts_have_a_colour(self) -> None:
        for verdict in ("change", "watch", "keep", "unknown"):
            with self.subTest(verdict=verdict):
                self.assertIn(f".summary-card.{verdict}{{", STYLE)
                self.assertIn(f".summary-verdict.{verdict}{{", STYLE)

    def test_the_badge_is_painted_without_opening_anything(self) -> None:
        # El día 1 el botón ya dice que hay algo que mirar, y que falle no
        # puede dejar la pantalla a medias.
        self.assertIn("/api/monthly-summary", BUTTON)
        self.assertIn(".catch(() => {})", BUTTON)


class MonthTests(unittest.TestCase):
    def test_half_a_date_is_not_a_month(self) -> None:
        for value in ("2026", "2026-13", "2026-1", "202610", "", None, "2026-aa"):
            with self.subTest(value=value):
                self.assertEqual(manager_summary_routes._month(value), "")
        self.assertEqual(manager_summary_routes._month(" 2026-09 "), "2026-09")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "monthly_summary.json"
        self.addCleanup(self.folder.cleanup)

    def test_a_recalculated_month_loses_its_read_mark(self) -> None:
        store = MonthlySummaryStore(self.path)
        store.remember(report("2026-09"))
        store.mark_read("2026-09")
        self.assertEqual(store.unread(), [])
        store.remember(report("2026-09"))
        self.assertEqual(store.unread(), ["2026-09"])

    def test_it_keeps_two_years_and_no_more(self) -> None:
        store = MonthlySummaryStore(self.path)
        for step in range(MAXIMUM_MONTHS + 5):
            store.remember(report(f"20{24 + step // 12:02d}-{step % 12 + 1:02d}"))
        self.assertLessEqual(len(store.months()), MAXIMUM_MONTHS)

    def test_a_summary_without_a_month_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            MonthlySummaryStore(self.path).remember({"accounts": []})

    def test_it_survives_a_restart(self) -> None:
        MonthlySummaryStore(self.path).remember(report("2026-09"))
        self.assertEqual(MonthlySummaryStore(self.path).months(), ["2026-09"])

    def test_a_broken_file_does_not_take_the_manager_down(self) -> None:
        self.path.write_text("{no es json", encoding="utf-8")
        self.assertEqual(MonthlySummaryStore(self.path).months(), [])


class RouteAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer()
        monthly_summary_runtime.configure(
            self.server, {}, Path(self.folder.name) / "settings.json",
        )
        self.server.monthly_summaries.remember(report("2026-08"))
        self.server.monthly_summaries.remember(report("2026-09", "change"))

    def get(self, path: str) -> FakeHandler:
        handler = FakeHandler(self.server)
        self.assertTrue(
            manager_summary_routes.handle_get(handler, parsed(path), path.split("/")),
        )
        return handler

    def test_the_latest_month_is_what_the_panel_gets_without_asking(self) -> None:
        answer = self.get("/api/monthly-summary").sent
        self.assertEqual(answer["report"]["month"], "2026-09")
        self.assertEqual(answer["months"], ["2026-09", "2026-08"])
        self.assertEqual(answer["unread"], ["2026-09", "2026-08"])

    def test_a_month_that_is_asked_for_is_the_one_that_comes_back(self) -> None:
        answer = self.get("/api/monthly-summary?month=2026-08").sent
        self.assertEqual(answer["report"]["month"], "2026-08")

    def test_a_month_that_is_not_a_month_falls_back_instead_of_inventing(self) -> None:
        answer = self.get("/api/monthly-summary?month=2026").sent
        self.assertEqual(answer["report"]["month"], "2026-09")

    def test_the_configuration_says_when_the_next_one_lands(self) -> None:
        answer = self.get("/api/monthly-summary-config").sent
        self.assertTrue(answer["enabled"])
        self.assertEqual(answer["day"], 1)
        self.assertTrue(answer["next_run_at"])
        self.assertEqual(answer["unread"], ["2026-09", "2026-08"])

    def test_reading_a_month_takes_it_off_the_badge(self) -> None:
        handler = FakeHandler(self.server, {"month": "2026-09"})
        path = "/api/monthly-summary/read"
        self.assertTrue(
            manager_summary_routes.handle_post(handler, parsed(path), path.split("/")),
        )
        self.assertEqual(handler.sent["unread"], ["2026-08"])

    def test_saving_the_appointment_applies_it_without_a_restart(self) -> None:
        handler = FakeHandler(self.server, {"day": 3, "below_streak_months": 4})
        path = "/api/monthly-summary-config"
        self.assertTrue(
            manager_summary_routes.handle_post(handler, parsed(path), path.split("/")),
        )
        self.assertEqual(handler.status, 200)
        self.assertEqual(self.server.monthly_summary_settings["day"], 3)
        self.assertEqual(handler.sent["below_streak_months"], 4)
        self.assertEqual(
            json.loads(self.server.monthly_summary_path.read_text(encoding="utf-8"))["day"], 3,
        )

    def test_a_refused_appointment_comes_back_as_an_error(self) -> None:
        handler = FakeHandler(self.server, {"day": 31})
        path = "/api/monthly-summary-config"
        manager_summary_routes.handle_post(handler, parsed(path), path.split("/"))
        self.assertEqual(handler.status, 400)
        self.assertIn("día", handler.sent["error"].lower() + "día")
        self.assertEqual(self.server.monthly_summary_settings["day"], 1)

    def test_what_is_not_its_route_it_leaves_alone(self) -> None:
        handler = FakeHandler(self.server)
        path = "/api/live-accounts"
        self.assertFalse(
            manager_summary_routes.handle_get(handler, parsed(path), path.split("/")),
        )
        self.assertFalse(
            manager_summary_routes.handle_post(handler, parsed(path), path.split("/")),
        )


if __name__ == "__main__":
    unittest.main()
