"""El panel del resumen mensual: sus rutas, su almacén y lo que no recalcula.

En este equipo no hay runtime de JavaScript, así que la pantalla se comprueba
por el texto de su fichero. Lo que importa aquí es que el panel **pinte y no
decida**: los veredictos, los avisos y sus frases vienen hechos de
`monthly_summary_notices.py`, y dos criterios para el mismo número acabarían
diciendo cosas distintas del mismo mes. La única excepción es el veredicto de
reproducción, que vive en `live_audit_reproduction.js` desde antes y que el
panel pide prestado en vez de copiar.

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
from mt5_manager.monthly_summary_store import MAXIMUM_MONTHS, MonthlySummaryStore

STATIC = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"
SCRIPT = (STATIC / "monthly_summary.js").read_text(encoding="utf-8")
STYLE = (STATIC / "monthly_summary.css").read_text(encoding="utf-8")
HOSTS = ("accounts.html", "dashboard.html")


def ids_of(script: str) -> set[str]:
    """Los ids que el script pide, por `querySelector` y por su atajo `field`."""
    return set(re.findall(r"(?:querySelector|field)\('#([\w-]+)'\)", script))


def declared_ids(script: str) -> set[str]:
    return set(re.findall(r'id="([\w-]+)"', script)) | set(
        re.findall(r"\.id = '([\w-]+)'", script),
    )


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
    def test_both_assets_are_servable(self) -> None:
        self.assertLessEqual(
            {"monthly_summary.js", "monthly_summary.css"}, manager_handler.STATIC_FILES,
        )

    def test_only_the_two_real_operation_screens_mount_it(self) -> None:
        for page in STATIC.glob("*.html"):
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual("/monthly_summary.js" in source, page.name in HOSTS)

    def test_the_panel_takes_its_stylesheet_and_its_borrowed_rules_with_it(self) -> None:
        # El panel se monta con `appendChild`, así que sin su hoja saldría al
        # final del documento y sin estilo; y sin los dos ficheros del
        # veredicto de reproducción las tarjetas se quedarían sin esa línea.
        for page in HOSTS:
            source = (STATIC / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                for asset in (
                    "/monthly_summary.css", "/live_audit_schedule.css",
                    "/live_audit_reproduction.js", "/dashboard_metrics.js",
                ):
                    self.assertIn(asset, source)
                self.assertLess(
                    source.index("/live_audit_reproduction.js"),
                    source.index("/monthly_summary.js"),
                    "el panel lee `window.LiveAuditReproduction` al abrirse",
                )

    def test_the_bell_link_lands_on_a_page_that_exists(self) -> None:
        from mt5_manager import monthly_summary_watch

        page, _, query = monthly_summary_watch.LINK.partition("?")
        source = Path(manager_handler.__file__).read_text(encoding="utf-8")
        self.assertIn(f'if parsed.path == "{page}":', source)
        self.assertIn(query.partition("=")[0], SCRIPT)


class ScriptTests(unittest.TestCase):
    def test_every_id_it_asks_for_is_one_it_creates(self) -> None:
        # `#toast` y `#restart-manager` son de la pantalla, no del panel.
        asked = ids_of(SCRIPT) - {"toast", "restart-manager"}
        self.assertLessEqual(asked, declared_ids(SCRIPT))
        self.assertIn("monthly-summary-cards", asked)

    def test_the_verdict_and_the_notices_come_from_the_manager(self) -> None:
        for expression in ("card.notices", "card.verdict", "report.counts"):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)

    def test_it_does_not_measure_the_month_again(self) -> None:
        # Las cuatro cosas que el manager ya decidió. Si alguna se calculase
        # aquí, el panel y la campana podrían decir distinto del mismo mes.
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
        self.assertLess(SCRIPT.index("renderReport(state"), SCRIPT.index("loadReproduction(state"))

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
        self.assertIn("get(SUMMARY).then(renderBadge).catch(() => {})", SCRIPT)


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
