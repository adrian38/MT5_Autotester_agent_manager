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
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
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
    settings_inputs,
)


Progress = Callable[[str], None]
MAX_IMPROVEMENT_ADDITIONS = 5
#: Cuántas veces se veta la candidata de relleno y se vuelve a seleccionar antes
#: de darse por vencido con ese tamaño. Cada reintento es una optimización
#: completa; sin tope, un pool grande podría recorrerlo entero.
MAX_FILLER_RETRIES = 3
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
    mejora conserva del formulario **sólo** las claves ``improvement_*``.
    Sólo afecta a las candidatas: las originales se reincorporan aparte.
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

    Ausente significa heredar el de la base. La validación es explícita y no usa
    ``normalize_margin_profile``, que devuelve «roboforex» para cualquier texto
    desconocido: una errata cambiaría el apalancamiento en silencio.
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

    Clave propia del diálogo de cadena. Sin ella el valor se heredaba de la
    variante guardada —5 % por defecto de la generación— y era invisible e
    intocable, pese a ser la puerta que rechazaba todos los intentos de una
    cartera ya mejorada: el umbral se mide contra el beneficio 6M del portafolio
    **completo**, que crece con cada generación mientras el hueco de lotaje para
    la novata se encoge.

    Ausente significa heredar, que es el comportamiento del motor base. Un `0`
    explícito desactiva la puerta, y queda registrado como tal en la auditoría.
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
            "status": "unavailable",
            "selection_priority": priority,
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
    p95_delta = improved.valley_dd_p95 - baseline_stress.valley_dd_p95
    probability_delta = (
        improved.probability_exceed_effective_pct
        - baseline_stress.probability_exceed_effective_pct
    )
    if probability_delta > 1e-9:
        direction = "higher"
    elif probability_delta < -1e-9:
        direction = "lower"
    else:
        direction = "unchanged"
    audit["selection_priority"] = priority
    audit["stress_comparison"] = {
        "status": "completed",
        "selection_priority": priority,
        "direction": direction,
        "baseline": asdict(baseline_stress),
        "improved": asdict(improved),
        "valley_dd_p95_delta": round(float(p95_delta), 6),
        "valley_dd_p95_delta_pct": (
            round(float(p95_delta / baseline_stress.valley_dd_p95 * 100.0), 6)
            if baseline_stress.valley_dd_p95 > 0
            else None
        ),
        "probability_exceed_effective_delta_pp": round(float(probability_delta), 6),
    }
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


