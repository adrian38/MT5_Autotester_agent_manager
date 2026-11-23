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
from unittest import mock

from mt5_manager.live_audit.live_audit_engine import LiveAuditController
from mt5_manager.live_audit.live_audit_lifecycle import _native_history_note
from mt5_manager.live_audit.mt5_native_history_report import (
    NativeHistoryReportError, _RemoteAllocationError, _WindowsTerminalReportExporter,
    export_native_history_report,
)
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


class IsolatedReportDestinationTests(unittest.TestCase):
    """El plan B en otra terminal chocaba con lo que dejó el intento fallido.

    `_export_isolated_native_report` reutiliza el mismo destino, y
    `_save_report` empieza por rechazar un destino ocupado. Los fallos que
    dejan fichero son justo los que más piden otra terminal:
    `validate_native_history_report` rechaza el informe de otra cuenta
    **después** de que MT5 lo haya escrito. Cada respaldo hacía la pasada
    entera por la GUI para morir con «el destino ya existe», que además tapaba
    la causa real en la lista de errores.
    """

    @staticmethod
    def _controller(root: Path) -> LiveAuditController:
        controller = LiveAuditController(FakeOwner("idle"), root)
        controller._native_report_profiles = lambda _excluded: [
            ("Terminal.2", {"name": "MT5 respaldo", "mt5_path": r"C:\IC\2\terminal64.exe"}),
        ]
        # Con el proceso de la principal vivo, el plan B es la otra
        # instalación y no un relanzamiento de la suya.
        controller._terminal_pids_for_path = lambda _path: {4242}
        controller._close_terminal_pids = lambda _pids: None
        controller._remember_real_account_terminal = lambda *_args: None
        controller._synchronised_history = lambda *_args: ([], {})
        return controller

    def test_the_report_rejected_by_the_first_terminal_does_not_block_the_backup(self) -> None:
        exported: list[Path] = []

        def exporting(**kwargs) -> dict[str, object]:
            destination = Path(kwargs["destination"])
            if destination.exists():
                raise NativeHistoryReportError(
                    f"El destino del reporte MT5 ya existe: {destination.name}"
                )
            exported.append(destination)
            destination.write_text("informe de la cuenta buena", encoding="utf-8")
            return {"native_terminal_report": True}

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "real.html"
            destination.write_text("informe de la cuenta equivocada", encoding="utf-8")
            controller = self._controller(Path(folder))
            mt5 = FakeMt5()
            mt5.initialize = lambda **_kwargs: True
            with mock.patch(
                "mt5_manager.live_audit.live_audit_extraction.export_native_history_report",
                side_effect=exporting,
            ):
                metadata = controller._export_isolated_native_report(
                    mt5=mt5, request={"audit_key": "audit-1", "source_password": "clave"},
                    primary_path=Path(r"C:\IC\1\terminal64.exe"), login="111",
                    server="IC-Real", period_start=PERIOD_START, period_end=PERIOD_END,
                    destination=destination,
                    primary_error=NativeHistoryReportError(
                        "El reporte MT5 no corresponde a la cuenta auditada"
                    ),
                    profile_name="MT5 principal",
                )
            written = destination.read_text(encoding="utf-8")

        self.assertEqual(exported, [destination])
        self.assertTrue(metadata["isolated_capture_terminal"])
        self.assertEqual(metadata["capture_terminal_profile"], "MT5 respaldo")
        self.assertEqual(written, "informe de la cuenta buena")


