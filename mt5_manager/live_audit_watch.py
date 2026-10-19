"""El vigilante de las auditorias en vivo: campana siempre, lanzar solo si toca.

Un unico hilo hace las dos cosas con la misma lectura del nodo:

1. **Vigilar.** Mira el estado de cada uso configurado y, cuando uno pasa de
   estar corriendo a terminado, anota un aviso en la campana. Esto funciona
   tambien con las auditorias lanzadas a mano, y por eso el hilo corre siempre:
   un aviso que solo existe mientras hay un navegador abierto no sirve para
   contar lo que paso el sabado por la noche.
2. **Lanzar.** Solo si el interruptor del cron esta puesto. Es el segundo
   candado de la regla de `manager_server`: vigilar no escribe nada en el
   agente, lanzar si, y una auditoria pausa el pipeline del nodo.

De uno en uno por nodo: el agente pausa su pipeline, audita y lo reanuda, asi
que dos usos a la vez sobre el mismo agente se pisarian.
"""
from __future__ import annotations

import sys
import threading
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import live_audit_analysis, live_audit_schedule, manager_http
from .common import load_json, safe_int, save_json, utc_now
from .live_audit_core import RUNNING_STATUSES

# Como se lee cada estado final del nodo: (resultado del intento, nivel del
# aviso, titulo). `idle` esta aqui porque un agente reiniciado olvida el uso que
# tenia a medias: para el cron es un intento perdido, no un turno cumplido.
FINAL_STATUSES = {
    "completed": ("success", "success", "Auditoría completada"),
    "not_comparable": ("success", "warning", "Auditoría sin comparación"),
    "failed": ("failed", "error", "Auditoría fallida"),
    "stopped": ("stopped", "info", "Auditoría detenida"),
    "idle": ("failed", "error", "Auditoría interrumpida"),
}


