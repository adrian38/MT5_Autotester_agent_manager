"""Orquestación del laboratorio «Experimenta».

Lee los candidatos aceptados de las memorias de los agentes elegidos, los
mezcla en un pool cruzado, evalúa el perfil de margen del broker de destino y
lanza la simulación de doce meses de `experiment_lab`.

Reutiliza sin tocar: `PortfolioSource` (lectura por copia de las memorias
remotas), `candidate_rows` (las cuatro etapas aceptadas), `cached_report`
(parseo con caché de los HTML de MT5), `load_robust_sets_from_rows` y
`build_margin_model`. Este módulo no escribe nada en el proyecto de ningún
agente: lo único que persiste es su propia configuración y el último resultado,
en `runtime/` de este repositorio.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from portfolio_manager.ubs_portfolio import (
    DEFAULT_ACCOUNT_LEVERAGE,
    load_robust_sets_from_rows,
    load_symbol_specs,
    portfolio_symbol_key,
)
from ubs.universe import load_asset_universe

from . import experiment_lab as lab
from .common import load_json, safe_float, safe_int, save_json, utc_now
from .portfolio_service import PortfolioSource, build_margin_model, cached_report


DEFAULT_SETTINGS: dict[str, Any] = {
    # El objetivo del experimento, tal cual se planteó: un millón en un año.
    "capital": 10000.0,
    "target_equity": 1000000.0,
    "horizon_months": 12,
    "max_dd_pct": 35.0,
    "max_margin_pct": 40.0,
    # 1 = recomponer lotes cada mes (lo que ya hace el EA por balance).
    "rebalance_months": 1,
    "max_units_per_strategy": 8,
    "max_units_per_symbol": 24,
    "max_units_total": 400,
    "max_pair_corr": 0.7,
    "pool_limit": lab.DEFAULT_POOL_LIMIT,
    "greedy_steps": lab.DEFAULT_GREEDY_STEPS,
    # Lo que domina el reloj no es la búsqueda: es parsear los HTML de MT5, en
    # torno a un segundo por candidata. ICTrading solo ya tiene 1.251 sets
    # aceptados, así que sin tope una primera prueba tarda media hora por
    # broker. Se cargan las más recientes y la pantalla dice cuántas quedaron
    # fuera; 0 significa todas, para la pasada exhaustiva.
    "max_candidates_per_node": 300,
    "require_portable": True,
    "target_node": "",
    "source_nodes": [],
}
LOG_LIMIT = 4000


class ExperimentCancelled(RuntimeError):
    """El usuario detuvo el experimento en curso."""


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Valida el formulario. Los topes existen para que un cero no cuelgue el hilo."""
    value = dict(DEFAULT_SETTINGS)
    unknown = set(raw) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    value.update(raw)
    normalized: dict[str, Any] = {
        "capital": max(safe_float(value["capital"], 10000.0), 1.0),
        "target_equity": max(safe_float(value["target_equity"], 1000000.0), 1.0),
        "horizon_months": safe_int(value["horizon_months"], 12, minimum=1, maximum=72),
        "max_dd_pct": min(max(safe_float(value["max_dd_pct"], 35.0), 1.0), 95.0),
        "max_margin_pct": min(max(safe_float(value["max_margin_pct"], 40.0), 1.0), 400.0),
        "rebalance_months": safe_int(value["rebalance_months"], 1, minimum=0, maximum=12),
        "max_units_per_strategy": safe_int(
            value["max_units_per_strategy"], 8, minimum=1, maximum=200,
        ),
        "max_units_per_symbol": safe_int(
            value["max_units_per_symbol"], 24, minimum=1, maximum=2000,
        ),
        "max_units_total": safe_int(value["max_units_total"], 400, minimum=1, maximum=20000),
        "max_pair_corr": min(max(safe_float(value["max_pair_corr"], 0.7), 0.0), 1.0),
        "pool_limit": safe_int(value["pool_limit"], lab.DEFAULT_POOL_LIMIT, minimum=1, maximum=300),
        "greedy_steps": safe_int(
            value["greedy_steps"], lab.DEFAULT_GREEDY_STEPS, minimum=0, maximum=2000,
        ),
        "max_candidates_per_node": safe_int(
            value["max_candidates_per_node"], 300, minimum=0, maximum=100000,
        ),
        "require_portable": bool(value["require_portable"]),
        "target_node": str(value["target_node"] or "").strip(),
        "source_nodes": [
            str(item).strip() for item in (value["source_nodes"] or []) if str(item).strip()
        ],
    }
    if normalized["target_equity"] <= normalized["capital"]:
        raise ValueError("El objetivo tiene que ser mayor que el capital inicial")
    return normalized


