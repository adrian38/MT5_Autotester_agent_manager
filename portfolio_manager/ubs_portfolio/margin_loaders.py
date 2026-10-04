"""Modelos de margen por broker: apalancamiento, contract size y uso."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterable

from .symbols import (
    portfolio_group_key,
    portfolio_symbol_key,
)
from .models import RobustStrategySet


from .margin_models import (
    roboforex_margin_leverage,
    roboforex_contract_size,
    AXI_FALLBACK_GROUP_LEVERAGE,
    ACCOUNT_LEVERAGE_CHOICES,
    DEFAULT_ACCOUNT_LEVERAGE,
    ttp_leverage_for,
    MarginModel,
    margin_model_for_profile,
    resolve_margin_model,
)

def _load_json_dict(path: str | Path) -> tuple[dict, str]:
    file_path = Path(path)
    if not file_path.is_file():
        return {}, ""
    try:
        data = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}, ""
    return (data, str(file_path)) if isinstance(data, dict) else ({}, "")


def load_symbol_specs(
    path: str | Path,
) -> tuple[dict[str, float], dict[str, float], dict[str, float], float | None, str]:
    """Lee del volcado del terminal el margen y el lote minimo por simbolo.

    ``margin_min_lot`` es lo que MT5 exige por UNA posicion al ``volume_min`` del
    simbolo, con el apalancamiento que tenia la cuenta al medir
    (``account_leverage``). Incluye ya el tramo de margen del producto y el
    tamano de contrato, asi que no hay nada que deducir.

    ``volume_min`` es la otra mitad del problema: el backtest se lanza pidiendo
    0.01 lotes y MT5 lo sube al minimo del simbolo, asi que la curva ``_001`` de
    un simbolo con minimo 1.0 es en realidad la de 1.0 lotes. Una unidad del
    portafolio es una posicion al minimo, no 0.01 lotes.

    Devuelve (margen, lote_minimo, tamano_contrato, apalancamiento_ref, origen),
    todo indexado por la clave de simbolo del portafolio.
    """
    data, source = _load_json_dict(path)
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        return {}, {}, {}, None, ""
    reference = data.get("account_leverage")
    try:
        reference_leverage = float(reference) if reference else None
    except (TypeError, ValueError):
        reference_leverage = None
    margins: dict[str, float] = {}
    min_lots: dict[str, float] = {}
    contract_sizes: dict[str, float] = {}

    def number(spec: dict, field_name: str) -> float:
        try:
            return float(spec.get(field_name) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    for name, spec in symbols.items():
        if not isinstance(spec, dict):
            continue
        key = portfolio_symbol_key(name)
        margin = number(spec, "margin_min_lot")
        if margin > 0:
            # Nombres MT5 distintos pueden colapsar en la misma clave del
            # portafolio; el mayor, porque pasarse de margen es el lado seguro.
            margins[key] = max(margins.get(key, 0.0), margin)
        volume_min = number(spec, "volume_min")
        if volume_min > 0:
            min_lots[key] = max(min_lots.get(key, 0.0), volume_min)
        contract_size = number(spec, "contract_size")
        if contract_size > 0:
            contract_sizes[key] = max(contract_sizes.get(key, 0.0), contract_size)
    return margins, min_lots, contract_sizes, reference_leverage, source


def load_symbol_notional_from_specs(path: str | Path) -> tuple[dict[str, float], str]:
    """Nocional medido de UNA posicion al lote minimo, en divisa de cuenta.

    El volcado lo publica ya convertido (campo ``notional_note``), asi que
    evita el fallo de moneda de ``lote x contrato x precio``: esa formula
    multiplica por el precio cotizado sin convertir, y con el tamano de
    contrato real un simbolo cotizado en otra divisa se descuadra por el tipo de
    cambio entero. USDJPY con 0.03 lotes daba 485.031 de nocional donde son
    2.598, y ese solo simbolo triplicaba el margen del portafolio.

    A cambio, el nocional medido usa el precio del dia del volcado, no el
    maximo visto en los reportes que usa la estimacion conservadora. El error de
    divisa es de un orden de magnitud y el de precio de un 15%: manda el medido,
    igual que en el modelo AXI.
    """
    data, source = _load_json_dict(path)
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        return {}, ""
    notionals: dict[str, float] = {}
    for name, spec in symbols.items():
        if not isinstance(spec, dict):
            continue
        try:
            value = float(spec.get("notional_min_lot") or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            key = portfolio_symbol_key(name)
            # Nombres MT5 distintos colapsan en la misma clave; el mayor,
            # porque pasarse de margen es el lado seguro.
            notionals[key] = max(notionals.get(key, 0.0), value)
    return notionals, source


def load_max_product_leverage(path: str | Path) -> dict[str, float]:
    """Tope de apalancamiento por simbolo publicado por el broker."""
    data, _ = _load_json_dict(path)
    raw = data.get("max_product_leverage")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for name, value in raw.items():
        try:
            leverage = float(value)
        except (TypeError, ValueError):
            continue
        if leverage <= 0:
            continue
        key = portfolio_symbol_key(name)
        # Ante dos nombres que colapsan, el tope mas bajo: margen mas alto.
        result[key] = min(result.get(key, leverage), leverage)
    return result


def load_symbol_notional(path: str | Path) -> tuple[dict[str, float], dict[str, float], str]:
    """Lee el nocional real por simbolo del fichero de normalizacion del broker.

    El agente UBS mide en MT5 ``volume_min``, ``volume_step``, ``contract_size``,
    ``tick_value``, ``tick_size`` y ``price``, y los colapsa en un factor
    ``reference_notional / max(nocional_real, min_notional)``. Invertirlo devuelve
    el nocional de una posicion al lote que MT5 ejecuta de verdad, que es justo lo
    que necesita el margen.

    Los simbolos cuyo nocional real quedo por debajo de ``min_notional`` estan
    topados en el factor, asi que aqui salen como ``min_notional``: se sobreestima
    el margen, que es el lado seguro. Devuelve (por_simbolo, por_grupo, origen).
    """
    file_path = Path(path)
    if not file_path.is_file():
        return {}, {}, ""
    try:
        data = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}, {}, ""
    if not isinstance(data, dict):
        return {}, {}, ""
    reference = float(data.get("reference_notional") or 0.0)
    if reference <= 0:
        return {}, {}, ""

    def notionals(raw: object) -> dict[str, float]:
        result: dict[str, float] = {}
        if not isinstance(raw, dict):
            return result
        for name, factor in raw.items():
            try:
                value = float(factor)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            result[str(name)] = reference / value
        return result

    by_symbol: dict[str, float] = {}
    for name, notional in notionals(data.get("symbol_net_profit_factors")).items():
        key = portfolio_symbol_key(name)
        # Varios nombres MT5 pueden colapsar en la misma clave del portafolio
        # (USDJPY.sa y USDJPY). Nos quedamos con el mayor: sobreestimar margen es
        # el error aceptable.
        by_symbol[key] = max(by_symbol.get(key, 0.0), notional)
    by_group = notionals(data.get("group_net_profit_factors"))
    return by_symbol, by_group, str(file_path)


def load_unmeasured_symbols(path: str | Path) -> frozenset[str]:
    """Simbolos que el agente no pudo medir en MT5 (``skipped_symbols``).

    El agente los publica precisamente para que nadie invente su nocional. Sin
    esta lista, un simbolo sin medida caia al factor del grupo y volvia a salir
    con un margen que nadie habia medido.
    """
    data, _ = _load_json_dict(path)
    raw = data.get("skipped_symbols")
    if not isinstance(raw, (list, tuple, set)):
        return frozenset()
    return frozenset(portfolio_symbol_key(str(name)) for name in raw if str(name).strip())


#: Perfiles financieros que ofrece el formulario, con su nombre visible. Un
#: solo sitio para los cuatro: el selector de generacion, el de la mejora y la
#: etiqueta de la auditoria no pueden discrepar sobre cuales existen.