class RelaunchedTerminalTests(unittest.TestCase):
    """Un uso de una sola terminal no tenía plan B si su terminal moría.

    Los tres reintentos de `export_native_history_report` se gastan esperando
    hasta `timeout` una ventana que no puede volver: el 2026-11-22 la auditoría
    del portafolio #2 de Vantage murió así, 62 s esperando el proceso que el
    agente de RoboForex le había cerrado. Relanzar la instalación arregla ese
    caso, y sólo ése: con el proceso vivo el tropiezo fue de la GUI, y por ahí
    ya se pasó tres veces.
    """

    PRIMARY = Path(r"C:\Vantage\terminal64.exe")

    @staticmethod
    def _controller(root: Path, *, alive: bool) -> LiveAuditController:
        controller = LiveAuditController(FakeOwner("idle"), root)
        controller.single_terminal_audit = True
        # Una sola instalación: sin el relanzamiento no hay ningún candidato.
        controller._native_report_profiles = lambda _primary: []
        controller._terminal_pids_for_path = lambda _path: {7} if alive else set()
        controller._close_terminal_pids = lambda _pids: None
        controller._remember_real_account_terminal = lambda *_args: None
        controller._synchronised_history = lambda *_args: ([], {})
        return controller

    def _export(self, controller: LiveAuditController, destination: Path, exporting) -> dict:
        mt5 = FakeMt5()
        mt5.initialize = lambda **_kwargs: True
        with mock.patch(
            "mt5_manager.live_audit.live_audit_extraction.export_native_history_report",
            side_effect=exporting,
        ):
            return controller._export_isolated_native_report(
                mt5=mt5, request={"audit_key": "audit-2", "source_password": "clave"},
                primary_path=self.PRIMARY, login="111", server="IC-Real",
                period_start=PERIOD_START, period_end=PERIOD_END, destination=destination,
                primary_error=NativeHistoryReportError(
                    "No se identificó de forma unívoca la ventana MT5 de 111 en "
                    "IC-Real: el terminal no tenía ninguna ventana abierta"
                ),
                profile_name="MT5 principal",
            )

    def test_a_terminal_that_died_is_relaunched_and_asked_again(self) -> None:
        asked: list[Path] = []

        def exporting(**kwargs) -> dict[str, object]:
            asked.append(Path(kwargs["terminal_path"]))
            return {"native_terminal_report": True}

        with tempfile.TemporaryDirectory() as folder:
            controller = self._controller(Path(folder), alive=False)
            metadata = self._export(controller, Path(folder) / "real.html", exporting)

        self.assertEqual(asked, [self.PRIMARY])
        self.assertTrue(metadata["relaunched_capture_terminal"])
        self.assertNotIn("isolated_capture_terminal", metadata)
        self.assertEqual(metadata["capture_terminal_profile"], "MT5 principal")

    def test_a_gui_stumble_with_the_process_alive_is_not_relaunched(self) -> None:
        asked: list[Path] = []

        with tempfile.TemporaryDirectory() as folder:
            controller = self._controller(Path(folder), alive=True)
            with self.assertRaises(NativeHistoryReportError) as raised:
                self._export(controller, Path(folder) / "real.html", asked.append)

        self.assertEqual(asked, [])
        self.assertIn("ninguna ventana abierta", str(raised.exception))