def lab_config(settings: dict[str, Any]) -> lab.LabConfig:
    return lab.LabConfig(
        capital=float(settings["capital"]),
        target_equity=float(settings["target_equity"]),
        max_dd_pct=float(settings["max_dd_pct"]),
        max_margin_pct=float(settings["max_margin_pct"]),
        rebalance_months=int(settings["rebalance_months"]),
        max_units_per_strategy=int(settings["max_units_per_strategy"]),
        max_units_per_symbol=int(settings["max_units_per_symbol"]),
        max_units_total=int(settings["max_units_total"]),
        max_pair_corr=float(settings["max_pair_corr"]),
        pool_limit=int(settings["pool_limit"]),
        greedy_steps=int(settings["greedy_steps"]),
    )


def portable_symbols_for(source: PortfolioSource) -> frozenset[str]:
    """Símbolos que el broker de destino puede ejecutar de verdad.

    La fuente buena es el volcado del terminal (``<broker>_symbol_specs.json``):
    si MT5 midió el símbolo, el símbolo existe en esa cuenta. El universo
    ``<broker>_assets.ini`` es el respaldo cuando no hay volcado, y es más laxo
    porque lista lo que el agente quiere generar, no lo que el terminal
    confirmó. Sin ninguno de los dos no hay comprobación posible y el pool
    entero se marca como portable: es mejor decirlo que inventar un veredicto.
    """
    symbols: set[str] = set()
    specs = getattr(source, "symbol_specs", None)
    if specs and Path(specs).is_file():
        _margins, min_lots, _contracts, _leverage, _origin = load_symbol_specs(specs)
        symbols.update(min_lots)
    if not symbols and source.universe.is_file():
        groups, _aliases = load_asset_universe(source.universe, include_disabled=True)
        for names in groups.values():
            symbols.update(portfolio_symbol_key(name) for name in names)
    return frozenset(symbols)


