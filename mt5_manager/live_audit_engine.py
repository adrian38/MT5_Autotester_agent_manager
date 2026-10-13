from __future__ import annotations

from .live_audit_core import *  # noqa: F403
from .live_audit_comparison import _ComparisonMixin
from .live_audit_extraction import _ExtractionMixin
from .live_audit_lifecycle import _LifecycleMixin
from .live_audit_terminals import _TerminalMixin
from .live_audit_tester import _TesterMixin
from .live_audit_report_upload import replace_tester_reports


class LiveAuditController(
    _LifecycleMixin, _TesterMixin, _ExtractionMixin, _TerminalMixin, _ComparisonMixin,
):
    """Ejecuta auditorías en el agente sin persistir las credenciales recibidas."""

    history_sync_attempts = 6
    history_sync_delay_seconds = 1.0
    tester_login_settle_seconds = 30.0
    account_probe_seconds = 2.0

    def __init__(self, owner: Any, runtime_dir: Path) -> None:
        self.owner = owner
        self.runtime_dir = runtime_dir / "live_audits"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.lock = threading.RLock()
        self.states: dict[str, dict[str, Any]] = {}
        # Terminales donde esta auditoría activó la cuenta real. MT5 recuerda la
        # última cuenta de cada terminal, así que hay que devolverlos a la cuenta
        # configurada para restauración antes de soltarlos: ver `_restore_tester_login`.
        self.real_account_terminals: dict[str, list[dict[str, str]]] = {}
        # Botón de detener: una auditoría a la vez, así que la petición y el
        # proceso del tester que hay que matar caben en el controlador.
        self.stop_requested = False
        self.tester_process: Any = None
        self.tester_terminal_paths: list[str] = []
        if self.state_path.is_file():
            try:
                stored = load_json(self.state_path)
                if isinstance(stored, dict):
                    self.states = {str(key): dict(value) for key, value in stored.items() if isinstance(value, dict)}
                    for value in self.states.values():
                        if str(value.get("status")) in RUNNING_STATUSES:
                            value.update(status="failed", finished_at=utc_now(), error="Auditoría interrumpida al reiniciar el agente")
            except ValueError:
                pass
        self._persist()

    def _persist(self) -> None:
        save_json(self.state_path, self.states)

    def is_running(self) -> bool:
        with self.lock:
            return any(str(item.get("status")) in RUNNING_STATUSES for item in self.states.values())

    def all_states(self) -> dict[str, Any]:
        with self.lock:
            return {key: _safe_state(value) for key, value in self.states.items()}

    def state(self, audit_key: str | int) -> dict[str, Any]:
        with self.lock:
            key = str(audit_key)
            raw = self.states.get(key) or {"audit_key": key, "status": "idle"}
            return _safe_state(raw)

    def replace_reports(self, audit_key: str | int, payload: Any) -> dict[str, Any]:
        """Sustituye reportes tester de la ejecución visible, sin relanzar MT5."""
        return replace_tester_reports(self, str(audit_key), payload)

    def artifact_path(self, audit_key: str, audit_id: str, filename: str) -> Path:
        """Resuelve únicamente reportes de la ejecución visible de una auditoría."""
        if any(
            not value or len(value) > 255 or not all(char.isalnum() or char in "-_." for char in value)
            for value in (str(audit_key), str(audit_id))
        ):
            raise ValueError("Identificador de artefacto no válido")
        if not filename or Path(filename).name != filename:
            raise ValueError("Nombre de artefacto no válido")
        if Path(filename).suffix.casefold() not in {".htm", ".html", ".png", ".gif", ".jpg", ".jpeg"}:
            raise ValueError("Tipo de artefacto no permitido")
        with self.lock:
            raw = self.states.get(str(audit_key))
            if not raw or str(raw.get("audit_id") or "") != str(audit_id):
                raise FileNotFoundError("La ejecución solicitada no es la ejecución visible")
        reports_dir = (
            self.runtime_dir / f"audit_{audit_key}" / str(audit_id) / "reports"
        ).resolve()
        path = (reports_dir / filename).resolve()
        if path.parent != reports_dir or not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def stop(self, audit_key: str | int = "") -> dict[str, Any]:
        """Detiene la auditoría en curso: la configuración pudo salir mal.

        No se mata el hilo: se pide parar y se corta el Strategy Tester, que es
        lo que puede tardar horas. La auditoría sale por su `finally` de
        siempre, así que deja cada terminal en su cuenta y reanuda el pipeline.
        """
        with self.lock:
            running = [
                key for key, value in self.states.items()
                if str(value.get("status")) in RUNNING_STATUSES
            ]
            if not running:
                raise ValueError("No hay ninguna auditoría en curso que detener")
            requested = str(audit_key)
            key = requested if requested in running else running[0]
            self.stop_requested = True
            process = self.tester_process
            paths = list(self.tester_terminal_paths)
            status = str(self.states[key].get("status") or "")
        if process is not None and process.poll() is None:
            self._terminate_tester_process(process)
        closed = self._close_tester_terminals(paths)
        self._update(
            key, status, "Deteniendo la auditoría a petición del usuario.",
            "Detención pedida por el usuario: cortado el lanzador del tester y "
            f"cerradas {closed} terminal(es) suyas; vuelven a su cuenta configurada.",
        )
        return self.state(key)

    def _close_tester_terminals(self, paths: list[str]) -> int:
        """Cierra las terminales que el tester tenía abiertas, sin perder su cuenta.

        Matar el lanzador no para lo que ya abrió: el backtest sigue corriendo
        en cada MT5. Se cierran por el camino ordenado, que es el que deja a
        MT5 escribir su configuración antes de irse.
        """
        pids: set[int] = set()
        for path in paths:
            if path:
                pids |= self._terminal_pids_for_path(path)
        if pids:
            self._close_terminal_pids_gracefully(pids)
        return len(pids)

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_request(payload)
        portfolio_id = request["portfolio_id"]
        audit_key = request["audit_key"]
        # Solo el UBS estable entra en este servicio. El mensual sigue congelado.
        self._portfolio_members(portfolio_id, request["portfolio_type"])
        with self.lock:
            if self.is_running():
                raise RuntimeError("Ya hay una auditoría utilizando las terminales del nodo")
            # Una detención pedida sobre la anterior no puede matar a ésta.
            self.stop_requested = False
            self.tester_process = None
            audit_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self.states[audit_key] = {
                "audit_key": audit_key, "portfolio_id": portfolio_id,
                "portfolio_type": request["portfolio_type"], "audit_id": audit_id, "status": "queued",
                "started_at": utc_now(), "finished_at": None, "error": None,
                "progress_text": "Preparando la auditoría en el nodo.",
                "log_lines": [
                    f"[{utc_now()}] Inicio {audit_key}: portafolio #{portfolio_id}, "
                    f"variante {request['portfolio_type']}, cuenta real {request['source_login']} "
                    f"({request['source_server']}), tester {request['tester_login']} ({request['tester_server']})"
                ],
                "last_result": (self.states.get(audit_key) or {}).get("last_result"),
            }
            self._persist()
        thread = threading.Thread(target=self._run, args=(request, audit_id), daemon=True)
        thread.start()
        return self.state(audit_key)
