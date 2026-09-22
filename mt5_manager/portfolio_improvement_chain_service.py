"""Motor de **mejora sobre una mejora** (UBS normal, cadena de profundidad >= 2).

Por qué existe este fichero
---------------------------
`portfolio_improvement_service.py` mejora un portafolio **base** y funciona. Una
mejora de una mejora es otro problema: cada generación sube el beneficio 6M total
y consume el hueco de DD, así que la candidata número N+1 entra con muy pocas
unidades. La puerta de aporte mínimo Final Tick 6M es **relativa al total**, de
modo que el mismo umbral que es razonable al construir de cero se vuelve
inalcanzable al ampliar una cartera ya ampliada.

Corregir eso dentro del motor base habría cambiado el comportamiento de la mejora
que ya funciona. Por petición explícita del usuario, la cadena vive aquí, con su
propia copia de la orquestación; el motor base queda intacto.

Estado actual: el usuario pidió después llevar también al motor base el umbral
elegible (`improvement_min_recent_contribution_pct`) y el reintento vetando la
candidata de relleno. Así que **hoy los dos intentos son el mismo algoritmo** y
esta copia no aporta ninguna diferencia de comportamiento; lo único que cambia es
la etiqueta `engine`.

Eso no la hace inútil: es el sitio donde cambiar la cadena sin tocar la base. Pero
mientras no diverja de verdad, cualquier arreglo tiene que entrar en las dos, y lo
vigila `ChainForkParityTests` en `tests/test_portfolio_improvement_chain.py`,
que compara por AST las funciones copiadas y el intento completo. El día que esta
copia se separe a conciencia, se quita esa prueba y se documenta el porqué en
`ai_context/portfolio_saved_base_improvement.md`.

La orquestación es la de siempre: originales bloqueadas, búsqueda desde el mínimo
de incorporaciones hasta el límite, prioridad de selección, comparación de estrés
y persistencia como otro portafolio.

El cálculo lo ejecuta el proceso manager; el nodo sólo persiste los inputs ya
serializados, así que este fichero **no requiere port a `manager_node_runtime/`**.
El mensual permanece congelado y no usa esta rama.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    optimizer_overrides,
    MARGIN_PROFILES,
    BootstrapDrawdownAnalysis,
    PortfolioEvaluation,
    PortfolioResult,
    bootstrap_valley_drawdown,
    evaluate_portfolio,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    optimize_portfolio,
    portfolio_group_key,
    summarize_robust_rows,
)

from .portfolio_improvement_common import (
    allocation_units,
    improvement_options as _shared_improvement_options,
    member_rows,
    recent_positive_candidates,
    unique_original_members,
    used_paths_for_improvement,
    validate_and_attach_improvement_audit,
)
from .portfolio_service import (
    ACCOUNT_LEVERAGE_CHOICES,
    ASSET_GROUPS,
    DEFAULT_ACCOUNT_LEVERAGE,
    IMPROVEMENT_PRIORITY_LABELS,
    PORTFOLIO_TYPES,
    TYPE_LABELS,
    PortfolioSource,
    _is_bundle_portfolio,
    _normalized_improvement_lineage,
    _optimizer_kwargs,
    _portable_portfolio_uid,
    _valid_portfolio_uid,
    _resolve_source_path,
    _reserve_pct,
    _seasonal_coverage,
    _underrepresented_recent_allocation_ids,
    build_margin_model,
    cached_report,
    filter_rows_by_disabled_symbols,
    settings_inputs,
)


from .portfolio_improvement_chain_support import (
    IMPROVEMENT_SELECTION_PRIORITIES,
    MAX_FILLER_RETRIES,
    MAX_IMPROVEMENT_ADDITIONS,
    Progress,
    _attach_stress_comparison,
    _improvement_rank,
    _lineage_from_parent,
    _load_full_history_improvement_pool,
    _saved_single_mode,
    _selected_variant_detail,
    improvement_account_leverage,
    improvement_allowed_groups,
    improvement_grid_off,
    improvement_margin_profile,
    improvement_min_recent_contribution_pct,
    improvement_options,
    improvement_selection_priority,
    minimum_additions,
)


from . import portfolio_improvement_chain_attempt as _attempt_engine


def _generate_full_history_improvement_attempt(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _attempt_engine._generate_full_history_improvement_attempt(
        source, portfolio_id, inputs, progress,
    )


@dataclass(frozen=True)
class _ImprovementSearch:
    inputs: dict[str, Any]
    target_type: str
    requested: int
    priority: str
    allowed_groups: list[str]


def _prepare_improvement_search(inputs: dict[str, Any]) -> _ImprovementSearch:
    target_type = str(
        inputs.get("improvement_portfolio_type")
        or inputs.get("portfolio_type")
        or "balanced"
    ).strip().lower()
    if target_type not in PORTFOLIO_TYPES:
        raise ValueError("Elige la variante a mejorar: Agresivo, Moderado o Conservador")
    inputs = {
        **inputs,
        "portfolio_type": target_type,
        "improvement_portfolio_type": target_type,
    }
    requested = minimum_additions(inputs)
    priority = improvement_selection_priority(inputs)
    allowed_groups = improvement_allowed_groups(inputs)
    margin_profile = improvement_margin_profile(inputs)
    account_leverage = improvement_account_leverage(inputs)
    grid_off = improvement_grid_off(inputs)
    if "improvement_min_recent_contribution_pct" in inputs:
        inputs["improvement_min_recent_contribution_pct"] = (
            improvement_min_recent_contribution_pct(inputs)
        )
    inputs["improvement_min_additions"] = requested
    inputs["improvement_allowed_asset_groups"] = allowed_groups
    if inputs.get("improvement_margin_profile"):
        inputs["improvement_margin_profile"] = margin_profile
    if "improvement_account_leverage" in inputs:
        inputs["improvement_account_leverage"] = account_leverage
    if "improvement_grid_off" in inputs:
        inputs["improvement_grid_off"] = grid_off
    inputs.setdefault("_improvement_portfolio_uid", str(uuid.uuid4()))
    return _ImprovementSearch(
        inputs, target_type, requested, priority, allowed_groups,
    )


def _decorate_improvement_attempt(
    availability: dict[str, Any],
    proposals: list[dict[str, Any]],
    search: _ImprovementSearch,
    baseline_stress: BootstrapDrawdownAnalysis | None,
) -> BootstrapDrawdownAnalysis | None:
    improvement = availability.setdefault("improvement", {})
    improvement.update({
        "minimum_additions": search.requested,
        "maximum_additions": MAX_IMPROVEMENT_ADDITIONS,
        "selection_priority": search.priority,
        "allowed_asset_groups": list(search.allowed_groups),
    })
    for proposal in proposals:
        proposal_inputs = proposal.setdefault("inputs", {})
        proposal_inputs.update({
            "improvement_min_additions": search.requested,
            "improvement_max_additions": MAX_IMPROVEMENT_ADDITIONS,
            "improvement_selection_priority": search.priority,
            "improvement_allowed_asset_groups": list(search.allowed_groups),
        })
        effective_grid_off = bool(proposal_inputs.get("grid_off"))
        if "improvement_grid_off" in search.inputs:
            proposal_inputs["improvement_grid_off"] = effective_grid_off
        baseline = proposal.pop("_improvement_baseline", None)
        if baseline is not None:
            baseline_stress = _attach_stress_comparison(
                result=proposal["result"],
                baseline=baseline,
                priority=search.priority,
                baseline_stress=baseline_stress,
            )
        audit = (proposal["result"].seasonal_validation or {}).get(
            "portfolio_improvement"
        )
        if isinstance(audit, dict):
            audit["minimum_additions"] = search.requested
            audit["maximum_additions"] = MAX_IMPROVEMENT_ADDITIONS
            audit["grid_off"] = effective_grid_off
    return baseline_stress


def generate_full_history_chain_improvement(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compare valid addition counts for the selected mode; prefer fewer on ties."""
    search = _prepare_improvement_search(inputs)
    failures: list[str] = []
    best = None
    best_rank: tuple[float, ...] | None = None
    baseline_stress: BootstrapDrawdownAnalysis | None = None
    for additions in range(search.requested, MAX_IMPROVEMENT_ADDITIONS + 1):
        if progress:
            progress(
                f"Mejora {TYPE_LABELS[search.target_type]} · probando "
                f"{additions} incorporación(es) con mínimo {search.requested} "
                f"y límite de búsqueda {MAX_IMPROVEMENT_ADDITIONS}"
            )
        attempt_inputs = {
            **search.inputs,
            "improvement_additions": additions,
            "_improvement_exact_additions": True,
        }
        try:
            availability, proposals = _generate_full_history_improvement_attempt(
                source, portfolio_id, attempt_inputs, progress,
            )
        except ValueError as exc:
            failures.append(f"{additions}: {exc}")
            continue
        baseline_stress = _decorate_improvement_attempt(
            availability, proposals, search, baseline_stress,
        )
        rank = (
            _improvement_rank(proposals[0], additions, search.priority)
            if proposals else (float("-inf"),)
        )
        if best is None or best_rank is None or rank > best_rank:
            best, best_rank = (availability, proposals), rank
    if best is not None:
        return best
    detail = "; ".join(failures) if failures else "sin candidatas válidas"
    raise ValueError(
        f"No se encontró una mejora válida con al menos {search.requested} "
        f"estrategias nuevas (límite de búsqueda: {MAX_IMPROVEMENT_ADDITIONS}). "
        f"No se rebaja el mínimo. Intentos: {detail}"
    )
