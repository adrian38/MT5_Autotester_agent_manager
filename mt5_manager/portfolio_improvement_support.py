from __future__ import annotations

import uuid
from dataclasses import asdict, replace
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
    PortfolioType,
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


Progress = Callable[[str], None]
MAX_IMPROVEMENT_ADDITIONS = 5
#: Cuántas veces se veta la candidata de relleno y se vuelve a seleccionar antes
#: de darse por vencido con ese tamaño. Cada reintento es una optimización
#: completa; sin tope, un pool grande podría recorrerlo entero.
MAX_FILLER_RETRIES = 3
#: Las claves validas y su etiqueta visible, en un solo sitio: el formulario,
#: la auditoria guardada y el listado tienen que llamar igual a lo mismo.
IMPROVEMENT_SELECTION_PRIORITIES = IMPROVEMENT_PRIORITY_LABELS


def _lineage_from_parent(
    detail: dict[str, Any], portfolio_id: int, target: str,
) -> dict[str, Any]:
    """Build root -> immediate-parent ancestry without trusting local ids alone."""
    metrics = detail.get("metrics") if isinstance(detail.get("metrics"), dict) else {}
    saved = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
    audit = (metrics.get("seasonal_validation") or {}).get("portfolio_improvement") or {}
    origin = detail.get("improvement_origin") if isinstance(detail.get("improvement_origin"), dict) else {}
    parent_source_id = int(
        saved.get("improvement_source_portfolio_id")
        or audit.get("source_portfolio_id")
        or origin.get("source_id")
        or 0
    )
    parent_depth = int(saved.get("improvement_depth") or audit.get("depth") or (1 if parent_source_id else 0))
    root_id = int(
        saved.get("improvement_root_portfolio_id")
        or audit.get("root_portfolio_id")
        or origin.get("root_id")
        or parent_source_id
        or portfolio_id
    )
    parent_uid = _portable_portfolio_uid(detail)
    root_uid = _valid_portfolio_uid(
        saved.get("improvement_root_uid")
        or audit.get("root_uid")
        or origin.get("root_uid")
        or ""
    )
    if not root_uid and root_id == portfolio_id:
        root_uid = parent_uid
    lineage = _normalized_improvement_lineage(
        saved.get("improvement_lineage") or audit.get("lineage") or origin.get("lineage")
    )
    if not lineage and parent_source_id > 0:
        lineage.append({"portfolio_id": parent_source_id, "label": f"Portafolio #{parent_source_id}"})
    if not root_uid and lineage and int(lineage[0].get("portfolio_id") or 0) == root_id:
        root_uid = str(lineage[0].get("portfolio_uid") or "")
    parent_entry = {
        "portfolio_id": portfolio_id,
        "portfolio_uid": parent_uid,
        "label": str(detail.get("name") or f"Portafolio #{portfolio_id}"),
        "mode": target,
    }
    if not lineage or int(lineage[-1].get("portfolio_id") or 0) != portfolio_id:
        lineage.append(parent_entry)
    return {
        "improvement_parent_uid": parent_uid,
        "improvement_root_portfolio_id": root_id,
        "improvement_root_uid": root_uid,
        "improvement_depth": parent_depth + 1,
        "improvement_lineage": lineage,
    }


def minimum_additions(inputs: dict[str, Any]) -> int:
    # Keep the old request key usable for an already-open normal UBS page.
    value = inputs.get("improvement_min_additions", inputs.get("improvement_additions", 2))
    try:
        count = int(value)
        valid = not isinstance(value, bool) and float(value) == count
    except (TypeError, ValueError, OverflowError):
        valid, count = False, 0
    if not valid or not 1 <= count <= MAX_IMPROVEMENT_ADDITIONS:
        raise ValueError("El mínimo de estrategias a añadir debe ser un entero entre 1 y 5")
    return count


def improvement_options(inputs: dict[str, Any]):
    options = _shared_improvement_options(inputs)
    if inputs.get("improvement_min_efficiency_gain_pct") == 0:
        options = replace(options, min_efficiency_gain_pct=0.0)
    return options


