from __future__ import annotations

import configparser
import json
import math
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now
from .mt5_native_history_report import NativeHistoryReportError, export_native_history_report


RUNNING_STATUSES = frozenset({"queued", "pausing", "extracting", "testing", "comparing", "resuming"})
STATUS_LABELS = {
    "idle": "NO EJECUTADO", "queued": "EN COLA", "pausing": "PAUSANDO",
    "extracting": "EXTRAYENDO", "testing": "PROBANDO", "comparing": "COMPARANDO",
    "resuming": "REANUDANDO", "completed": "COMPLETADA", "not_comparable": "NO COMPARABLE",
    "failed": "FALLIDA",
}
PROGRESS = {
    "idle": ("idle", 0), "queued": ("preparing", 5), "pausing": ("preparing", 10),
    "extracting": ("extracting", 25), "testing": ("testing", 55),
    "comparing": ("comparing", 85), "resuming": ("comparing", 95),
    "completed": ("completed", 100), "not_comparable": ("completed", 100),
    "failed": ("completed", 100),
}


def _as_int(value: Any, name: str, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser un entero") from exc
    if result < minimum:
        raise ValueError(f"{name} debe ser como mínimo {minimum}")
    return result


def _as_float(value: Any, name: str, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser numérico") from exc
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{name} está fuera del rango permitido")
    return result


def normalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    audit_key = str(value.get("audit_key") or value.get("portfolio_id") or "").strip()
    if not audit_key or len(audit_key) > 120 or not all(char.isalnum() or char in "-_." for char in audit_key):
        raise ValueError("audit_key no es un identificador válido")
    portfolio_type = str(value.get("portfolio_type") or "").strip().lower()
    if portfolio_type not in {"aggressive", "balanced", "conservative"}:
        raise ValueError("portfolio_type debe ser aggressive, balanced o conservative")
    result = {
        "audit_key": audit_key,
        "portfolio_id": _as_int(value.get("portfolio_id"), "portfolio_id", 1),
        "portfolio_type": portfolio_type,
        "deployment_name": str(value.get("deployment_name") or "").strip()[:120],
        "source_login": str(value.get("source_login") or "").strip(),
        "source_server": str(value.get("source_server") or "").strip(),
        "source_password": str(value.get("source_password") or ""),
        "tester_login": str(value.get("tester_login") or "").strip(),
        "tester_server": str(value.get("tester_server") or "").strip(),
        "tester_password": str(value.get("tester_password") or ""),
        "period_days": _as_int(value.get("period_days"), "period_days", 1),
        "min_tick_history_quality_pct": _as_float(
            value.get("min_tick_history_quality_pct"), "min_tick_history_quality_pct", 0, 100
        ),
        "trade_time_tolerance_seconds": _as_int(
            value.get("trade_time_tolerance_seconds"), "trade_time_tolerance_seconds", 0
        ),
        "price_tolerance_points": _as_float(value.get("price_tolerance_points"), "price_tolerance_points"),
        "volume_tolerance_pct": _as_float(value.get("volume_tolerance_pct"), "volume_tolerance_pct", 0, 100),
        "pnl_deviation_warning_pct": _as_float(value.get("pnl_deviation_warning_pct"), "pnl_deviation_warning_pct"),
        "drawdown_deviation_warning_pct": _as_float(
            value.get("drawdown_deviation_warning_pct"), "drawdown_deviation_warning_pct"
        ),
        "execution_delay_mode": str(value.get("execution_delay_mode") or "measured"),
        "fixed_delay_ms": _as_int(value.get("fixed_delay_ms", 0), "fixed_delay_ms", 0),
    }
    for key in ("source_login", "tester_login"):
        if not result[key].isdigit():
            raise ValueError(f"{key} debe contener solo números")
    for key in ("source_server", "tester_server", "source_password", "tester_password"):
        if not result[key]:
            raise ValueError(f"Falta {key}")
    return result


def _metric_number(metrics: dict[str, str], *names: str) -> float | None:
    folded = {str(key).casefold(): str(value) for key, value in metrics.items()}
    for name in names:
        raw = folded.get(name.casefold())
        if not raw:
            continue
        match = re.search(r"-?\d+(?:[.,]\d+)?", raw.replace(" ", ""))
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                pass
    return None


def _drawdown(trades: list[dict[str, Any]]) -> float:
    equity = peak = maximum = 0.0
    for trade in sorted(trades, key=lambda row: row["close_time"]):
        equity += float(trade.get("profit") or 0)
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _trade_view(trade: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convierte una operación interna en un registro JSON auditable."""
    if trade is None:
        return None
    result: dict[str, Any] = {}
    for key in (
        "strategy", "symbol", "side", "open_time", "close_time",
        "open_price", "close_price", "volume", "profit",
    ):
        value = trade.get(key)
        result[key] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _redact_runner_output(text: str, *secrets: str) -> str:
    """El runner imprime el INI; nunca permitir contraseñas en artefactos o errores."""
    result = str(text or "")
    for secret in secrets:
        if secret:
            result = result.replace(str(secret), "[REDACTED]")
    return re.sub(r"(?mi)^(\s*Password\s*=).*$", r"\1[REDACTED]", result)


def _read_set_text(path: Path) -> tuple[str, str]:
    """Lee .set UTF-8/UTF-16 sin convertir sus parámetros en texto con NUL."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data[:4096].count(b"\x00") > max(8, len(data[:4096]) // 8):
        return data.decode("utf-16-le"), "utf-16"
    return data.decode("utf-8-sig", errors="replace"), "utf-8"


def _redact_log_files(directory: Path, *secrets: str) -> None:
    """Sanea también los logs propios de run_tests.py, no solo su stdout."""
    if not directory.is_dir():
        return
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in {".log", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            redacted = _redact_runner_output(text, *secrets)
            if redacted != text:
                path.write_text(redacted, encoding="utf-8")
        except OSError:
            continue


def _safe_state(raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status") or "idle")
    stage, progress = PROGRESS.get(status, ("idle", 0))
    return {
        "audit_key": str(raw.get("audit_key") or raw.get("portfolio_id") or ""),
        "portfolio_id": int(raw.get("portfolio_id") or 0),
        "portfolio_type": str(raw.get("portfolio_type") or ""),
        "audit_id": raw.get("audit_id"),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.upper()),
        "stage": stage,
        "progress_pct": progress,
        "progress_text": str(raw.get("progress_text") or "Aún no se ha ejecutado ninguna auditoría."),
        "started_at": raw.get("started_at"),
        "finished_at": raw.get("finished_at"),
        "can_run": status not in RUNNING_STATUSES,
        "log_lines": list(raw.get("log_lines") or [])[-500:],
        "last_result": raw.get("last_result"),
        "terminal_restore": raw.get("terminal_restore"),
        "error": raw.get("error"),
    }


class LiveAuditController:
    """Ejecuta auditorías en el agente sin persistir las credenciales recibidas."""

    history_sync_attempts = 6
    history_sync_delay_seconds = 1.0

    def __init__(self, owner: Any, runtime_dir: Path) -> None:
        self.owner = owner
        self.runtime_dir = runtime_dir / "live_audits"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.lock = threading.RLock()
        self.states: dict[str, dict[str, Any]] = {}
        # Terminales donde esta auditoría activó la cuenta real. MT5 recuerda la
        # última cuenta de cada terminal, así que hay que devolverlos a la cuenta
        # de pruebas antes de soltarlos: ver `_restore_tester_login`.
        self.real_account_terminals: dict[str, list[dict[str, str]]] = {}
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

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_request(payload)
        portfolio_id = request["portfolio_id"]
        audit_key = request["audit_key"]
        # Solo el UBS estable entra en este servicio. El mensual sigue congelado.
        self._portfolio_members(portfolio_id, request["portfolio_type"])
        with self.lock:
            if self.is_running():
                raise RuntimeError("Ya hay una auditoría utilizando las terminales del nodo")
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

    def _update(self, audit_key: str, status: str, text: str, log: str | None = None, **changes: Any) -> None:
        with self.lock:
            raw = self.states[audit_key]
            raw.update(status=status, progress_text=text, **changes)
            if log:
                raw.setdefault("log_lines", []).append(f"[{utc_now()}] {log}")
            self._persist()

    def _log(self, audit_key: str, line: str) -> None:
        """Registra un hecho sin tocar el estado terminal ya publicado."""
        with self.lock:
            raw = self.states.get(audit_key)
            if raw is None:
                return
            raw.setdefault("log_lines", []).append(f"[{utc_now()}] {line}")
            self._persist()

    def _remember_real_account_terminal(
        self, audit_key: str, section: str, profile: dict[str, str]
    ) -> None:
        """Anota un terminal en el que se activó la cuenta real."""
        path = str(profile.get("mt5_path") or "")
        if not path:
            return
        with self.lock:
            touched = self.real_account_terminals.setdefault(audit_key, [])
            if any(row["mt5_path"].casefold() == path.casefold() for row in touched):
                return
            touched.append({
                "section": section,
                "terminal": str(profile.get("name") or section),
                "mt5_path": path,
            })

    def _wait_for_pause(self, timeout: float = 180.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.owner.lock:
                status = str(self.owner.state.get("status") or "")
            if status == "paused":
                return True
            if status not in {"running", "stopping"}:
                return False
            time.sleep(0.25)
        raise TimeoutError("El pipeline no confirmó la pausa dentro del tiempo permitido")

    def _run(self, request: dict[str, Any], audit_id: str) -> None:
        portfolio_id = request["portfolio_id"]
        audit_key = request["audit_key"]
        paused_by_auditor = False
        status_after_resume: str | None = None
        unrestored: list[dict[str, Any]] = []
        with self.lock:
            self.real_account_terminals[audit_key] = []
        try:
            with self.owner.lock:
                job_status = str(self.owner.state.get("status") or "idle")
                has_process = self.owner.process is not None
            if has_process and job_status in {"running", "stopping"}:
                self._update(audit_key, "pausing", "Pausando el proceso activo.", "Pausa solicitada al pipeline activo")
                self.owner.pause()
                paused_by_auditor = self._wait_for_pause()
                if not paused_by_auditor:
                    raise RuntimeError("El proceso terminó sin confirmar la pausa; la auditoría no ocupó sus terminales")
            elif job_status in {"paused", "interrupted"}:
                self._update(audit_key, "queued", "El pipeline ya estaba pausado; se conservará así.", "Pausa previa del usuario detectada")

            self._update(audit_key, "extracting", "Extrayendo operaciones de la cuenta real.", "Conectando la cuenta real")
            period_end = datetime.now(timezone.utc)
            period_start = period_end - timedelta(days=request["period_days"])
            reports_dir = self.runtime_dir / f"audit_{audit_key}" / audit_id / "reports"
            native_report_path = reports_dir / "real_account_mt5_report.html"
            real_trades, symbol_points, account = self._extract_real(
                request, period_start, period_end, native_report_path,
            )
            real_account_report = dict(account.pop("native_report", {}) or {})
            if not real_account_report.get("native_terminal_report"):
                raise RuntimeError("MT5 no entregó el HTML nativo del historial de la cuenta real")
            real_history_detail = dict(account.pop("history_detail", {}) or {})
            self._update(
                audit_key, "extracting", "Historial de la cuenta real sincronizado.",
                f"Cuenta MT5 verificada: login {account.get('login')}, servidor {account.get('server')}, "
                f"terminal {account.get('terminal_profile')}; "
                f"{real_history_detail.get('period_raw_deals', 0)} deals brutos, "
                f"{real_history_detail.get('closing_deals', 0)} cierres y "
                f"{real_history_detail.get('positions_recovered', 0)} apertura(s) anterior(es) recuperada(s) "
                f"tras {real_history_detail.get('sync_attempts', 0)} consulta(s). "
                f"HTML nativo {real_account_report.get('filename')} capturado por "
                f"{real_account_report.get('capture_terminal_profile') or account.get('terminal_profile')} "
                f"con periodo {real_account_report.get('period_mode')} "
                f"{real_account_report.get('period_start_date')} a {real_account_report.get('period_end_date')}, "
                f"{real_account_report.get('bytes', 0)} bytes, sha256 "
                f"{str(real_account_report.get('sha256') or '')[:16]}...",
            )
            _detail, selected_members = self._portfolio_members(portfolio_id, request["portfolio_type"])
            signatures: set[tuple[str, str]] = set()
            for member in selected_members:
                raw_set = str(member.get("set_path") or member.get("set_id") or "")
                if not raw_set:
                    continue
                source = self._resolve_set(raw_set)
                set_text, _encoding = _read_set_text(source)
                magic = self._set_parameter(set_text, "EA_MagicNumber")
                if magic:
                    signatures.add((str(member.get("symbol") or "").casefold(), magic))
            if signatures:
                before_filter = len(real_trades)
                real_trades = [
                    trade for trade in real_trades
                    if (str(trade.get("symbol") or "").casefold(), str(trade.get("strategy") or "")) in signatures
                ]
                ignored = before_filter - len(real_trades)
                self._update(
                    audit_key, "extracting", "Filtrando operaciones de la variante seleccionada.",
                    f"Filtro por símbolo/magic: {len(real_trades)} cierres del portafolio, "
                    f"{ignored} cierres ajenos ignorados; firmas {sorted(signatures)}",
                )
                real_history_detail["portfolio_closures"] = len(real_trades)
                real_history_detail["foreign_closures_ignored"] = ignored
            real_groups: dict[str, int] = {}
            for trade in real_trades:
                key = f"{trade.get('symbol') or '?'} / magic {trade.get('strategy') or '?'}"
                real_groups[key] = real_groups.get(key, 0) + 1
            real_summary = ", ".join(f"{key}: {count}" for key, count in sorted(real_groups.items())) or "sin cierres"
            self._update(
                audit_key, "testing", "Ejecutando el portafolio con ticks reales en el nodo.",
                f"{len(real_trades)} operaciones reales extraídas ({real_summary})",
            )
            tester_trades, qualities, strategies, strategy_artifacts = self._run_tester(
                request, audit_id, period_start, period_end
            )
            tester_groups: dict[str, int] = {}
            for trade in tester_trades:
                key = f"{trade.get('symbol') or '?'} / {trade.get('strategy') or '?'}"
                tester_groups[key] = tester_groups.get(key, 0) + 1
            tester_summary = ", ".join(f"{key}: {count}" for key, count in sorted(tester_groups.items())) or "sin operaciones"
            self._update(
                audit_key, "comparing", "Comparando cuenta real y Strategy Tester.",
                f"{len(tester_trades)} operaciones del tester ({tester_summary})",
            )
            quality = min(qualities) if qualities else None
            if quality is None or quality < request["min_tick_history_quality_pct"]:
                result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
                result.update(
                    status="not_comparable", status_label="NO COMPARABLE", matched_trades=0,
                    discrepancies=0, stalled_strategies=0,
                    summary=("MT5 no informó History Quality." if quality is None else
                             f"History Quality {quality:.2f}% inferior al mínimo {request['min_tick_history_quality_pct']:.2f}%.")
                )
                final_status = "not_comparable"
            else:
                comparison = self._compare(real_trades, tester_trades, symbol_points, request, strategies)
                result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
                result.update(comparison)
                invalid_tester = sum((comparison.get("comparison_detail") or {}).get("tester_data_issues", {}).values())
                result["summary"] = (
                    f"{comparison['matched_trades']} parejas alineadas, "
                    f"{comparison['within_tolerance_trades']} dentro de todas las tolerancias y "
                    f"{comparison['discrepancies']} discrepancias; "
                    f"{comparison['stalled_strategies']} estrategia(s) sin continuidad"
                    + (f"; {invalid_tester} operación(es) tester con tiempos inválidos." if invalid_tester else ".")
                )
                result["status"] = result["status_label"] = "completed"
                result["status_label"] = "COMPLETADA"
                final_status = "completed"
            result["account"] = account
            result["real_history_detail"] = real_history_detail
            result["audit_key"] = audit_key
            result["audit_id"] = audit_id
            result["portfolio_type"] = request["portfolio_type"]
            result["strategy_artifacts"] = strategy_artifacts
            result["real_account_report"] = real_account_report
            detail = result.get("comparison_detail") or {}
            detail_log = "; ".join(
                f"{key}={detail[key]}" for key in (
                    "matched_by_strategy", "within_tolerance_by_strategy", "deviating_by_strategy",
                    "missing_by_strategy", "unmatched_real", "deviation_reasons", "tester_data_issues",
                ) if detail.get(key)
            )
            self._update(
                audit_key, final_status, result["summary"],
                f"Comparación finalizada" + (f": {detail_log}" if detail_log else ""), last_result=result,
            )
        except Exception as exc:
            self._update(
                audit_key, "failed", f"La auditoría falló: {exc}", str(exc),
                error=str(exc), finished_at=utc_now(),
            )
        finally:
            # La cuenta activa de un terminal es estado persistente de MT5, así que
            # se restaura antes de reanudar: el pipeline reanudado vuelve a probar
            # estrategias en esos mismos terminales.
            try:
                restored = self._restore_tester_login(request)
            except Exception as exc:
                # Un fallo aquí no puede tapar el resultado de la auditoría.
                restored = [{
                    "terminal": "desconocido", "mt5_path": "", "section": "",
                    "expected_login": str(request.get("tester_login") or ""),
                    "expected_server": str(request.get("tester_server") or ""),
                    "login": None, "server": None, "restored": False,
                    "error": _redact_runner_output(
                        str(exc), str(request.get("tester_password") or ""),
                        str(request.get("source_password") or ""),
                    ),
                }]
            with self.lock:
                self.real_account_terminals.pop(audit_key, None)
            if restored:
                unrestored = [row for row in restored if not row["restored"]]
                self._log(audit_key, "Cuenta dejada en cada terminal: " + "; ".join(
                    f"{row['terminal']} → {row['expected_login']} ({row['expected_server']})"
                    if row["restored"] else
                    f"{row['terminal']} → SIN RESTAURAR: {row['error']}"
                    for row in restored
                ))
                with self.lock:
                    raw = self.states[audit_key]
                    raw["terminal_restore"] = restored
                    last_result = raw.get("last_result")
                    if isinstance(last_result, dict) and str(last_result.get("audit_id") or "") == audit_id:
                        last_result["terminal_restore"] = restored
                    self._persist()
            if paused_by_auditor:
                try:
                    with self.lock:
                        status_after_resume = str(self.states[audit_key].get("status") or "completed")
                    self._update(audit_key, "resuming", "Reanudando el proceso que pausó el auditor.", "Reanudación solicitada")
                    self.owner.resume()
                except Exception as exc:
                    self._update(audit_key, "failed", f"La auditoría terminó, pero no se pudo reanudar: {exc}", str(exc), error=str(exc))
            with self.lock:
                raw = self.states[audit_key]
                if str(raw.get("status")) == "resuming":
                    raw.update(
                        status=status_after_resume or "completed",
                        progress_text=str(
                            (raw.get("last_result") or {}).get("summary")
                            or raw.get("error") or "Auditoría finalizada."
                        ),
                    )
                if unrestored:
                    # No cambia el veredicto de la comparación, pero el usuario tiene
                    # que enterarse sin abrir los logs: el terminal quedó en otra cuenta.
                    raw["progress_text"] = str(raw.get("progress_text") or "") + (
                        " ⚠ "
                        + ", ".join(str(row["terminal"]) for row in unrestored)
                        + f" no quedó en la cuenta de pruebas {request['tester_login']}."
                    )
                raw["finished_at"] = utc_now()
                self._persist()
            if getattr(self.owner, "queue", None):
                self.owner._schedule_queue_drain()

    def _settings_path(self) -> Path:
        project = Path(str(self.owner.config["project_dir"])).expanduser().resolve()
        path = Path(str(self.owner.config.get("settings_file") or "ui_settings.ini"))
        return path if path.is_absolute() else project / path

    def _terminal_profiles(self) -> list[tuple[str, dict[str, str]]]:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self._settings_path(), encoding="utf-8")
        profiles: list[tuple[str, dict[str, str]]] = []
        for section in parser.sections():
            if not section.casefold().startswith("terminal."):
                continue
            if parser.getboolean(section, "enabled", fallback=False):
                path = Path(parser.get(section, "mt5_path", fallback="").strip())
                if path.is_file():
                    profiles.append((section, dict(parser[section])))
        if profiles:
            return profiles
        path = Path(parser.get("Paths", "mt5_path", fallback="").strip())
        if path.is_file():
            return [("Terminal.1", {"enabled": "1", "mt5_path": str(path)})]
        raise ValueError("No hay una ruta terminal64.exe habilitada en ICTrading")

    def _terminal_path(self) -> Path:
        return Path(self._terminal_profiles()[0][1]["mt5_path"])

    def _native_report_profiles(self, excluded_path: Path) -> list[tuple[str, dict[str, str]]]:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self._settings_path(), encoding="utf-8")
        excluded = excluded_path.resolve()
        profiles: list[tuple[str, dict[str, str]]] = []
        for section in parser.sections():
            if not section.casefold().startswith("terminal."):
                continue
            profile = dict(parser[section])
            path = Path(str(profile.get("mt5_path") or "")).expanduser()
            if path.is_file() and path.resolve() != excluded:
                profiles.append((section, profile))
        profiles.sort(
            key=lambda item: 0 if any(
                token in " ".join(item[1].values()).casefold()
                for token in ("mt5_ic", "capital point", "ictrading")
            ) else 1
        )
        return profiles

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

    def _restore_tester_login(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Deja cada terminal que abrió la cuenta real en la cuenta de pruebas.

        El auditor cambia la cuenta del terminal con `initialize(login=...)` y MT5
        recuerda la última: sin esta restauración el pipeline seguiría probando
        cada estrategia con la cuenta real en lugar de la demo de pruebas. Se
        ejecuta siempre, también cuando la auditoría falla, y antes de reanudar.
        """
        audit_key = str(request["audit_key"])
        with self.lock:
            terminals = list(self.real_account_terminals.get(audit_key) or [])
        if not terminals:
            return []
        login, server = str(request["tester_login"]), str(request["tester_server"])
        secrets = (str(request.get("tester_password") or ""), str(request.get("source_password") or ""))
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return [{
                **terminal, "expected_login": login, "expected_server": server,
                "login": None, "server": None, "restored": False,
                "error": "MetaTrader5 no está instalado en el agente",
            } for terminal in terminals]
        rows: list[dict[str, Any]] = []
        for terminal in terminals:
            row: dict[str, Any] = {
                **terminal, "expected_login": login, "expected_server": server,
                "login": None, "server": None, "restored": False, "error": None,
            }
            before = self._terminal_pids()
            launched: set[int] = set()
            try:
                if not mt5.initialize(
                    path=terminal["mt5_path"], login=int(login),
                    password=request["tester_password"], server=server, timeout=60000,
                ):
                    row["error"] = f"MT5 no aceptó la cuenta de pruebas: {mt5.last_error()}"
                else:
                    launched = self._terminal_pids() - before
                    info = mt5.account_info()
                    actual_server = str(getattr(info, "server", "") or "") if info is not None else ""
                    row["login"] = str(info.login) if info is not None else None
                    row["server"] = actual_server or None
                    if info is None or int(info.login) != int(login):
                        row["error"] = "el terminal no confirmó la cuenta de pruebas"
                    elif actual_server.casefold() != server.casefold():
                        row["error"] = f"la cuenta quedó en el servidor {actual_server!r}"
                    else:
                        row["restored"] = True
            except Exception as exc:
                row["error"] = _redact_runner_output(str(exc), *secrets)
            finally:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                self._close_terminal_pids_gracefully(launched or (self._terminal_pids() - before))
            rows.append(row)
        return rows

    def _login_terminal(
        self, login: str, password: str, server: str, *, remember_for: str | None = None
    ) -> tuple[Any, str, dict[str, str], set[int]]:
        """Activa una cuenta en la primera terminal que la confirme.

        `remember_for` marca los logins de la cuenta **real**: cada terminal que
        acepta esas credenciales queda anotado para devolverlo después a la
        cuenta de pruebas.
        """
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError("MetaTrader5 no está instalado en el agente") from exc
        errors: list[str] = []
        server_key = re.sub(r"[^a-z0-9]+", "", server.casefold().split("-", 1)[0])
        profiles = self._terminal_profiles()
        profiles.sort(
            key=lambda item: 0 if server_key and server_key in re.sub(
                r"[^a-z0-9]+", "", " ".join(str(value) for value in item[1].values()).casefold()
            ) else 1
        )
        for section, profile in profiles:
            path = str(profile.get("mt5_path") or "")
            before = self._terminal_pids()
            if not mt5.initialize(path=path, login=int(login), password=password, server=server, timeout=60000):
                errors.append(f"{Path(path).parent.name}: {mt5.last_error()}")
                mt5.shutdown()
                self._close_terminal_pids(self._terminal_pids() - before)
                continue
            # El terminal aceptó las credenciales: desde aquí su cuenta guardada
            # ya cambió, tanto si el login se confirma como si no.
            if remember_for:
                self._remember_real_account_terminal(remember_for, section, profile)
            info = mt5.account_info()
            if info is not None and int(info.login) == int(login):
                return mt5, section, profile, self._terminal_pids() - before
            errors.append(f"{Path(path).parent.name}: el terminal no confirmó el login")
            mt5.shutdown()
            self._close_terminal_pids(self._terminal_pids() - before)
        raise RuntimeError("No se pudo iniciar sesión en ninguna terminal configurada: " + " | ".join(errors))

    def _extract_real(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        native_report_path: Path | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
        mt5, section, profile, launched_pids = self._login_terminal(
            request["source_login"], request["source_password"], request["source_server"],
            remember_for=str(request["audit_key"]),
        )
        try:
            info = mt5.account_info()
            if info is None or int(info.login) != int(request["source_login"]):
                raise RuntimeError("MT5 no confirmó el login de la cuenta real")
            actual_server = str(getattr(info, "server", "") or "")
            if actual_server.casefold() != str(request["source_server"]).casefold():
                raise RuntimeError(
                    f"MT5 confirmó el login, pero en el servidor {actual_server!r} y no "
                    f"{request['source_server']!r}"
                )
            terminal = mt5.terminal_info()
            if terminal is None or not bool(getattr(terminal, "connected", False)):
                raise RuntimeError("MT5 confirmó el login local, pero el terminal no está conectado al broker")

            period_deals, sync_detail = self._synchronised_history(mt5, period_start, period_end)
            market_deals = [deal for deal in period_deals if self._is_market_deal(deal)]
            opening_positions = {
                int(getattr(deal, "position_id", 0) or 0)
                for deal in market_deals if int(getattr(deal, "entry", -1)) in {0, 2}
            }
            closing_positions = {
                int(getattr(deal, "position_id", 0) or 0)
                for deal in market_deals if int(getattr(deal, "entry", -1)) in {1, 2, 3}
            }
            missing_open_positions = closing_positions - opening_positions
            all_deals = list(period_deals)
            recovered_positions = 0
            unresolved_positions: list[int] = []
            for position_id in sorted(missing_open_positions):
                position_deals = mt5.history_deals_get(position=position_id)
                if position_deals is None:
                    unresolved_positions.append(position_id)
                    continue
                prior_openings = [
                    deal for deal in position_deals
                    if self._is_market_deal(deal) and int(getattr(deal, "entry", -1)) in {0, 2}
                ]
                if prior_openings:
                    recovered_positions += 1
                    all_deals.extend(position_deals)
                else:
                    unresolved_positions.append(position_id)

            unique_deals: dict[tuple[Any, ...], Any] = {}
            for deal in all_deals:
                unique_deals[self._deal_identity(deal)] = deal
            trades = [
                trade for trade in self._real_trades(unique_deals.values())
                if period_start <= trade["close_time"] <= period_end
            ]
            points: dict[str, float] = {}
            for symbol in {row["symbol"] for row in trades}:
                symbol_info = mt5.symbol_info(symbol)
                points[symbol] = float(getattr(symbol_info, "point", 0.0) or 0.0)
            history_detail = {
                **sync_detail,
                "period_raw_deals": len(period_deals),
                "market_deals": len(market_deals),
                "opening_deals": sum(int(getattr(deal, "entry", -1)) in {0, 2} for deal in market_deals),
                "closing_deals": sum(int(getattr(deal, "entry", -1)) in {1, 2, 3} for deal in market_deals),
                "positions_closed": len(closing_positions),
                "positions_missing_open_in_period": len(missing_open_positions),
                "positions_recovered": recovered_positions,
                "positions_unresolved": len(unresolved_positions),
                "trades_reconstructed": len(trades),
            }
            account = {
                "login": str(info.login), "server": actual_server, "currency": str(info.currency),
                "connected": True, "terminal_profile": str(profile.get("name") or section),
                "history_detail": history_detail,
            }
            if native_report_path is not None:
                account["native_report"] = self._export_native_account_report(
                    mt5=mt5, request=request, profile_name=str(profile.get("name") or section),
                    terminal_path=Path(str(profile.get("mt5_path") or "")),
                    login=str(info.login), server=actual_server, period_start=period_start,
                    period_end=period_end, destination=native_report_path,
                )
            return trades, points, account
        finally:
            mt5.shutdown()
            self._close_terminal_pids(launched_pids)

    def _export_native_account_report(
        self, *, mt5: Any, request: dict[str, Any], profile_name: str,
        terminal_path: Path, login: str, server: str, period_start: datetime,
        period_end: datetime, destination: Path,
    ) -> dict[str, object]:
        try:
            metadata = export_native_history_report(
                terminal_path=terminal_path, login=login, server=server,
                period_start=period_start, period_end=period_end, destination=destination,
            )
            metadata["capture_terminal_profile"] = profile_name
            return metadata
        except NativeHistoryReportError as primary_error:
            # MetaTrader5 puede adjuntarse a un terminal abierto en otra sesión
            # de Windows. La API funciona, pero su ventana no es automatizable
            # desde el nodo. Para el reporte se abre una copia IC aislada en la
            # sesión del nodo y se cierra al terminar.
            mt5.shutdown()
            errors = [f"{profile_name}: {primary_error}"]
            for section, profile in self._native_report_profiles(terminal_path):
                path = Path(str(profile.get("mt5_path") or ""))
                before = self._terminal_pids()
                launched: set[int] = set()
                try:
                    if not mt5.initialize(
                        path=str(path), login=int(login), password=request["source_password"],
                        server=server, timeout=60000,
                    ):
                        errors.append(f"{profile.get('name') or section}: {mt5.last_error()}")
                        continue
                    launched = self._terminal_pids() - before
                    # Este terminal no participa en el pipeline, pero también se
                    # queda con la cuenta real hasta que se restaure.
                    self._remember_real_account_terminal(
                        str(request["audit_key"]), section, profile
                    )
                    info = mt5.account_info()
                    terminal = mt5.terminal_info()
                    if (
                        info is None or int(info.login) != int(login)
                        or str(getattr(info, "server", "") or "").casefold() != server.casefold()
                        or terminal is None or not bool(getattr(terminal, "connected", False))
                    ):
                        errors.append(f"{profile.get('name') or section}: la cuenta no quedó conectada")
                        continue
                    self._synchronised_history(mt5, period_start, period_end)
                    metadata = export_native_history_report(
                        terminal_path=path, login=login, server=server,
                        period_start=period_start, period_end=period_end, destination=destination,
                    )
                    metadata["capture_terminal_profile"] = str(profile.get("name") or section)
                    metadata["isolated_capture_terminal"] = True
                    return metadata
                except Exception as exc:
                    errors.append(f"{profile.get('name') or section}: {exc}")
                finally:
                    mt5.shutdown()
                    self._close_terminal_pids(launched or (self._terminal_pids() - before))
            raise NativeHistoryReportError(
                "No se pudo obtener el HTML nativo en ninguna terminal IC accesible: "
                + " | ".join(errors)
            ) from primary_error

    def _synchronised_history(
        self, mt5: Any, period_start: datetime, period_end: datetime
    ) -> tuple[list[Any], dict[str, Any]]:
        """Espera a que el historial del login recién activado deje de ser caché vacía/inestable."""
        latest: list[Any] | None = None
        snapshots: list[int | None] = []
        previous_fingerprint: tuple[int, int, int] | None = None
        stable_non_empty = 0
        for attempt in range(1, self.history_sync_attempts + 1):
            current = mt5.history_deals_get(period_start, period_end)
            if current is None:
                snapshots.append(None)
            else:
                latest = list(current)
                snapshots.append(len(latest))
                fingerprint = (
                    len(latest),
                    max((int(getattr(deal, "ticket", 0) or 0) for deal in latest), default=0),
                    max((int(getattr(deal, "time_msc", 0) or 0) for deal in latest), default=0),
                )
                stable_non_empty = (
                    stable_non_empty + 1
                    if fingerprint == previous_fingerprint and latest else int(bool(latest))
                )
                previous_fingerprint = fingerprint
                if attempt >= 3 and stable_non_empty >= 2:
                    break
            if attempt < self.history_sync_attempts:
                time.sleep(self.history_sync_delay_seconds)
        if latest is None:
            raise RuntimeError(f"No se pudo sincronizar el historial real: {mt5.last_error()}")
        return latest, {
            "sync_attempts": len(snapshots),
            "sync_snapshots": snapshots,
            "history_empty_after_sync": not bool(latest),
        }

    @staticmethod
    def _is_market_deal(deal: Any) -> bool:
        return (
            int(getattr(deal, "type", -1)) in {0, 1}
            and bool(int(getattr(deal, "position_id", 0) or 0))
        )

    @staticmethod
    def _deal_identity(deal: Any) -> tuple[Any, ...]:
        ticket = int(getattr(deal, "ticket", 0) or 0)
        if ticket:
            return ("ticket", ticket)
        return (
            "fallback", int(getattr(deal, "position_id", 0) or 0),
            int(getattr(deal, "time_msc", 0) or 0), int(getattr(deal, "entry", -1)),
            float(getattr(deal, "volume", 0.0) or 0.0), float(getattr(deal, "price", 0.0) or 0.0),
        )

    @staticmethod
    def _real_trades(deals: Any) -> list[dict[str, Any]]:
        opened: dict[int, list[Any]] = {}
        trades: list[dict[str, Any]] = []
        for deal in sorted(deals, key=lambda item: (int(getattr(item, "time_msc", 0)), int(getattr(item, "ticket", 0)))):
            position = int(getattr(deal, "position_id", 0) or 0)
            entry = int(getattr(deal, "entry", -1))
            deal_type = int(getattr(deal, "type", -1))
            if deal_type not in {0, 1} or not position:
                continue
            if entry in {0, 2}:
                opened.setdefault(position, []).append(deal)
            if entry not in {1, 2, 3}:
                continue
            sources = opened.get(position) or []
            if not sources:
                continue
            first = sources[0]
            volume = float(getattr(deal, "volume", 0.0) or 0.0)
            profit = sum(float(getattr(deal, key, 0.0) or 0.0) for key in ("profit", "commission", "swap", "fee"))
            trades.append({
                "strategy": str(getattr(first, "magic", 0) or getattr(first, "comment", "") or position),
                "symbol": str(getattr(deal, "symbol", "") or getattr(first, "symbol", "")),
                "side": "buy" if int(getattr(first, "type", 0)) == 0 else "sell",
                "open_time": datetime.fromtimestamp(int(getattr(first, "time", 0)), timezone.utc),
                "close_time": datetime.fromtimestamp(int(getattr(deal, "time", 0)), timezone.utc),
                "open_price": float(getattr(first, "price", 0.0) or 0.0),
                "close_price": float(getattr(deal, "price", 0.0) or 0.0),
                "volume": volume, "profit": profit,
            })
        return trades

    def _portfolio_members(
        self, portfolio_id: int, portfolio_type: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        detail = self.owner.portfolio_detail(portfolio_id, "full_history")["portfolio"]
        members = [dict(row) for row in detail.get("members") or []]
        matching = [row for row in members if str(row.get("variant_key") or "") == portfolio_type]
        if not matching:
            available = sorted({str(row.get("variant_key") or "") for row in members if row.get("variant_key")})
            raise ValueError(
                f"El portafolio #{portfolio_id} no contiene la variante {portfolio_type}; "
                f"disponibles: {', '.join(available) or 'ninguna'}"
            )
        return detail, matching

    def _broker_volume_rules(self) -> dict[str, tuple[float, float]]:
        """Carga volume_min/volume_step publicados por el agente del broker."""
        project = Path(str(self.owner.config["project_dir"])).expanduser().resolve()
        broker = str(self.owner.config.get("broker") or "ICTRADING").strip().lower()
        path = project / "assets" / f"{broker}_symbol_specs.json"
        try:
            data = load_json(path)
        except (OSError, ValueError):
            return {}
        symbols = data.get("symbols") if isinstance(data, dict) else None
        if not isinstance(symbols, dict):
            return {}
        rules: dict[str, tuple[float, float]] = {}
        for symbol, raw in symbols.items():
            if not isinstance(raw, dict):
                continue
            try:
                volume_min = float(raw.get("volume_min") or 0.0)
                volume_step = float(raw.get("volume_step") or volume_min or 0.0)
            except (TypeError, ValueError):
                continue
            if volume_min > 0:
                rules[str(symbol).casefold()] = (volume_min, volume_step if volume_step > 0 else volume_min)
        return rules

    @staticmethod
    def _tester_lot(
        member: dict[str, Any], rules: dict[str, tuple[float, float]],
    ) -> tuple[float, float, float | None, float | None, int]:
        """Normaliza un lote guardado antiguo al mínimo y paso reales del broker."""
        portfolio_lot = float(member.get("lot") if member.get("lot") is not None else .01)
        try:
            units = max(1, int(member.get("units") or 1))
        except (TypeError, ValueError):
            units = 1
        rule = rules.get(str(member.get("symbol") or "").casefold())
        if not rule:
            return portfolio_lot, portfolio_lot, None, None, units
        volume_min, volume_step = rule
        tester_lot = max(portfolio_lot, volume_min * units)
        if volume_step > 0:
            tester_lot = math.ceil((tester_lot - 1e-12) / volume_step) * volume_step
        return portfolio_lot, round(tester_lot, 8), volume_min, volume_step, units

    @staticmethod
    def _set_value(text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"(?mi)^{re.escape(key)}=([^|\r\n]*)(.*)$")
        if pattern.search(text):
            return pattern.sub(lambda match: f"{key}={value}{match.group(2)}", text, count=1)
        return text + f"\n{key}={value}||{value}||0||0||N\n"

    @staticmethod
    def _set_parameter(text: str, key: str) -> str:
        match = re.search(rf"(?mi)^{re.escape(key)}=([^|\r\n]*)", text)
        return match.group(1).strip() if match else ""

    def _resolve_set(self, raw: str) -> Path:
        project = Path(str(self.owner.config["project_dir"])).expanduser().resolve()
        path = Path(raw)
        if path.is_file():
            return path
        normalized = raw.replace("\\", "/")
        for prefix in ("/data/ic/", "/data/axi/", "/data/roboforex/"):
            if normalized.casefold().startswith(prefix):
                candidate = project / normalized[len(prefix):]
                if candidate.is_file():
                    return candidate
        matches = list(project.rglob(path.name)) if path.name else []
        if len(matches) == 1:
            return matches[0]
        raise FileNotFoundError(f"No se encontró el set del portafolio: {path.name or raw}")

    def _run_tester(
        self, request: dict[str, Any], audit_id: str, period_start: datetime, period_end: datetime
    ) -> tuple[list[dict[str, Any]], list[float], dict[str, int], list[dict[str, Any]]]:
        from portfolio_manager.mt5_report import parse_report

        detail, members = self._portfolio_members(request["portfolio_id"], request["portfolio_type"])
        if not members:
            raise ValueError("El portafolio no contiene estrategias")
        work = self.runtime_dir / f"audit_{request['audit_key']}" / audit_id
        sets_dir, reports_dir, configs_dir, logs_dir = (work / name for name in ("sets", "reports", "configs", "logs"))
        for directory in (sets_dir, reports_dir, configs_dir, logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        set_files: list[Path] = []
        member_by_stem: dict[str, dict[str, Any]] = {}
        volume_rules = self._broker_volume_rules()
        for index, member in enumerate(members, 1):
            source = self._resolve_set(str(member.get("set_path") or member.get("set_id") or ""))
            text, set_encoding = _read_set_text(source)
            portfolio_lot, tester_lot, volume_min, volume_step, units = self._tester_lot(member, volume_rules)
            text = self._set_value(text, "StartLots", f"{tester_lot:.8f}".rstrip("0").rstrip("."))
            target = sets_dir / f"audit_{index:03d}_{source.name}"
            target.write_text(text, encoding=set_encoding, newline="\n")
            runtime_text, _runtime_encoding = _read_set_text(target)
            runtime_lot_text = self._set_parameter(runtime_text, "StartLots")
            try:
                runtime_lot = float(runtime_lot_text)
            except (TypeError, ValueError):
                runtime_lot = None
            set_files.append(target)
            member_by_stem[target.stem] = {
                "member": member,
                "artifact": {
                    "strategy": str(member.get("candidate_id") or target.stem),
                    "symbol": str(member.get("symbol") or ""),
                    "configured_lot": portfolio_lot,
                    "tester_lot": tester_lot,
                    "portfolio_units": units,
                    "broker_volume_min": volume_min,
                    "broker_volume_step": volume_step,
                    "lot_adjusted_to_broker_rules": not math.isclose(
                        portfolio_lot, tester_lot, rel_tol=0, abs_tol=1e-9
                    ),
                    "runtime_start_lots": runtime_lot,
                    "lot_matches_portfolio": (
                        runtime_lot is not None and math.isclose(runtime_lot, portfolio_lot, rel_tol=0, abs_tol=1e-9)
                    ),
                    "magic": self._set_parameter(runtime_text, "EA_MagicNumber"),
                    "source_set": source.name,
                    "runtime_set": target.name,
                },
            }
        selected_summary = ", ".join(
            f"{member.get('symbol') or '?'}:{member.get('candidate_id') or Path(str(member.get('set_path') or '')).stem}"
            for member in members
        )
        self._update(
            request["audit_key"], "testing", "Preparando Strategy Tester.",
            f"Variante {request['portfolio_type']} seleccionada con {len(members)} estrategias: {selected_summary}",
        )
        template = configparser.ConfigParser(interpolation=None)
        template.optionxform = str
        template.read_dict({
            "Common": {"Login": request["tester_login"], "Password": request["tester_password"], "Server": request["tester_server"]},
            "Tester": {
                "Expert": "", "Symbol": "XAUUSD", "Period": "H1", "Model": "4",
                "FromDate": period_start.strftime("%Y.%m.%d"), "ToDate": period_end.strftime("%Y.%m.%d"),
                "Deposit": str(float(detail.get("capital") or 1000)), "Currency": "EUR", "Leverage": "1:500",
                "Optimization": "0", "Visual": "0", "ReplaceReport": "1", "ShutdownTerminal": "1", "Report": "",
            },
        })
        template_path = work / "tester.ini"
        with template_path.open("w", encoding="utf-8", newline="\n") as handle:
            template.write(handle)
        wrapper = (
            "import sys,run_tests; from pathlib import Path; "
            f"run_tests.REPORT_DIR=Path({str(reports_dir)!r}); run_tests.CONFIG_DIR=Path({str(configs_dir)!r}); "
            f"run_tests.LOG_DIR=Path({str(logs_dir)!r}); sys.argv=['run_tests.py']+sys.argv[1:]; "
            "raise SystemExit(run_tests.main())"
        )
        tester_mt5, _tester_section, tester_profile, tester_pids = self._login_terminal(
            request["tester_login"], request["tester_password"], request["tester_server"]
        )
        tester_mt5.shutdown()
        self._close_terminal_pids(tester_pids)
        terminal_config = configparser.ConfigParser(interpolation=None)
        terminal_config.optionxform = str
        terminal_config["Multiterminal"] = {
            "enabled": "1", "workers": "1",
            "broker": str(tester_profile.get("broker") or self.owner.config.get("broker") or "ICTRADING"),
        }
        terminal_config["Terminal.1"] = {**tester_profile, "enabled": "1"}
        terminal_config_path = work / "terminals.ini"
        with terminal_config_path.open("w", encoding="utf-8", newline="\n") as handle:
            terminal_config.write(handle)
        workers = 1
        command = [
            sys.executable, "-u", "-c", wrapper, "--template", str(template_path),
            "--multi-terminal", "--terminals-config", str(terminal_config_path), "--max-workers", str(workers),
            "--infer-tester-from-set", "--prefer-set-path-timeframe", "--model", "4",
            "--from-date", period_start.strftime("%Y.%m.%d"), "--to-date", period_end.strftime("%Y.%m.%d"),
        ]
        for set_file in set_files:
            command.extend(["--set-file", str(set_file)])
        try:
            completed = subprocess.run(
                command, cwd=str(Path(str(self.owner.config["project_dir"])).resolve()),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=86400,
            )
        finally:
            # Los INI contienen la contraseña del tester: nunca se conservan en el agente.
            for secret_file in (template_path, terminal_config_path):
                try:
                    secret_file.unlink(missing_ok=True)
                except OSError:
                    pass
            if configs_dir.is_dir():
                for secret_file in configs_dir.iterdir():
                    try:
                        if secret_file.is_file():
                            secret_file.unlink()
                    except OSError:
                        pass
            _redact_log_files(
                logs_dir, request.get("source_password", ""), request.get("tester_password", "")
            )
        runner_output = _redact_runner_output(
            completed.stdout or "", request.get("source_password", ""), request.get("tester_password", "")
        )
        (work / "runner.log").write_text(runner_output, encoding="utf-8")
        if completed.returncode:
            tail = "\n".join(runner_output.splitlines()[-20:])
            raise RuntimeError(f"Strategy Tester terminó con código {completed.returncode}: {tail}")
        tester_trades: list[dict[str, Any]] = []
        qualities: list[float] = []
        strategies: dict[str, int] = {}
        strategy_artifacts: list[dict[str, Any]] = []
        for stem, prepared in member_by_stem.items():
            member = prepared["member"]
            candidates = [reports_dir / f"{stem}.htm", reports_dir / f"{stem}.html"]
            report_path = next((path for path in candidates if path.is_file()), None)
            if report_path is None:
                raise RuntimeError(f"MT5 no generó el reporte de {member.get('set_name') or stem}")
            report = parse_report(report_path)
            quality = _metric_number(report.metrics, "History Quality", "Calidad del historial")
            if quality is not None:
                qualities.append(quality)
            strategy = str(member.get("candidate_id") or stem)
            strategies[strategy] = len(report.trades)
            observed_trade_volumes = sorted({round(float(trade.size), 8) for trade in report.trades})
            runtime_lot = prepared["artifact"].get("runtime_start_lots")
            artifact = dict(prepared["artifact"])
            artifact.update(
                report_file=report_path.name,
                tester_trades=len(report.trades),
                history_quality_pct=quality,
                observed_trade_volumes=observed_trade_volumes,
                report_volumes_match_start_lots=(
                    all(math.isclose(value, runtime_lot, rel_tol=0, abs_tol=1e-9) for value in observed_trade_volumes)
                    if observed_trade_volumes and runtime_lot is not None else None
                ),
            )
            strategy_artifacts.append(artifact)
            self._update(
                request["audit_key"], "testing", "Leyendo reportes del Strategy Tester.",
                f"Reporte {report.symbol} / {strategy}: {len(report.trades)} operaciones, "
                f"History Quality {quality if quality is not None else 'no informada'}",
            )
            for trade in report.trades:
                open_time = trade.open_time.replace(tzinfo=timezone.utc) if trade.open_time.tzinfo is None else trade.open_time
                close_time = trade.close_time.replace(tzinfo=timezone.utc) if trade.close_time.tzinfo is None else trade.close_time
                tester_trades.append({
                    "strategy": strategy, "symbol": report.symbol, "side": trade.trade_type.casefold(),
                    "open_time": open_time, "close_time": close_time, "open_price": trade.open_price,
                    "close_price": trade.close_price, "volume": trade.size, "profit": trade.profit_loss,
                })
        return tester_trades, qualities, strategies, strategy_artifacts

    @staticmethod
    def _compare(
        real: list[dict[str, Any]], tester: list[dict[str, Any]], points: dict[str, float],
        request: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        unused = set(range(len(real)))
        matched = 0
        within_tolerance = 0
        deviations = 0
        matched_by_strategy: dict[str, int] = {}
        within_tolerance_by_strategy: dict[str, int] = {}
        deviating_by_strategy: dict[str, int] = {}
        missing_by_strategy: dict[str, int] = {}
        deviation_reasons = {"close_time": 0, "open_price": 0, "volume": 0, "pnl": 0, "drawdown": 0}
        tester_data_issues: dict[str, int] = {}
        operation_comparisons: list[dict[str, Any]] = []
        time_limit = request["trade_time_tolerance_seconds"]
        for tester_index, expected in enumerate(tester, 1):
            strategy = str(expected["strategy"])
            data_issues: list[str] = []
            if expected["close_time"] < expected["open_time"]:
                data_issues.append("close_before_open")
                tester_data_issues["close_before_open"] = tester_data_issues.get("close_before_open", 0) + 1
            candidates: list[tuple[float, int]] = []
            same_market: list[tuple[float, int]] = []
            for index in unused:
                actual = real[index]
                if actual["symbol"].casefold() != expected["symbol"].casefold() or actual["side"] != expected["side"]:
                    continue
                delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
                same_market.append((delta, index))
                if delta <= time_limit:
                    candidates.append((delta, index))
            if not candidates:
                missing_by_strategy[strategy] = missing_by_strategy.get(strategy, 0) + 1
                nearest = min(same_market) if same_market else None
                nearest_trade = real[nearest[1]] if nearest else None
                operation_comparisons.append({
                    "tester_index": tester_index,
                    "status": "missing",
                    "strategy": strategy,
                    "tester": _trade_view(expected),
                    "real": None,
                    "nearest_unused_real": _trade_view(nearest_trade),
                    "measurements": {
                        "nearest_open_time_delta_seconds": round(nearest[0], 3) if nearest else None,
                    },
                    "limits": {"open_time_seconds": time_limit},
                    "data_issues": data_issues,
                    "reasons": [
                        "open_time_outside_tolerance" if nearest else "no_real_same_symbol_and_side"
                    ],
                })
                continue
            open_time_delta, index = min(candidates)
            unused.remove(index)
            actual = real[index]
            matched += 1
            matched_by_strategy[strategy] = matched_by_strategy.get(strategy, 0) + 1
            point = points.get(actual["symbol"], 0.0)
            price_limit = request["price_tolerance_points"] * point
            volume_limit = max(expected["volume"], 1e-9) * request["volume_tolerance_pct"] / 100
            pnl_limit = max(abs(expected["profit"]), 1.0) * request["pnl_deviation_warning_pct"] / 100
            close_time_delta = abs((actual["close_time"] - expected["close_time"]).total_seconds())
            open_price_delta = abs(float(actual["open_price"]) - float(expected["open_price"]))
            volume_delta = abs(float(actual["volume"]) - float(expected["volume"]))
            pnl_delta = abs(float(actual["profit"]) - float(expected["profit"]))
            reasons: list[str] = []
            if close_time_delta > time_limit:
                reasons.append("close_time")
            if point > 0 and open_price_delta > price_limit:
                reasons.append("open_price")
            if volume_delta > volume_limit:
                reasons.append("volume")
            if pnl_delta > pnl_limit:
                reasons.append("pnl")
            if reasons:
                deviations += 1
                deviating_by_strategy[strategy] = deviating_by_strategy.get(strategy, 0) + 1
                for reason in reasons:
                    deviation_reasons[reason] += 1
            else:
                within_tolerance += 1
                within_tolerance_by_strategy[strategy] = within_tolerance_by_strategy.get(strategy, 0) + 1
            operation_comparisons.append({
                "tester_index": tester_index,
                "real_index": index + 1,
                "status": "deviation" if reasons else "matched",
                "strategy": strategy,
                "tester": _trade_view(expected),
                "real": _trade_view(actual),
                "nearest_unused_real": None,
                "measurements": {
                    "open_time_delta_seconds": round(open_time_delta, 3),
                    "close_time_delta_seconds": round(close_time_delta, 3),
                    "open_price_delta": round(open_price_delta, 10),
                    "open_price_delta_points": round(open_price_delta / point, 3) if point > 0 else None,
                    "volume_delta": round(volume_delta, 8),
                    "volume_delta_pct": round(volume_delta / max(abs(float(expected["volume"])), 1e-9) * 100, 3),
                    "pnl_delta": round(pnl_delta, 2),
                    "pnl_delta_pct": round(pnl_delta / max(abs(float(expected["profit"])), 1.0) * 100, 3),
                },
                "limits": {
                    "open_time_seconds": time_limit,
                    "close_time_seconds": time_limit,
                    "open_price_points": request["price_tolerance_points"],
                    "open_price_absolute": round(price_limit, 10),
                    "volume_pct": request["volume_tolerance_pct"],
                    "volume_absolute": round(volume_limit, 8),
                    "pnl_pct": request["pnl_deviation_warning_pct"],
                    "pnl_absolute": round(pnl_limit, 2),
                },
                "data_issues": data_issues,
                "reasons": reasons,
            })
        missing = len(tester) - matched
        extra = len(unused)
        real_dd, tester_dd = _drawdown(real), _drawdown(tester)
        dd_deviation = abs(real_dd - tester_dd) / max(tester_dd, 1.0) * 100
        if dd_deviation > request["drawdown_deviation_warning_pct"]:
            deviations += 1
            deviation_reasons["drawdown"] += 1
        stalled = sum(1 for strategy, count in strategies.items() if count and not matched_by_strategy.get(strategy))
        unmatched_real: dict[str, int] = {}
        unmatched_real_operations: list[dict[str, Any]] = []
        for index in unused:
            trade = real[index]
            key = f"{trade.get('symbol') or '?'} / magic {trade.get('strategy') or '?'}"
            unmatched_real[key] = unmatched_real.get(key, 0) + 1
            unmatched_real_operations.append({
                "real_index": index + 1,
                "status": "extra",
                "real": _trade_view(trade),
                "reason": "not_used_by_any_tester_operation",
            })
        strategy_summary = []
        for strategy in sorted(strategies):
            strategy_summary.append({
                "strategy": strategy,
                "tester_trades": int(strategies.get(strategy) or 0),
                "aligned": matched_by_strategy.get(strategy, 0),
                "within_tolerance": within_tolerance_by_strategy.get(strategy, 0),
                "with_deviations": deviating_by_strategy.get(strategy, 0),
                "missing_real": missing_by_strategy.get(strategy, 0),
            })
        return {
            "matched_trades": matched, "within_tolerance_trades": within_tolerance,
            "missing_real_trades": missing, "extra_real_trades": extra,
            "deviating_pairs": sum(deviating_by_strategy.values()),
            "deviating_trades": deviations, "discrepancies": missing + extra + deviations,
            "stalled_strategies": stalled, "real_drawdown": round(real_dd, 2),
            "tester_drawdown": round(tester_dd, 2), "drawdown_deviation_pct": round(dd_deviation, 2),
            "comparison_detail": {
                "matched_by_strategy": matched_by_strategy,
                "within_tolerance_by_strategy": within_tolerance_by_strategy,
                "deviating_by_strategy": deviating_by_strategy,
                "missing_by_strategy": missing_by_strategy,
                "unmatched_real": unmatched_real,
                "deviation_reasons": {key: value for key, value in deviation_reasons.items() if value},
                "tester_data_issues": tester_data_issues,
                "time_tolerance_seconds": time_limit,
                "methodology": {
                    "alignment": "Mismo símbolo y lado; apertura dentro de tolerancia; se elige el menor delta y cada real se usa una vez.",
                    "validation": "Después se validan cierre, precio de apertura, volumen y PnL; el drawdown se valida sobre el conjunto.",
                    "tolerances": {
                        "time_seconds": time_limit,
                        "price_points": request["price_tolerance_points"],
                        "volume_pct": request["volume_tolerance_pct"],
                        "pnl_pct": request["pnl_deviation_warning_pct"],
                        "drawdown_pct": request["drawdown_deviation_warning_pct"],
                    },
                },
                "strategy_summary": strategy_summary,
                "operation_comparisons": operation_comparisons,
                "unmatched_real_operations": unmatched_real_operations,
                "drawdown": {
                    "real": round(real_dd, 2),
                    "tester": round(tester_dd, 2),
                    "deviation_pct": round(dd_deviation, 2),
                    "limit_pct": request["drawdown_deviation_warning_pct"],
                    "outside_tolerance": dd_deviation > request["drawdown_deviation_warning_pct"],
                },
            },
        }

    @staticmethod
    def _result_base(
        request: dict[str, Any], period_start: datetime, period_end: datetime,
        real: list[dict[str, Any]], tester: list[dict[str, Any]], quality: float | None,
    ) -> dict[str, Any]:
        return {
            "audit_key": request["audit_key"], "portfolio_id": request["portfolio_id"],
            "portfolio_type": request["portfolio_type"], "completed_at": utc_now(),
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "period_days": request["period_days"],
            "history_quality_pct": round(quality, 2) if quality is not None else None,
            "real_trades": len(real), "tester_trades": len(tester),
        }
