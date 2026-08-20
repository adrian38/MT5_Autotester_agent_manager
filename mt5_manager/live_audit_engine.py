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
            real_trades, symbol_points, account = self._extract_real(request, period_start, period_end)
            real_history_detail = dict(account.pop("history_detail", {}) or {})
            self._update(
                audit_key, "extracting", "Historial de la cuenta real sincronizado.",
                f"Cuenta MT5 verificada: login {account.get('login')}, servidor {account.get('server')}, "
                f"terminal {account.get('terminal_profile')}; "
                f"{real_history_detail.get('period_raw_deals', 0)} deals brutos, "
                f"{real_history_detail.get('closing_deals', 0)} cierres y "
                f"{real_history_detail.get('positions_recovered', 0)} apertura(s) anterior(es) recuperada(s) "
                f"tras {real_history_detail.get('sync_attempts', 0)} consulta(s).",
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
            tester_trades, qualities, strategies = self._run_tester(request, audit_id, period_start, period_end)
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
                result["summary"] = (
                    f"{comparison['matched_trades']} coincidencias y {comparison['discrepancies']} discrepancias; "
                    f"{comparison['stalled_strategies']} estrategia(s) sin continuidad."
                )
                result["status"] = result["status_label"] = "completed"
                result["status_label"] = "COMPLETADA"
                final_status = "completed"
            result["account"] = account
            result["real_history_detail"] = real_history_detail
            result["audit_key"] = audit_key
            result["portfolio_type"] = request["portfolio_type"]
            detail = result.get("comparison_detail") or {}
            detail_log = "; ".join(f"{key}={value}" for key, value in detail.items())
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

    def _login_terminal(self, login: str, password: str, server: str) -> tuple[Any, str, dict[str, str], set[int]]:
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
            info = mt5.account_info()
            if info is not None and int(info.login) == int(login):
                return mt5, section, profile, self._terminal_pids() - before
            errors.append(f"{Path(path).parent.name}: el terminal no confirmó el login")
            mt5.shutdown()
            self._close_terminal_pids(self._terminal_pids() - before)
        raise RuntimeError("No se pudo iniciar sesión en ninguna terminal configurada: " + " | ".join(errors))

    def _extract_real(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime
    ) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
        mt5, section, profile, launched_pids = self._login_terminal(
            request["source_login"], request["source_password"], request["source_server"]
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
            return trades, points, account
        finally:
            mt5.shutdown()
            self._close_terminal_pids(launched_pids)

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
    ) -> tuple[list[dict[str, Any]], list[float], dict[str, int]]:
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
        for index, member in enumerate(members, 1):
            source = self._resolve_set(str(member.get("set_path") or member.get("set_id") or ""))
            text, set_encoding = _read_set_text(source)
            text = self._set_value(text, "StartLots", f"{float(member.get('lot') or .01):.8f}".rstrip("0").rstrip("."))
            target = sets_dir / f"audit_{index:03d}_{source.name}"
            target.write_text(text, encoding=set_encoding, newline="\n")
            set_files.append(target)
            member_by_stem[target.stem] = member
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
        for stem, member in member_by_stem.items():
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
        return tester_trades, qualities, strategies

    @staticmethod
    def _compare(
        real: list[dict[str, Any]], tester: list[dict[str, Any]], points: dict[str, float],
        request: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        unused = set(range(len(real)))
        matched = 0
        deviations = 0
        matched_by_strategy: dict[str, int] = {}
        missing_by_strategy: dict[str, int] = {}
        deviation_reasons = {"close_time": 0, "open_price": 0, "volume": 0, "pnl": 0, "drawdown": 0}
        time_limit = request["trade_time_tolerance_seconds"]
        for expected in tester:
            candidates: list[tuple[float, int]] = []
            for index in unused:
                actual = real[index]
                if actual["symbol"].casefold() != expected["symbol"].casefold() or actual["side"] != expected["side"]:
                    continue
                delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
                if delta <= time_limit:
                    candidates.append((delta, index))
            if not candidates:
                strategy = str(expected["strategy"])
                missing_by_strategy[strategy] = missing_by_strategy.get(strategy, 0) + 1
                continue
            _, index = min(candidates)
            unused.remove(index)
            actual = real[index]
            matched += 1
            strategy = str(expected["strategy"])
            matched_by_strategy[strategy] = matched_by_strategy.get(strategy, 0) + 1
            point = points.get(actual["symbol"], 0.0)
            price_limit = request["price_tolerance_points"] * point
            volume_limit = max(expected["volume"], 1e-9) * request["volume_tolerance_pct"] / 100
            pnl_limit = max(abs(expected["profit"]), 1.0) * request["pnl_deviation_warning_pct"] / 100
            reasons = []
            if abs(actual["close_time"].timestamp() - expected["close_time"].timestamp()) > time_limit:
                reasons.append("close_time")
            if point > 0 and abs(actual["open_price"] - expected["open_price"]) > price_limit:
                reasons.append("open_price")
            if abs(actual["volume"] - expected["volume"]) > volume_limit:
                reasons.append("volume")
            if abs(actual["profit"] - expected["profit"]) > pnl_limit:
                reasons.append("pnl")
            if reasons:
                deviations += 1
                for reason in reasons:
                    deviation_reasons[reason] += 1
        missing = len(tester) - matched
        extra = len(unused)
        real_dd, tester_dd = _drawdown(real), _drawdown(tester)
        dd_deviation = abs(real_dd - tester_dd) / max(tester_dd, 1.0) * 100
        if dd_deviation > request["drawdown_deviation_warning_pct"]:
            deviations += 1
            deviation_reasons["drawdown"] += 1
        stalled = sum(1 for strategy, count in strategies.items() if count and not matched_by_strategy.get(strategy))
        unmatched_real: dict[str, int] = {}
        for index in unused:
            trade = real[index]
            key = f"{trade.get('symbol') or '?'} / magic {trade.get('strategy') or '?'}"
            unmatched_real[key] = unmatched_real.get(key, 0) + 1
        return {
            "matched_trades": matched, "missing_real_trades": missing, "extra_real_trades": extra,
            "deviating_trades": deviations, "discrepancies": missing + extra + deviations,
            "stalled_strategies": stalled, "real_drawdown": round(real_dd, 2),
            "tester_drawdown": round(tester_dd, 2), "drawdown_deviation_pct": round(dd_deviation, 2),
            "comparison_detail": {
                "matched_by_strategy": matched_by_strategy,
                "missing_by_strategy": missing_by_strategy,
                "unmatched_real": unmatched_real,
                "deviation_reasons": {key: value for key, value in deviation_reasons.items() if value},
                "time_tolerance_seconds": time_limit,
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