def improvement_allowed_groups(inputs: dict[str, Any]) -> list[str]:
    """Grupos de activos de los que puede salir una incorporación.

    Viaja como ``improvement_allowed_asset_groups`` porque el atajo de la
    mejora conserva del formulario **sólo** las claves ``improvement_*``: todo
    lo demás lo impone el portafolio guardado. Sin esta clave propia, la
    selección quedaba congelada a los grupos con los que se generó la base y no
    había forma de abrir uno nuevo desde el diálogo.

    Sólo afecta a las candidatas. Las originales se reincorporan aparte, así
    que quitar un grupo nunca expulsa a un miembro que ya está dentro.
    """
    raw = inputs.get("improvement_allowed_asset_groups")
    if raw is None:
        return sorted(set(inputs.get("allowed_asset_groups") or ASSET_GROUPS))
    if isinstance(raw, str) or not isinstance(raw, (list, tuple, set)):
        raise ValueError("Los grupos permitidos de la mejora deben ser una lista")
    groups = sorted({str(value) for value in raw if str(value) in ASSET_GROUPS})
    if not groups:
        raise ValueError("Selecciona al menos un grupo de activos para la mejora")
    return groups


def improvement_selection_priority(inputs: dict[str, Any]) -> str:
    value = str(inputs.get("improvement_selection_priority") or "balanced").strip().lower()
    if value not in IMPROVEMENT_SELECTION_PRIORITIES:
        raise ValueError(
            "La prioridad de mejora debe ser equilibrada, máxima eficiencia o menor estrés"
        )
    return value


def improvement_margin_profile(inputs: dict[str, Any]) -> str:
    """Perfil financiero con el que se calcula la mejora.

    Viaja como ``improvement_margin_profile`` por lo mismo que los grupos: el
    motor reimpone los ``inputs`` guardados de la base sobre los de la petición
    y de éstos sólo sobreviven las claves ``improvement_*``. Sin clave propia,
    el diálogo podía enseñar el perfil pero no cambiarlo.

    Ausente significa heredar el de la base, que es el comportamiento anterior.
    El perfil decide sólo la política de margen: el lote mínimo y el tamaño de
    contrato siguen siendo los del broker de origen, ver
    ``ai_context/portfolio_broker_min_lot_vs_margin_profile.md``.

    La validación es explícita y no usa ``normalize_margin_profile``, que
    devuelve «roboforex» para cualquier texto que no reconoce: una errata en el
    formulario cambiaría el apalancamiento de la mejora en silencio.
    """
    raw = inputs.get("improvement_margin_profile")
    if raw in (None, ""):
        return str(inputs.get("margin_profile") or "").strip().lower()
    value = str(raw).strip().lower()
    if value not in MARGIN_PROFILES:
        raise ValueError(
            "El perfil de margen de la mejora debe ser ICTRADING, AXI, ROBOFOREX o TTP"
        )
    return value


def improvement_account_leverage(inputs: dict[str, Any]) -> float:
    """Resolve and validate the AXI account leverage used by an improvement."""
    raw = inputs.get("improvement_account_leverage")
    if raw in (None, ""):
        raw = inputs.get("account_leverage", DEFAULT_ACCOUNT_LEVERAGE)
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = 0.0
    if isinstance(raw, bool) or value not in ACCOUNT_LEVERAGE_CHOICES:
        choices = ", ".join(f"1:{int(item)}" for item in ACCOUNT_LEVERAGE_CHOICES)
        raise ValueError(f"El apalancamiento de la mejora debe ser {choices}")
    return value


def improvement_grid_off(inputs: dict[str, Any]) -> bool:
    """Resolve the dialog override, or inherit Grid OFF from the saved base."""
    raw = inputs.get("improvement_grid_off")
    if raw is None:
        return bool(inputs.get("grid_off"))
    if not isinstance(raw, bool):
        raise ValueError("Grid OFF de la mejora debe ser verdadero o falso")
    return raw


def improvement_min_recent_contribution_pct(inputs: dict[str, Any]) -> float:
    """Aporte mínimo Final Tick 6M exigido a **cada incorporación**.

    Clave propia del diálogo por lo mismo que el perfil de margen y Grid OFF: de
    la petición sólo sobreviven al merge las `improvement_*`, así que antes el
    umbral se heredaba en silencio de la variante guardada —5 % por defecto de la
    generación— y no había forma de tocarlo pese a ser la puerta que más rechaza.

    Ausente significa heredar, que es el comportamiento anterior. Un `0`
    explícito desactiva la puerta y queda registrado como tal en la auditoría.

    Incumplirlo no aborta el intento: se veta esa candidata y se vuelve a
    seleccionar, hasta `MAX_FILLER_RETRIES`.
    """
    raw = inputs.get("improvement_min_recent_contribution_pct")
    if raw in (None, ""):
        raw = inputs.get("min_strategy_recent_contribution_pct", 0.0)
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = -1.0
    if isinstance(raw, bool) or not 0.0 <= value <= 100.0:
        raise ValueError(
            "El aporte mínimo Final Tick 6M de la mejora debe estar entre 0 y 100"
        )
    return value