class ExperimentCoordinator:
    """Un solo experimento a la vez, con su log y su último resultado."""

    def __init__(self, nodes: list[dict[str, Any]], settings_path: Path) -> None:
        self.nodes = {str(node.get("id")): node for node in nodes}
        self.settings_path = Path(settings_path)
        self.result_path = self.settings_path.with_name("experiment_last_result.json")
        self.lock = threading.RLock()
        self.log_lines: deque[str] = deque(maxlen=LOG_LIMIT)
        self.cancel = threading.Event()
        self.job: dict[str, Any] = {"status": "idle", "progress": "", "error": None}
        self.result: dict[str, Any] | None = None
        self.stored: dict[str, Any] = {}
        if self.settings_path.is_file():
            try:
                self.stored = normalize_settings(load_json(self.settings_path))
            except ValueError:
                self.stored = {}
        if self.result_path.is_file():
            try:
                self.result = load_json(self.result_path)
            except ValueError:
                self.result = None

    # ------------------------------------------------------------------ estado

    def settings(self) -> dict[str, Any]:
        with self.lock:
            stored = dict(self.stored)
        settings = normalize_settings({
            key: value for key, value in stored.items() if key in DEFAULT_SETTINGS
        })
        if not settings["source_nodes"]:
            # Todos por defecto, sin comprobar disponibilidad: `config()` ya
            # paga esa comprobación una vez por nodo y tocar `Y:`/`X:` cuando la
            # unidad no responde cuesta segundos por llamada.
            settings["source_nodes"] = list(self.nodes)
        if settings["target_node"] not in self.nodes:
            settings["target_node"] = next(
                (node_id for node_id in settings["source_nodes"] if node_id in self.nodes),
                next(iter(self.nodes), ""),
            )
        return settings

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        current.update(changes or {})
        normalized = normalize_settings(current)
        with self.lock:
            self.stored = normalized
        save_json(self.settings_path, normalized)
        return normalized

    def _availability(self, node_id: str) -> dict[str, Any]:
        """¿Se puede leer hoy la memoria de este nodo desde el manager?

        AXI vive en `Y:` y RoboForex en `X:`; si la unidad no está montada,
        `PortfolioSource` falla al construirse. El experimento sigue con los
        nodos que sí responden y lo dice en pantalla, en vez de caerse entero
        porque falta una unidad de red.
        """
        node = self.nodes.get(node_id) or {}
        try:
            source = PortfolioSource(node)
        except (ValueError, OSError) as exc:
            return {"available": False, "reason": str(exc)}
        return {
            "available": True,
            "reason": "",
            "memory": source.memory.name,
            "broker": source.broker,
            "account": source.account,
        }

    def config(self) -> dict[str, Any]:
        nodes = []
        for node_id, node in self.nodes.items():
            availability = self._availability(node_id)
            nodes.append({
                "id": node_id,
                "name": str(node.get("name") or node_id),
                "broker": str(node.get("portfolio_broker") or ""),
                "account": str(node.get("portfolio_account_type") or ""),
                **availability,
            })
        return {
            "nodes": nodes,
            "settings": self.settings(),
            "defaults": dict(DEFAULT_SETTINGS),
            "state": self.state(),
            "observed_at": utc_now(),
        }

    def state(self) -> dict[str, Any]:
        with self.lock:
            job = dict(self.job)
            result = self.result
        return {"job": job, "result": result}

    def log(self, lines: int = 400) -> dict[str, Any]:
        with self.lock:
            tail = list(self.log_lines)[-max(int(lines), 1):]
        return {"lines": tail}

    def _note(self, message: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')} {message}"
        with self.lock:
            self.log_lines.append(stamped)
            self.job["progress"] = message

    def _checkpoint(self, message: str) -> None:
        if self.cancel.is_set():
            raise ExperimentCancelled("Experimento detenido")
        self._note(message)

    # ------------------------------------------------------------- ejecución

    def start(self, changes: dict[str, Any]) -> dict[str, Any]:
        settings = self.update_settings(changes or {})
        if not settings["source_nodes"]:
            raise ValueError("Elige al menos un broker de origen")
        if settings["target_node"] not in self.nodes:
            raise ValueError("Elige la cuenta de destino")
        with self.lock:
            if self.job.get("status") == "running":
                raise ValueError("Ya hay un experimento en curso")
            self.cancel = threading.Event()
            self.log_lines.clear()
            self.job = {
                "status": "running",
                "started_at": utc_now(),
                "finished_at": None,
                "progress": "Preparando el pool cruzado",
                "error": None,
                "settings": settings,
            }
            self.result = None
        threading.Thread(target=self._worker, args=(settings,), daemon=True).start()
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.job.get("status") != "running":
                raise ValueError("No hay experimento en curso")
            self.cancel.set()
            self.job["progress"] = "Deteniendo…"
        return self.state()

    @staticmethod
    def _trim_candidates(
        rows: list[dict[str, Any]], settings: dict[str, Any], origin: str, warnings: list[str],
    ) -> list[dict[str, Any]]:
        """Recorta a N candidatas por broker repartiendo entre símbolos, y **lo dice**.

        El recorte existe porque el reloj se lo come el parseo de los HTML, no
        la búsqueda. Dentro de cada símbolo se prefieren las de `candidate_id`
        más alto, que es el mismo criterio con el que
        `load_robust_sets_from_rows` desempata dos versiones del mismo set.

        Lo que no puede hacer es coger las N más recientes a secas. Medido el
        2026-09-10: las 60 últimas candidatas de AXI eran **todas** del mismo
        símbolo (`COCOA.FS`), que el broker de destino no tiene medido, así que
        AXI aportaba cero al pool cruzado y el recorte se comía el experimento
        entero. Repartir por símbolo en ronda evita que el último run decida el
        pool. Un tope silencioso se leería como «esto es todo el pool», así que
        el aviso viaja en el resultado.
        """
        limit = safe_int(settings.get("max_candidates_per_node"), 0, minimum=0)
        if not limit or len(rows) <= limit:
            return rows
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = portfolio_symbol_key(
                str(row.get("target_symbol") or row.get("symbol") or "")
            )
            by_symbol.setdefault(key, []).append(row)
        for group in by_symbol.values():
            group.sort(
                key=lambda row: safe_int(
                    row.get("source_candidate_id") or row.get("candidate_id"), 0,
                ),
                reverse=True,
            )
        picked: list[dict[str, Any]] = []
        depth = 0
        while len(picked) < limit:
            round_picked = 0
            for group in by_symbol.values():
                if len(picked) >= limit:
                    break
                if depth < len(group):
                    picked.append(group[depth])
                    round_picked += 1
            if not round_picked:
                break
            depth += 1
        warnings.append(
            f"{origin}: solo se leyeron {len(picked)} candidatas de {len(rows)}, repartidas "
            f"entre {len(by_symbol)} símbolos; sube «máx. candidatas por broker» para la "
            "pasada completa"
        )
        return picked

    def _load_pool(
        self, settings: dict[str, Any],
    ) -> tuple[list[lab.LabStrategy], lab.LabAxis, list[str], dict[str, Any]]:
        target_source = PortfolioSource(self.nodes[settings["target_node"]])
        # El perfil de margen es siempre el del broker de destino: la cuenta
        # única es suya. `account_leverage` solo lo usa el modelo AXI, y se pasa
        # el mismo valor por defecto que la pantalla UBS para no cambiar de
        # criterio entre pantallas.
        margin_model = build_margin_model(target_source, {
            "margin_profile": target_source.broker.lower(),
            "account_leverage": DEFAULT_ACCOUNT_LEVERAGE,
        })
        portable = portable_symbols_for(target_source)
        self._checkpoint(
            f"Destino {target_source.broker}/{target_source.account}: "
            f"{len(portable) or 'sin'} símbolos medidos para comprobar portabilidad"
        )
        loaded: list[tuple[Any, str, str]] = []
        warnings: list[str] = []
        origins: dict[str, Any] = {}
        for node_id in settings["source_nodes"]:
            self._checkpoint(f"Leyendo la memoria de {node_id}")
            node = self.nodes.get(node_id)
            if node is None:
                warnings.append(f"{node_id}: nodo desconocido, fuera del experimento")
                continue
            try:
                source = PortfolioSource(node)
                rows = source.candidate_rows(include_quarantined=False)
            except (ValueError, OSError, sqlite3.Error) as exc:
                warnings.append(f"{node_id}: {exc}")
                origins[node_id] = {
                    "broker": str(node.get("portfolio_broker") or node_id),
                    "rows": 0, "read": 0, "loaded": 0, "error": str(exc),
                }
                continue
            origin = source.broker
            accepted = len(rows)
            rows = self._trim_candidates(rows, settings, origin, warnings)
            self._checkpoint(
                f"{origin}: {accepted} candidatos aceptados, cargando reportes de {len(rows)}"
            )
            sets, load_warnings = load_robust_sets_from_rows(
                rows, [], parse=cached_report,
                progress=lambda message, origin=origin: self._checkpoint(f"{origin} · {message}"),
            )
            warnings.extend(f"{origin}: {item}" for item in load_warnings)
            loaded.extend((strategy, origin, node_id) for strategy in sets)
            origins[node_id] = {
                "broker": origin, "rows": accepted, "read": len(rows),
                "loaded": len(sets), "error": "",
            }
        if not loaded:
            raise ValueError(
                "Ninguna memoria elegida devolvió estrategias cargables. "
                "Comprueba que las unidades de red de los agentes están montadas."
            )
        self._checkpoint(f"Pool cruzado: {len(loaded)} estrategias, recortando a la ventana")
        pool, axis, pool_warnings = lab.build_lab_strategies(
            loaded,
            margin_model=margin_model,
            portable_symbols=portable,
            window_months=int(settings["horizon_months"]),
            progress=self._checkpoint,
        )
        warnings.extend(pool_warnings)
        meta = {
            "target": {
                "node": settings["target_node"],
                "broker": target_source.broker,
                "account": target_source.account,
                "margin_profile": margin_model.profile,
                "measured_symbols": len(portable),
            },
            "origins": origins,
        }
        return pool, axis, warnings, meta

    def _worker(self, settings: dict[str, Any]) -> None:
        try:
            pool, axis, warnings, meta = self._load_pool(settings)
            if not axis.size:
                raise ValueError("El pool no tiene ningún día con operaciones en la ventana")
            config = lab_config(settings)
            self._checkpoint(
                f"Ventana {axis.days[0]} → {axis.days[-1]} · {axis.months} meses · {axis.size} días"
            )
            candidates, notes = lab.select_candidates(
                pool, config, require_portable=bool(settings["require_portable"]),
            )
            warnings.extend(notes)
            if not candidates:
                raise ValueError(
                    "Ninguna estrategia del pool pasa los filtros: revisa el neto positivo, "
                    "la portabilidad al broker de destino y el límite de correlación."
                )
            self._checkpoint(f"Candidatas seleccionadas: {len(candidates)} de {len(pool)}")
            allocation = lab.search_allocation(
                candidates, axis, config, progress=self._checkpoint,
            )
            verdict = lab.build_verdict(allocation, candidates, axis, config)
            payload = lab.allocation_payload(allocation, candidates, verdict, axis, config)
            payload.update({
                "settings": settings,
                "pool": {
                    "strategies": len(pool),
                    "candidates": len(candidates),
                    "by_origin": lab.pool_summary(pool),
                },
                "warnings": warnings,
                "target": meta["target"],
                "origins": meta["origins"],
                "finished_at": utc_now(),
            })
            with self.lock:
                self.result = payload
                self.job.update({
                    "status": "completed",
                    "finished_at": utc_now(),
                    "progress": verdict.note or "Experimento terminado",
                })
            self._note(verdict.note or "Experimento terminado")
            try:
                save_json(self.result_path, payload)
            except OSError as exc:
                self._note(f"El resultado no se pudo guardar en runtime: {exc}")
        except ExperimentCancelled:
            with self.lock:
                self.job.update({
                    "status": "cancelled", "finished_at": utc_now(),
                    "progress": "Experimento detenido",
                })
            self._note("Experimento detenido")
        except Exception as exc:  # noqa: BLE001 - la pantalla necesita el motivo
            with self.lock:
                self.job.update({
                    "status": "failed", "finished_at": utc_now(),
                    "progress": "Experimento fallido", "error": str(exc),
                })
            self._note(f"Error: {exc}")
