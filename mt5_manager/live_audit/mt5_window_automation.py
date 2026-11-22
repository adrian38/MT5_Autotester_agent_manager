"""Las primitivas Win32 bajo las lecturas nativas del historial.

Ventanas, controles, diálogos y memoria remota del proceso MT5: lo que no sabe
nada del informe que se está pidiendo y comparten los dos exportadores, el de
MT5 (`mt5_native_history_report`) y el de MT4
(`..live_account.mt4_native_history_report`), que hereda del primero.

Salió de `mt5_native_history_report.py`, que estaba clavado en el techo de 600
líneas y no admitía una línea más.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
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


class NativeHistoryReportError(RuntimeError):
    pass


class _RemoteAllocationError(NativeHistoryReportError):
    pass


class _WindowsAutomation:
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
        self.kernel32.GetLastError.argtypes = []
        self.kernel32.GetLastError.restype = wintypes.DWORD
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

    def _process(self, pid: int) -> int:
        process = self.kernel32.OpenProcess(PROCESS_VM, False, pid)
        if not process:
            raise NativeHistoryReportError("No se pudo acceder al proceso MT5 para fijar el periodo")
        return process

    def _remote_allocation(self, process: int, size: int) -> int:
        pointer = self.kernel32.VirtualAllocEx(
            process, None, size, MEM_COMMIT_RESERVE, PAGE_READWRITE,
        )
        if not pointer:
            code = int(self.kernel32.GetLastError())
            raise _RemoteAllocationError(
                f"La automatización no reservó memoria remota en MT5 (Win32 {code})"
            )
        return pointer

    def _remote_buffer(self, process: int, payload: bytes) -> int:
        pointer = self._remote_allocation(process, len(payload))
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

    def _dialog(
        self, pid: int, required_ids: tuple[int, ...],
        required_classes: dict[int, str] | None = None,
    ) -> int:
        """Encuentra el diálogo #32770 con los IDs pedidos.

        `required_classes` exige además la clase exacta de ese ID: sin ella,
        dos IDs tan convencionales como IDOK=1 e IDCANCEL=2 hacen que
        cualquier diálogo ajeno (una alerta de MT5, por ejemplo) se confunda
        con el que se busca, y el fallo sólo se descubre después, al pedir el
        control por clase con `_descendant`.
        """
        required_classes = required_classes or {}
        for hwnd in self._top_windows(pid):
            if self._class_name(hwnd) != "#32770" or not self.user32.IsWindowVisible(hwnd):
                continue
            descendants = self._children(hwnd)
            ids = {self.user32.GetDlgCtrlID(child) for child in descendants}
            ids.add(self.user32.GetDlgCtrlID(hwnd))
            if not all(control_id in ids for control_id in required_ids):
                continue
            if all(
                self._descendant(hwnd, control_id, class_name)
                for control_id, class_name in required_classes.items()
            ):
                return hwnd
        return 0

    def _descendant(self, hwnd: int, control_id: int, class_name: str | None = None) -> int:
        for child in self._children(hwnd):
            if self.user32.GetDlgCtrlID(child) != control_id:
                continue
            if class_name is None or self._class_name(child) == class_name:
                return child
        return 0
