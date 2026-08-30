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
from typing import Callable


WM_COMMAND = 0x0111
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
BM_CLICK = 0x00F5
CB_GETCOUNT = 0x0146
CB_GETCURSEL = 0x0147
CB_SETCURSEL = 0x014E
TCM_GETITEMCOUNT = 0x1304
TCM_GETCURSEL = 0x130B
TCM_GETITEMRECT = 0x130A
TCM_GETITEMW = 0x133C
DTM_GETSYSTEMTIME = 0x1001
DTM_SETSYSTEMTIME = 0x1002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM = 0x0038
MEM_COMMIT_RESERVE = 0x3000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
SW_RESTORE = 9
SW_MINIMIZE = 6

TOOLBOX_ID = 32841
CUSTOM_PERIOD_COMMAND = 33046
HTML_REPORT_COMMAND = 33116
PERIOD_COMBO_ID = 10212
FROM_DATE_ID = 10119
TO_DATE_ID = 10110


class NativeHistoryReportError(RuntimeError):
    pass


class _TCItemW(ctypes.Structure):
    _fields_ = [
        ("mask", wintypes.UINT),
        ("state", wintypes.DWORD),
        ("state_mask", wintypes.DWORD),
        ("text", ctypes.c_void_p),
        ("text_max", ctypes.c_int),
        ("image", ctypes.c_int),
        ("param", ctypes.c_ssize_t),
    ]