class LiveAuditRunState:
    """Lo que el vigilante recuerda de cada uso entre vuelta y vuelta.

    Una entrada por `nodo/uso`: el turno que esta sirviendo, cuantos intentos
    lleva en el, como acabo el ultimo y el ultimo estado visto en el nodo. Se
    persiste porque un reinicio del manager no puede reiniciar la cuenta de
    reintentos ni repetir avisos ya dados.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                stored = load_json(self.path)
            except ValueError:
                stored = {}
            self.records = {
                str(key): dict(value)
                for key, value in (stored.get("audits") or {}).items()
                if isinstance(value, dict)
            }

    @staticmethod
    def key(node_id: str, audit_id: str) -> str:
        return f"{node_id}/{audit_id}"

    def record(self, key: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.records.get(key) or {})

    def update(self, key: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            record = {**(self.records.get(key) or {}), **changes}
            self.records[key] = record
            save_json(self.path, {"audits": self.records})
            return dict(record)

    def state(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {key: dict(value) for key, value in self.records.items()}


def live_audit_ids(state: dict[str, Any]) -> set[str]:
    """Los usos marcados «operando en real», con el mismo criterio que la portada."""
    marked = state.get("live_audit_ids")
    if marked is None:
        marked = [
            audit_id for audit_id, profile in (state.get("profiles") or {}).items()
            if isinstance(profile, dict) and profile.get("live_trading")
        ]
    return {str(audit_id) for audit_id in marked}


def _profile_is_configured(state: dict[str, Any], audit_id: str) -> bool:
    if audit_id in {str(value) for value in state.get("configured_audit_ids") or []}:
        return True
    profile = (state.get("profiles") or {}).get(audit_id) or {}
    credentials = (state.get("credential_state") or {}).get(audit_id) or {}
    required = (
        "portfolio_id", "portfolio_type", "source_login", "source_server",
        "tester_login", "tester_server",
    )
    return all(profile.get(key) for key in required) and all((
        credentials.get("source_password_saved"),
        credentials.get("tester_password_saved"),
    ))


def scheduled_audit_ids(state: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """Los usos que le tocan al cron: configurados y, por defecto, solo reales."""
    configured = [str(audit_id) for audit_id in state.get("configured_audit_ids") or []]
    if not settings.get("only_live_trading", True):
        return configured
    return [
        audit_id for audit_id in sorted(live_audit_ids(state))
        if _profile_is_configured(state, audit_id)
    ]


def audit_label(profile: dict[str, Any], audit_id: str) -> str:
    name = str(profile.get("deployment_name") or "").strip()
    portfolio_id = safe_int(profile.get("portfolio_id"), 0, minimum=1)
    return name or (f"Portafolio #{portfolio_id}" if portfolio_id else f"Uso {audit_id}")


def _audit_use_created_at(audit_id: str) -> datetime | None:
    """Fecha del ID moderno que crea la UI; los IDs heredados no la inventan."""
    try:
        _prefix, stamp, sequence = audit_id.rsplit("-", 2)
        if not sequence.isdigit():
            return None
        return datetime.fromtimestamp(int(stamp, 36) / 1000, timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def node_live_audits(
    node: dict[str, Any], node_id: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """Los usos del nodo y, si no se pudieron leer, código y explicación.

    Un agente con el auditor anterior no cuenta como error: se anota y se
    salta, porque lo que le falta es un reinicio, no configuracion.
    """
    try:
        status_status, node_state = manager_http.node_request(
            node, "GET", "/api/v1/status", timeout=10,
        )
        capabilities = (
            node_state.get("capabilities")
            if status_status == 200 and isinstance(node_state, dict) else {}
        )
        if not isinstance(capabilities, dict) or not capabilities.get("live_audit_restore_account"):
            message = "El agente tiene un auditor antiguo; hay que reiniciarlo o portarlo."
            sys.stderr.write(f"[live-audit-scheduler] {node_id}: {message}\n")
            return None, "old_runtime", message
        status, value = manager_http.node_request(node, "GET", "/api/v1/live-audits", timeout=10)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        message = f"No se pudo consultar el agente: {exc}"
        sys.stderr.write(f"[live-audit-scheduler] {node_id}: {message}\n")
        return None, "unreachable", message
    if status != 200 or not isinstance(value, dict):
        message = f"La consulta de auditorías devolvió HTTP {status}: {value}"
        sys.stderr.write(f"[live-audit-scheduler] {node_id}: {message}\n")
        return None, "http_error", message
    audits = value.get("audits") if isinstance(value.get("audits"), dict) else {}
    return audits, "", ""


def record_node_issue(
    server: Any, node: dict[str, Any], node_id: str, code: str, message: str,
) -> None:
    """Avisa una vez por caída y otra al recuperarse, sólo con el cron armado."""
    runs = server.live_audit_runs
    key = runs.key(node_id, "__scheduler_watch__")
    previous = str(runs.record(key).get("watch_issue") or "")
    if code:
        if not server.live_audit_scheduler_enabled:
            return
        if code == previous:
            return
        runs.update(key, watch_issue=code, watch_message=message)
        level = "warning" if code == "old_runtime" else "error"
        server.live_audit_notifications.add(
            level, f"El cron no puede consultar {node.get('name') or node_id}", message,
            node_id=node_id, node_name=str(node.get("name") or node_id),
            kind="scheduler_node_unavailable",
        )
        return
    if not previous:
        return
    runs.update(key, watch_issue="", watch_message="")
    if server.live_audit_scheduler_enabled:
        server.live_audit_notifications.add(
            "success", f"El cron vuelve a ver {node.get('name') or node_id}",
            "El agente ya responde y las citas pendientes vuelven a poder lanzarse.",
            node_id=node_id, node_name=str(node.get("name") or node_id),
            kind="scheduler_node_recovered",
        )


def _execution_id(audit: dict[str, Any]) -> str:
    result = audit.get("last_result") if isinstance(audit.get("last_result"), dict) else {}
    return str(
        audit.get("audit_id") or result.get("audit_id") or result.get("completed_at") or ""
    )


def archive_execution(
    server: Any, node_id: str, audit_key: str, audit: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    """Guarda en el diario la ejecución terminada que el nodo enseña.

    Se guarda **el veredicto del manager**, no la materia prima: el Excel de
    atribución y las parejas decididas a mano ya están aplicados, y así la
    serie histórica enseña lo mismo que enseñó Análisis ese día.

    Idempotente por ejecución, que es lo que la convierte también en la forma
    de rescatar lo que ya existía: la primera vuelta archiva la última
    auditoría de cada uso antes de que la siguiente la sustituya.
    """
    archive = getattr(server, "live_audit_archive", None)
    execution_id = _execution_id(audit)
    if archive is None or not execution_id:
        return
    if archive.knows(node_id, audit_key, execution_id):
        return
    analysed = live_audit_analysis.analyse_node_state({"audit": dict(audit)}, profile)
    result = ((analysed or {}).get("audit") or {}).get("last_result")
    if not isinstance(result, dict) or not result.get("comparison_detail"):
        return
    archive.remember(node_id, {
        "audit_key": audit_key, "execution_id": execution_id,
        "portfolio_id": safe_int(result.get("portfolio_id"), 0, minimum=0),
        "portfolio_type": str(result.get("portfolio_type") or ""),
        "deployment_name": str(profile.get("deployment_name") or ""),
        "executed_at": str(
            (result.get("analysis") or {}).get("executed_at") or result.get("completed_at") or ""
        ),
        "completed_at": str(result.get("completed_at") or ""),
        "period_start": str(result.get("period_start") or ""),
        "period_end": str(result.get("period_end") or ""),
        "currency": str((result.get("account") or {}).get("currency") or ""),
        "status": str(audit.get("status") or ""),
    }, dict(result.get("comparison_detail") or {}))


def _finished_message(audit: dict[str, Any]) -> str:
    error = str(audit.get("error") or "").strip()
    if error:
        return error
    progress = str(audit.get("progress_text") or "").strip()
    finished = str(audit.get("finished_at") or "").strip()
    return progress or (f"Terminó a las {finished}" if finished else "")


def record_transitions(
    server: Any, node: dict[str, Any], node_id: str, state: dict[str, Any],
    audits: dict[str, Any],
) -> None:
    """Anota en la campana cada uso que acaba de terminar.

    Solo avisa de un paso de «corriendo» a «terminado»: la primera vuelta
    despues de arrancar el manager se limita a recordar lo que ve, para no
    anunciar como recien hecha una auditoria de hace tres semanas.
    """
    runs = server.live_audit_runs
    profiles = state.get("profiles") or {}
    for audit_id in [str(value) for value in state.get("configured_audit_ids") or []]:
        audit = dict(audits.get(audit_id) or {})
        status = str(audit.get("status") or "idle")
        key = runs.key(node_id, audit_id)
        record = runs.record(key)
        if status in FINAL_STATUSES and status != "idle":
            archive_execution(
                server, node_id, audit_id, audit, dict(profiles.get(audit_id) or {}),
            )
        if status == str(record.get("last_status") or ""):
            continue
        changes: dict[str, Any] = {"last_status": status}
        if str(record.get("last_status") or "") in RUNNING_STATUSES and status in FINAL_STATUSES:
            outcome, level, title = FINAL_STATUSES[status]
            changes["outcome"] = outcome
            profile = dict(profiles.get(audit_id) or {})
            server.live_audit_notifications.add(
                level, f"{title}: {audit_label(profile, audit_id)}",
                _finished_message(audit), node_id=node_id,
                node_name=str(node.get("name") or node_id), audit_id=audit_id,
                portfolio_id=safe_int(profile.get("portfolio_id"), 0, minimum=0),
                kind=f"audit_{status}",
            )
        runs.update(key, **changes)


def _launch_payload(
    server: Any, node_id: str, audit_id: str, profile: dict[str, Any],
    audit: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    """Lo que viaja al nodo: el perfil sin el criterio del manager y el periodo
    enganchado al final de la ultima auditoria.

    Lo que se queda aqui -Excel de atribucion, parejas a mano, estrategias ya
    revisadas y escalado de la vista- no se pierde: vive en el perfil del uso y
    se aplica al leer el resultado, asi que la ejecucion nueva lo hereda.
    """
    node_profile = {
        key: value for key, value in profile.items()
        if key not in live_audit_schedule.MANAGER_ONLY_PROFILE_KEYS
    }
    return {
        **node_profile,
        **live_audit_schedule.chained_period(
            server.live_audit_scheduler_settings, audit, now,
        ),
        **server.live_audit_settings.credentials(node_id, audit_id),
        **server.live_audit_settings.restore_credentials(node_id),
        "audit_key": audit_id,
        "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=1),
    }


def _attempt_text(attempts: int, settings: dict[str, Any]) -> str:
    if attempts <= 1:
        return "intento 1"
    return f"reintento {attempts - 1} de {int(settings.get('retry_attempts', 3))}"


def launch(
    server: Any, node: dict[str, Any], node_id: str, audit_id: str,
    profile: dict[str, Any], context: dict[str, Any],
) -> None:
    """Lanza un uso y deja escrito como fue, en la campana y en el estado."""
    runs, settings = server.live_audit_runs, server.live_audit_scheduler_settings
    key, attempts = runs.key(node_id, audit_id), int(context["attempts"])
    label, note = audit_label(profile, audit_id), _attempt_text(attempts, settings)
    common = {
        "node_id": node_id, "node_name": str(node.get("name") or node_id),
        "audit_id": audit_id,
        "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=0),
    }
    base = {
        "window": context["window"], "attempts": attempts,
        "last_attempt_at": utc_now(),
    }
    payload = _launch_payload(
        server, node_id, audit_id, profile, context["audit"], context["now"],
    )
    restore_fields = ("restore_login", "restore_server", "restore_password")
    if not all(payload.get(field) for field in restore_fields) or not payload["portfolio_id"]:
        runs.update(key, **base, outcome="failed")
        server.live_audit_notifications.add(
            "error", f"No se pudo lanzar: {label}",
            "Falta completar la cuenta final de terminales o el portafolio del uso.",
            kind="launch_blocked", **common,
        )
        return
    try:
        status, response = manager_http.node_request(
            node, "POST", f"/api/v1/live-audits/{payload['portfolio_id']}/run",
            payload, timeout=30,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        runs.update(key, **base, outcome="failed")
        server.live_audit_notifications.add(
            "error", f"No se pudo lanzar: {label}", str(exc), kind="launch_error", **common,
        )
        return
    if status >= 400:
        runs.update(key, **base, outcome="failed")
        server.live_audit_notifications.add(
            "error", f"El agente rechazó la auditoría: {label}",
            f"HTTP {status}: {response}", kind="launch_rejected", **common,
        )
        return
    runs.update(key, **base, outcome="running", last_status="queued")
    period = (
        f"periodo {payload['period_start_date']} a {payload['period_end_date']}, "
        "enganchado al final de la última auditoría"
        if payload.get("period_mode") == "fixed_dates" and payload.get("period_start_date")
        else "periodo configurado en el uso"
    )
    server.live_audit_notifications.add(
        "info", f"Auditoría programada lanzada: {label}",
        f"{note} · {live_audit_schedule.schedule_label(settings)} · {period}",
        kind="audit_started", **common,
    )


def _exhausted(server: Any, node: dict[str, Any], node_id: str, audit_id: str,
               profile: dict[str, Any], window: str) -> None:
    runs = server.live_audit_runs
    runs.update(runs.key(node_id, audit_id), window=window, outcome="exhausted")
    server.live_audit_notifications.add(
        "error", f"Reintentos agotados: {audit_label(profile, audit_id)}",
        "La auditoría falló en todos los intentos de este turno. El cron no "
        "volverá a lanzarla hasta la próxima cita.",
        node_id=node_id, node_name=str(node.get("name") or node_id), audit_id=audit_id,
        portfolio_id=safe_int(profile.get("portfolio_id"), 0, minimum=0),
        kind="retries_exhausted",
    )


def _completion(audit: dict[str, Any]) -> datetime | None:
    result = audit.get("last_result") if isinstance(audit.get("last_result"), dict) else {}
    return live_audit_schedule.parse_timestamp(
        result.get("completed_at") or audit.get("finished_at")
    )


def launch_due(
    server: Any, node: dict[str, Any], node_id: str, state: dict[str, Any],
    audits: dict[str, Any], now: datetime,
) -> None:
    """Lanza como mucho un uso de este nodo: el primero al que le toque."""
    if any(
        str((row or {}).get("status") or "") in RUNNING_STATUSES
        for row in audits.values() if isinstance(row, dict)
    ):
        return
    runs, settings = server.live_audit_runs, server.live_audit_scheduler_settings
    profiles = state.get("profiles") or {}
    for audit_id in scheduled_audit_ids(state, settings):
        audit = dict(audits.get(audit_id) or {})
        key = runs.key(node_id, audit_id)
        record = runs.record(key)
        window, pending = live_audit_schedule.pending_window(
            settings, _completion(audit), now, _audit_use_created_at(audit_id),
        )
        if not pending:
            if str(record.get("window") or "") != window:
                runs.update(key, window=window, attempts=0, outcome="success")
            continue
        decision = live_audit_schedule.due_decision(settings, record, window, now)
        if decision == "exhausted":
            _exhausted(server, node, node_id, audit_id, dict(profiles.get(audit_id) or {}), window)
            continue
        if not decision:
            continue
        launch(
            server, node, node_id, audit_id, dict(profiles.get(audit_id) or {}),
            {
                "window": window, "audit": audit, "now": now,
                "attempts": int(record.get("attempts") or 0) + 1
                if decision == "retry" else 1,
            },
        )
        return


def _incomplete_target_reason(state: dict[str, Any], audit_id: str) -> str:
    profile = (state.get("profiles") or {}).get(audit_id) or {}
    credentials = (state.get("credential_state") or {}).get(audit_id) or {}
    labels = {
        "portfolio_id": "portafolio", "portfolio_type": "variante",
        "source_login": "login real", "source_server": "servidor real",
        "tester_login": "login tester", "tester_server": "servidor tester",
    }
    missing = [label for key, label in labels.items() if not profile.get(key)]
    if credentials and not credentials.get("source_password_saved"):
        missing.append("contraseña real")
    if credentials and not credentials.get("tester_password_saved"):
        missing.append("contraseña tester")
    if not missing:
        return "La configuración del uso o sus credenciales no está completa."
    return f"Falta configurar: {', '.join(missing)}."


def _target_block_reason(
    state: dict[str, Any], audit_id: str, configured: set[str], chosen: set[str],
) -> str:
    if audit_id not in configured:
        return _incomplete_target_reason(state, audit_id)
    restore = state.get("restore_account") or {}
    if not isinstance(restore, dict) or not restore.get("configured"):
        return "Falta configurar la cuenta final que debe quedar en los terminales."
    if audit_id not in chosen:
        return "Este uso no entra en el alcance actual de la programación."
    return ""


def targets(server: Any) -> dict[str, Any]:
    """A quien alcanza el cron hoy, sin preguntarle nada a ningun nodo.

    Lo usa el diálogo para que el usuario vea lo que va a pasar antes de
    activarlo. `blocked` son los usos del alcance que el cron **no** puede
    lanzar porque el perfil, sus credenciales o la cuenta final no están
    completos. `scheduler_issues` enseña agentes inaccesibles o antiguos: sin
    esas diferencias visibles, el sábado no pasa nada y nadie sabe por qué.
    """
    settings = server.live_audit_scheduler_settings
    scheduled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        state = server.live_audit_settings.state(node_id)
        profiles = state.get("profiles") or {}
        configured = {
            str(audit_id) for audit_id in profiles
            if _profile_is_configured(state, str(audit_id))
        }
        chosen = set(scheduled_audit_ids(state, settings))
        watch = server.live_audit_runs.record(LiveAuditRunState.key(
            node_id, "__scheduler_watch__",
        ))
        if watch.get("watch_issue"):
            issues.append({
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "message": str(watch.get("watch_message") or "El agente no responde."),
            })
        for audit_id in sorted(live_audit_ids(state) | chosen):
            profile = dict(profiles.get(audit_id) or {})
            reason = _target_block_reason(state, audit_id, configured, chosen)
            row = {
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "audit_id": audit_id, "label": audit_label(profile, audit_id),
                "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=0),
                "live_trading": bool(profile.get("live_trading")),
                "block_reason": reason,
                **server.live_audit_runs.record(LiveAuditRunState.key(node_id, audit_id)),
            }
            (blocked if reason else scheduled).append(row)
    return {"audits": scheduled, "blocked": blocked, "scheduler_issues": issues}


def tick(server: Any) -> None:
    """Una vuelta: una lectura por nodo, avisos siempre y lanzamiento si toca."""
    now = datetime.now(timezone.utc)
    for node in server.nodes:
        if server.live_audit_stop.is_set():
            return
        node_id = str(node.get("id") or "")
        state = server.live_audit_settings.state(node_id)
        if not state.get("configured_audit_ids"):
            continue
        audits, issue, message = node_live_audits(node, node_id)
        record_node_issue(server, node, node_id, issue, message)
        if audits is None:
            continue
        record_transitions(server, node, node_id, state, audits)
        if not server.live_audit_scheduler_enabled or server.live_audit_stop.is_set():
            continue
        launch_due(server, node, node_id, state, audits, now)
