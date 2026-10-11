from __future__ import annotations

import base64
import io
import unittest
import zipfile
from datetime import datetime, timezone

from mt5_manager.live_audit_analysis import analyze
from mt5_manager.live_audit_autolot import balance_history_from_html, import_workbook
from mt5_manager.live_audit_settings_schema import normalize_live_audit_settings


STRATEGY = "ROBOFOREX/ECN:autolot"


def workbook_bytes() -> bytes:
    shared = ["ESTRATEGIA", "Dd_0.01", "%_riesgo", STRATEGY, "11.5"]
    strings = "".join(f"<si><t>{value}</t></si>" for value in shared)
    sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
      <row r="3"><c r="B3" t="s"><v>0</v></c><c r="C3" t="s"><v>1</v></c><c r="D3" t="s"><v>2</v></c></row>
      <row r="4"><c r="B4" t="s"><v>3</v></c><c r="C4"><v>20</v></c><c r="D4" t="s"><v>4</v></c></row>
    </sheetData></worksheet>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""")
        archive.writestr("xl/_rels/workbook.xml.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>""")
        archive.writestr("xl/sharedStrings.xml", f"<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">{strings}</sst>")
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def audit_profile(**changes: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "trade_time_tolerance_seconds": 120, "price_tolerance_points": 15.0,
        "volume_tolerance_pct": 1.0, "pnl_deviation_warning_pct": 10.0,
        "drawdown_deviation_warning_pct": 1000.0, "min_tick_history_quality_pct": 80.0,
        "real_strategy_lots": {},
    }
    profile.update(changes)
    return profile


def trade(opened: datetime, volume: float, profit: float) -> dict[str, object]:
    return {
        "strategy": STRATEGY, "symbol": "EURUSD", "side": "buy",
        "open_time": opened.isoformat(),
        "close_time": opened.replace(minute=opened.minute + 10).isoformat(),
        "open_price": 1.1, "close_price": 1.11, "volume": volume, "profit": profit,
    }


def audit_payload() -> dict[str, object]:
    first = datetime(2026, 9, 22, 9, 30, tzinfo=timezone.utc)
    second = datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)
    return {
        "audit_id": "run-autolot", "completed_at": "2026-09-22T12:00:00+00:00",
        "period_start": "2026-09-22T00:00:00+00:00", "period_end": "2026-09-22T23:59:59+00:00",
        "request": {
            "audit_key": "audit-148", "portfolio_id": 148, "portfolio_type": "aggressive",
            "period_mode": "fixed_dates", "period_days": 1,
            "period_start_date": "2026-09-22", "period_end_date": "2026-09-22",
        },
        "tester_trades": [trade(first, 0.01, 1.0), trade(second, 0.01, 1.0)],
        "real_trades": [trade(first, 0.1, 10.0), trade(second, 0.2, 20.0)],
        "symbol_points": {"EURUSD": 0.00001}, "strategies": {STRATEGY: 2},
        "qualities": [100.0], "strategy_artifacts": [],
        "selected_members": [{"candidate_id": STRATEGY, "symbol": "EURUSD", "lot": 0.01}],
        "volume_rules": {"eurusd": {"volume_min": 0.01, "volume_step": 0.01}},
        "account": {}, "real_history_detail": {}, "tester_execution": {},
        "real_account_report": {}, "terminal_restore": [], "open_positions_at_period_end": [],
    }


class WorkbookImportTests(unittest.TestCase):
    def test_the_supplied_columns_accept_numeric_text_and_become_rules(self) -> None:
        contents = workbook_bytes()
        result = import_workbook({
            "filename": "riesgo.xlsx",
            "content_base64": base64.b64encode(contents).decode("ascii"),
        })
        self.assertTrue(result["enabled"])
        self.assertEqual(result["rules"][STRATEGY], {"dd_001": 20.0, "risk_pct": 11.5})

    def test_autolot_settings_are_validated_and_persistable(self) -> None:
        configured = normalize_live_audit_settings({
            "autolot": {
                "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
                "rules": {STRATEGY: {"dd_001": "20", "risk_pct": "11.5"}},
            },
        })
        self.assertEqual(configured["autolot"]["rules"][STRATEGY]["risk_pct"], 11.5)
        with self.assertRaisesRegex(ValueError, "después de importar"):
            normalize_live_audit_settings({"autolot": {"enabled": True, "rules": {}}})


class AccountBalanceTests(unittest.TestCase):
    def test_the_native_mt5_report_supplies_the_running_balance(self) -> None:
        headers = [
            "Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price",
            "Order", "Commission", "Swap", "Profit", "Balance", "Comment",
        ]
        rows = [
            ["2026.09.22 09:00:00", "1", "", "balance", "", "0", "0", "", "0", "0", "1000", "1000", "deposit"],
            ["2026.09.22 10:00:00", "2", "EURUSD", "buy", "out", "0.1", "1.1", "1", "0", "0", "1000", "2000", ""],
        ]
        cells = lambda values: "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
        document = ("<html><body>" + cells(["Deals"]) + cells(headers)
                    + "".join(cells(row) for row in rows) + "</body></html>").encode()
        history = balance_history_from_html(document)
        self.assertEqual([row["balance"] for row in history], [1000.0, 2000.0])


class DynamicAuditTests(unittest.TestCase):
    def test_each_operation_uses_the_balance_available_at_its_opening(self) -> None:
        result = analyze(audit_payload(), audit_profile(
            autolot={
                "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
                "rules": {STRATEGY: {"dd_001": 10.0, "risk_pct": 10.0}},
            },
            _autolot_balance_history=[
                {"time": "2026-09-22T09:00:00+00:00", "balance": 1000.0},
                {"time": "2026-09-22T10:00:00+00:00", "balance": 2000.0},
            ],
        ))
        rows = result["comparison_detail"]["operation_comparisons"]
        self.assertEqual(result["within_tolerance_trades"], 2)
        self.assertEqual([row["limits"]["volume_expected_real"] for row in rows], [0.1, 0.2])
        self.assertEqual([row["limits"]["balance"] for row in rows], [1000.0, 2000.0])
        self.assertTrue(all(row["limits"]["volume_expected_source"] == "autolot" for row in rows))
        self.assertEqual(result["portfolio_filter"]["portfolio_closures"], 2)

    def test_enabled_autolot_never_silently_falls_back_without_balance_history(self) -> None:
        with self.assertRaisesRegex(ValueError, "saldo histórico"):
            analyze(audit_payload(), audit_profile(autolot={
                "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
                "rules": {STRATEGY: {"dd_001": 10.0, "risk_pct": 10.0}},
            }))


if __name__ == "__main__":
    unittest.main()
