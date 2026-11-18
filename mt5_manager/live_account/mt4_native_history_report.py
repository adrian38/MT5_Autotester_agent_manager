"""Exportación nativa del Account History de MT4 mediante su interfaz Win32."""
from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import sys
import time
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from ..live_audit import mt5_native_history_report as mt5_report

MN_GETHMENU = 0x01E1
MF_BYPOSITION = 0x0400
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_RBUTTON = 0x0002
HISTORY_PAGE_ID = 33208


class NativeMt4HistoryReportError(RuntimeError):
    pass


def validate_native_mt4_history_report(path: Path, login: str) -> dict[str, object]:
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 512:
        raise NativeMt4HistoryReportError("MT4 no creó un reporte HTML de cuenta válido")
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = payload.decode("utf-16", errors="ignore")
    else:
        text = payload.decode("utf-8", errors="ignore")
        if "Closed Transactions" not in text:
            text = payload.decode("cp1252", errors="ignore")
    folded = text.casefold()
    if "closed transactions" not in folded or "summary" not in folded:
        raise NativeMt4HistoryReportError(
            "El HTML guardado no tiene la estructura del reporte de historial MT4"
        )
    if str(login) not in text:
        raise NativeMt4HistoryReportError("El reporte MT4 no corresponde a la cuenta sondeada")
    return {
        "filename": path.name, "native_terminal_report": True,
        "source": "mt4_terminal_history_report", "terminal": "mt4",
        "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _label(value: str) -> str:
    return " ".join(
        value.casefold().replace("&", "").replace("…", "").replace("...", "").split()
    )


class _WindowsMt4ReportExporter(mt5_report._WindowsTerminalReportExporter):
    def _terminal_window(self) -> tuple[int, int]:
        candidates: list[tuple[int, int]] = []
        titled: list[tuple[int, int]] = []
        for hwnd in self._top_windows():
            if self._class_name(hwnd) != "MetaQuotes::MetaTrader::4.00":
                continue
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self._process_path(pid.value) != self.terminal_path:
                continue
            candidates.append((hwnd, pid.value))
            title = self._window_text(hwnd).casefold()
            if self.login in title:
                titled.append((hwnd, pid.value))
        if len(titled) == 1:
            return titled[0]
        raise NativeMt4HistoryReportError(
            f"MT4 aún no confirmó la cuenta {self.login} en {self.server}"
        )

    def _ready_terminal_window(self) -> tuple[int, int] | None:
        try:
            return self._terminal_window()
        except NativeMt4HistoryReportError:
            return None

    def _window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def _terminal_workspace(self, main: int) -> int:
        """El contenedor del panel Terminal, donde conviven la franja y las páginas."""
        docks = [
            child for child in self._children(main)
            if self._class_name(child).startswith("AfxControlBar")
            and _label(self._window_text(child)) == "terminal"
        ]
        for dock in docks:
            for child in self._children(dock):
                if _label(self._window_text(child)) == "terminal":
                    return child
        return 0

    def _tab_strip(self, workspace: int) -> int:
        """La franja de pestañas: la única banda visible y ancha del contenedor."""
        for child in self._children(workspace):
            if self.user32.GetParent(child) != workspace:
                continue
            left, top, right, bottom = self._window_rect(child)
            if (
                self._class_name(child).startswith("AfxWnd")
                and self.user32.IsWindowVisible(child)
                and right - left > 300 and 0 < bottom - top <= 40
            ):
                return child
        return 0

    def _select_page(self, strip: int, page: int) -> None:
        """Pulsa la franja de izquierda a derecha hasta destapar esa página."""
        left, top, right, bottom = self._window_rect(strip)
        middle, step = (bottom - top) // 2, 20
        for x in range(step // 2, right - left, step):
            if self.user32.IsWindowVisible(page):
                return
            position = (middle << 16) | x
            self.user32.SendMessageW(strip, WM_LBUTTONDOWN, 1, position)
            self.user32.SendMessageW(strip, WM_LBUTTONUP, 0, position)
            time.sleep(0.1)
        if not self.user32.IsWindowVisible(page):
            raise NativeMt4HistoryReportError(
                "MT4 no activó la pestaña Historial de cuentas del Terminal"
            )

    def _custom_history_tab(self, main: int) -> tuple[int, int, int] | None:
        """Localiza el Historial cuando MT4 dibuja sus pestañas en un control MFC.

        El MT4 de Axi no monta un SysTabControl32 en el panel Terminal: dibuja
        «Operaciones / Exposición / Historial de cuentas / …» en una franja
        propia sin texto que preguntar. Lo que sí es estable es el id del panel
        que cada pestaña destapa, así que se busca el del historial y se pulsa
        la franja hasta que ese panel queda visible. La pestaña previa no se
        restaura: no hay índice que devolverle a `_click_tab`.
        """
        workspace = self._terminal_workspace(main)
        strip = self._tab_strip(workspace) if workspace else 0
        history = next((
            child for child in self._children(workspace)
            if self.user32.GetParent(child) == workspace
            and self.user32.GetDlgCtrlID(child) == HISTORY_PAGE_ID
        ), 0) if workspace else 0
        if not strip or not history:
            return None
        self._select_page(strip, history)
        return strip, -1, -1

    def _ready_custom_history_tab(self, main: int) -> tuple[int, int, int] | None:
        """Espera a que el panel MFC termine de construirse tras conectar."""
        try:
            return self._custom_history_tab(main)
        except NativeMt4HistoryReportError:
            return None

    def _history_tab(self, pid: int, main: int) -> tuple[int, int, int]:
        process = self._process(pid)
        try:
            for tab in (
                child for child in self._children(main)
                if self._class_name(child) == "SysTabControl32"
            ):
                count = int(self.user32.SendMessageW(tab, mt5_report.TCM_GETITEMCOUNT, 0, 0))
                labels = [_label(self._tab_text(process, tab, index)) for index in range(count)]
                index = next((
                    item for item, label in enumerate(labels)
                    if label in {"account history", "historial de cuentas", "historial"}
                ), -1)
                if index >= 0:
                    previous = int(self.user32.SendMessageW(
                        tab, mt5_report.TCM_GETCURSEL, 0, 0
                    ))
                    self._click_tab(pid, tab, index)
                    self._wait(
                        lambda: self.user32.SendMessageW(
                            tab, mt5_report.TCM_GETCURSEL, 0, 0
                        ) == index,
                        "MT4 no activó la pestaña Account History",
                    )
                    return tab, previous, index
        finally:
            self.kernel32.CloseHandle(process)
        return self._wait(
            lambda: self._ready_custom_history_tab(main),
            "No se encontró Account History en la terminal MT4",
        )

    def _history_control(self, main: int, tab: int) -> int:
        parent = self.user32.GetParent(tab)
        visible = [
            child for child in self._children(parent)
            if child != tab and self.user32.IsWindowVisible(child)
            and self._class_name(child) in {"SysListView32", "SysListBox32"}
        ]
        if visible:
            return visible[-1]
        candidates = [
            child for child in self._children(parent)
            if child != tab and self.user32.IsWindowVisible(child)
        ]
        if not candidates:
            raise NativeMt4HistoryReportError("No se encontró la lista de Account History de MT4")
        return candidates[-1]

    def _popup(self, pid: int) -> int:
        return next((
            hwnd for hwnd in self._top_windows(pid)
            if self._class_name(hwnd) == "#32768" and self.user32.IsWindowVisible(hwnd)
        ), 0)

    def _menu_items(self, popup: int) -> list[tuple[str, int]]:
        menu = int(self.user32.SendMessageW(popup, MN_GETHMENU, 0, 0))
        if not menu:
            return []
        result: list[tuple[str, int]] = []
        for index in range(int(self.user32.GetMenuItemCount(menu))):
            buffer = ctypes.create_unicode_buffer(512)
            self.user32.GetMenuStringW(menu, index, buffer, len(buffer), MF_BYPOSITION)
            command = int(self.user32.GetMenuItemID(menu, index))
            if command != 0xFFFFFFFF:
                result.append((_label(buffer.value), command))
        return result

    def _context_command(
        self, main: int, pid: int, control: int, labels: Iterable[str],
    ) -> None:
        # MT4 sólo despliega el menú si el WM_RBUTTONDOWN declara el botón
        # pulsado en su wparam: con 0 ignora el clic y la exportación moría en
        # «no abrió el menú» con la pestaña correcta ya seleccionada.
        self.user32.PostMessageW(control, WM_RBUTTONDOWN, MK_RBUTTON, (12 << 16) | 12)
        self.user32.PostMessageW(control, WM_RBUTTONUP, 0, (12 << 16) | 12)
        popup = int(self._wait(lambda: self._popup(pid), "MT4 no abrió el menú de Account History"))
        wanted = tuple(_label(label) for label in labels)
        command = next((
            item_id for text, item_id in self._menu_items(popup)
            if any(label in text for label in wanted)
        ), 0)
        self.user32.PostMessageW(popup, 0x0010, 0, 0)
        if not command:
            raise NativeMt4HistoryReportError(
                "El menú MT4 no contiene la opción " + "/".join(labels)
            )
        self.user32.PostMessageW(main, mt5_report.WM_COMMAND, command, 0)

    def _period_dialog(self, pid: int) -> tuple[int, list[int]] | None:
        for dialog in self._top_windows(pid):
            if self._class_name(dialog) != "#32770" or not self.user32.IsWindowVisible(dialog):
                continue
            dates = [
                child for child in self._children(dialog)
                if self._class_name(child) == "SysDateTimePick32"
            ]
            if len(dates) >= 2 and self._descendant(dialog, 1, "Button"):
                return dialog, dates[:2]
        return None

    def _set_custom_period(
        self, main: int, pid: int, control: int, start: date, end: date,
    ) -> None:
        self._context_command(
            main, pid, control,
            ("custom period", "periodo personalizado", "período personalizado"),
        )
        dialog, dates = self._wait(
            lambda: self._period_dialog(pid), "MT4 no abrió Custom Period"
        )
        ordered = sorted(dates, key=lambda item: self._screen_left(item))
        process = self._process(pid)
        try:
            self._set_date(process, ordered[0], start)
            self._set_date(process, ordered[1], end)
        finally:
            self.kernel32.CloseHandle(process)
        self.user32.SendMessageW(self._descendant(dialog, 1, "Button"), mt5_report.BM_CLICK, 0, 0)
        self._wait(lambda: not self.user32.IsWindow(dialog), "MT4 no cerró Custom Period")

    def _screen_left(self, hwnd: int) -> int:
        rect = wintypes.RECT()
        self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return int(rect.left)

    def _save_report(
        self, main: int, pid: int, control: int, destination: Path,
    ) -> None:
        if destination.exists():
            raise NativeMt4HistoryReportError(
                f"El destino del reporte MT4 ya existe: {destination.name}"
            )
        self._context_command(
            main, pid, control,
            ("save as report", "guardar como informe", "guardar como reporte"),
        )
        dialog = int(self._wait(
            lambda: self._dialog(pid, (1, 2, 1001), {1: "Button", 2: "Button", 1001: "Edit"}),
            "MT4 no abrió Guardar como para el reporte HTML",
        ))
        edit = self._descendant(dialog, 1001, "Edit")
        save = self._descendant(dialog, 1, "Button")
        self._type_filename(dialog, edit, save, str(destination))
        self._wait(lambda: not self.user32.IsWindow(dialog), "MT4 no cerró Guardar como")
        self._wait(
            lambda: destination.is_file() and destination.stat().st_size >= 512,
            "MT4 cerró Guardar como, pero no creó el HTML nativo",
        )

    def export(
        self, period_start: datetime, period_end: datetime, destination: Path,
    ) -> dict[str, object]:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        start, end = period_start.astimezone().date(), period_end.astimezone().date()
        if start > end:
            raise NativeMt4HistoryReportError("El periodo nativo MT4 tiene las fechas invertidas")
        main, pid = self._wait(
            self._ready_terminal_window,
            f"MT4 no conectó la cuenta {self.login} en {self.server}",
        )
        minimized = bool(self.user32.IsIconic(main))
        foreground = self.user32.GetForegroundWindow()
        tab = previous = 0
        try:
            if minimized:
                self.user32.ShowWindow(main, mt5_report.SW_RESTORE)
            tab, previous, _index = self._history_tab(pid, main)
            control = self._history_control(main, tab)
            self._set_custom_period(main, pid, control, start, end)
            time.sleep(1.0)
            self._save_report(main, pid, control, destination)
            metadata = validate_native_mt4_history_report(destination, self.login)
            metadata.update({
                "period_mode": "custom", "period_start_date": start.isoformat(),
                "period_end_date": end.isoformat(), "companion_images": [],
            })
            return metadata
        finally:
            if tab:
                self._click_tab(pid, tab, previous)
            if minimized and self.user32.IsWindow(main):
                self.user32.ShowWindow(main, mt5_report.SW_MINIMIZE)
            if foreground and self.user32.IsWindow(foreground):
                self.user32.SetForegroundWindow(foreground)


def export_native_mt4_history_report(
    *, terminal_path: Path, login: str, server: str,
    period_start: datetime, period_end: datetime, destination: Path,
    timeout: float = 60.0,
) -> dict[str, object]:
    if sys.platform != "win32":
        raise NativeMt4HistoryReportError("La exportación del historial MT4 requiere Windows")
    exporter = _WindowsMt4ReportExporter(terminal_path, login, server, timeout)
    return exporter.export(period_start, period_end, destination)
