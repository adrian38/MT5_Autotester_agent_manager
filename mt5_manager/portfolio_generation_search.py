from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    CandidateFunnel,
    PortfolioResult,
    PortfolioType,
    SearchLimits,
    SearchPlan,
    optimize_portfolio,
    optimizer_overrides,
)

from .portfolio_antifiller import (
    _optimize_without_recent_fillers,
    _underrepresented_recent_allocation_ids,
)
from .portfolio_full_experimental import optimize_experimental_full_portfolio
from .portfolio_identity import LOCKED_VARIANTS, PORTFOLIO_TYPES, TYPE_LABELS
from .portfolio_persistence import settings_inputs


# Avisos que describen el torneo experimental y viajan a las tres variantes
# bloqueadas. Se capturan de la primera pasada: es la unica que tiene rondas.
EXPERIMENTAL_WARNING_PREFIXES = (
    "Búsqueda UBS experimental:",
    "Estabilidad UBS experimental IS/OOS/6M:",
    "Advertencia experimental UBS:",
    "Regla antirrelleno 6M en la búsqueda experimental:",
)
def _reserve_pct(configured: float, portfolio_type: PortfolioType) -> float:
    if portfolio_type == PortfolioType.CONSERVATIVE:
        return max(configured, 25.0)
    if portfolio_type == PortfolioType.BALANCED:
        return max(configured, 15.0)
    return configured


def _optimizer_kwargs(
    inputs: dict[str, Any],
    objective_type: PortfolioType,
    existing_curves: list[list[float]],
    reserve: float,
) -> dict[str, Any]:
    """Traduce los ajustes de pantalla a los argumentos del optimizador."""
    return {
        "capital": float(inputs["capital"]),
        "valley_dd_pct": float(inputs["valley_dd_pct"]),
        "point_dd_pct": float(inputs["point_dd_pct"]),
        "portfolio_type": objective_type,
        "limits": _optimizer_limits(inputs, existing_curves),
        "funnel": CandidateFunnel(
            min_trades_2020_2026=int(inputs["min_trades_2020_2026"]),
            top_k_per_symbol=int(inputs["top_k_per_symbol"]),
            max_total_candidates=int(inputs["max_total_candidates"]),
        ),
        "search": SearchPlan(
            run_local_search=bool(inputs.get("run_local_search", True)),
            search_restarts=int(inputs.get("search_restarts") or 0),
            dd_reserve_pct=reserve,
        ),
    }


def _optimizer_limits(
    inputs: dict[str, Any], existing_curves: list[list[float]],
) -> SearchLimits:
    """Los topes con los que se mide cada incremento."""
    use_corr = bool(inputs.get("use_correlation", True))
    validate_margin = bool(inputs.get("validate_margin", True))
    return SearchLimits(
        max_units_per_set=inputs.get("max_units_per_set"),
        max_total_units=inputs.get("max_total_units"),
        max_units_per_symbol=inputs.get("max_units_per_symbol"),
        max_sets_per_symbol=inputs.get("max_sets_per_symbol"),
        max_pair_corr=inputs.get("max_pair_corr") if use_corr else None,
        max_downside_corr=inputs.get("max_downside_corr") if use_corr else None,
        max_dd_overlap=inputs.get("max_dd_overlap") if use_corr else None,
        existing_portfolio_curves=existing_curves,
        max_portfolio_corr=inputs.get("max_portfolio_corr") if use_corr else None,
        margin_balance=float(inputs["capital"]) if validate_margin else None,
        max_margin_pct=(
            float(inputs.get("max_margin_pct") or 100.0) if validate_margin else None
        ),
        # Un MarginModel completo sustituye al nombre del perfil cuando el
        # llamante lo construyo (hoy solo el portafolio UBS full history). El
        # mensual no lo pone, asi que conserva el modelo heredado.
        margin_profile=(
            inputs.get("margin_model") or str(inputs.get("margin_profile") or "ictrading")
        ),
        max_daily_dd=inputs.get("max_daily_dd"),
        enforce_point_dd=bool(inputs.get("enforce_point_dd", False)),
        daily_dd_full_history=bool(inputs.get("daily_dd_full_history", False)),
    )