class NativeReportFallbackTests(LiveAuditEngineTestCase):
    def test_a_failed_remote_allocation_has_a_readable_error(self) -> None:
        exporter = _WindowsTerminalReportExporter.__new__(_WindowsTerminalReportExporter)
        exporter.kernel32 = SimpleNamespace(
            VirtualAllocEx=lambda *_args: None, GetLastError=lambda: 8,
        )

        with self.assertRaisesRegex(NativeHistoryReportError, "Win32 8"):
            exporter._remote_allocation(123, 16)

    def test_a_transient_remote_allocation_reopens_the_terminal_process(self) -> None:
        failed = mock.Mock()
        failed.export.side_effect = _RemoteAllocationError("Win32 487")
        recovered = mock.Mock()
        recovered.export.return_value = {"native_terminal_report": True}
        with mock.patch(
            "mt5_manager.live_audit.mt5_native_history_report._WindowsTerminalReportExporter",
            side_effect=(failed, recovered),
        ) as exporter, mock.patch("mt5_manager.live_audit.mt5_native_history_report.time.sleep"):
            result = export_native_history_report(
                terminal_path=Path("terminal64.exe"), login="1", server="Broker",
                period_start=PERIOD_START, period_end=PERIOD_END,
                destination=Path("report.html"),
            )
        self.assertTrue(result["native_terminal_report"])
        self.assertEqual(exporter.call_count, 2)

    def _export(self, destination: Path) -> dict:
        return export_native_history_report(
            terminal_path=Path("terminal64.exe"), login="1", server="Broker",
            period_start=PERIOD_START, period_end=PERIOD_END, destination=destination,
        )

    def test_a_gui_stumble_without_report_gets_another_pass(self) -> None:
        """El 2026-11-22 tres auditorías murieron por un tropiezo de la GUI.

        «MT5 no abrió el selector Custom period» y «no se identificó de forma
        unívoca la ventana» salían a la primera, y un uso de una sola terminal
        no tiene otra donde recuperarse: sólo se reintentaba la reserva de
        memoria remota.
        """
        failed, recovered = mock.Mock(), mock.Mock()
        failed.export.side_effect = NativeHistoryReportError("MT5 no abrió el selector Custom period")
        recovered.export.return_value = {"native_terminal_report": True}
        with tempfile.TemporaryDirectory() as folder, mock.patch(
            "mt5_manager.live_audit.mt5_native_history_report._WindowsTerminalReportExporter",
            side_effect=(failed, recovered),
        ) as exporter, mock.patch("mt5_manager.live_audit.mt5_native_history_report.time.sleep"):
            result = self._export(Path(folder) / "report.html")
        self.assertTrue(result["native_terminal_report"])
        self.assertEqual(exporter.call_count, 2)

    def test_a_failure_that_already_wrote_the_report_is_not_repeated(self) -> None:
        """Con fichero en el destino, repetir sólo cambiaría el error por «ya existe»."""
        failed = mock.Mock()
        failed.export.side_effect = NativeHistoryReportError(
            "El reporte MT5 no corresponde a la cuenta auditada"
        )
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "report.html"
            destination.write_text("informe a medias", encoding="utf-8")
            with mock.patch(
                "mt5_manager.live_audit.mt5_native_history_report._WindowsTerminalReportExporter",
                side_effect=(failed, failed, failed),
            ) as exporter, self.assertRaisesRegex(NativeHistoryReportError, "no corresponde"):
                self._export(destination)
        self.assertEqual(exporter.call_count, 1)

    @staticmethod
    def _window_exporter(timeout: float) -> _WindowsTerminalReportExporter:
        exporter = _WindowsTerminalReportExporter.__new__(_WindowsTerminalReportExporter)
        exporter.login, exporter.server, exporter.timeout = "1", "Broker", timeout
        return exporter

    def test_the_terminal_window_is_awaited_instead_of_photographed(self) -> None:
        """Era el único paso de la exportación que no esperaba a MT5."""
        exporter = self._window_exporter(1.0)
        rounds = [([], []), ([], []), ([(5, 7)], [])]
        exporter._terminal_candidates = lambda: rounds.pop(0)
        with mock.patch("mt5_manager.live_audit.mt5_native_history_report.time.sleep"):
            self.assertEqual(exporter._terminal_window(), (5, 7))
        self.assertEqual(rounds, [])

    def test_the_error_tells_no_window_from_two(self) -> None:
        """Un terminal cerrado y dos instancias no se arreglan igual."""
        exporter = self._window_exporter(0.0)
        exporter._terminal_candidates = lambda: ([], [])
        with self.assertRaisesRegex(NativeHistoryReportError, "ninguna ventana abierta"):
            exporter._terminal_window()
        exporter._terminal_candidates = lambda: ([(1, 10), (2, 20)], [])
        exporter._window_text = lambda hwnd: f"ventana {hwnd}"
        with self.assertRaisesRegex(NativeHistoryReportError, "2 ventanas del mismo terminal"):
            exporter._terminal_window()

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