def validate_native_history_report(path: Path, login: str) -> dict[str, object]:
    """Rechaza cualquier HTML que no sea el informe guardado por el terminal MT5."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 512:
        raise NativeHistoryReportError("MT5 no creó un reporte HTML de cuenta válido")
    payload = path.read_bytes()
    encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    text = payload.decode(encoding, errors="ignore")
    folded = text.casefold()
    native_title = any(
        title in folded
        for title in ("trade history report", "informe del historial de trading")
    )
    if '<meta name="generator" content="client terminal">' not in folded or not native_title:
        raise NativeHistoryReportError(
            "El HTML guardado no tiene la firma del reporte de historial del terminal MT5"
        )
    if str(login) not in text:
        raise NativeHistoryReportError("El reporte MT5 no corresponde a la cuenta auditada")
    return {
        "filename": path.name,
        "native_terminal_report": True,
        "source": "mt5_terminal_history_report",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


class _WindowsTerminalReportExporter:
    def __init__(self, terminal_path: Path, login: str, server: str, timeout: float) -> None:
        if sys.platform != "win32":
            raise NativeHistoryReportError("La exportación nativa del historial MT5 requiere Windows")
        self.terminal_path = os.path.normcase(os.path.abspath(str(terminal_path)))
        self.login = str(login)
        self.server = str(server)
        self.timeout = timeout
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self._configure_types()

    def _configure_types(self) -> None:
        self.user32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.VirtualAllocEx.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD,
        ]
        self.kernel32.VirtualAllocEx.restype = ctypes.c_void_p
        self.kernel32.VirtualFreeEx.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
        ]
        self.kernel32.WriteProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.kernel32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]

    def _wait(self, predicate: Callable[[], object], message: str) -> object:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        raise NativeHistoryReportError(message)

    def _window_text(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(1024)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _control_text(self, hwnd: int) -> str:
        # GetWindowText no recupera controles pertenecientes a otro proceso.
        # WM_GETTEXT sí está serializado por Windows para mensajes del sistema.
        length = int(self.user32.SendMessageW(hwnd, 0x000E, 0, 0))  # WM_GETTEXTLENGTH
        buffer = ctypes.create_unicode_buffer(max(length + 1, 2))
        self.user32.SendMessageW(hwnd, 0x000D, len(buffer), ctypes.addressof(buffer))  # WM_GETTEXT
        return buffer.value

    def _class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def _process_path(self, pid: int) -> str:
        process = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return ""
            return os.path.normcase(os.path.abspath(buffer.value))
        finally:
            self.kernel32.CloseHandle(process)

    def _top_windows(self, pid: int | None = None) -> list[int]:
        result: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect(hwnd: int, _lparam: int) -> bool:
            window_pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if pid is None or window_pid.value == pid:
                result.append(hwnd)
            return True

        callback = callback_type(collect)
        self.user32.EnumWindows(callback, 0)
        return result

    def _children(self, hwnd: int) -> list[int]:
        result: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect(child: int, _lparam: int) -> bool:
            result.append(child)
            return True

        callback = callback_type(collect)
        self.user32.EnumChildWindows(hwnd, callback, 0)
        return result

    def _terminal_window(self) -> tuple[int, int]:
        path_candidates: list[tuple[int, int]] = []
        titled_candidates: list[tuple[int, int]] = []
        for hwnd in self._top_windows():
            if self._class_name(hwnd) != "MetaQuotes::MetaTrader::5.00":
                continue
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self._process_path(pid.value) != self.terminal_path:
                continue
            path_candidates.append((hwnd, pid.value))
            title = self._window_text(hwnd).casefold()
            if self.login in title and (not self.server or self.server.casefold() in title):
                titled_candidates.append((hwnd, pid.value))
        if len(titled_candidates) == 1:
            return titled_candidates[0]
        if len(path_candidates) == 1:
            return path_candidates[0]
        if len(path_candidates) != 1:
            raise NativeHistoryReportError(
                f"No se identificó de forma unívoca la ventana MT5 de {self.login} en {self.server}"
            )
        return path_candidates[0]

    def _process(self, pid: int) -> int:
        process = self.kernel32.OpenProcess(PROCESS_VM, False, pid)
        if not process:
            raise NativeHistoryReportError("No se pudo acceder al proceso MT5 para fijar el periodo")
        return process

    def _remote_buffer(self, process: int, payload: bytes) -> int:
        pointer = self.kernel32.VirtualAllocEx(
            process, None, len(payload), MEM_COMMIT_RESERVE, PAGE_READWRITE,
        )
        if not pointer:
            raise NativeHistoryReportError("MT5 no reservó memoria para configurar el periodo")
        local = ctypes.create_string_buffer(payload)
        written = ctypes.c_size_t()
        if not self.kernel32.WriteProcessMemory(
            process, pointer, local, len(payload), ctypes.byref(written),
        ) or written.value != len(payload):
            self.kernel32.VirtualFreeEx(process, pointer, 0, MEM_RELEASE)
            raise NativeHistoryReportError("No se pudo escribir el periodo en el selector de MT5")
        return pointer

    def _remote_read(self, process: int, pointer: int, size: int) -> bytes:
        local = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        if not self.kernel32.ReadProcessMemory(
            process, pointer, local, size, ctypes.byref(read),
        ) or read.value != size:
            raise NativeHistoryReportError("No se pudo verificar el periodo seleccionado en MT5")
        return local.raw

    def _tab_text(self, process: int, tab: int, index: int) -> str:
        text_size = 256 * ctypes.sizeof(ctypes.c_wchar)
        text_pointer = self.kernel32.VirtualAllocEx(
            process, None, text_size, MEM_COMMIT_RESERVE, PAGE_READWRITE,
        )
        if not text_pointer:
            return ""
        item_pointer = 0
        try:
            item = _TCItemW(mask=1, text=text_pointer, text_max=256)
            payload = ctypes.string_at(ctypes.byref(item), ctypes.sizeof(item))
            item_pointer = self._remote_buffer(process, payload)
            if not self.user32.SendMessageW(tab, TCM_GETITEMW, index, item_pointer):
                return ""
            raw = self._remote_read(process, text_pointer, text_size)
            return raw.decode("utf-16-le", errors="ignore").split("\0", 1)[0]
        finally:
            if item_pointer:
                self.kernel32.VirtualFreeEx(process, item_pointer, 0, MEM_RELEASE)
            self.kernel32.VirtualFreeEx(process, text_pointer, 0, MEM_RELEASE)

    def _select_history_tab(self, pid: int, main: int) -> tuple[int, int]:
        toolbox = next(
            (child for child in self._children(main) if self.user32.GetDlgCtrlID(child) == TOOLBOX_ID),
            0,
        )
        if not toolbox:
            raise NativeHistoryReportError("No se encontró el Toolbox del terminal MT5")
        tab = next(
            (child for child in self._children(toolbox) if self._class_name(child) == "SysTabControl32"),
            0,
        )
        if not tab:
            raise NativeHistoryReportError("No se encontró el selector de pestañas del Toolbox MT5")
        process = self._process(pid)
        try:
            count = self.user32.SendMessageW(tab, TCM_GETITEMCOUNT, 0, 0)
            labels = [self._tab_text(process, tab, index).casefold().replace("&", "") for index in range(count)]
            history_index = next(
                (index for index, label in enumerate(labels) if label in {"history", "historial"}),
                2 if count > 2 else -1,
            )
            if history_index < 0:
                raise NativeHistoryReportError("El Toolbox MT5 no contiene la pestaña History")
            previous = self.user32.SendMessageW(tab, TCM_GETCURSEL, 0, 0)
            if previous != history_index:
                pointer = self.kernel32.VirtualAllocEx(
                    process, None, 16, MEM_COMMIT_RESERVE, PAGE_READWRITE,
                )
                try:
                    if not self.user32.SendMessageW(tab, TCM_GETITEMRECT, history_index, pointer):
                        raise NativeHistoryReportError("MT5 no devolvió la posición de la pestaña History")
                    left, top, right, bottom = struct.unpack("<4i", self._remote_read(process, pointer, 16))
                    x, y = (left + right) // 2, (top + bottom) // 2
                    position = (y << 16) | (x & 0xFFFF)
                    self.user32.SendMessageW(tab, WM_LBUTTONDOWN, 1, position)
                    self.user32.SendMessageW(tab, WM_LBUTTONUP, 0, position)
                finally:
                    self.kernel32.VirtualFreeEx(process, pointer, 0, MEM_RELEASE)
                self._wait(
                    lambda: self.user32.SendMessageW(tab, TCM_GETCURSEL, 0, 0) == history_index,
                    "MT5 no activó la pestaña History",
                )
            return tab, int(previous)
        finally:
            self.kernel32.CloseHandle(process)

    def _click_tab(self, pid: int, tab: int, index: int) -> None:
        if index < 0 or self.user32.SendMessageW(tab, TCM_GETCURSEL, 0, 0) == index:
            return
        process = self._process(pid)
        pointer = self.kernel32.VirtualAllocEx(process, None, 16, MEM_COMMIT_RESERVE, PAGE_READWRITE)
        try:
            if self.user32.SendMessageW(tab, TCM_GETITEMRECT, index, pointer):
                left, top, right, bottom = struct.unpack("<4i", self._remote_read(process, pointer, 16))
                x, y = (left + right) // 2, (top + bottom) // 2
                position = (y << 16) | (x & 0xFFFF)
                self.user32.SendMessageW(tab, WM_LBUTTONDOWN, 1, position)
                self.user32.SendMessageW(tab, WM_LBUTTONUP, 0, position)
        finally:
            self.kernel32.VirtualFreeEx(process, pointer, 0, MEM_RELEASE)
            self.kernel32.CloseHandle(process)

    def _dialog(self, pid: int, required_ids: tuple[int, ...]) -> int:
        for hwnd in self._top_windows(pid):
            if self._class_name(hwnd) != "#32770" or not self.user32.IsWindowVisible(hwnd):
                continue
            descendants = self._children(hwnd)
            ids = {self.user32.GetDlgCtrlID(child) for child in descendants}
            ids.add(self.user32.GetDlgCtrlID(hwnd))
            if all(control_id in ids for control_id in required_ids):
                return hwnd
        return 0

    def _descendant(self, hwnd: int, control_id: int, class_name: str | None = None) -> int:
        for child in self._children(hwnd):
            if self.user32.GetDlgCtrlID(child) != control_id:
                continue
            if class_name is None or self._class_name(child) == class_name:
                return child
        return 0

    def _set_date(self, process: int, control: int, value: date) -> None:
        day_of_week = (value.weekday() + 1) % 7
        payload = struct.pack("<8H", value.year, value.month, day_of_week, value.day, 0, 0, 0, 0)
        pointer = self._remote_buffer(process, payload)
        try:
            if not self.user32.SendMessageW(control, DTM_SETSYSTEMTIME, 0, pointer):
                raise NativeHistoryReportError("El selector de fecha MT5 rechazó el periodo")
            self.user32.SendMessageW(control, DTM_GETSYSTEMTIME, 0, pointer)
            selected = struct.unpack("<8H", self._remote_read(process, pointer, len(payload)))
            if (selected[0], selected[1], selected[3]) != (value.year, value.month, value.day):
                raise NativeHistoryReportError("MT5 no conservó la fecha solicitada")
        finally:
            self.kernel32.VirtualFreeEx(process, pointer, 0, MEM_RELEASE)

    def _set_custom_period(self, pid: int, main: int, start: date, end: date) -> None:
        self.user32.PostMessageW(main, WM_COMMAND, CUSTOM_PERIOD_COMMAND, 0)
        dialog = int(self._wait(
            lambda: self._dialog(pid, (PERIOD_COMBO_ID, FROM_DATE_ID, TO_DATE_ID)),
            "MT5 no abrió el selector Custom period",
        ))
        combo = self._descendant(dialog, PERIOD_COMBO_ID, "ComboBox")
        if not combo or self.user32.SendMessageW(combo, CB_GETCOUNT, 0, 0) < 2:
            raise NativeHistoryReportError("MT5 no mostró el selector Period")
        self.user32.SendMessageW(combo, CB_SETCURSEL, 0, 0)
        self.user32.SendMessageW(dialog, WM_COMMAND, PERIOD_COMBO_ID | (1 << 16), combo)
        if self.user32.SendMessageW(combo, CB_GETCURSEL, 0, 0) != 0:
            raise NativeHistoryReportError("MT5 no seleccionó Custom period")
        process = self._process(pid)
        try:
            self._set_date(process, self._descendant(dialog, FROM_DATE_ID), start)
            self._set_date(process, self._descendant(dialog, TO_DATE_ID), end)
        finally:
            self.kernel32.CloseHandle(process)
        ok = self._descendant(dialog, 1, "Button")
        if not ok:
            raise NativeHistoryReportError("No se encontró el botón OK de Custom period")
        self.user32.SendMessageW(ok, BM_CLICK, 0, 0)
        self._wait(lambda: not self.user32.IsWindow(dialog), "MT5 no cerró el selector Custom period")

    def _save_report(self, pid: int, main: int, destination: Path) -> None:
        if destination.exists():
            raise NativeHistoryReportError(f"El destino del reporte MT5 ya existe: {destination.name}")
        self.user32.PostMessageW(main, WM_COMMAND, HTML_REPORT_COMMAND, 0)
        dialog = int(self._wait(
            lambda: self._dialog(pid, (1, 2, 1001)),
            "MT5 no abrió Guardar como para el reporte HTML",
        ))
        edit = self._descendant(dialog, 1001, "Edit")
        save = self._descendant(dialog, 1, "Button")
        if not edit or not save:
            raise NativeHistoryReportError("No se pudo indicar el destino del reporte HTML de MT5")
        self._type_filename(dialog, edit, save, str(destination))
        confirmation = self._wait(
            lambda: ("closed", 0) if not self.user32.IsWindow(dialog) else
            (("confirm", candidate) if (candidate := self._overwrite_confirmation(pid, dialog)) else None),
            "MT5 no respondió al guardar el reporte HTML",
        )
        if confirmation[0] == "confirm":
            confirm_dialog = int(confirmation[1])
            no = next(
                (
                    child for child in self._children(confirm_dialog)
                    if self._class_name(child) == "Button"
                    and self._window_text(child).casefold().replace("&", "") == "no"
                ),
                0,
            )
            if no:
                self.user32.PostMessageW(no, BM_CLICK, 0, 0)
            raise NativeHistoryReportError(
                "MT5 intentó sobrescribir otro reporte; no aceptó el destino único solicitado"
            )
        self._wait(lambda: not self.user32.IsWindow(dialog), "MT5 no cerró Guardar como")
        self._wait(
            lambda: destination.is_file() and destination.stat().st_size >= 512,
            "MT5 cerró Guardar como, pero no creó el HTML nativo",
        )

    def _type_filename(self, dialog: int, edit: int, save: int, value: str) -> None:
        current_thread = self.kernel32.GetCurrentThreadId()
        foreground = self.user32.GetForegroundWindow()
        foreground_thread = self.user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        target_thread = self.user32.GetWindowThreadProcessId(dialog, None)
        attached: list[int] = []
        try:
            for thread in {foreground_thread, target_thread} - {0, current_thread}:
                if self.user32.AttachThreadInput(current_thread, thread, True):
                    attached.append(thread)
            self.user32.ShowWindow(dialog, SW_RESTORE)
            self.user32.keybd_event(0x12, 0, 0, 0)
            self.user32.keybd_event(0x12, 0, 2, 0)
            self.user32.BringWindowToTop(dialog)
            activated = bool(self.user32.SetForegroundWindow(dialog))
            if not activated:
                self.user32.SwitchToThisWindow(dialog, True)
                activated = self.user32.GetForegroundWindow() == dialog
            if not activated:
                # En una sesión RDP sin escritorio interactivo Windows puede
                # rechazar SetForegroundWindow aunque el hilo del diálogo ya
                # esté unido a nuestra cola de entrada. SetActiveWindow y
                # mensajes directos al Edit siguen siendo válidos dentro de
                # esa cola. WM_CHAR genera las notificaciones de cambio que el
                # diálogo común no genera con un simple SetWindowText.
                self.user32.SetActiveWindow(dialog)
                self.user32.SendMessageW(edit, 0x0007, 0, 0)  # WM_SETFOCUS
                self.user32.SendMessageW(edit, 0x00B1, 0, -1)  # EM_SETSEL
                self.user32.SendMessageW(edit, 0x0303, 0, 0)  # WM_CLEAR
                for character in value:
                    self.user32.SendMessageW(edit, 0x0102, ord(character), 1)  # WM_CHAR
                if self._control_text(edit) != value:
                    raise NativeHistoryReportError(
                        "Guardar como de MT5 no conservó el destino solicitado"
                    )
                self.user32.SendMessageW(save, BM_CLICK, 0, 0)
                time.sleep(0.1)
                return
            self.user32.SetFocus(edit)
            self.user32.keybd_event(0x11, 0, 0, 0)
            self.user32.keybd_event(0x41, 0, 0, 0)
            self.user32.keybd_event(0x41, 0, 2, 0)
            self.user32.keybd_event(0x11, 0, 2, 0)
            for character in value:
                key = self.user32.VkKeyScanW(ord(character))
                if key == -1:
                    raise NativeHistoryReportError(
                        f"Guardar como no admite el carácter {character!r} del destino"
                    )
                virtual_key = key & 0xFF
                modifiers = (key >> 8) & 0xFF
                if modifiers & 1:
                    self.user32.keybd_event(0x10, 0, 0, 0)
                if modifiers & 2:
                    self.user32.keybd_event(0x11, 0, 0, 0)
                if modifiers & 4:
                    self.user32.keybd_event(0x12, 0, 0, 0)
                self.user32.keybd_event(virtual_key, 0, 0, 0)
                self.user32.keybd_event(virtual_key, 0, 2, 0)
                if modifiers & 4:
                    self.user32.keybd_event(0x12, 0, 2, 0)
                if modifiers & 2:
                    self.user32.keybd_event(0x11, 0, 2, 0)
                if modifiers & 1:
                    self.user32.keybd_event(0x10, 0, 2, 0)
            self.user32.keybd_event(0x0D, 0, 0, 0)
            self.user32.keybd_event(0x0D, 0, 2, 0)
            time.sleep(0.1)
        finally:
            for thread in reversed(attached):
                self.user32.AttachThreadInput(current_thread, thread, False)

    def _overwrite_confirmation(self, pid: int, save_dialog: int) -> int:
        for hwnd in self._top_windows(pid):
            if hwnd == save_dialog or self._class_name(hwnd) != "#32770":
                continue
            buttons = [child for child in self._children(hwnd) if self._class_name(child) == "Button"]
            labels = {self._window_text(button).casefold().replace("&", "") for button in buttons}
            title = self._window_text(hwnd).casefold()
            if len(buttons) == 2 and "no" in labels and ("confirm" in title or "guardar" in title):
                return hwnd
        return 0

    def export(self, period_start: datetime, period_end: datetime, destination: Path) -> dict[str, object]:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        start_date = period_start.astimezone().date()
        end_date = period_end.astimezone().date()
        if start_date > end_date:
            raise NativeHistoryReportError("El periodo nativo MT5 tiene las fechas invertidas")
        main, pid = self._terminal_window()
        was_minimized = bool(self.user32.IsIconic(main))
        foreground = self.user32.GetForegroundWindow()
        tab = 0
        previous_tab = -1
        try:
            if was_minimized:
                self.user32.ShowWindow(main, SW_RESTORE)
                time.sleep(0.2)
            tab, previous_tab = self._select_history_tab(pid, main)
            self._set_custom_period(pid, main, start_date, end_date)
            time.sleep(0.8)
            self._save_report(pid, main, destination)
            metadata = validate_native_history_report(destination, self.login)
            image = destination.with_suffix(".png")
            metadata.update({
                "period_mode": "custom",
                "period_start_date": start_date.isoformat(),
                "period_end_date": end_date.isoformat(),
                "companion_images": [image.name] if image.is_file() else [],
            })
            return metadata
        finally:
            for dialog in self._top_windows(pid):
                if self._class_name(dialog) == "#32770" and self.user32.IsWindowVisible(dialog):
                    cancel = self._descendant(dialog, 2, "Button")
                    if cancel:
                        self.user32.PostMessageW(cancel, BM_CLICK, 0, 0)
            if tab:
                self._click_tab(pid, tab, previous_tab)
            if was_minimized and self.user32.IsWindow(main):
                self.user32.ShowWindow(main, SW_MINIMIZE)
            if foreground and self.user32.IsWindow(foreground):
                self.user32.SetForegroundWindow(foreground)


def export_native_history_report(
    *, terminal_path: Path, login: str, server: str,
    period_start: datetime, period_end: datetime, destination: Path,
    timeout: float = 20.0,
) -> dict[str, object]:
    """Guarda el Report/HTML original del History de MT5 en Custom period."""
    exporter = _WindowsTerminalReportExporter(terminal_path, login, server, timeout)
    return exporter.export(period_start, period_end, destination)
