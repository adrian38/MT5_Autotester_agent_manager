"""Comprobación manual: qué se comprueba, qué se exporta y quién avisa."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import manager_portfolio_routes, manual_check_watch, portfolio_manual_check


ROOT = Path(__file__).parents[1]


def _member(candidate: str, name: str, symbol: str = "XAUUSD", **extra) -> dict:
    return {
        "candidate_id": candidate, "set_name": name, "set_path": f"/data/ic/outputs/{name}",
        "symbol": symbol, "timeframe": "H1", "units": 1, **extra,
    }


class FakeSource:
    def __init__(self, portfolios: dict[int, dict], project: Path | None = None) -> None:
        self.portfolios = portfolios
        self.project = project or Path("/data/ic")

    def saved_portfolio_detail(self, portfolio_id: int, _scope: str) -> dict:
        if portfolio_id not in self.portfolios:
            raise ValueError(f"No existe el portafolio #{portfolio_id}")
        return {"portfolio": self.portfolios[portfolio_id]}


class NewStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = FakeSource({
            48: {"id": 48, "members": [_member("IC:1", "uno.set")]},
            53: {
                "id": 53,
                "members": [_member("IC:1", "uno.set"), _member("IC:2", "dos.set")],
                "improvement_origin": {"source_id": 48, "root_id": 48, "lineage": [
                    {"portfolio_id": 48},
                ]},
            },
            62: {
                "id": 62, "alias": "Arreglo",
                "members": [
                    _member("IC:1", "uno.set"), _member("IC:2", "dos.set"),
                    _member("IC:3", "tres.set", "EURUSD"),
                ],
                "improvement_origin": {"source_id": 53, "root_id": 48, "lineage": [
                    {"portfolio_id": 48}, {"portfolio_id": 53},
                ]},
            },
        })

    def test_only_what_no_ancestor_already_had_is_new(self) -> None:
        preview = portfolio_manual_check.new_strategies(self.source, 62, "full_history")

        self.assertEqual([row["set_name"] for row in preview["strategies"]], ["tres.set"])
        self.assertEqual(preview["total_members"], 3)
        self.assertEqual(preview["origin_ids"], [53, 48])
        self.assertEqual(preview["unreadable_origins"], [])

    def test_a_portfolio_without_ancestors_is_new_whole(self) -> None:
        preview = portfolio_manual_check.new_strategies(self.source, 48, "full_history")

        self.assertEqual([row["set_name"] for row in preview["strategies"]], ["uno.set"])
        self.assertEqual(preview["origin_ids"], [])

    def test_a_deleted_ancestor_is_declared_instead_of_counted_as_empty(self) -> None:
        del self.source.portfolios[53]

        preview = portfolio_manual_check.new_strategies(self.source, 62, "full_history")

        self.assertEqual(preview["unreadable_origins"], [53])
        # 48 sigue leyendose, asi que `uno.set` no vuelve a MT5 por el fallo.
        self.assertEqual([row["set_name"] for row in preview["strategies"]], ["dos.set", "tres.set"])

    def test_excluded_members_are_not_comprobable(self) -> None:
        self.source.portfolios[62]["members"][2]["excluded"] = True

        preview = portfolio_manual_check.new_strategies(self.source, 62, "full_history")

        self.assertEqual(preview["strategies"], [])

    def test_the_default_period_is_the_year_that_ends_today(self) -> None:
        self.assertEqual(
            portfolio_manual_check.default_period(date(2026, 10, 21)),
            ("2025.10.21", "2026.10.21"),
        )
        self.assertEqual(
            portfolio_manual_check.default_period(date(2024, 2, 29))[0], "2023.02.28",
        )

    def test_the_request_sent_to_the_node_recalculates_the_strategies(self) -> None:
        request = portfolio_manual_check.start_request(self.source, 62, "full_history", {
            "from_date": "2025.01.01", "to_date": "2026.01.01",
            "models": ["tick"], "use_multiterminal": False, "lot": 0.05,
            "strategies": [{"set_path": "/data/ic/outputs/inventado.set"}],
        })

        self.assertEqual([row["set_name"] for row in request["strategies"]], ["tres.set"])
        self.assertEqual(request["models"], ["tick"])
        self.assertFalse(request["use_multiterminal"])
        self.assertEqual(request["lot"], 0.05)

    def test_a_portfolio_without_new_strategies_does_not_start(self) -> None:
        self.source.portfolios[62]["members"] = [_member("IC:1", "uno.set")]

        with self.assertRaises(ValueError):
            portfolio_manual_check.start_request(self.source, 62, "full_history", {})


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "outputs").mkdir()
        (self.project / "outputs" / "tres.set").write_text("StartLots=0.01", encoding="utf-8")
        self.source = FakeSource({
            62: {"id": 62, "members": [
                _member("IC:3", "tres.set"), _member("IC:4", "falta.set"),
            ]},
        }, self.project)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _check_folder(self) -> Path:
        work = self.project / "runtime" / "nodo" / "manual_checks" / "check_20261021_120000_000001"
        (work / "reports" / "ohlc").mkdir(parents=True)
        (work / "reports" / "tick").mkdir(parents=True)
        (work / "check.json").write_text(json.dumps({"portfolio_id": 62}), encoding="utf-8")
        (work / "reports" / "ohlc" / "check_001_tres.htm").write_text("<html>", encoding="utf-8")
        (work / "reports" / "ohlc" / "check_001_tres-hst.png").write_bytes(b"png")
        (work / "reports" / "tick" / "check_001_tres.htm").write_text("<html>", encoding="utf-8")
        return work

    def test_only_the_new_sets_travel_in_their_own_zip(self) -> None:
        with patch.object(
            portfolio_manual_check, "_resolve_source_path",
            side_effect=lambda raw, project: project / "outputs" / Path(str(raw)).name,
        ):
            result = portfolio_manual_check.sets_archive(self.source, 62, "full_history")

        names = zipfile.ZipFile(io.BytesIO(result["content"])).namelist()
        self.assertEqual(names, ["NUEVAS_62/tres.set"])
        self.assertEqual(result["exported"], 1)
        self.assertEqual(result["missing"], ["falta.set"])

    def test_the_reports_zip_carries_the_chart_images_next_to_the_html(self) -> None:
        self._check_folder()

        result = portfolio_manual_check.reports_archive(
            self.project, "check_20261021_120000_000001",
        )

        names = zipfile.ZipFile(io.BytesIO(result["content"])).namelist()
        self.assertIn("COMPROBACION_check_20261021_120000_000001/ohlc/check_001_tres.htm", names)
        self.assertIn("COMPROBACION_check_20261021_120000_000001/ohlc/check_001_tres-hst.png", names)
        self.assertIn("COMPROBACION_check_20261021_120000_000001/tick/check_001_tres.htm", names)
        self.assertEqual(result["exported"], 3)

    def test_the_state_counts_documents_and_reads_the_request(self) -> None:
        self._check_folder()

        state = portfolio_manual_check.check_state(
            self.project, "check_20261021_120000_000001",
        )

        self.assertEqual(state["documents"], 2)
        self.assertEqual(state["request"]["portfolio_id"], 62)
        self.assertEqual(len(state["reports"]["ohlc"]), 2)

    def test_an_invented_check_identifier_cannot_reach_another_folder(self) -> None:
        for check_id in ("../../etc", "no_existe", ""):
            with self.subTest(check_id=check_id):
                with self.assertRaises(ValueError):
                    portfolio_manual_check.check_dir(self.project, check_id)

    def test_a_check_without_reports_says_so_instead_of_sending_an_empty_zip(self) -> None:
        work = self.project / "runtime" / "nodo" / "manual_checks" / "check_vacia"
        work.mkdir(parents=True)

        with self.assertRaises(ValueError):
            portfolio_manual_check.reports_archive(self.project, "check_vacia")


class RouteTests(unittest.TestCase):
    class Handler:
        def __init__(self, portfolios) -> None:
            self.server = SimpleNamespace(portfolios=portfolios)
            self.sent = None
            self.downloaded = None

        def _send_json(self, status, value) -> None:
            self.sent = (status, value)

        def _send_download(self, result) -> None:
            self.downloaded = result

    def test_the_start_is_forwarded_to_the_node_with_the_recalculated_request(self) -> None:
        portfolios = SimpleNamespace(
            manual_check_request=lambda *_args: {"portfolio_id": 62, "strategies": []},
        )
        handler = self.Handler(portfolios)
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
            return_value=(202, {"job_id": "manual_check_1"}),
        ) as request:
            manager_portfolio_routes.manual_check_action(
                handler, "manual-check-start", {"url": "http://ic"}, "ic", "full_history",
                {"portfolio_id": 62},
            )

        request.assert_called_once_with(
            {"url": "http://ic"}, "POST", "/api/v1/jobs/manual-check",
            {"portfolio_id": 62, "strategies": []}, timeout=120,
        )
        self.assertEqual(handler.sent, (202, {"job_id": "manual_check_1"}))

    def test_an_agent_without_the_port_says_what_to_do(self) -> None:
        portfolios = SimpleNamespace(manual_check_request=lambda *_args: {})
        handler = self.Handler(portfolios)
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
            return_value=(404, {"error": "Ruta no encontrada"}),
        ):
            with self.assertRaises(ValueError) as error:
                manager_portfolio_routes.manual_check_action(
                    handler, "manual-check-start", {"url": "http://ic"}, "ic",
                    "full_history", {"portfolio_id": 62},
                )

        self.assertIn("actualizarlo y reiniciarlo", str(error.exception))

    def test_an_unknown_action_is_left_for_the_next_link_of_the_chain(self) -> None:
        handler = self.Handler(SimpleNamespace())
        self.assertFalse(manager_portfolio_routes.manual_check_action(
            handler, "export", {}, "ic", "full_history", {},
        ))


class NoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.notices: list[dict] = []
        self.server = SimpleNamespace(
            nodes=[{"id": "ic", "name": "ICTrading", "url": "http://ic"}],
            live_audit_stop=SimpleNamespace(is_set=lambda: False),
            live_audit_settings_path=Path(self.temp.name) / "live_audit_settings.json",
            live_audit_notifications=SimpleNamespace(
                add=lambda *args, **kwargs: self.notices.append({"args": args, **kwargs}),
            ),
        )
        self.job = {
            "job_type": "manual_check", "job_id": "manual_check_20261021", "status": "completed",
            "request": {
                "portfolio_id": 62, "scope": "full_history", "models": ["ohlc", "tick"],
                "strategies": [{"set_name": "tres.set"}],
            },
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _tick(self) -> None:
        with patch.object(
            manual_check_watch.manager_http, "node_request",
            return_value=(200, {"job": self.job}),
        ):
            manual_check_watch.tick(self.server)

    def test_a_finished_check_is_announced_once(self) -> None:
        self._tick()
        self._tick()

        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.notices[0]["args"][1], "Comprobación manual terminada")
        self.assertIn("Portafolio #62", self.notices[0]["args"][2])
        self.assertIn("1 estrategia(s)", self.notices[0]["args"][2])
        self.assertEqual(self.notices[0]["link"], "/portfolios.html?node=ic")
        self.assertEqual(self.notices[0]["kind"], "manual_check")

    def test_a_running_check_is_not_announced(self) -> None:
        self.job["status"] = "running"

        self._tick()

        self.assertEqual(self.notices, [])

    def test_a_failure_is_announced_as_an_error_with_its_reason(self) -> None:
        self.job["status"] = "failed"
        self.job["error"] = "MT5 no generó el reporte"

        self._tick()

        self.assertEqual(self.notices[0]["args"][0], "error")
        self.assertIn("MT5 no generó el reporte", self.notices[0]["args"][2])

    def test_another_job_type_is_none_of_its_business(self) -> None:
        self.job["job_type"] = "generation"

        self._tick()

        self.assertEqual(self.notices, [])


class ScreenTests(unittest.TestCase):
    def test_the_button_and_its_script_are_on_the_ubs_screen(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "portfolio_manual_check.js").read_text(
            encoding="utf-8",
        )

        self.assertIn('id="detail-manual-check"', page)
        self.assertIn("/portfolio_manual_check.js", page)
        # Las dos casillas de tipo de datos y la del multiterminal, marcadas.
        self.assertIn('name="model_ohlc" type="checkbox" checked', script)
        self.assertIn('name="model_tick" type="checkbox" checked', script)
        self.assertIn('name="use_multiterminal" type="checkbox" checked', script)
        self.assertIn("manual-check-sets-download", script)
        self.assertIn("manual-check-download", script)
