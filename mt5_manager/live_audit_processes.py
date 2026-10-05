from __future__ import annotations

from .live_audit_core import *  # noqa: F403


class _ProcessMixin:
    """Procesos de MT5: cuales corren, como se cierran y como se arrancan.

    Lo de abajo del todo del auditor: no sabe de perfiles ni de cuentas, solo
    de terminal64.exe. Esta aparte porque `live_audit_terminals` pasaba de las
    600 lineas.
    """

    @staticmethod
    def _terminal_pids() -> set[int]:
        if sys.platform != "win32":
            return set()
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"name='terminal64.exe'\" | Select-Object ProcessId | ConvertTo-Json -Compress",
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15,
        )
        if completed.returncode or not completed.stdout.strip():
            return set()
        parsed = json.loads(completed.stdout)
        rows = [parsed] if isinstance(parsed, dict) else parsed
        return {int(row["ProcessId"]) for row in rows or [] if int(row.get("ProcessId") or 0) > 0}

    @staticmethod
    def _terminal_pids_for_path(terminal_path: str) -> set[int]:
        """Devuelve solo los procesos de una instalación concreta de MT5."""
        if sys.platform != "win32":
            return set()
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"name='terminal64.exe'\" | "
                "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress",
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15,
        )
        if completed.returncode or not completed.stdout.strip():
            return set()
        parsed = json.loads(completed.stdout)
        rows = [parsed] if isinstance(parsed, dict) else parsed
        expected = os.path.normcase(os.path.abspath(terminal_path))
        return {
            int(row["ProcessId"])
            for row in rows or []
            if int(row.get("ProcessId") or 0) > 0
            and os.path.normcase(os.path.abspath(str(row.get("ExecutablePath") or ""))) == expected
        }

    @staticmethod
    def _close_terminal_pids(pids: set[int]) -> None:
        for pid in sorted(pids):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30,
            )

    def _close_terminal_pids_gracefully(self, pids: set[int], timeout: float = 30.0) -> None:
        """Pide el cierre con WM_CLOSE y solo fuerza a los que no obedecen.

        `taskkill /F` mata el proceso antes de que MT5 escriba su configuración,
        así que la cuenta que se acaba de restaurar se perdería y el terminal
        volvería a abrirse en la cuenta real.
        """
        if not pids:
            return
        for pid in sorted(pids):
            subprocess.run(
                ["taskkill", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = pids & self._terminal_pids()
            if not remaining:
                return
            time.sleep(0.5)
        self._close_terminal_pids(pids & self._terminal_pids())

    def _launch_terminal(self, terminal_path: str, config_path: Path | None = None) -> set[int]:
        """Arranca una instalación y espera a identificar su proceso exacto."""
        command = [terminal_path]
        if config_path is not None:
            command.append(f"/config:{config_path}")
        subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            pids = self._terminal_pids_for_path(terminal_path)
            if pids:
                return pids
            time.sleep(0.25)
        raise RuntimeError(f"MT5 no abrió el proceso de {Path(terminal_path).parent.name}")
