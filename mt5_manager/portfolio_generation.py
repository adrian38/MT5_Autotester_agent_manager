from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    PortfolioType,
    filter_rows_by_recent_positive_months,
    load_max_product_leverage,
    load_robust_sets_from_rows,
    load_symbol_notional,
    load_symbol_notional_from_specs,
    load_symbol_specs,
    load_unmeasured_symbols,
    margin_model_for_profile,
    normalize_margin_profile,
    portfolio_display_symbol,
    portfolio_group_key,
    portfolio_symbol_key,
    summarize_robust_rows,
)

from .common import safe_float
from .portfolio_generation_search import _locked_full_proposals, _reserve_pct
from .portfolio_identity import LOCKED_VARIANTS, PORTFOLIO_TYPES
from .portfolio_report_cache import cached_report
from .portfolio_valley_floor import (
    _with_executable_valley_floor,
    describe_eligibility,
    eligibility_counts,
)

if TYPE_CHECKING:
    from .portfolio_service import PortfolioSource


def filter_rows_by_disabled_symbols(
    rows: list[dict[str, Any]],
    disabled_symbols: Any,
    *,
    universe_files: list[Path],
) -> list[dict[str, Any]]:
    """Exclude candidate symbols selected in the UBS inventory table."""
    if disabled_symbols is None:
        return list(rows)
    if not isinstance(disabled_symbols, (list, tuple, set)):
        raise ValueError("Los símbolos deshabilitados deben ser una lista")
    if any(not isinstance(symbol, str) for symbol in disabled_symbols):
        raise ValueError("Cada símbolo deshabilitado debe ser texto")
    disabled_keys = {
        portfolio_symbol_key(
            portfolio_display_symbol(str(symbol), universe_files=universe_files)
        )
        for symbol in disabled_symbols
        if str(symbol).strip()
    }
    if not disabled_keys:
        return list(rows)
    return [
        row
        for row in rows
        if portfolio_symbol_key(
            portfolio_display_symbol(
                str(
                    row.get("executable_symbol")
                    or row.get("target_symbol")
                    or row.get("symbol")
                    or ""
                ),
                universe_files=universe_files,
            )
        )
        not in disabled_keys
    ]


def build_margin_model(source: PortfolioSource, inputs: dict[str, Any]):
    """Modelo de margen del perfil configurado, con el nocional medido si lo hay.

    AXI usa tramos por grupo, apalancamiento de cuenta y nocional real.
    Los lotes ejecutables pertenecen al broker de origen, no al perfil de
    margen elegido: ICTrading con perfil TTP también necesita ``volume_min``.
    El resto de datos medidos sigue reservado al modelo de margen AXI.
    """
    profile = normalize_margin_profile(inputs.get("margin_profile"))
    symbol_specs_path = getattr(source, "symbol_specs", None)
    if not symbol_specs_path:
        return margin_model_for_profile(inputs.get("margin_profile"))
    (
        symbol_margin, symbol_min_lot, symbol_contract_size, reference_leverage, margin_source,
    ) = load_symbol_specs(symbol_specs_path)
    if profile != "axi":
        # PortfolioSource resuelve las especificaciones del broker real.
        # Cambiar el perfil financiero no cambia sus restricciones de lotaje ni
        # el tamano de contrato del instrumento: ambos son del simbolo. Lo que
        # no se propaga es `symbol_margin`, medido con los tramos y el
        # apalancamiento del broker de origen.
        specs_notional, specs_notional_source = load_symbol_notional_from_specs(
            symbol_specs_path
        )
        return margin_model_for_profile(
            inputs.get("margin_profile"),
            symbol_min_lot=symbol_min_lot,
            symbol_contract_size=symbol_contract_size,
            # Ya convertido a divisa de cuenta por el volcado. Sin el, el
            # tamano de contrato real convierte el fallo de moneda de
            # `lote x contrato x precio` en un error de un orden de magnitud.
            symbol_notional=specs_notional,
            notional_source=specs_notional_source,
        )
    symbol_notional, group_notional, notional_source = load_symbol_notional(source.normalization)
    unmeasured_symbols = load_unmeasured_symbols(source.normalization)
    return margin_model_for_profile(
        inputs.get("margin_profile"),
        account_leverage=safe_float(inputs.get("account_leverage"), 0) or None,
        reference_account_leverage=reference_leverage,
        symbol_margin=symbol_margin,
        symbol_min_lot=symbol_min_lot,
        symbol_contract_size=symbol_contract_size,
        max_product_leverage=load_max_product_leverage(source.product_leverage),
        symbol_notional=symbol_notional,
        group_notional=group_notional,
        unmeasured_symbols=unmeasured_symbols,
        margin_source=margin_source,
        notional_source=notional_source,
    )


