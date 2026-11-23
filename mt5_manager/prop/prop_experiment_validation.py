"""Validación MT5 OHLC/Every Tick del Portafolio Prop congelado."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Callable

from ..manual_check import manual_check_analysis
from ..portfolio import portfolio_manual_check
from .prop_experiment_periods import PHASE_TWO_FROM, PHASE_TWO_TO


NodeRequest = Callable[[str, str, str, dict[str, Any] | None], tuple[int, Any]]


def _validation_groups(result: dict[str, Any]) -> list[tuple[str, float, list[dict[str, Any]]]]:
    """Agrupa por broker y lote: el runtime admite un lote común por trabajo."""
    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in result.get("allocations") or []:
        node_id = str(row.get("origin_node") or "")
        lot = round(float(row.get("lot") or 0.0), 8)
        if not node_id or lot <= 0:
            raise ValueError("El Portafolio Prop contiene una estrategia sin broker o lote")
        grouped[(node_id, lot)].append({
            "set_path": str(row.get("set_path") or ""),
            "set_name": str(row.get("set_id") or ""),
            "symbol": str(row.get("symbol") or ""),
            "timeframe": str(row.get("timeframe") or ""),
            "candidate_id": str(row.get("key") or ""),
        })
    return [
        (node_id, lot, strategies)
        for (node_id, lot), strategies in sorted(grouped.items())
    ]


def _request(lot: float, strategies: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        # El runtime sólo exige un identificador positivo; esta comprobación no
        # guarda ni modifica un portafolio UBS.
        "portfolio_id": 1, "scope": "full_history",
        "portfolio_label": "Portafolio Prop",
        "from_date": PHASE_TWO_FROM, "to_date": PHASE_TWO_TO,
        "models": ["ohlc", "tick"], "use_multiterminal": True,
        "lot": lot, "retries": 3, "strategies": strategies,
    }


def _check_id(response: dict[str, Any], source: Any) -> str:
    request = response.get("request") if isinstance(response, dict) else None
    found = str((request or {}).get("check_id") or "")
    if found:
        return found
    latest = portfolio_manual_check.latest_check(source.project, 1, "full_history")
    found = str((latest or {}).get("check_id") or "")
    if not found:
        raise ValueError("El agente aceptó la validación pero no publicó su identificador")
    return found


def _terminal_state(
    node_id: str, check_id: str, node_request: NodeRequest,
) -> tuple[bool, dict[str, Any]]:
    status, payload = node_request(node_id, "GET", "/api/v1/status", None)
    if status != 200 or not isinstance(payload, dict):
        raise ValueError(f"{node_id}: no se pudo leer el estado de la validación")
    job = payload.get("job") or {}
    request = job.get("request") or {}
    if str(request.get("check_id") or "") != check_id:
        return False, job
    return str(job.get("status") or "") in {
        "completed", "failed", "stopped", "cancelled", "interrupted",
    }, job


def _ensure_available(node_id: str, node_request: NodeRequest) -> None:
    status, payload = node_request(node_id, "GET", "/api/v1/status", None)
    if status != 200 or not isinstance(payload, dict):
        raise ValueError(f"{node_id}: no se pudo comprobar si el agente está libre")
    job_status = str((payload.get("job") or {}).get("status") or "idle")
    queued = int((payload.get("task_queue") or {}).get("count") or 0)
    if job_status in {"running", "paused", "stopping", "pausing"} or queued:
        raise ValueError(
            f"{node_id}: el agente está ocupado; termina su trabajo y vuelve a ejecutar la fase 2"
        )


def validate_portfolio(
    result: dict[str, Any], source_factory: Callable[[str], Any],
    node_request: NodeRequest, checkpoint: Callable[[str], None],
    *, poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """Ejecuta los dos modelos y devuelve sus curvas por estrategia."""
    groups = _validation_groups(result)
    if not groups:
        raise ValueError("El Portafolio Prop no contiene estrategias validables")
    checks: list[dict[str, Any]] = []
    for index, (node_id, lot, strategies) in enumerate(groups, 1):
        _ensure_available(node_id, node_request)
        checkpoint(
            f"Fase 2 · lote {index}/{len(groups)} · {node_id} · "
            f"{lot:.2f} lotes · {len(strategies)} estrategia(s)"
        )
        status, response = node_request(
            node_id, "POST", "/api/v1/jobs/manual-check", _request(lot, strategies),
        )
        if status not in {200, 201, 202} or not isinstance(response, dict):
            detail = response.get("error") if isinstance(response, dict) else response
            raise ValueError(f"{node_id}: no se pudo iniciar la validación: {detail}")
        source = source_factory(node_id)
        check_id = _check_id(response, source)
        terminal = False
        job: dict[str, Any] = {}
        while not terminal:
            checkpoint(f"Fase 2 · {node_id} · esperando {check_id}")
            terminal, job = _terminal_state(node_id, check_id, node_request)
            if not terminal:
                time.sleep(poll_seconds)
        analysis = manual_check_analysis.analysis(source.project, check_id)
        checks.append({
            "node_id": node_id, "lot": lot, "check_id": check_id,
            "status": str(job.get("status") or ""),
            "error": job.get("error"), "analysis": analysis,
        })
    return {
        "status": "completed", "from": PHASE_TWO_FROM, "to": PHASE_TWO_TO,
        "checks": checks,
        "failed_checks": sum(1 for item in checks if item["status"] != "completed"),
    }