def _stress_direction(probability_delta: float) -> str:
    if probability_delta > 1e-9:
        return "higher"
    if probability_delta < -1e-9:
        return "lower"
    return "unchanged"


def _stress_comparison_payload(
    improved: BootstrapDrawdownAnalysis,
    baseline: BootstrapDrawdownAnalysis,
    priority: str,
) -> dict[str, Any]:
    p95_delta = improved.valley_dd_p95 - baseline.valley_dd_p95
    probability_delta = (
        improved.probability_exceed_effective_pct
        - baseline.probability_exceed_effective_pct
    )
    return {
        "status": "completed",
        "selection_priority": priority,
        "direction": _stress_direction(probability_delta),
        "baseline": asdict(baseline),
        "improved": asdict(improved),
        "valley_dd_p95_delta": round(float(p95_delta), 6),
        "valley_dd_p95_delta_pct": (
            round(float(p95_delta / baseline.valley_dd_p95 * 100.0), 6)
            if baseline.valley_dd_p95 > 0 else None
        ),
        "probability_exceed_effective_delta_pp": round(float(probability_delta), 6),
    }


def _attach_stress_comparison(
    *,
    result: PortfolioResult,
    baseline: PortfolioEvaluation,
    priority: str,
    baseline_stress: BootstrapDrawdownAnalysis | None = None,
) -> BootstrapDrawdownAnalysis | None:
    """Record a like-for-like bootstrap comparison without changing validity."""
    audit = (result.seasonal_validation or {}).get("portfolio_improvement")
    if not isinstance(audit, dict):
        return baseline_stress
    improved = result.stress_bootstrap
    curve = getattr(baseline, "equity_curve_2020_2026", None)
    if improved is None or not curve:
        audit["selection_priority"] = priority
        audit["stress_comparison"] = {
            "status": "unavailable", "selection_priority": priority,
        }
        return baseline_stress
    if baseline_stress is None:
        baseline_stress = bootstrap_valley_drawdown(
            curve,
            nominal_valley_dd_limit=improved.nominal_valley_dd_limit,
            effective_valley_dd_limit=improved.effective_valley_dd_limit,
            simulations=improved.simulations,
            block_size=improved.block_size,
            seed=improved.seed,
        )
    audit["selection_priority"] = priority
    audit["stress_comparison"] = _stress_comparison_payload(
        improved, baseline_stress, priority,
    )
    result.warnings.insert(
        1,
        "Comparativa de estrés frente a la base: "
        f"P95 {baseline_stress.valley_dd_p95:.2f} -> {improved.valley_dd_p95:.2f}; "
        "probabilidad de exceder el DD efectivo "
        f"{baseline_stress.probability_exceed_effective_pct:.1f}% -> "
        f"{improved.probability_exceed_effective_pct:.1f}%. "
        "Dato informativo; la validez sigue determinada por los límites declarados.",
    )
    return baseline_stress


def _improvement_rank(
    proposal: dict[str, Any], additions: int, priority: str
) -> tuple[float, ...]:
    audit = (proposal["result"].seasonal_validation or {}).get(
        "portfolio_improvement", {}
    )
    gain = float(audit.get("efficiency_gain_pct", 0))
    comparison = audit.get("stress_comparison") or {}
    if comparison.get("status") != "completed":
        return (gain, -float(additions))
    improved = comparison.get("improved") or {}
    probability = float(improved.get("probability_exceed_effective_pct") or 0)
    p95 = float(improved.get("valley_dd_p95") or 0)
    delta = float(comparison.get("probability_exceed_effective_delta_pp") or 0)
    if priority == "efficiency":
        return (gain, -probability, -p95, -float(additions))
    if priority == "stress":
        return (-probability, -p95, gain, -float(additions))
    # Balanced is a preference, not a hidden risk limit: first prefer a result
    # whose estimated exceedance probability does not rise; if none exists,
    # choose the smallest increase. Historical efficiency breaks ties.
    if delta <= 1e-9:
        return (1.0, gain, -probability, -float(additions))
    return (0.0, -delta, gain, -float(additions))


