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


ENGINE_LABEL = "chain"


@dataclass(frozen=True)
class _AttemptContext:
    source: PortfolioSource
    portfolio_id: int
    target: str
    detail: dict[str, Any]
    lineage: dict[str, Any]
    inputs: dict[str, Any]
    options: Any
    original_sets: list[Any]
    raw_sets: list[Any]
    rows: list[dict[str, Any]]
    used: list[str]
    warnings: list[str]
    original_ids: list[str]
    minimum_target: int
    maximum_target: int
    base_type: PortfolioType
    reserve: float
    existing: list[Any]
    variant_existing: list[Any]


def _merged_attempt_inputs(
    source: PortfolioSource,
    requested: dict[str, Any],
    saved_inputs: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    inputs = {
        **requested,
        **saved_inputs,
        **{
            key: value for key, value in requested.items()
            if key.startswith("improvement_") or key.startswith("_improvement_")
        },
        "portfolio_type": target,
        "use_correlation": True,
    }
    profile = improvement_margin_profile(inputs)
    if profile:
        inputs["margin_profile"] = profile
    inputs["account_leverage"] = improvement_account_leverage(inputs)
    inputs["grid_off"] = improvement_grid_off(inputs)
    minimum_recent_pct = improvement_min_recent_contribution_pct(inputs)
    inputs["min_strategy_recent_contribution_pct"] = minimum_recent_pct
    inputs["margin_model"] = build_margin_model(source, inputs)
    return inputs


def _attempt_context(
    source: PortfolioSource,
    portfolio_id: int,
    requested: dict[str, Any],
    progress: Progress | None,
) -> _AttemptContext:
    target = str(requested["portfolio_type"])
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    lineage = _lineage_from_parent(detail, portfolio_id, target)
    variant = (detail.get("metrics") or {}).get("variants", {}).get(target, {})
    saved_inputs = variant.get("inputs") or {}
    inputs = _merged_attempt_inputs(source, requested, saved_inputs, target)
    options = improvement_options(inputs)
    detail = _selected_variant_detail(detail, target)
    original_sets, raw_sets, rows, used, warnings = _load_full_history_improvement_pool(
        source, detail, portfolio_id, inputs, progress,
    )
    original_ids = [strategy.set_id for strategy in original_sets]
    minimum_target = len(original_ids) + (
        options.max_additions if inputs.get("_improvement_exact_additions") else 1
    )
    maximum_target = len(original_ids) + options.max_additions
    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
    reserve = (
        float(saved_inputs["dd_reserve_pct"])
        if "dd_reserve_pct" in saved_inputs
        else _reserve_pct(configured_reserve, base_type)
    )
    curves = dict(monthly=False, portfolio_type=base_type, exclude_portfolio_id=portfolio_id)
    existing = source.saved_curves(**curves)
    variant_existing = source.saved_curves(**curves)
    return _AttemptContext(
        source, portfolio_id, target, detail, lineage, inputs, options,
        original_sets, raw_sets, rows, used, warnings, original_ids,
        minimum_target, maximum_target, base_type, reserve, existing,
        variant_existing,
    )


def _available_pool(
    context: _AttemptContext,
    banned: set[str],
    rejected_fillers: list[str],
) -> list[Any]:
    pool = [item for item in context.raw_sets if item.set_id not in banned]
    if len(pool) >= context.minimum_target:
        return pool
    minimum_pct = context.inputs["min_strategy_recent_contribution_pct"]
    if banned:
        names = ", ".join(
            Path(value).name for value in sorted(set(rejected_fillers))
        )
        raise ValueError(
            "Las incorporaciones no alcanzan el aporte mínimo Final Tick 6M "
            f"de {minimum_pct:.1f}%: {names}. Se agotaron las candidatas tras "
            f"vetar {len(banned)}. Baja ese mínimo en el diálogo si quieres "
            "admitir aportaciones más pequeñas"
        )
    raise ValueError(
        f"Solo hay {len(pool) - len(context.original_ids)} candidatas nuevas con "
        "aporte Final Tick 6M positivo; se necesitan "
        f"{context.minimum_target - len(context.original_ids)}"
    )


def _selection_kwargs(context: _AttemptContext) -> dict[str, Any]:
    inputs, options = context.inputs, context.options
    kwargs = _optimizer_kwargs(
        inputs, context.base_type, context.existing, context.reserve,
    )
    return optimizer_overrides(kwargs, **{
        "required_set_ids": context.original_ids,
        "preserve_required_allocations": False,
        "minimum_active_strategies": context.minimum_target,
        "maximum_active_strategies": context.maximum_target,
        "prefer_breadth_below_minimum": True,
        "max_sets_per_group": context.maximum_target,
        "top_k_per_symbol": max(
            int(inputs["top_k_per_symbol"]), context.maximum_target,
        ),
        "max_sets_per_symbol": (
            context.maximum_target
            if options.allow_same_symbol else int(inputs["max_sets_per_symbol"])
        ),
        "run_local_search": False,
        "search_restarts": 0,
    })


def _select_composition(
    context: _AttemptContext,
    pool: list[Any],
    retry: int,
    banned: set[str],
    progress: Progress | None,
) -> tuple[list[str], list[Any], int]:
    if progress:
        progress(
            f"4/5 · Buscando {context.options.max_additions} incorporación(es) "
            "con baja dependencia"
            + (f" · reintento {retry} tras vetar {len(banned)}" if banned else "")
        )
    kwargs = _selection_kwargs(context)
    selected = optimize_portfolio(
        raw_sets=pool,
        **{**kwargs, "search": kwargs["search"].with_deep_refinement(False)},
    )
    selected_ids = [
        allocation.set_id for allocation in selected.allocations
        if allocation.units > 0
    ]
    if not set(context.original_ids).issubset(selected_ids):
        raise ValueError("El selector intentó retirar una estrategia original")
    actual_additions = len(selected_ids) - len(context.original_ids)
    if not context.minimum_target <= len(selected_ids) <= context.maximum_target:
        raise ValueError(
            f"El selector añadió {actual_additions} estrategias; este intento "
            f"requiere {context.minimum_target - len(context.original_ids)}"
        )
    by_id = {strategy.set_id: strategy for strategy in pool}
    return selected_ids, [by_id[set_id] for set_id in selected_ids], actual_additions


def _optimize_selected(
    context: _AttemptContext,
    selected_ids: list[str],
    selected_sets: list[Any],
    progress: Progress | None,
) -> PortfolioResult:
    if progress:
        progress("5/5 · Validando beneficio/DD de la variante elegida")
    inputs, selected_target = context.inputs, len(selected_ids)
    kwargs = _optimizer_kwargs(
        inputs, context.base_type, context.variant_existing, context.reserve,
    )
    kwargs = optimizer_overrides(kwargs, **{
        "required_set_ids": selected_ids,
        "preserve_required_allocations": False,
        "minimum_active_strategies": selected_target,
        "maximum_active_strategies": selected_target,
        "top_k_per_symbol": selected_target,
        "max_total_candidates": None,
        "max_sets_per_symbol": (
            selected_target
            if context.options.allow_same_symbol
            else int(inputs["max_sets_per_symbol"])
        ),
        "max_sets_per_group": selected_target,
        "group_unit_cap_bootstrap": max(selected_target, 1),
    })
    return optimize_portfolio(
        raw_sets=selected_sets,
        **{
            **kwargs,
            "search": kwargs["search"].with_deep_refinement(
                bool(inputs.get("deep_optimization"))
            ),
        },
    )


def _search_valid_composition(
    context: _AttemptContext,
    progress: Progress | None,
) -> tuple[PortfolioResult, list[str], list[Any], int, list[str]]:
    banned: set[str] = set()
    rejected: list[str] = []
    minimum_pct = context.inputs["min_strategy_recent_contribution_pct"]
    for retry in range(MAX_FILLER_RETRIES + 1):
        pool = _available_pool(context, banned, rejected)
        selected_ids, selected_sets, additions = _select_composition(
            context, pool, retry, banned, progress,
        )
        result = _optimize_selected(context, selected_ids, selected_sets, progress)
        fillers = _underrepresented_recent_allocation_ids(
            result, minimum_pct,
        ) - set(context.original_ids)
        if not fillers:
            return result, selected_ids, selected_sets, additions, rejected
        rejected.extend(sorted(fillers))
        banned |= fillers
        if retry >= MAX_FILLER_RETRIES:
            names = ", ".join(Path(value).name for value in sorted(fillers))
            raise ValueError(
                "Las incorporaciones no alcanzan el aporte mínimo Final Tick 6M "
                f"de {minimum_pct:.1f}% tras {MAX_FILLER_RETRIES} reintento(s) "
                f"vetando candidatas: {names}. Baja ese mínimo en el diálogo si "
                "quieres admitir aportaciones más pequeñas"
            )
    raise AssertionError("Bucle de reintentos agotado sin resultado")


def _baseline(context: _AttemptContext, result: PortfolioResult) -> PortfolioEvaluation:
    resolve_path = lambda value: _resolve_source_path(value, context.source.project)
    return evaluate_portfolio(
        context.original_sets,
        allocation_units(context.detail, context.target, resolve_path=resolve_path),
        result.target_valley_dd,
        result.target_point_dd,
        enforce_point_dd=False,
    )


def _source_snapshot(
    context: _AttemptContext,
    baseline: PortfolioEvaluation,
) -> dict[str, Any]:
    detail, lineage = context.detail, context.lineage
    return {
        "id": context.portfolio_id,
        "portfolio_uid": lineage["improvement_parent_uid"],
        "portfolio_type": context.target,
        "label": str(detail.get("name") or f"Portafolio #{context.portfolio_id}"),
        "improvement_origin": dict(detail.get("improvement_origin") or {}),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capital": float(context.inputs["capital"]),
        "total_net_profit": float(baseline.total_net_profit),
        "actual_valley_dd": float(baseline.valley_dd),
        "total_units": sum(int(row.get("units") or 0) for row in detail["members"]),
        "total_lot": sum(float(row.get("lot") or 0) for row in detail["members"]),
        "active_strategies": len(context.original_ids),
        "members": [dict(member) for member in detail["members"]],
    }


def _attach_attempt_audit(
    context: _AttemptContext,
    result: PortfolioResult,
    baseline: PortfolioEvaluation,
    selected_sets: list[Any],
    actual_additions: int,
    rejected: list[str],
) -> dict[str, Any]:
    audit = validate_and_attach_improvement_audit(
        result=result,
        baseline=baseline,
        all_sets=selected_sets,
        original_ids=context.original_ids,
        options=context.options,
        inputs=context.inputs,
        scope="full_history",
        minimum_gain_pct=context.options.min_efficiency_gain_pct,
    )
    if audit["added_count"] != actual_additions:
        raise ValueError("El ajuste final no conservó las incorporaciones seleccionadas")
    inputs, lineage = context.inputs, context.lineage
    audit.update({
        "target_portfolio_type": context.base_type.value,
        "target_portfolio_type_label": TYPE_LABELS[context.base_type.value],
        "margin_profile": str(inputs.get("margin_profile") or ""),
        "account_leverage": float(inputs.get("account_leverage") or 0),
        "grid_off": bool(inputs.get("grid_off")),
        "min_recent_contribution_pct": inputs["min_strategy_recent_contribution_pct"],
        "recent_contribution_rejections": [Path(value).name for value in rejected],
        "engine": ENGINE_LABEL,
        "source_portfolio_id": context.portfolio_id,
        "save_as_new": True,
        "portfolio_uid": str(inputs["_improvement_portfolio_uid"]),
        "label": f"Mejora del portafolio #{context.portfolio_id} | modo "
                 f"{TYPE_LABELS[context.target]}",
        "parent_uid": lineage["improvement_parent_uid"],
        "root_portfolio_id": lineage["improvement_root_portfolio_id"],
        "root_uid": lineage["improvement_root_uid"],
        "depth": lineage["improvement_depth"],
        "lineage": lineage["improvement_lineage"],
    })
    audit["source_snapshot"] = _source_snapshot(context, baseline)
    return audit


def _proposal(
    context: _AttemptContext,
    result: PortfolioResult,
    baseline: PortfolioEvaluation,
    audit: dict[str, Any],
    actual_additions: int,
) -> dict[str, Any]:
    inputs = settings_inputs(context.inputs)
    inputs.update({
        "optimization_profile": context.target,
        "optimization_profile_label": TYPE_LABELS[context.target],
        "portfolio_type": context.base_type.value,
        "portfolio_type_label": TYPE_LABELS[context.base_type.value],
        "composition_portfolio_type": context.base_type.value,
        "composition_portfolio_type_label": TYPE_LABELS[context.base_type.value],
        "dd_reserve_pct": context.reserve,
        "improvement_source_portfolio_id": context.portfolio_id,
        "improvement_portfolio_type": context.target,
        "improvement_original_count": len(context.original_ids),
        "improvement_added_count": actual_additions,
        "improvement_max_additions": context.options.max_additions,
        "improvement_min_recent_contribution_pct": (
            context.inputs["min_strategy_recent_contribution_pct"]
        ),
        "portfolio_uid": str(context.inputs["_improvement_portfolio_uid"]),
        "improvement_label": audit["label"],
        **context.lineage,
    })
    return {
        "key": context.target,
        "label": TYPE_LABELS[context.target],
        "reserve_pct": context.reserve,
        "inputs": inputs,
        "result": result,
        "_improvement_baseline": baseline,
    }


def _availability(
    context: _AttemptContext,
    actual_additions: int,
    rejected: list[str],
    selected_ids: list[str],
) -> dict[str, Any]:
    availability = asdict(summarize_robust_rows(context.rows, context.used))
    inputs = context.inputs
    availability.update({
        "loaded_sets": len(context.raw_sets),
        "warnings": context.warnings,
        "improvement": {
            "engine": ENGINE_LABEL,
            "originals_locked": len(context.original_ids),
            "maximum_additions": context.options.max_additions,
            "actual_additions": actual_additions,
            "margin_profile": str(inputs.get("margin_profile") or ""),
            "account_leverage": float(inputs.get("account_leverage") or 0),
            "grid_off": bool(inputs.get("grid_off")),
            "min_recent_contribution_pct": inputs["min_strategy_recent_contribution_pct"],
            "recent_contribution_rejections": [Path(value).name for value in rejected],
            "selected_set_names": [Path(value).name for value in selected_ids],
        },
    })
    return availability


def _generate_full_history_improvement_attempt(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Improve only the selected saved variant and propose a new portfolio."""
    context = _attempt_context(source, portfolio_id, inputs, progress)
    result, selected_ids, selected_sets, additions, rejected = (
        _search_valid_composition(context, progress)
    )
    baseline = _baseline(context, result)
    audit = _attach_attempt_audit(
        context, result, baseline, selected_sets, additions, rejected,
    )
    _seasonal_coverage(result, selected_sets)
    result.warnings.extend(context.warnings)
    proposal = _proposal(context, result, baseline, audit, additions)
    availability = _availability(context, additions, rejected, selected_ids)
    return availability, [proposal]