def _seasonal_coverage(result: PortfolioResult, strategies: list[Any]) -> None:
    by_id = {strategy.set_id: strategy for strategy in strategies}
    result.seasonal_coverage = {
        allocation.set_id: {
            "target_month": by_id[allocation.set_id].target_month,
            "years": list(by_id[allocation.set_id].month_years),
            "positive_years": list(by_id[allocation.set_id].positive_month_years),
            "year_count": len(by_id[allocation.set_id].month_years),
            "positive_year_count": len(by_id[allocation.set_id].positive_month_years),
            "trades": by_id[allocation.set_id].trades_2020_2026,
        }
        for allocation in result.allocations
        if allocation.set_id in by_id and by_id[allocation.set_id].target_month is not None
    }


def _normal_proposals(
    raw_sets: list[Any],
    inputs: dict[str, Any],
    existing_curves: list[list[float]],
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured = float(inputs.get("dd_reserve_pct") or 0)
    specs = (
        ("profit", "Maximo beneficio", base_type, configured),
        ("balanced", "Equilibrada", PortfolioType.BALANCED, max(configured, 15.0)),
        ("margin", "Maximo margen DD", PortfolioType.CONSERVATIVE, max(configured, 25.0)),
    )
    proposals: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, (key, label, objective_type, reserve) in enumerate(specs, 1):
        if progress:
            progress(f"Calculando propuesta {index}/3: {label}")
        proposal_inputs = settings_inputs(inputs)
        proposal_inputs.update({
            "optimization_profile": key,
            "optimization_profile_label": label,
            "portfolio_type": objective_type.value,
            "portfolio_type_label": TYPE_LABELS[objective_type.value],
            "dd_reserve_pct": reserve,
        })
        kwargs = _optimizer_kwargs(inputs, objective_type, existing_curves, reserve)
        try:
            def optimize(candidate_sets: list[Any]) -> PortfolioResult:
                return optimize_portfolio(
                    raw_sets=candidate_sets,
                    **{**kwargs, "search": kwargs["search"].with_deep_refinement(bool(inputs.get("deep_optimization")))},
                )

            result, _removed = _optimize_without_recent_fillers(
                raw_sets,
                float(inputs.get("min_strategy_recent_contribution_pct") or 0.0),
                optimize,
                progress=progress,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        _seasonal_coverage(result, raw_sets)
        proposals.append({"key": key, "label": label, "reserve_pct": reserve, "inputs": proposal_inputs, "result": result})
    if not proposals:
        raise ValueError("Ninguna propuesta fue viable. " + " | ".join(errors))
    return proposals


@dataclass
class _LockedComposition:
    """La composicion comun A/M/C, ya fijada, y lo que costo llegar a ella."""

    sets: list[Any]
    ids: list[str]
    base_reserve: float
    removed_ids: list[str]
    refill_base: bool
    experimental_audit: dict[str, Any] | None
    experimental_warnings: list[str]


def _base_optimizer(
    base_inputs: dict[str, Any],
    base_kwargs: dict[str, Any],
    telemetry: dict[str, Any],
    minimum_recent_pct: float,
    progress: Callable[[str], None] | None,
) -> Callable[[list[Any]], PortfolioResult]:
    """El motor que elige la composicion base: experimental o normal."""

    def recent_filler_ids(result: PortfolioResult) -> set[str]:
        # La regla tiene una sola definicion. Se inyecta en el motor
        # experimental para que aplique el mismo criterio que este llamador, en
        # lugar de reimplementarlo y arriesgar que los dos se separen.
        return _underrepresented_recent_allocation_ids(result, minimum_recent_pct)

    def optimize_base(candidate_sets: list[Any]) -> PortfolioResult:
        if not base_inputs.get("experimental_full_search"):
            return optimize_portfolio(
                raw_sets=candidate_sets,
                **optimizer_overrides(
                    base_kwargs,
                    use_deep_refinement=bool(base_inputs.get("deep_optimization")),
                ),
            )
        result = optimize_experimental_full_portfolio(
            raw_sets=candidate_sets,
            use_deep_refinement=bool(base_inputs.get("deep_optimization")),
            progress=progress,
            recent_filler_ids=recent_filler_ids,
            **base_kwargs,
        )
        # El torneo corre una vez. Si la regla compartida vuelve a entrar en
        # este callback con los supervivientes, esa segunda pasada no tiene
        # rondas y sobreescribiria el registro de la busqueda real con
        # «0 ronda(s)».
        if "warnings" not in telemetry:
            telemetry["warnings"] = [
                warning
                for warning in result.warnings
                if warning.startswith(EXPERIMENTAL_WARNING_PREFIXES)
            ]
            telemetry["audit"] = (
                result.seasonal_validation or {}
            ).get("experimental_full_history_stability")
        return result

    return optimize_base


def _locked_sets_from(
    base: PortfolioResult, raw_sets: list[Any],
) -> tuple[list[str], list[Any]]:
    """Los sets activos de la base, comprobando que siguen en el pool."""
    locked_ids = [
        allocation.set_id for allocation in base.allocations if allocation.units > 0
    ]
    if not locked_ids:
        raise ValueError("La composicion base no produjo ningun set activo")
    raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
    missing = [set_id for set_id in locked_ids if set_id not in raw_by_id]
    if missing:
        raise ValueError(
            "Faltan sets de la composicion base: "
            + ", ".join(Path(value).name for value in missing)
        )
    return locked_ids, [raw_by_id[set_id] for set_id in locked_ids]


def _locked_composition(
    raw_sets: list[Any],
    inputs: dict[str, Any],
    base_type: PortfolioType,
    existing_by_type: dict[PortfolioType, list[list[float]]],
    progress: Callable[[str], None] | None,
) -> _LockedComposition:
    """Elige la composicion que las tres variantes van a compartir."""
    base_reserve = max(
        _reserve_pct(float(inputs.get("dd_reserve_pct") or 0), portfolio_type)
        for _key, _label, portfolio_type in LOCKED_VARIANTS
    )
    base_inputs = {**inputs, "dd_reserve_pct": base_reserve}
    minimum_recent_pct = float(inputs.get("min_strategy_recent_contribution_pct") or 0.0)
    if progress:
        progress(f"4/5 · Seleccionando composicion base {TYPE_LABELS[base_type.value]}")
    telemetry: dict[str, Any] = {}
    # El motor experimental repone los rellenos dentro del torneo, donde conoce
    # el lote ganador; reabrir el pool aqui con el torneo como callback es el
    # bucle de doce horas de `ubs_generation_repeated_tournaments.md`. Sin el
    # motor nadie reponia: la cuota dejaba la composicion en los supervivientes
    # y el DD liberado ya no se podia gastar. El #25 del 2026-09-08 cerro con
    # 4 sets de los 8 elegidos y 7.959 de neto teniendo 748 candidatos
    # disponibles, frente a los 21.886 del #21 con 7 sets el dia anterior.
    refill_base = not bool(base_inputs.get("experimental_full_search"))
    base, removed_ids = _optimize_without_recent_fillers(
        raw_sets,
        minimum_recent_pct,
        _base_optimizer(
            base_inputs,
            _optimizer_kwargs(
                base_inputs, base_type, existing_by_type.get(base_type, []), base_reserve,
            ),
            telemetry,
            minimum_recent_pct,
            progress,
        ),
        progress=progress,
        refill_from_pool=refill_base,
    )
    locked_ids, locked_sets = _locked_sets_from(base, raw_sets)
    return _LockedComposition(
        sets=locked_sets,
        ids=locked_ids,
        base_reserve=base_reserve,
        removed_ids=removed_ids,
        refill_base=refill_base,
        # Del torneo real, no de una reejecucion sobre los supervivientes: esa
        # no tiene rondas y declararia «0 ronda(s)» con la auditoria calculada
        # sobre la composicion ya recortada.
        experimental_audit=(
            telemetry.get("audit")
            if base_inputs.get("experimental_full_search")
            else None
        ),
        experimental_warnings=list(telemetry.get("warnings") or []),
    )


def _locked_variant_proposal(
    variant: tuple[str, str, PortfolioType],
    composition: _LockedComposition,
    inputs: dict[str, Any],
    base_type: PortfolioType,
    existing_by_type: dict[PortfolioType, list[list[float]]],
) -> tuple[dict[str, Any] | None, str]:
    """Una variante sobre la composicion fijada, o el motivo de descartarla."""
    key, label, portfolio_type = variant
    locked_count = len(composition.sets)
    reserve = _reserve_pct(float(inputs.get("dd_reserve_pct") or 0), portfolio_type)
    proposal_inputs = settings_inputs(inputs)
    proposal_inputs.update({
        "optimization_profile": key,
        "optimization_profile_label": label,
        "portfolio_type": portfolio_type.value,
        "portfolio_type_label": TYPE_LABELS[portfolio_type.value],
        "composition_portfolio_type": base_type.value,
        "composition_portfolio_type_label": TYPE_LABELS[base_type.value],
        "dd_reserve_pct": reserve,
    })
    kwargs = optimizer_overrides(
        _optimizer_kwargs(
            inputs, portfolio_type, existing_by_type.get(portfolio_type, []), reserve,
        ),
        top_k_per_symbol=max(int(inputs["top_k_per_symbol"]), locked_count),
        max_total_candidates=None,
        max_sets_per_group=locked_count,
        group_unit_cap_bootstrap=max(locked_count, 1),
        minimum_active_strategies=locked_count,
        maximum_active_strategies=locked_count,
        search_restarts=0,
        use_deep_refinement=bool(inputs.get("deep_optimization")),
    )
    try:
        result = optimize_portfolio(raw_sets=composition.sets, **kwargs)
    except Exception as exc:
        return None, f"{label}: {exc}"
    active = {
        allocation.set_id for allocation in result.allocations if allocation.units > 0
    }
    if active != set(composition.ids):
        return None, f"{label}: no mantuvo todos los sets comunes"
    _seasonal_coverage(result, composition.sets)
    if composition.experimental_audit is not None:
        result.seasonal_validation = dict(result.seasonal_validation or {})
        result.seasonal_validation["experimental_full_history_stability"] = (
            composition.experimental_audit
        )
        result.warnings[:0] = composition.experimental_warnings
    return {
        "key": key, "label": label, "reserve_pct": reserve,
        "inputs": proposal_inputs, "result": result,
    }, ""


def _annotate_locked_proposals(
    proposals: list[dict[str, Any]],
    errors: list[str],
    composition: _LockedComposition,
) -> None:
    """Explica la composicion comun, la antirrelleno y las variantes caidas."""
    locked_count = len(composition.sets)
    for proposal in proposals:
        warnings = proposal["result"].warnings
        warnings.insert(
            0,
            f"Composicion comun A/M/C: {locked_count} sets; "
            f"reserva base {composition.base_reserve:.1f}%",
        )
        if composition.removed_ids:
            warnings.insert(
                1,
                "Regla antirrelleno 6M: "
                f"{len(composition.removed_ids)} estrategia(s) eliminada(s) "
                + ("y repuestas desde el pool " if composition.refill_base else "")
                + "antes de fijar la composicion A/M/C.",
            )
        # Una variante inviable no anula a las demas: el redondeo ejecutable
        # puede dejar fuera solo a la mas restrictiva. Se entregan las viables
        # para poder mirarlas, y `prepare_save` bloquea el guardado mientras el
        # paquete no tenga las tres.
        if errors:
            warnings.insert(
                2 if composition.removed_ids else 1,
                f"Paquete A/M/C incompleto: {len(proposals)}/3 variantes viables. "
                "No se puede guardar hasta recalcular. " + " | ".join(errors),
            )


def _locked_full_proposals(
    raw_sets: list[Any],
    inputs: dict[str, Any],
    existing_by_type: dict[PortfolioType, list[list[float]]],
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Las tres variantes A/M/C sobre una composicion comun.

    Un paquete guardado siempre comparte composicion: solo cambian las unidades.
    """
    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    composition = _locked_composition(raw_sets, inputs, base_type, existing_by_type, progress)
    locked_count = len(composition.sets)
    if inputs.get("max_total_units") is not None and int(inputs["max_total_units"]) < locked_count:
        raise ValueError("Max unidades es menor que la composicion comun")
    proposals: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, variant in enumerate(LOCKED_VARIANTS, 1):
        if progress:
            progress(f"5/5 · Calculando variante {index}/3: {variant[1]}")
        proposal, error = _locked_variant_proposal(
            variant, composition, inputs, base_type, existing_by_type,
        )
        if proposal is None:
            errors.append(error)
            continue
        proposals.append(proposal)
    if not proposals:
        raise ValueError(
            "No se pudo calcular ninguna variante bloqueada. " + " | ".join(errors)
        )
    _annotate_locked_proposals(proposals, errors, composition)
    return proposals
