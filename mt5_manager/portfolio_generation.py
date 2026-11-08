from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    PortfolioType,
    _logical_stem,
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
from .portfolio_identity import (
    LOCKED_VARIANTS,
    PORTFOLIO_TYPES,
    _allocation_source_rows,
)
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


def _set_identity(value: Any) -> str:
    """La identidad de una estrategia es el nombre de su `.set`, no su ruta.

    Un portafolio activo puede ser manual, y entonces sus miembros apuntan a la
    copia estable del nodo —`runtime/<nodo>/manual_portfolios/<uuid>/sets/`—
    mientras el mismo candidato vive en `outputs/ubs_agent/…`. Son la misma
    estrategia y por ruta no coinciden jamás. Medido el 2026-11-08 sobre AXI:
    de sus seis usos en real, tres son manuales, y con sólo ésos el filtro se
    quedaba sin un candidato y abortaba, aunque los cuatro sets de uno de ellos
    son candidatos aceptados del pool.

    Por nombre entran también las filas distintas que comparten fichero —el
    mismo set regenerado en varios runs—, y eso es lo que se quiere: lo que el
    usuario marcó en real es la estrategia, no la fila que la descubrió.
    """
    return Path(str(value or "").replace("\\", "/")).name.casefold()


def _logical_identity(value: Any) -> str:
    """El tronco lógico del `.set`: su nombre sin el prefijo `robust_AAAAMM_`.

    Es la misma agrupación que usa `load_robust_sets_from_rows`, normalizando
    antes los separadores: las rutas de la memoria las escribió un nodo Windows
    y en el manager Linux `Path(...).stem` devolvería la ruta entera.
    """
    return _logical_stem(str(value or "").replace("\\", "/")).casefold()


def _sample_names(names: list[str]) -> str:
    """Hasta cinco nombres, y la señal de que hay más."""
    ordered = sorted(names)
    return ", ".join(ordered[:5]) + ("…" if len(ordered) > 5 else "")


def _unaccepted_member_rows(
    members: list[dict[str, Any]], matched: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Los miembros en real que el pool aceptado no trae, y los que no se pueden ver.

    La fila se construye con lo que guardó su asignación —ruta del `.set`,
    símbolo, marco temporal e informes base, robustez, Final Tick e histórico—,
    que es justo lo que `load_robust_sets_from_rows` lee. Sin informe base y de
    robustez no hay fila que construir: el único histórico de un set subido a
    mano es la pasada OHLC completa 2020→2026 que escribe `Generar histórico
    OHLC`, un solo informe, y partirlo en dos períodos inventaría un fuera de
    muestra que nunca lo fue. Esos se devuelven nombrados aparte.

    La comparación con lo ya presente es por tronco lógico y no por nombre de
    fichero, porque esa deduplicación agrupa por `account_type:tronco` y la
    fila de un miembro no trae `account_type`: el set subido a mano y su copia
    `robust_AAAAMM_` del pool llegarían como dos estrategias distintas.

    Un miembro excluido no vuelve por esta puerta. La cuarentena ya lo sacó del
    pool aceptado y es una decisión del usuario sobre esa estrategia, no un
    veredicto del embudo.
    """
    seen = {_logical_identity(row.get("set_path")) for row in matched}
    usable: list[dict[str, Any]] = []
    blind: list[str] = []
    for member in members:
        reference = str(member.get("set_path") or member.get("set_id") or "")
        identity = _logical_identity(reference)
        if member.get("excluded") or not identity or identity in seen:
            continue
        seen.add(identity)
        if member.get("is_report_path") and member.get("oos_report_path"):
            usable.append(member)
        else:
            blind.append(Path(reference.replace("\\", "/")).name)
    return _allocation_source_rows(usable), blind


def _note_active_pool(
    warnings: list[str] | None, extra: list[dict[str, Any]], blind: list[str],
) -> None:
    """Dice cuántas entraron sin ser candidatas y cuántas no se pudieron ver."""
    if warnings is None:
        return
    if extra:
        warnings.append(
            f"{len(extra)} estrategia(s) de portafolios activos entran al pool sin ser "
            "candidatas aceptadas; se evalúan con los informes de su asignación."
        )
    if blind:
        warnings.append(
            f"{len(blind)} estrategia(s) de portafolios activos quedan fuera del pool: "
            "no son candidatas aceptadas y su portafolio no guardó informe base ni de "
            f"robustez con el que evaluarlas ({_sample_names(blind)})."
        )


def filter_rows_by_active_portfolios(
    rows: list[dict[str, Any]],
    inputs: dict[str, Any],
    warnings: list[str] | None = None,
) -> list[dict[str, Any]]:
    """El pool cuando sólo se admiten estrategias de portafolios activos.

    No es un recorte del pool aceptado: es el pool. Lo que el usuario puso en
    real entra aunque nunca pasara el embudo, porque marcarlo en real ya es el
    veredicto, y entra con los informes que guardó su asignación.

    Medido el 2026-11-08 sobre AXI: sus seis usos en real son 64 miembros, de
    los que 31 son candidatos aceptados del pool. Los otros 39 son los sets
    subidos a mano a sus tres portafolios manuales, que nunca fueron candidatos
    y tampoco guardaron informes; se nombran en vez de desaparecer.
    """
    if not inputs.get("active_portfolios_only"):
        return rows
    members = [
        member for member in inputs.get("_active_portfolio_members") or []
        if isinstance(member, dict)
    ]
    identities = {
        _set_identity(member.get(key))
        for member in members
        for key in ("set_path", "set_id") if member.get(key)
    }
    matched = [row for row in rows if _set_identity(row.get("set_path")) in identities]
    extra, blind = _unaccepted_member_rows(members, matched)
    _note_active_pool(warnings, extra, blind)
    if not matched and not extra:
        raise ValueError(_empty_active_pool(blind))
    return matched + extra


def _empty_active_pool(blind: list[str]) -> str:
    """El error cuando el filtro deja el pool vacío, con la causa dentro."""
    message = (
        "Ninguna estrategia de los portafolios activos de este broker pudo entrar al pool"
    )
    if not blind:
        return message
    return (
        f"{message}: sus {len(blind)} estrategia(s) no son candidatas aceptadas y su "
        "portafolio no guardó informe base ni de robustez con el que evaluarlas "
        f"({_sample_names(blind)})"
    )


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
    rows = filter_rows_by_active_portfolios(rows, inputs, warnings)
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