def _eligible_generation_rows(
    source: PortfolioSource,
    inputs: dict[str, Any],
    warnings: list[str],
    progress: Callable[[str], None] | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Los candidatos que pasan los filtros del formulario, y el reparto por grupo.

    Cada filtro tiene su propio mensaje de error: son cuatro motivos distintos
    por los que el pool puede quedarse vacio y el usuario necesita cual fue.
    """
    if progress:
        progress("1/5 · Leyendo candidatos Final Tick aceptados")
    rows = source.candidate_rows(include_quarantined=False)
    if not rows:
        raise ValueError("No hay candidatos con Final Tick continuo y 6M aceptados")
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows, min_positive_months=3, window_months=6, parse=cached_report,
        )
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(inputs["allowed_asset_groups"])
    group_counts: dict[str, int] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        group = portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        )
        group_counts[group] = group_counts.get(group, 0) + 1
        if group in allowed:
            filtered.append(row)
    rows = filtered
    if not rows:
        raise ValueError("No quedan candidatos tras aplicar los grupos permitidos")
    rows = filter_rows_by_disabled_symbols(
        rows,
        inputs.get("disabled_symbols"),
        universe_files=[source.universe],
    )
    if not rows:
        raise ValueError("No quedan candidatos tras deshabilitar los símbolos seleccionados")
    return rows, group_counts


def _loaded_generation_sets(
    source: PortfolioSource,
    inputs: dict[str, Any],
    rows: list[dict[str, Any]],
    used: list[str],
    warnings: list[str],
    progress: Callable[[str], None] | None,
) -> list[Any]:
    """Lee los informes de cada fila y vuelve a filtrar por grupo permitido.

    El segundo filtro no sobra: la fila trae el simbolo de la memoria y el set
    cargado trae el suyo, y un simbolo ejecutable puede caer en otro grupo.
    """
    if progress:
        progress(f"2/5 · Cargando reportes de {len(rows)} candidatos")
    raw_sets, load_warnings = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(load_warnings)
    allowed = set(inputs["allowed_asset_groups"])
    raw_sets = [
        strategy for strategy in raw_sets
        if portfolio_group_key(strategy.symbol, universe_files=[source.universe]) in allowed
    ]
    if not raw_sets:
        raise ValueError("No quedan sets cargados después de los filtros")
    return raw_sets


def _require_eligible_sets(
    raw_sets: list[Any], minimum_trades: int, progress: Callable[[str], None] | None,
) -> None:
    eligibility = eligibility_counts(raw_sets, minimum_trades)
    eligibility_text = describe_eligibility(eligibility, minimum_trades)
    if progress:
        progress(f"3/5 · Elegibilidad: {eligibility_text}")
    if not eligibility["eligible"]:
        raise ValueError(
            eligibility_text
            + ". Revise mínimo de trades, sets ya usados y la recuperación reciente 6M."
        )


def _bundle_proposals(
    raw_sets: list[Any],
    inputs: dict[str, Any],
    existing_by_type: dict[PortfolioType, list[list[float]]],
    warnings: list[str],
    minimum_trades: int,
    progress: Callable[[str], None] | None,
) -> list[dict[str, Any]]:
    """El paquete A/M/C, con el suelo de valle ejecutable ya aplicado."""
    requested_valley_pct = float(inputs["valley_dd_pct"])
    proposals, auto_adjusted, applied_pct = _with_executable_valley_floor(
        lambda attempt_inputs: _locked_full_proposals(
            raw_sets, attempt_inputs, existing_by_type, progress,
        ),
        inputs,
        raw_sets,
        minimum_trades=minimum_trades,
        reserve_pct=max(
            _reserve_pct(float(inputs.get("dd_reserve_pct") or 0.0), portfolio_type)
            for _key, _label, portfolio_type in LOCKED_VARIANTS
        ),
        warnings=warnings,
    )
    for proposal in proposals:
        proposal["result"].warnings[:0] = warnings
        proposal["auto_adjusted_valley"] = auto_adjusted
        proposal["requested_valley_dd_pct"] = requested_valley_pct
        proposal["adjusted_valley_dd_pct"] = applied_pct
    return proposals


def generate_proposals(
    source: PortfolioSource,
    inputs: dict[str, Any],
    progress: Callable[[str], None] | None = None,
    *,
    exclude_portfolio_id: int | None = None,
    lock_portfolio_type: PortfolioType | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Generate only the full-history UBS A/M/C bundle."""
    if inputs.get("portfolio_scope") == "monthly":
        raise ValueError("El cálculo mensual debe usar portfolio_monthly_service")
    inputs = {**inputs, "margin_model": build_margin_model(source, inputs)}
    warnings: list[str] = []
    rows, group_counts = _eligible_generation_rows(source, inputs, warnings, progress)
    used = (
        source.used_set_paths(
            "full_history",
            exclude_portfolio_id=exclude_portfolio_id,
            portfolio_type=lock_portfolio_type,
        )
        if inputs.get("exclude_used_sets", True) else []
    )
    availability = asdict(summarize_robust_rows(rows, used))
    raw_sets = _loaded_generation_sets(source, inputs, rows, used, warnings, progress)
    minimum_trades = int(inputs["min_trades_2020_2026"])
    _require_eligible_sets(raw_sets, minimum_trades, progress)
    existing_by_type = {
        kind: source.saved_curves(
            monthly=False,
            portfolio_type=kind,
            exclude_portfolio_id=exclude_portfolio_id,
        )
        for kind in PORTFOLIO_TYPES.values()
    }
    proposals = _bundle_proposals(
        raw_sets, inputs, existing_by_type, warnings, minimum_trades, progress
    )
    availability.update({
        "loaded_sets": len(raw_sets),
        "group_counts": group_counts,
        "warnings": warnings,
    })
    return availability, proposals