def _load_full_history_improvement_pool(
    source: PortfolioSource,
    detail: dict[str, Any],
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None,
) -> tuple[list[Any], list[Any], list[dict[str, Any]], list[str], list[str]]:
    base_key = str(inputs["portfolio_type"])
    originals = unique_original_members(detail, base_key)
    if not originals:
        raise ValueError("El portafolio guardado no contiene una base reconstruible")
    if progress:
        progress(f"1/5 · Reconstruyendo y bloqueando {len(originals)} estrategias originales")
    original_sets, warnings = load_robust_sets_from_rows(
        member_rows(
            originals,
            resolve_path=lambda value: _resolve_source_path(value, source.project),
        ),
        [],
        parse=cached_report,
    )
    if len(original_sets) != len(originals):
        raise ValueError(
            "No se pudieron reconstruir todas las estrategias originales; "
            "la mejora no puede retirar ninguna sin evidencia"
        )

    if progress:
        progress("2/5 · Aplicando el embudo de cuatro etapas y los filtros guardados")
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
        row
        for row in rows
        if portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        )
        in allowed
    ]
    options = improvement_options(inputs)
    used = (
        used_paths_for_improvement(source, "full_history", portfolio_id)
        if options.exclude_used_sets
        else []
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


def _generate_full_history_improvement_attempt(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Improve only the selected saved variant of an already-improved portfolio."""
    target = str(inputs["portfolio_type"])
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    lineage = _lineage_from_parent(detail, portfolio_id, target)
    variant = (detail.get("metrics") or {}).get("variants", {}).get(target, {})
    saved_inputs = variant.get("inputs") or {}
    inputs = {
        **inputs,
        **saved_inputs,
        **{key: value for key, value in inputs.items() if key.startswith("improvement_") or key.startswith("_improvement_")},
        "portfolio_type": target,
        "use_correlation": True,
    }
    profile = improvement_margin_profile(inputs)
    if profile:
        inputs["margin_profile"] = profile
    inputs["account_leverage"] = improvement_account_leverage(inputs)
    inputs["grid_off"] = improvement_grid_off(inputs)
    # La puerta que bloqueaba la cadena. Se resuelve aquí, después del merge, por
    # lo mismo que el perfil: la clave `improvement_*` es la única que sobrevive
    # a que se reimpongan los inputs de la variante guardada.
    minimum_recent_pct = improvement_min_recent_contribution_pct(inputs)
    inputs["min_strategy_recent_contribution_pct"] = minimum_recent_pct
    inputs["margin_model"] = build_margin_model(source, inputs)
    options = improvement_options(inputs)
    detail = _selected_variant_detail(detail, target)
    original_sets, raw_sets, rows, used, warnings = _load_full_history_improvement_pool(
        source, detail, portfolio_id, inputs, progress,
    )
    original_ids = [strategy.set_id for strategy in original_sets]
    resolve_saved_path = lambda value: _resolve_source_path(value, source.project)
    minimum_target = (
        len(original_ids) + options.max_additions
        if inputs.get("_improvement_exact_additions")
        else len(original_ids) + 1
    )
    maximum_target = len(original_ids) + options.max_additions

    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
    selection_reserve = float(saved_inputs["dd_reserve_pct"]) if "dd_reserve_pct" in saved_inputs else _reserve_pct(configured_reserve, base_type)
    existing = source.saved_curves(
        monthly=False,
        portfolio_type=base_type,
        exclude_portfolio_id=portfolio_id,
    )
    key, label, portfolio_type = target, TYPE_LABELS[target], base_type
    reserve = selection_reserve
    variant_existing = source.saved_curves(
        monthly=False,
        portfolio_type=portfolio_type,
        exclude_portfolio_id=portfolio_id,
    )

    # Vetar una candidata de relleno y volver a seleccionar. El motor base
    # abortaba el tamaño entero al primer rechazo; aquí la composición siguiente
    # del mismo tamaño sí se prueba, que es justo lo que faltaba para que una
    # cartera ya mejorada pudiera crecer otra vez.
    banned: set[str] = set()
    rejected_fillers: list[str] = []
    for retry in range(MAX_FILLER_RETRIES + 1):
        pool = [strategy for strategy in raw_sets if strategy.set_id not in banned]
        if len(pool) < minimum_target:
            # Quedarse sin pool *por haber vetado* no es escasez de candidatas:
            # la causa es el umbral, y el mensaje tiene que decir eso y no
            # «solo hay 0 candidatas», que manda a buscar donde no está.
            if banned:
                raise ValueError(
                    "Las incorporaciones no alcanzan el aporte mínimo Final Tick 6M "
                    f"de {minimum_recent_pct:.1f}%: "
                    + ", ".join(
                        Path(value).name for value in sorted(set(rejected_fillers))
                    )
                    + f". Se agotaron las candidatas tras vetar {len(banned)}. Baja "
                    "ese mínimo en el diálogo si quieres admitir aportaciones más "
                    "pequeñas"
                )
            raise ValueError(
                f"Solo hay {len(pool) - len(original_ids)} candidatas nuevas con aporte "
                f"Final Tick 6M positivo; se necesitan {minimum_target - len(original_ids)}"
            )

        selector_kwargs = _optimizer_kwargs(inputs, base_type, existing, selection_reserve)
        selector_kwargs.update(
            {
                "required_set_ids": original_ids,
                "preserve_required_allocations": False,
                "minimum_active_strategies": minimum_target,
                "maximum_active_strategies": maximum_target,
                # Aquí el objetivo es colocar el número pedido de incorporaciones,
                # no sacar el máximo de la siguiente. Sin esto la holgura se gasta
                # en la candidata más rentable y las demás no entran.
                "prefer_breadth_below_minimum": True,
                # Sin esto la mejora hereda el tope de sets por grupo del perfil,
                # pensado para construir de cero, no para ampliar una cartera que
                # ya lo agota. La concentración sigue acotada por
                # `max_units_per_group_pct`, que no se toca.
                "max_sets_per_group": maximum_target,
                "top_k_per_symbol": max(int(inputs["top_k_per_symbol"]), maximum_target),
                "max_sets_per_symbol": (
                    maximum_target
                    if options.allow_same_symbol
                    else int(inputs["max_sets_per_symbol"])
                ),
                # This pass chooses only the composition. Lot refinement is
                # performed below once the selected composition is fixed.
                "run_local_search": False,
                "search_restarts": 0,
            }
        )
        if progress:
            progress(
                f"4/5 · Buscando {options.max_additions} incorporación(es) con baja dependencia"
                + (f" · reintento {retry} tras vetar {len(banned)}" if banned else "")
            )
        selected_base = optimize_portfolio(
            raw_sets=pool,
            use_deep_refinement=False,
            **selector_kwargs,
        )
        selected_ids = [
            allocation.set_id
            for allocation in selected_base.allocations
            if allocation.units > 0
        ]
        if not set(original_ids).issubset(selected_ids):
            raise ValueError("El selector intentó retirar una estrategia original")
        actual_additions = len(selected_ids) - len(original_ids)
        if not minimum_target <= len(selected_ids) <= maximum_target:
            raise ValueError(
                f"El selector añadió {actual_additions} estrategias; este intento requiere "
                f"{minimum_target - len(original_ids)}"
            )
        selected_target = len(selected_ids)
        raw_by_id = {strategy.set_id: strategy for strategy in pool}
        selected_sets = [raw_by_id[set_id] for set_id in selected_ids]

        if progress:
            progress("5/5 · Validando beneficio/DD de la variante elegida")
        kwargs = _optimizer_kwargs(inputs, portfolio_type, variant_existing, reserve)
        kwargs.update(
            {
                "required_set_ids": selected_ids,
                "preserve_required_allocations": False,
                "minimum_active_strategies": selected_target,
                "maximum_active_strategies": selected_target,
                "top_k_per_symbol": selected_target,
                "max_total_candidates": None,
                "max_sets_per_symbol": (
                    selected_target
                    if options.allow_same_symbol
                    else int(inputs["max_sets_per_symbol"])
                ),
                "max_sets_per_group": selected_target,
                "group_unit_cap_bootstrap": max(selected_target, 1),
            }
        )
        result: PortfolioResult = optimize_portfolio(
            raw_sets=selected_sets,
            use_deep_refinement=bool(inputs.get("deep_optimization")),
            **kwargs,
        )
        fillers = _underrepresented_recent_allocation_ids(
            result, minimum_recent_pct,
        ) - set(original_ids)
        if not fillers:
            break
        names = ", ".join(Path(value).name for value in sorted(fillers))
        rejected_fillers.extend(sorted(fillers))
        banned |= fillers
        if retry >= MAX_FILLER_RETRIES:
            raise ValueError(
                "Las incorporaciones no alcanzan el aporte mínimo Final Tick 6M "
                f"de {minimum_recent_pct:.1f}% tras {MAX_FILLER_RETRIES} reintento(s) "
                f"vetando candidatas: {names}. Baja ese mínimo en el diálogo si "
                "quieres admitir aportaciones más pequeñas"
            )

    baseline = evaluate_portfolio(
        original_sets,
        allocation_units(detail, key, resolve_path=resolve_saved_path),
        result.target_valley_dd,
        result.target_point_dd,
        enforce_point_dd=False,
    )
    audit = validate_and_attach_improvement_audit(
        result=result,
        baseline=baseline,
        all_sets=selected_sets,
        original_ids=original_ids,
        options=options,
        inputs=inputs,
        scope="full_history",
        minimum_gain_pct=options.min_efficiency_gain_pct,
    )
    if audit["added_count"] != actual_additions:
        raise ValueError("El ajuste final no conservó las incorporaciones seleccionadas")
    audit["target_portfolio_type"] = base_type.value
    audit["target_portfolio_type_label"] = TYPE_LABELS[base_type.value]
    audit["margin_profile"] = str(inputs.get("margin_profile") or "")
    audit["account_leverage"] = float(inputs.get("account_leverage") or 0)
    audit["grid_off"] = bool(inputs.get("grid_off"))
    audit["min_recent_contribution_pct"] = minimum_recent_pct
    audit["recent_contribution_rejections"] = [
        Path(value).name for value in rejected_fillers
    ]
    audit["engine"] = "chain"
    audit["source_portfolio_id"] = portfolio_id
    audit["save_as_new"] = True
    audit.update({
        "portfolio_uid": str(inputs["_improvement_portfolio_uid"]),
        "label": f"Mejora del portafolio #{portfolio_id} | modo {TYPE_LABELS[target]}",
        "parent_uid": lineage["improvement_parent_uid"],
        "root_portfolio_id": lineage["improvement_root_portfolio_id"],
        "root_uid": lineage["improvement_root_uid"],
        "depth": lineage["improvement_depth"],
        "lineage": lineage["improvement_lineage"],
    })
    # Preserve the exact selected-mode baseline for the saved comparison, even
    # if the original portfolio is later changed or deleted.
    audit["source_snapshot"] = {
        "id": portfolio_id,
        "portfolio_uid": lineage["improvement_parent_uid"],
        "portfolio_type": target,
        "label": str(detail.get("name") or f"Portafolio #{portfolio_id}"),
        "improvement_origin": dict(detail.get("improvement_origin") or {}),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capital": float(inputs["capital"]),
        "total_net_profit": float(baseline.total_net_profit),
        "actual_valley_dd": float(baseline.valley_dd),
        "total_units": sum(int(member.get("units") or 0) for member in detail["members"]),
        "total_lot": sum(float(member.get("lot") or 0) for member in detail["members"]),
        "active_strategies": len(original_ids),
        "members": [dict(member) for member in detail["members"]],
    }
    _seasonal_coverage(result, selected_sets)
    result.warnings.extend(warnings)
    proposal_inputs = settings_inputs(inputs)
    proposal_inputs.update(
        {
            "optimization_profile": key,
            "optimization_profile_label": label,
            "portfolio_type": portfolio_type.value,
            "portfolio_type_label": TYPE_LABELS[portfolio_type.value],
            "composition_portfolio_type": base_type.value,
            "composition_portfolio_type_label": TYPE_LABELS[base_type.value],
            "dd_reserve_pct": reserve,
            "improvement_source_portfolio_id": portfolio_id,
            "improvement_portfolio_type": target,
            "improvement_original_count": len(original_ids),
            "improvement_added_count": actual_additions,
            "improvement_max_additions": options.max_additions,
            "improvement_min_recent_contribution_pct": minimum_recent_pct,
            "portfolio_uid": str(inputs["_improvement_portfolio_uid"]),
            "improvement_label": audit["label"],
            **lineage,
        }
    )
    proposals = [
        {
            "key": key,
            "label": label,
            "reserve_pct": reserve,
            "inputs": proposal_inputs,
            "result": result,
            # Consumed by the outer comparison across addition counts. It never
            # crosses the HTTP boundary or reaches persisted proposal inputs.
            "_improvement_baseline": baseline,
        }
    ]

    availability = asdict(summarize_robust_rows(rows, used))
    availability.update(
        {
            "loaded_sets": len(raw_sets),
            "warnings": warnings,
            "improvement": {
                "engine": "chain",
                "originals_locked": len(original_ids),
                "maximum_additions": options.max_additions,
                "actual_additions": actual_additions,
                "margin_profile": str(inputs.get("margin_profile") or ""),
                "account_leverage": float(inputs.get("account_leverage") or 0),
                "grid_off": bool(inputs.get("grid_off")),
                "min_recent_contribution_pct": minimum_recent_pct,
                "recent_contribution_rejections": [
                    Path(value).name for value in rejected_fillers
                ],
                "selected_set_names": [Path(value).name for value in selected_ids],
            },
        }
    )
    return availability, proposals


def generate_full_history_chain_improvement(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compare valid addition counts for the selected mode of an improved portfolio."""
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
    # Se valida y se fija aqui, antes del bucle: una lista mal formada tiene
    # que fallar con su mensaje, no repetido cinco veces por intento.
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
    # Sólo se reescribe cuando el diálogo lo mandó. Fijarlo siempre impondría el
    # perfil del portafolio sobre el de la variante guardada, que es el que
    # reimpone el intento cuando nadie elige nada.
    if inputs.get("improvement_margin_profile"):
        inputs["improvement_margin_profile"] = margin_profile
    if "improvement_account_leverage" in inputs:
        inputs["improvement_account_leverage"] = account_leverage
    if "improvement_grid_off" in inputs:
        inputs["improvement_grid_off"] = grid_off
    inputs.setdefault("_improvement_portfolio_uid", str(uuid.uuid4()))
    failures: list[str] = []
    best = None
    best_rank: tuple[float, ...] | None = None
    baseline_stress: BootstrapDrawdownAnalysis | None = None
    for additions in range(requested, MAX_IMPROVEMENT_ADDITIONS + 1):
        if progress:
            progress(
                f"Mejora en cadena {TYPE_LABELS[target_type]} · probando {additions} "
                f"incorporación(es) con mínimo {requested} y límite de búsqueda "
                f"{MAX_IMPROVEMENT_ADDITIONS}"
            )
        attempt_inputs = {
            **inputs,
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
        improvement = availability.setdefault("improvement", {})
        improvement["minimum_additions"] = requested
        improvement["maximum_additions"] = MAX_IMPROVEMENT_ADDITIONS
        improvement["selection_priority"] = priority
        improvement["allowed_asset_groups"] = list(allowed_groups)
        for proposal in proposals:
            proposal_inputs = proposal.setdefault("inputs", {})
            proposal_inputs.update({
                "improvement_min_additions": requested,
                "improvement_max_additions": MAX_IMPROVEMENT_ADDITIONS,
                "improvement_selection_priority": priority,
                "improvement_allowed_asset_groups": list(allowed_groups),
            })
            effective_grid_off = bool(proposal_inputs.get("grid_off"))
            if "improvement_grid_off" in inputs:
                proposal_inputs["improvement_grid_off"] = effective_grid_off
            baseline = proposal.pop("_improvement_baseline", None)
            if baseline is not None:
                baseline_stress = _attach_stress_comparison(
                    result=proposal["result"],
                    baseline=baseline,
                    priority=priority,
                    baseline_stress=baseline_stress,
                )
            audit = (proposal["result"].seasonal_validation or {}).get(
                "portfolio_improvement"
            )
            if isinstance(audit, dict):
                audit["minimum_additions"] = requested
                audit["maximum_additions"] = MAX_IMPROVEMENT_ADDITIONS
                audit["grid_off"] = effective_grid_off
        rank = (
            _improvement_rank(proposals[0], additions, priority)
            if proposals else (float("-inf"),)
        )
        if best is None or best_rank is None or rank > best_rank:
            best, best_rank = (availability, proposals), rank
    if best is not None:
        return best
    detail = "; ".join(failures) if failures else "sin candidatas válidas"
    raise ValueError(
        f"No se encontró una mejora válida en cadena con al menos {requested} estrategias "
        f"nuevas (límite de búsqueda: {MAX_IMPROVEMENT_ADDITIONS}). No se rebaja el mínimo. "
        f"Intentos: {detail}"
    )
