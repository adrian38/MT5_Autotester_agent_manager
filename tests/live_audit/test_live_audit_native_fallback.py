"""El informe nativo del terminal desmiente a `history_deals_get`.

El 2026-10-24 la auditoría `audit-170-mvdscovr-2` publicó «0 reales» del
periodo 18→24/10 de la cuenta 77049426: la API devolvió 0 deals en sus 6
consultas y nadie miró el HTML que ese mismo terminal acababa de escribir, que
traía 50 posiciones cerradas y 14 abiertas.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_engine import LiveAuditController
from mt5_manager.live_audit_lifecycle import _native_history_note
from tests.live_audit.live_audit_engine_base import FakeOwner, LiveAuditEngineTestCase, request

PERIOD_START = datetime(2026, 10, 18, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 10, 24, 23, 59, 59, 999999, tzinfo=timezone.utc)

CLOSED_POSITION = (
    '<tr align="right"><td>2026.10.19 15:34:30</td><td>772438775</td><td>XOM</td>'
    '<td>buy</td><td class="hidden" colspan="8"></td><td>0.01</td><td>176.51</td>'
    "<td>177.11</td><td>187.00</td><td>2026.10.19 16:31:18</td><td>177.04</td>"
    '<td>-0.06</td><td>-0.08</td><td colspan="2">0.47</td></tr>'
)

OPEN_POSITION = (
    '<tr align="right"><td>2026.10.16 16:32:00</td><td>772066112</td><td>WFC</td>'
    "<td>buy</td><td>0.02</td><td>80.10</td><td>75.00</td><td>90.00</td>"
    "<td>80.90</td><td>0.00</td><td>1.60</td><td></td></tr>"
)

NATIVE_REPORT = (
    "<html><head><title>77049426: Informe del historial de trading</title></head><body>"
    '<table><tr align="center"><th colspan="14"><b>Posiciones</b></th></tr>'
    '<tr align="center" bgcolor="#E5F0FC"><td><b>Fecha/Hora</b></td>'
    "<td><b>Posición</b></td><td><b>Símbolo</b></td><td><b>Tipo</b></td>"
    "<td><b>Volumen</b></td><td><b>Precio</b></td><td><b>S / L</b></td>"
    "<td><b>T / P</b></td><td><b>Fecha/Hora</b></td><td><b>Precio</b></td>"
    "<td><b>Comisión</b></td><td><b>Swap</b></td>"
    '<td colspan="2"><b>Beneficio</b></td></tr>'
    + CLOSED_POSITION
    + '<tr align="center"><th colspan="14"><b>Posiciones abiertas</b></th></tr>'
    '<tr align="center" bgcolor="#E5F0FC"><td><b>Fecha/Hora</b></td>'
    "<td><b>Posición</b></td><td><b>Símbolo</b></td><td><b>Tipo</b></td>"
    "<td><b>Volumen</b></td><td><b>Precio</b></td><td><b>S / L</b></td>"
    "<td><b>T / P</b></td><td><b>Precio de mercado</b></td><td><b>Swap</b></td>"
    "<td><b>Beneficio</b></td><td><b>Comentario</b></td></tr>"
    + OPEN_POSITION
    + "</table></body></html>"
)


def _deal(ticket: int, position: int, entry: int, moment: datetime) -> SimpleNamespace:
    timestamp = int(moment.timestamp())
    return SimpleNamespace(
        ticket=ticket, position_id=position, entry=entry, type=0 if entry == 0 else 1,
        time=timestamp, time_msc=timestamp * 1000, magic=11008, symbol="EURUSD",
        volume=.01, price=1.1, profit=1.0, commission=0.0, swap=0.0, fee=0.0, comment="",
    )


class FakeMt5:
    """Un terminal cuya caché de historial se quedó vacía."""

    def __init__(self, period_deals: list[SimpleNamespace] | None = None) -> None:
        self.period_deals = period_deals or []
        self.symbols_asked: list[str] = []
        self.shutdown_called = False

    account_info = staticmethod(
        lambda: SimpleNamespace(login=111, server="IC-Real", currency="EUR")
    )
    terminal_info = staticmethod(lambda: SimpleNamespace(connected=True))
    last_error = staticmethod(lambda: (1, "Success"))

    def symbol_info(self, symbol: str) -> SimpleNamespace:
        self.symbols_asked.append(symbol)
        return SimpleNamespace(point=.01)

    def history_deals_get(self, *_args, **kwargs):
        return [] if "position" in kwargs else list(self.period_deals)

    def history_orders_get(self, *_args, **_kwargs):
        return []

    def shutdown(self) -> None:
        self.shutdown_called = True


class NativeReportFallbackTests(LiveAuditEngineTestCase):
    def _extract(self, folder: Path, mt5: FakeMt5, report: str | None = NATIVE_REPORT):
        controller = LiveAuditController(FakeOwner("idle"), folder)
        controller.history_sync_attempts = 2
        controller.history_sync_delay_seconds = 0
        destination = folder / "real_account_mt5_report.html"

        def export(**_kwargs) -> dict:
            if report is not None:
                destination.write_text(report, encoding="utf-16")
            return {"native_terminal_report": True, "filename": destination.name}

        controller._export_native_account_report = export
        controller._login_terminal = lambda *_a, **_k: (
            mt5, "Terminal.2", {"name": "MT5_1", "mt5_path": "mt5.exe"}, set()
        )
        return controller._extract_real(request(), PERIOD_START, PERIOD_END, destination)

    def test_an_empty_api_is_replaced_by_what_the_terminal_printed(self) -> None:
        mt5 = FakeMt5()
        with tempfile.TemporaryDirectory() as temp:
            trades, points, account = self._extract(Path(temp), mt5)

        self.assertEqual([trade["position_id"] for trade in trades], [772438775])
        self.assertEqual(trades[0]["symbol"], "XOM")
        self.assertEqual(trades[0]["sl"], 177.11)
        detail = account["history_detail"]
        self.assertEqual(detail["real_trades_source"], "native_terminal_report")
        self.assertEqual(detail["native_closed_positions"], 1)
        self.assertEqual(detail["trades_reconstructed"], 1)
        # La posición viva al acabar el periodo también sale del informe: sin
        # ella, una real abierta se contaría como operación sin pareja.
        self.assertEqual(detail["positions_open_at_period_end"], 1)
        self.assertEqual(
            [row["position_id"] for row in detail["open_positions_at_period_end"]],
            [772066112],
        )
        # El punto del símbolo se pide para los que trae el informe, no para
        # los que no vinieron de la API.
        self.assertEqual(points, {"XOM": .01})
        self.assertEqual(mt5.symbols_asked, ["XOM"])

    def test_the_log_line_says_that_the_report_replaced_the_api(self) -> None:
        mt5 = FakeMt5()
        with tempfile.TemporaryDirectory() as temp:
            _trades, _points, account = self._extract(Path(temp), mt5)
        note = _native_history_note(account["history_detail"])
        self.assertIn("MT5 no devolvió ningún deal", note)
        self.assertIn("informe nativo del terminal", note)

    def test_an_api_with_operations_is_never_overridden_only_annotated(self) -> None:
        opening = _deal(1, 20, 0, datetime(2026, 10, 19, 10, tzinfo=timezone.utc))
        closing = _deal(2, 20, 1, datetime(2026, 10, 19, 12, tzinfo=timezone.utc))
        mt5 = FakeMt5([opening, closing])
        with tempfile.TemporaryDirectory() as temp:
            trades, points, account = self._extract(Path(temp), mt5)

        self.assertEqual([trade["position_id"] for trade in trades], [20])
        self.assertEqual(points, {"EURUSD": .01})
        detail = account["history_detail"]
        self.assertNotIn("real_trades_source", detail)
        # La discrepancia se anota igual: el informe se pide por fechas, sin
        # hora, así que un número distinto no basta para desconfiar.
        self.assertEqual(detail["native_closed_positions"], 1)
        self.assertEqual(_native_history_note(detail), "")

    def test_an_account_that_did_not_trade_keeps_its_empty_result(self) -> None:
        empty = NATIVE_REPORT.replace(CLOSED_POSITION, "").replace(OPEN_POSITION, "")
        mt5 = FakeMt5()
        with tempfile.TemporaryDirectory() as temp:
            trades, _points, account = self._extract(Path(temp), mt5, empty)

        self.assertEqual(trades, [])
        detail = account["history_detail"]
        self.assertEqual(detail["native_closed_positions"], 0)
        self.assertNotIn("real_trades_source", detail)

    def test_an_unreadable_report_with_an_empty_api_stops_the_audit(self) -> None:
        mt5 = FakeMt5()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "no se pudo leer"):
                self._extract(Path(temp), mt5, report=None)
        self.assertTrue(mt5.shutdown_called)


if __name__ == "__main__":
    unittest.main()
