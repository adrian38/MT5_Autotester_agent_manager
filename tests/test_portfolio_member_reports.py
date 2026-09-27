import io
import json
import sqlite3
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from mt5_manager.portfolio_service import PortfolioSource
from tests import test_integration as integration_helpers


class PortfolioMemberReportTests(unittest.TestCase):
    def make_source(self, root: Path) -> PortfolioSource:
        (root / "outputs").mkdir()
        (root / "assets").mkdir()
        (root / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
        return PortfolioSource({
            "portfolio_project_dir": str(root),
            "portfolio_broker": "ICTRADING",
            "portfolio_account_type": "STANDARD",
        })

    def test_opens_robustness_and_exports_every_report_that_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.make_source(root)
            reports = root / "reports"
            reports.mkdir()
            paths = {
                "is_report_path": reports / "base.htm",
                "oos_report_path": reports / "robust.htm",
                "full_history_report_path": reports / "continuous.htm",
                "final_ohlc_report_path": reports / "ohlc6m.htm",
                "final_tick_report_path": reports / "tick6m.htm",
            }
            for key, path in paths.items():
                path.write_bytes(f"<html>{key}</html>".encode())
            set_path = root / "NFLX_H1.set"
            set_path.write_text("Risk=1\n", encoding="utf-8")
            member = {
                "candidate_id": "ICTRADING/STANDARD:17",
                "set_path": str(set_path),
                **{
                    key: str(path)
                    for key, path in paths.items()
                    if key != "final_ohlc_report_path"
                },
            }
            detail = {"portfolio": {"members": [member]}}
            candidate = {
                "candidate_id": "ICTRADING/STANDARD:17",
                "set_path": str(set_path),
                "final_ohlc_report_path": str(paths["final_ohlc_report_path"]),
            }
            with patch.object(source, "saved_portfolio_detail", return_value=detail), patch.object(
                source, "import_candidate_rows", return_value=[candidate]
            ):
                family = source.member_reports(8, "full_history", str(set_path))
                opened = source.open_member_report(8, "full_history", str(set_path))
                exported = source.export_member_reports_archive(8, "full_history", str(set_path))

            self.assertEqual([row["code"] for row in family["reports"]], [
                "base", "robustez", "final_tick_continuo",
                "final_tick_6m_ohlc", "final_tick_6m_every_tick",
            ])
            self.assertEqual(opened["filename"], "robust.htm")
            self.assertEqual(opened["content_type"], "text/html")
            self.assertEqual(exported["exported"], 5)
            with zipfile.ZipFile(io.BytesIO(exported["content"])) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), 5)
                self.assertTrue(any("final_tick_6m_ohlc" in name for name in names))

    def test_saved_report_does_not_depend_on_current_candidate_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.make_source(root)
            report = root / "saved_robust.htm"
            report.write_bytes(b"<html>saved</html>")
            set_path = root / "NFLX_H1.set"
            detail = {"portfolio": {"members": [{
                "candidate_id": "ICTRADING/STANDARD:17",
                "set_path": str(set_path),
                "oos_report_path": str(report),
            }]}}
            with patch.object(source, "saved_portfolio_detail", return_value=detail), patch.object(
                source, "import_candidate_rows", side_effect=sqlite3.OperationalError("locked")
            ):
                opened = source.open_member_report(8, "full_history", str(set_path))

            self.assertEqual(opened["content"], b"<html>saved</html>")

    def test_screen_opens_html_in_browser_and_offers_report_zip(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("window.open('about:blank', '_blank')", script)
        self.assertIn("portfolio-manager/open-report", script)
        self.assertIn("Exportar reportes", script)
        self.assertIn("'export-member-reports'", script)


class PortfolioMemberReportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = integration_helpers.LocalIntegrationTests(
            "test_manager_reaches_node_starts_job_and_reads_log"
        )
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def post(self, action: str, payload: dict):
        return urllib.request.urlopen(urllib.request.Request(
            f"{self.fixture.base}/api/nodes/test-node/portfolio-manager/{action}",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        ), timeout=3)

    def test_http_route_renders_report_and_downloads_archive(self) -> None:
        payload = {"scope": "full_history", "portfolio_id": 8, "set_path": "NFLX.set"}
        report = {
            "filename": "robust.htm", "content": b"<html>robust</html>",
            "content_type": "text/html", "stage": "Robustez",
        }
        with patch.object(
            self.fixture.manager.portfolios, "open_report", return_value=report
        ):
            with self.post("open-report", payload) as response:
                self.assertEqual(response.headers.get_content_type(), "text/html")
                self.assertIn("inline", response.headers["Content-Disposition"])
                self.assertEqual(response.read(), report["content"])

        archive = {
            "filename": "REPORTES_NFLX.zip", "content": b"PK\x03\x04reports",
            "exported": 5, "missing": [],
        }
        with patch.object(
            self.fixture.manager.portfolios,
            "export_member_reports_archive",
            return_value=archive,
        ):
            with self.post("export-member-reports", payload) as response:
                self.assertEqual(response.headers.get_content_type(), "application/zip")
                self.assertEqual(response.headers["X-Exported-Files"], "5")
                self.assertEqual(response.read(), archive["content"])


if __name__ == "__main__":
    unittest.main()
