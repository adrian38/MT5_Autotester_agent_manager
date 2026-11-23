"""Coordinador aislado de Experimenta Prop; nunca escribe en los agentes."""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ..common import load_json, safe_float, save_json, utc_now
from .prop_experiment_engine import (
    SearchResult, evaluate, executable_pool, result_payload, search,
)
from .prop_experiment_periods import (
    PHASE_ONE_FROM, PHASE_ONE_TO, PHASE_THREE_FROM, PHASE_THREE_TO,
    PHASE_TWO_FROM, PHASE_TWO_TO, windowed_pool,
)
from .prop_experiment_pool import load_pool
from .prop_profiles import EXPERIMENT_PROFILE_IDS, profile, profiles_payload
from .prop_experiment_validation import NodeRequest, validate_portfolio


DEFAULT_SETTINGS: dict[str, Any] = {
    "profile_id": "the5ers-growth", "capital": 5000.0, "bankroll_eur": 1000.0,
    "symbol_families": ["Forex", "Metals", "Indices"],
    "source_nodes": [], "include_candidates": True, "include_seeds": True,
    "include_manual_portfolios": True, "active_portfolios_only": False,
    "auto_continue": False,
}


class PropExperimentCancelled(RuntimeError):
    """El usuario detuvo el cálculo prop."""


def _profile_choices(
    value: dict[str, Any], migrate: bool,
) -> tuple[str, float, list[str]]:
    profile_id = str(value["profile_id"])
    if profile_id not in EXPERIMENT_PROFILE_IDS:
        if not migrate:
            raise ValueError(f"Perfil prop no habilitado todavía: {profile_id}")
        profile_id = str(DEFAULT_SETTINGS["profile_id"])
    selected = profile(profile_id)
    capital = safe_float(value["capital"], selected.account_sizes[0])
    if capital not in selected.account_sizes:
        if not migrate:
            offered = ", ".join(f"{item:g}" for item in selected.account_sizes)
            raise ValueError(f"Capital no ofrecido por {selected.firm}: {offered}")
        capital = selected.account_sizes[0]
    raw_families = value.get("symbol_families")
    families = [str(item) for item in raw_families] if isinstance(raw_families, (list, tuple)) else []
    unknown = set(families) - set(selected.symbol_families)
    if unknown:
        if not migrate:
            raise ValueError(f"Familias no permitidas: {', '.join(sorted(unknown))}")
        families = [item for item in families if item in selected.symbol_families]
    if not families:
        if not migrate:
            raise ValueError("Elige al menos una familia de símbolos")
        families = list(selected.symbol_families)
    return profile_id, capital, families


def normalize_settings(raw: dict[str, Any], *, migrate: bool = False) -> dict[str, Any]:
    unknown = set(raw) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    value = {**DEFAULT_SETTINGS, **raw}
    profile_id, capital, families = _profile_choices(value, migrate)
    return {
        "profile_id": profile_id,
        "capital": capital,
        "bankroll_eur": max(safe_float(value["bankroll_eur"], 1000.0), 0.0),
        "symbol_families": families,
        "source_nodes": [str(item) for item in value["source_nodes"] if str(item)],
        "include_candidates": bool(value["include_candidates"]),
        "include_seeds": bool(value["include_seeds"]),
        "include_manual_portfolios": bool(value["include_manual_portfolios"]),
        "active_portfolios_only": bool(value["active_portfolios_only"]),
        "auto_continue": bool(value["auto_continue"]),
    }