def _selected_variant_detail(detail: dict[str, Any], target: str) -> dict[str, Any]:
    members = list(detail.get("members") or [])
    if _is_bundle_portfolio(detail):
        members = [row for row in members if row.get("variant_key") == target]
    elif _saved_single_mode(detail) != target:
        raise ValueError("El portafolio no contiene la variante elegida")
    if not members:
        raise ValueError("No hay estrategias guardadas para la variante elegida")
    return {**detail, "members": members}


def _saved_single_mode(detail: dict[str, Any]) -> str:
    """Resolve the A/M/C mode behind a saved single-mode improvement."""
    metrics = detail.get("metrics") or {}
    inputs = metrics.get("inputs") or {}
    audit = (metrics.get("seasonal_validation") or {}).get("portfolio_improvement") or {}
    origin = detail.get("improvement_origin") or {}
    for raw in (
        origin.get("mode"),
        inputs.get("improvement_portfolio_type"),
        audit.get("target_portfolio_type"),
        detail.get("portfolio_type"),
        inputs.get("portfolio_type"),
    ):
        mode = str(raw or "").strip().lower()
        if mode in PORTFOLIO_TYPES:
            return mode
    return ""


def _load_original_improvement_sets(
    source: PortfolioSource,
    detail: dict[str, Any],
    inputs: dict[str, Any],
    progress: Progress | None,
) -> tuple[list[Any], list[str]]:
    originals = unique_original_members(detail, str(inputs["portfolio_type"]))
    if not originals:
        raise ValueError("El portafolio guardado no contiene una base reconstruible")
    if progress:
        progress(f"1/5 · Reconstruyendo y bloqueando {len(originals)} estrategias originales")
    rows = member_rows(
        originals,
        resolve_path=lambda value: _resolve_source_path(value, source.project),
        project=source.project,
    )
    original_sets, warnings = load_robust_sets_from_rows(rows, [], parse=cached_report)
    recovered = [
        Path(str(row.get("set_path") or "")).name
        for row in rows if row.get("historical_reports_recovered")
    ]
    if recovered:
        warnings.append(
            "Informes recuperados del disco para originales cuyo veredicto cambió "
            "tras guardar el portafolio: " + ", ".join(recovered)
        )
    if len(original_sets) != len(originals):
        raise ValueError(
            "No se pudieron reconstruir todas las estrategias originales; "
            "la mejora no puede retirar ninguna sin evidencia"
        )
    return original_sets, warnings


def _candidate_rows_for_improvement(
    source: PortfolioSource,
    inputs: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows = source.candidate_rows(include_quarantined=False)
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows, min_positive_months=3, window_months=6, parse=cached_report,
        )
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(improvement_allowed_groups(inputs))
    rows = [
        row for row in rows
        if portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        ) in allowed
    ]
    return filter_rows_by_disabled_symbols(
        rows,
        inputs.get("improvement_disabled_symbols", inputs.get("disabled_symbols")),
        universe_files=[source.universe],
    )


def _load_full_history_improvement_pool(
    source: PortfolioSource,
    detail: dict[str, Any],
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None,
) -> tuple[list[Any], list[Any], list[dict[str, Any]], list[str], list[str]]:
    original_sets, warnings = _load_original_improvement_sets(
        source, detail, inputs, progress,
    )
    if progress:
        progress("2/5 · Aplicando el embudo de cuatro etapas y los filtros guardados")
    rows = _candidate_rows_for_improvement(source, inputs, warnings)
    options = improvement_options(inputs)
    used = (
        used_paths_for_improvement(source, "full_history", portfolio_id)
        if options.exclude_used_sets else []
    )
    if progress:
        progress(f"3/5 · Cargando reportes de {len(rows)} candidatos nuevos")
    candidate_sets, found = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(found)
    original_ids = {strategy.set_id for strategy in original_sets}
    candidate_sets = recent_positive_candidates(candidate_sets, original_ids)
    by_id = {strategy.set_id: strategy for strategy in candidate_sets}
    by_id.update({strategy.set_id: strategy for strategy in original_sets})
    return original_sets, list(by_id.values()), rows, used, warnings
