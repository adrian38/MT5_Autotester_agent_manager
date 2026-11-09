"""Coordinador aislado de Experimenta Prop; nunca escribe en los agentes."""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .common import load_json, safe_float, safe_int, save_json, utc_now
from .prop_experiment_engine import result_payload, risk_normalized_pool, search
from .prop_experiment_pool import load_pool
from .prop_profiles import PROFILE_BY_ID, profile, profiles_payload


DEFAULT_SETTINGS: dict[str, Any] = {
    "profile_id": "ftmo-1-step", "capital": 100000.0, "horizon_months": 36,
    "source_nodes": [], "include_candidates": True, "include_seeds": True,
    "max_candidates_per_node": 150, "max_seeds_per_node": 150,
    "risk_unit_pct": 0.25, "max_strategies": 8,
    "max_units_per_strategy": 6, "max_units_total": 24,
    "search_pool_limit": 40, "max_days_per_phase": 120,
    "trial_stride_days": 5, "max_trials": 100,
}


class PropExperimentCancelled(RuntimeError):
    """El usuario detuvo el cálculo prop."""


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = set(raw) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    value = {**DEFAULT_SETTINGS, **raw}
    profile_id = str(value["profile_id"])
    if profile_id not in PROFILE_BY_ID:
        raise ValueError(f"Perfil prop desconocido: {profile_id}")
    return {
        "profile_id": profile_id,
        "capital": max(safe_float(value["capital"], 100000.0), 1000.0),
        "horizon_months": safe_int(value["horizon_months"], 36, minimum=3, maximum=84),
        "source_nodes": [str(item) for item in value["source_nodes"] if str(item)],
        "include_candidates": bool(value["include_candidates"]),
        "include_seeds": bool(value["include_seeds"]),
        "max_candidates_per_node": safe_int(value["max_candidates_per_node"], 150, minimum=0, maximum=10000),
        "max_seeds_per_node": safe_int(value["max_seeds_per_node"], 150, minimum=0, maximum=10000),
        "risk_unit_pct": min(max(safe_float(value["risk_unit_pct"], 0.25), 0.01), 5.0),
        "max_strategies": safe_int(value["max_strategies"], 8, minimum=1, maximum=50),
        "max_units_per_strategy": safe_int(value["max_units_per_strategy"], 6, minimum=1, maximum=50),
        "max_units_total": safe_int(value["max_units_total"], 24, minimum=1, maximum=500),
        "search_pool_limit": safe_int(value["search_pool_limit"], 40, minimum=1, maximum=250),
        "max_days_per_phase": safe_int(value["max_days_per_phase"], 120, minimum=5, maximum=500),
        "trial_stride_days": safe_int(value["trial_stride_days"], 5, minimum=1, maximum=60),
        "max_trials": safe_int(value["max_trials"], 100, minimum=1, maximum=1000),
    }


class PropExperimentCoordinator:
    """Gestiona un cálculo prop en segundo plano y conserva su último resultado."""

    def __init__(
        self, nodes: list[dict[str, Any]], settings_path: Path,
        source_factory: Callable[[str], Any],
    ) -> None:
        self.nodes = {str(node.get("id")): node for node in nodes}
        self.settings_path = Path(settings_path)
        self.result_path = self.settings_path.with_name("prop_experiment_last_result.json")
        self.source_factory = source_factory
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
        settings = normalize_settings(stored)
        allowed = self._allowed_nodes()
        selected = [item for item in settings["source_nodes"] if item in allowed]
        settings["source_nodes"] = selected or allowed
        return settings

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.settings(), **(changes or {})}
        normalized = normalize_settings(merged)
        allowed = set(self._allowed_nodes())
        normalized["source_nodes"] = [item for item in normalized["source_nodes"] if item in allowed]
        if not normalized["include_candidates"] and not normalized["include_seeds"]:
            raise ValueError("Activa candidatas, seeds o ambas")
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
                "settings": settings,
            }
        threading.Thread(target=self._worker, args=(settings,), daemon=True).start()
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.job.get("status") != "running":
                raise ValueError("No hay experimento prop en curso")
            self.cancel.set()
            self.job["progress"] = "Deteniendo…"
        return self.state()

    def _run(self, settings: dict[str, Any]) -> None:
        pool, axis, seed_keys, warnings, origins = load_pool(
            settings, self.source_factory, self._checkpoint,
        )
        if not pool or not axis.size:
            raise ValueError("El pool no tiene días operables en la ventana elegida")
        normalized = risk_normalized_pool(
            pool, seed_keys, float(settings["capital"]), float(settings["risk_unit_pct"]),
        )
        self._checkpoint(f"Buscando con {len(pool)} estrategias ({len(seed_keys)} seeds)")
        found = search(normalized, axis, profile(settings["profile_id"]), settings, self._checkpoint)
        payload = result_payload(found)
        payload.update({
            "profile": profile(settings["profile_id"]).payload(), "settings": settings,
            "pool": {"strategies": len(pool), "seeds": len(seed_keys), "candidates": len(pool) - len(seed_keys)},
            "origins": origins, "warnings": warnings + [
                "El drawdown diario e intradía se aproxima con PnL cerrado diario; validar la cartera en MT5 antes de comprar una prueba."
            ],
            "axis": {"from": axis.days[0], "to": axis.days[-1], "days": axis.size},
            "finished_at": utc_now(),
        })
        with self.lock:
            self.result = payload
            self.job.update({"status": "completed", "finished_at": utc_now(), "progress": "Experimento terminado"})
        save_json(self.result_path, payload)

    def _worker(self, settings: dict[str, Any]) -> None:
        try:
            self._run(settings)
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