class PropExperimentCoordinator:
    """Gestiona un cálculo prop en segundo plano y conserva su último resultado."""

    def __init__(
        self, nodes: list[dict[str, Any]], settings_path: Path,
        source_factory: Callable[[str], Any],
        active_members_factory: Callable[[str, Any], list[dict[str, Any]]] | None = None,
        symbol_settings_factory: Callable[[str], dict[str, Any]] | None = None,
        node_request: NodeRequest | None = None,
    ) -> None:
        self.nodes = {str(node.get("id")): node for node in nodes}
        self.settings_path = Path(settings_path)
        self.result_path = self.settings_path.with_name("prop_experiment_last_result.json")
        self.source_factory = source_factory
        self.active_members_factory = active_members_factory
        self.symbol_settings_factory = symbol_settings_factory
        self.node_request = node_request
        self.lock = threading.RLock()
        self.log_lines: deque[str] = deque(maxlen=4000)
        self.cancel = threading.Event()
        self.job: dict[str, Any] = {"status": "idle", "progress": "", "error": None}
        self.result = self._load_optional(self.result_path)
        self.stored = self._load_optional(self.settings_path) or {}

    @staticmethod
    def _load_optional(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            return load_json(path)
        except ValueError:
            return None

    def _allowed_nodes(self) -> list[str]:
        return [
            node_id for node_id, node in self.nodes.items()
            if str(node.get("portfolio_broker") or "").upper() != "VANTAGE"
        ]

    def settings(self) -> dict[str, Any]:
        stored = {key: value for key, value in self.stored.items() if key in DEFAULT_SETTINGS}
        settings = normalize_settings(stored, migrate=True)
        allowed = self._allowed_nodes()
        selected = [item for item in settings["source_nodes"] if item in allowed]
        settings["source_nodes"] = selected or allowed
        return settings

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.settings(), **(changes or {})}
        normalized = normalize_settings(merged)
        allowed = set(self._allowed_nodes())
        normalized["source_nodes"] = [item for item in normalized["source_nodes"] if item in allowed]
        if not any(normalized[key] for key in (
            "include_candidates", "include_seeds", "include_manual_portfolios",
        )):
            raise ValueError("Activa candidatas, seeds o portafolios manuales")
        with self.lock:
            self.stored = normalized
        save_json(self.settings_path, normalized)
        return normalized

    def config(self) -> dict[str, Any]:
        nodes = []
        for node_id, node in self.nodes.items():
            broker = str(node.get("portfolio_broker") or "")
            nodes.append({
                "id": node_id, "name": str(node.get("name") or node_id), "broker": broker,
                "excluded": broker.upper() == "VANTAGE",
            })
        return {
            "nodes": nodes, "profiles": profiles_payload(), "settings": self.settings(),
            "phases": [
                {"id": 1, "from": PHASE_ONE_FROM, "to": PHASE_ONE_TO,
                 "label": "Construir Portafolio Prop"},
                {"id": 2, "from": PHASE_TWO_FROM, "to": PHASE_TWO_TO,
                 "label": "Validar OHLC frente a Every Tick"},
                {"id": 3, "from": PHASE_THREE_FROM, "to": PHASE_THREE_TO,
                 "label": "Validación histórica final"},
            ],
            "defaults": dict(DEFAULT_SETTINGS), "state": self.state(), "observed_at": utc_now(),
        }

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {"job": dict(self.job), "result": self.result}

    def log(self, lines: int = 400) -> dict[str, Any]:
        with self.lock:
            return {"lines": list(self.log_lines)[-max(lines, 1):]}

    def _note(self, message: str) -> None:
        with self.lock:
            self.log_lines.append(f"{time.strftime('%H:%M:%S')} {message}")
            self.job["progress"] = message

    def _checkpoint(self, message: str) -> None:
        if self.cancel.is_set():
            raise PropExperimentCancelled("Experimento detenido")
        self._note(message)

    def start(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Fase 1: construye un Portafolio Prop con composición y lotes."""
        settings = self.update_settings(changes)
        if not settings["source_nodes"]:
            raise ValueError("Elige al menos un broker distinto de Vantage")
        with self.lock:
            if self.job.get("status") == "running":
                raise ValueError("Ya hay un experimento prop en curso")
            self.cancel = threading.Event()
            self.log_lines.clear()
            self.result = None
            self.job = {
                "status": "running", "started_at": utc_now(), "finished_at": None,
                "progress": "Preparando candidatas y seeds", "error": None,
                "settings": settings, "phase": 1, "operation": "build",
            }
        threading.Thread(
            target=self._worker, args=("build", settings, {}), daemon=True,
        ).start()
        return self.state()

    def _start_result_operation(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.result or not self.result.get("allocations"):
            raise ValueError("Primero hay que construir un Portafolio Prop en la fase 1")
        phase = 2 if operation in {"repair", "validate_mt5"} else 3
        with self.lock:
            if self.job.get("status") == "running":
                raise ValueError("Ya hay una fase del Portafolio Prop en curso")
            self.cancel = threading.Event()
            self.log_lines.clear()
            self.job = {
                "status": "running", "started_at": utc_now(), "finished_at": None,
                "progress": "Preparando la recomposición" if phase == 2 else "Preparando validación 2020–2024",
                "error": None, "settings": self.settings(), "phase": phase,
                "operation": operation,
            }
        threading.Thread(
            target=self._worker, args=(operation, self.settings(), payload), daemon=True,
        ).start()
        return self.state()

    def repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Recompone lotes y miembros tras exclusiones locales de la fase 2."""
        raw = payload.get("excluded_keys") or []
        excluded = [str(item) for item in raw if str(item)] if isinstance(raw, list) else []
        if not excluded:
            raise ValueError("Marca al menos una estrategia que excluir")
        return self._start_result_operation("repair", {"excluded_keys": excluded})

    def validate_mt5(self) -> dict[str, Any]:
        """Fase 2: ejecuta el Portafolio Prop en OHLC y Every Tick."""
        if self.node_request is None:
            raise ValueError("El manager no tiene transporte hacia los agentes")
        return self._start_result_operation("validate_mt5", {})

    def validate_history(self) -> dict[str, Any]:
        """Fase 3: valida sin cambiar composición ni lotes."""
        return self._start_result_operation("validate", {})

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.job.get("status") != "running":
                raise ValueError("No hay experimento prop en curso")
            self.cancel.set()
            self.job["progress"] = "Deteniendo…"
        return self.state()

    def _load_executable(self, settings: dict[str, Any]):
        pool, axis, seed_keys, manual_keys, warnings, origins = load_pool(
            settings, self.source_factory, self._checkpoint, self.active_members_factory,
            self.symbol_settings_factory,
        )
        if not pool or not axis.size:
            raise ValueError("El pool no tiene días operables en la ventana elegida")
        return executable_pool(pool, seed_keys, manual_keys), axis, warnings, origins, (
            len(pool), len(seed_keys), len(manual_keys)
        )

    def _run_build(self, settings: dict[str, Any]) -> None:
        executable, full_axis, warnings, origins, counts = self._load_executable(settings)
        executable, axis = windowed_pool(
            executable, full_axis, PHASE_ONE_FROM, PHASE_ONE_TO,
        )
        pool_count, seed_count, manual_count = counts
        self._checkpoint(
            f"Buscando con {pool_count} estrategias "
            f"({seed_count} seeds, {manual_count} manuales)"
        )
        found = search(executable, axis, profile(settings["profile_id"]), settings, self._checkpoint)
        payload = result_payload(found, axis, float(settings["capital"]))
        payload.update({
            "kind": "prop_portfolio", "name": "Portafolio Prop", "revision": 1,
            "profile": profile(settings["profile_id"]).payload(), "settings": settings,
            "pool": {
                "strategies": pool_count, "seeds": seed_count,
                "manual": manual_count,
                "candidates": pool_count - seed_count - manual_count,
            },
            "origins": origins, "warnings": warnings + [
                "El drawdown diario e intradía se aproxima con PnL cerrado diario; validar la cartera en MT5 antes de comprar una prueba."
            ],
            "axis": {"from": axis.days[0], "to": axis.days[-1], "days": axis.size},
            "phase_one": {"status": "completed", "from": PHASE_ONE_FROM, "to": PHASE_ONE_TO},
            "phase_two": {"status": "pending", "from": PHASE_TWO_FROM, "to": PHASE_TWO_TO},
            "phase_three": {"status": "pending", "from": PHASE_THREE_FROM, "to": PHASE_THREE_TO},
            "finished_at": utc_now(),
        })
        with self.lock:
            self.result = payload
            self.job.update({"status": "completed", "finished_at": utc_now(), "progress": "Portafolio Prop construido"})
        save_json(self.result_path, payload)

    def _current_keys_and_units(self) -> tuple[set[str], dict[str, int]]:
        rows = self.result.get("allocations") if self.result else []
        allocations = {str(row["key"]): int(row["units"]) for row in rows or []}
        return set(allocations), allocations

    def _run_repair(self, settings: dict[str, Any], payload: dict[str, Any]) -> None:
        excluded = set(payload["excluded_keys"])
        current_keys, _old_units = self._current_keys_and_units()
        unknown = excluded - current_keys
        if unknown:
            raise ValueError("La exclusión contiene estrategias ajenas al Portafolio Prop")
        required = current_keys - excluded
        if not required:
            raise ValueError("No se pueden excluir todas las estrategias del Portafolio Prop")
        executable, full_axis, warnings, origins, counts = self._load_executable(settings)
        executable, axis = windowed_pool(executable, full_axis, PHASE_ONE_FROM, PHASE_ONE_TO)
        executable = [item for item in executable if item.lab.key not in excluded]
        found = search(
            executable, axis, profile(settings["profile_id"]), settings,
            self._checkpoint, required_keys=required,
        )
        updated = result_payload(found, axis, float(settings["capital"]))
        previous = dict(self.result or {})
        previous.pop("previous_revision", None)
        updated.update({
            **{key: value for key, value in (self.result or {}).items()
               if key not in {"allocations", "evaluation", "portfolio", "axis", "finished_at"}},
            "revision": int((self.result or {}).get("revision") or 1) + 1,
            "previous_revision": previous,
            "origins": origins, "warnings": warnings,
            "excluded_keys": sorted(excluded),
            "axis": {"from": axis.days[0], "to": axis.days[-1], "days": axis.size},
            "phase_two": {
                "status": "pending", "from": PHASE_TWO_FROM, "to": PHASE_TWO_TO,
                "excluded_keys": sorted(excluded),
                "message": "Composición reparada; falta validar de nuevo en OHLC y Every Tick",
            },
            "phase_three": {"status": "pending", "from": PHASE_THREE_FROM, "to": PHASE_THREE_TO},
            "finished_at": utc_now(),
        })
        with self.lock:
            self.result = updated
            self.job.update({"status": "completed", "finished_at": utc_now(), "progress": "Portafolio Prop recompuesto"})
        save_json(self.result_path, updated)

    def _run_validate_mt5(self) -> None:
        if self.node_request is None:
            raise ValueError("El manager no tiene transporte hacia los agentes")
        phase = validate_portfolio(
            dict(self.result or {}), self.source_factory, self.node_request,
            self._checkpoint,
        )
        updated = dict(self.result or {})
        updated["phase_two"] = phase
        updated["phase_three"] = {
            "status": "pending", "from": PHASE_THREE_FROM, "to": PHASE_THREE_TO,
        }
        updated["finished_at"] = utc_now()
        with self.lock:
            self.result = updated
            self.job.update({
                "status": "completed", "finished_at": utc_now(),
                "progress": "Validación OHLC/Every Tick terminada",
            })
        save_json(self.result_path, updated)

    def _run_validate(self, settings: dict[str, Any]) -> None:
        executable, full_axis, _warnings, _origins, _counts = self._load_executable(settings)
        executable, axis = windowed_pool(executable, full_axis, PHASE_THREE_FROM, PHASE_THREE_TO)
        current_keys, units = self._current_keys_and_units()
        selected = tuple(item for item in executable if item.lab.key in current_keys)
        if len(selected) != len(current_keys):
            raise ValueError("No se pudieron recuperar todas las estrategias del Portafolio Prop")
        measured = evaluate(
            selected, units, axis, profile(settings["profile_id"]),
            float(settings["capital"]), 120,
        )
        frozen = SearchResult(units, measured, selected)
        validation = result_payload(frozen, axis, float(settings["capital"]))
        updated = dict(self.result or {})
        updated["phase_three"] = {
            "status": "completed", "from": PHASE_THREE_FROM, "to": PHASE_THREE_TO,
            **validation,
        }
        updated["finished_at"] = utc_now()
        with self.lock:
            self.result = updated
            self.job.update({"status": "completed", "finished_at": utc_now(), "progress": "Validación 2020–2024 terminada"})
        save_json(self.result_path, updated)

    def _continue_to(self, phase: int, progress: str) -> None:
        with self.lock:
            self.job.update({
                "status": "running", "phase": phase, "progress": progress,
                "finished_at": None, "error": None,
            })

    def _automatic_follow_up(self, operation: str, settings: dict[str, Any]) -> None:
        if not settings.get("auto_continue"):
            return
        if operation in {"build", "repair"}:
            self._continue_to(2, "Continuando automáticamente con OHLC/Every Tick")
            self._run_validate_mt5()
            if int((self.result or {}).get("phase_two", {}).get("failed_checks") or 0):
                self._note("La fase 3 no se inicia: hay validaciones MT5 fallidas que revisar")
                return
        if operation in {"build", "repair", "validate_mt5"}:
            self._continue_to(3, "Continuando automáticamente con 2020–2024")
            self._run_validate(settings)

    def _worker(self, operation: str, settings: dict[str, Any], payload: dict[str, Any]) -> None:
        try:
            if operation == "build":
                self._run_build(settings)
            elif operation == "repair":
                self._run_repair(settings, payload)
            elif operation == "validate_mt5":
                self._run_validate_mt5()
            else:
                self._run_validate(settings)
            self._automatic_follow_up(operation, settings)
        except PropExperimentCancelled:
            with self.lock:
                self.job.update({"status": "cancelled", "finished_at": utc_now(), "progress": "Experimento detenido"})
        except Exception as exc:  # noqa: BLE001 - el error debe llegar a la pantalla
            with self.lock:
                self.job.update({
                    "status": "failed", "finished_at": utc_now(),
                    "progress": "Experimento fallido", "error": str(exc),
                })
            self._note(f"Error: {exc}")
