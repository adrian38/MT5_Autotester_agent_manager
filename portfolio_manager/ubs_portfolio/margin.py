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


def roboforex_margin_leverage(symbol: str) -> float:
    """Portfolio leverage rule requested for RoboForex portfolios."""
    return 20.0 if portfolio_group_key(symbol) == "Stocks" else 500.0


def roboforex_contract_size(symbol: str) -> float:
    """Contract-size rule requested for the portfolio margin guard."""
    return 100.0 if portfolio_group_key(symbol) == "Stocks" else 1.0


#: Tope por grupo para los simbolos que no tienen tope propio publicado.
#: Conservador a proposito. El apalancamiento efectivo de un simbolo es siempre
#: ``min(apalancamiento de cuenta, tope del producto)``: la cuenta manda salvo
#: que el instrumento la limite por debajo.
#:
#: Una version anterior restringia el efecto de la cuenta a forex y bullion,
#: leyendo del Product Schedule que "the Margin Requirements for Other CFDs are
#: not influenced by your Account leverage". La medicion lo desmiente: BTCUSD
#: publica 0.5% de margen minimo (1:200) y el terminal lo dio a 1:100, que es
#: exactamente el apalancamiento que tenia la cuenta. La cuenta si recorta.
AXI_FALLBACK_GROUP_LEVERAGE: dict[str, float] = {
    "Forex": 500.0,
    "Metals": 500.0,
    "Indices": 100.0,
    "Energies": 100.0,
    "Commodities": 100.0,
    "Softs": 100.0,
    "Bonds": 100.0,
    "Crypto": 50.0,
    "Stocks": 10.0,
}


#: Apalancamientos de cuenta que ofrece el formulario.
ACCOUNT_LEVERAGE_CHOICES: tuple[float, ...] = (1000.0, 500.0, 100.0)


DEFAULT_ACCOUNT_LEVERAGE = 1000.0


def ttp_leverage_for(symbol: str) -> float:
    """Tramo de margen de The Trading Pit para un simbolo.

    Unica implementacion del tramo: la llaman
    ``margin_leverage_for_profile`` y ``MarginModel.leverage_for``. Vivia solo
    en la primera, asi que cuando el calculo paso a ``MarginModel`` el perfil
    TTP se quedo sin tramos y todo caia a ``default_leverage`` (1:500) mientras
    el aviso al usuario seguia prometiendo la tabla publicada. El #27 del
    2026-09-08 declaraba 51.29 de margen donde la tabla pide 4973.51.

    TTP es un perfil "y si": el tramo ES el requisito, asi que no lo mueven ni
    el tope del producto ni el apalancamiento de la cuenta de origen.
    """
    group = portfolio_group_key(symbol)
    symbol_key = portfolio_symbol_key(symbol)
    if group == "Stocks" or group == "Crypto":
        return 2.0
    if group == "Metals":
        return 10.0
    if group in {"Indices", "Energies", "IndicesEnergies"}:
        return 10.0 if group == "Energies" or symbol_key in {"BRENT", "WTI"} else 15.0
    if group == "Forex":
        return 50.0
    return 50.0


@dataclass(frozen=True)
class MarginModel:
    """Reglas de margen de un perfil concreto.

    Existe para que cada broker pueda tener su propio modelo sin que el resto se
    entere. Antes el margen eran cuatro escalares globales
    (``stock_leverage``/``default_leverage``/``*_contract_size``) que ignoraban el
    perfil: ``margin_leverage_for_profile`` solo ramificaba para TTP y el tamano
    de contrato era 100 para acciones y 1 para todo lo demas, con lo que un lote
    de forex se valoraba en ``0.01 x 1 x precio`` en vez de en su nocional real.

    Hay tres fuentes, de mejor a peor:

    1. ``symbol_margin``: el margen que el propio terminal calcula para UNA
       posicion al lote minimo (``order_calc_margin``). Ya lleva dentro el tramo
       de margen del producto, el tamano de contrato y el apalancamiento que
       tenia la cuenta al medir, asi que no hay nada que estimar.
    2. ``symbol_notional``: el nocional de esa misma posicion, deducido del
       fichero de normalizacion. Requiere dividir por un apalancamiento.
    3. ``lote x contrato x precio`` con el precio maximo de los reportes, que es
       lo unico que existia antes y lo que dejaba el margen de forex tres ordenes
       de magnitud por debajo del real.
    """

    profile: str = "roboforex"
    account_leverage: float | None = None
    reference_account_leverage: float | None = None
    max_product_leverage: dict[str, float] = field(default_factory=dict)
    group_leverage: dict[str, float] = field(default_factory=dict)
    stock_leverage: float = 20.0
    default_leverage: float = 500.0
    stock_contract_size: float = 100.0
    default_contract_size: float = 1.0
    symbol_margin: dict[str, float] = field(default_factory=dict)
    symbol_min_lot: dict[str, float] = field(default_factory=dict)
    symbol_contract_size: dict[str, float] = field(default_factory=dict)
    symbol_notional: dict[str, float] = field(default_factory=dict)
    group_notional: dict[str, float] = field(default_factory=dict)
    unmeasured_symbols: frozenset[str] = frozenset()
    margin_source: str = ""
    notional_source: str = ""

    def min_lot_for(self, symbol: str) -> float:
        """Lote de UNA unidad del portafolio. 0.01 mientras no se haya medido."""
        value = self.symbol_min_lot.get(portfolio_symbol_key(symbol))
        return float(value) if value and value > 0 else 0.01

    def lot_increments_for(self, symbol: str) -> int:
        """Cuantos escalones de 0.01 lotes ocupa una unidad.

        El EA dimensiona en pasos de 0.01 (``LotPerBalance_step``), asi que para
        colocar N unidades de un simbolo con minimo 1.0 hay que pedirle 100xN
        escalones. Sin esto el EA pide 0.01xN, MT5 lo sube al minimo y las N
        unidades se ejecutan como una sola.
        """
        return max(1, int(round(self.min_lot_for(symbol) / 0.01)))

    def lot_size_for(self, symbol: str, units: int) -> float:
        return round(max(int(units), 0) * self.min_lot_for(symbol), 2)

    def product_cap_for(self, symbol: str) -> float | None:
        """Tope de apalancamiento del instrumento, sin contar la cuenta."""
        cap = self.max_product_leverage.get(portfolio_symbol_key(symbol))
        if cap is None:
            cap = self.group_leverage.get(portfolio_group_key(symbol))
        return float(cap) if cap and cap > 0 else None

    def effective_leverage_for(self, symbol: str, account_leverage: float | None) -> float | None:
        """``min(cuenta, tope del producto)``. La cuenta manda salvo que el
        instrumento la limite por debajo."""
        if not account_leverage or account_leverage <= 0:
            return None
        cap = self.product_cap_for(symbol)
        return min(float(account_leverage), cap) if cap else float(account_leverage)

    def account_leverage_scale(self, symbol: str) -> float:
        """Factor sobre el margen medido al pasar a otro apalancamiento de cuenta.

        La medida se tomo con la cuenta en ``reference_account_leverage``, asi que
        ya incorpora el tope del producto si este era el que ataba. Lo que cambia
        al elegir otro apalancamiento es solo la relacion entre los dos efectivos:

            escala = min(referencia, tope) / min(elegido, tope)

        Airbus+ (tope 1:25) sale 1.0 tanto a 1:100 como a 1:1000, porque el
        producto ataba en ambos casos. BTCUSD (tope 1:200, medido con la cuenta a
        1:100) sale 0.5 al pasar a 1:1000: la cuenta deja de ser el limite y el
        producto permite el doble.
        """
        effective_now = self.effective_leverage_for(symbol, self.account_leverage)
        effective_measured = self.effective_leverage_for(symbol, self.reference_account_leverage)
        if not effective_now or not effective_measured:
            return 1.0
        return effective_measured / effective_now

    def leverage_for(self, symbol: str) -> float:
        # Con margen medido, el apalancamiento honesto es el efectivo. Derivarlo
        # de ``nocional / margen`` mezclaba dos mediciones de fechas distintas
        # (el nocional viene del fichero de normalizacion) y salian cifras como
        # 1:1013 u 1:205, imposibles: nunca se supera el tope del producto.
        if self.symbol_margin.get(portfolio_symbol_key(symbol)):
            effective = self.effective_leverage_for(symbol, self.account_leverage)
            if effective:
                return effective
        if self.profile == "ttp":
            # El tramo publicado es el requisito completo: no lo recorta el tope
            # del producto ni el apalancamiento de la cuenta de origen. Sin esta
            # rama el perfil caia a `default_leverage` y el margen salia dos
            # ordenes de magnitud por debajo del real.
            return ttp_leverage_for(symbol)
        group = portfolio_group_key(symbol)
        if self.group_leverage:
            leverage = self.group_leverage.get(
                group, self.stock_leverage if group == "Stocks" else self.default_leverage
            )
        else:
            leverage = self.stock_leverage if group == "Stocks" else self.default_leverage
        capped = self.effective_leverage_for(symbol, self.account_leverage)
        if capped:
            leverage = min(leverage, capped)
        return float(leverage)

    def contract_size_for(self, symbol: str) -> float:
        # El medido manda. La aproximacion por grupo (acciones 100, resto 1) era
        # falsa de raiz: forex son 100.000, el oro 100, NAS100 20 y BRENT 1.000.
        measured = self.symbol_contract_size.get(portfolio_symbol_key(symbol))
        if measured and measured > 0:
            return float(measured)
        return (
            self.stock_contract_size
            if portfolio_group_key(symbol) == "Stocks"
            else self.default_contract_size
        )

    def margin_for_one(self, symbol: str) -> float | None:
        """Margen medido de una posicion al lote minimo, ya escalado, o None."""
        if not self.symbol_margin:
            return None
        value = self.symbol_margin.get(portfolio_symbol_key(symbol))
        if not value or value <= 0:
            return None
        return float(value) * self.account_leverage_scale(symbol)

    def notional_for(self, symbol: str) -> float | None:
        """Nocional medido de una posicion al lote minimo, o None si no se midio.

        Los simbolos que el agente declara sin medir no caen al nocional del
        grupo: MT5 no devuelve tick value para las acciones cotizadas en peniques
        y el numero del grupo les daba 100 USD cuando su posicion minima son
        varios miles, es decir margen hasta 95 veces por debajo del real. Sin
        medida no hay nocional, y el margen usa la fuente siguiente.
        """
        if not self.symbol_notional and not self.group_notional:
            return None
        key = portfolio_symbol_key(symbol)
        if key in self.unmeasured_symbols:
            return None
        value = self.symbol_notional.get(key)
        if value is None:
            value = self.group_notional.get(portfolio_group_key(symbol))
        return float(value) if value and value > 0 else None


def margin_model_for_profile(
    profile: str | None,
    *,
    account_leverage: float | None = None,
    reference_account_leverage: float | None = None,
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    symbol_margin: dict[str, float] | None = None,
    symbol_min_lot: dict[str, float] | None = None,
    symbol_contract_size: dict[str, float] | None = None,
    max_product_leverage: dict[str, float] | None = None,
    symbol_notional: dict[str, float] | None = None,
    group_notional: dict[str, float] | None = None,
    unmeasured_symbols: Iterable[str] | None = None,
    margin_source: str = "",
    notional_source: str = "",
) -> MarginModel:
    """Construye el modelo de margen del perfil indicado.

    Solo AXI usa margen medido y apalancamiento de cuenta. El resto de perfiles
    reciben del volcado del terminal las dos cosas que son del **instrumento**
    y no del perfil financiero: el lote mínimo, que define cuánto representa una
    unidad ejecutable, y el tamaño de contrato, que define el nocional. El
    margen medido no viaja: lleva dentro los tramos y el apalancamiento del
    broker que lo midió, así que sólo vale para su propio perfil.
    """
    normalized = normalize_margin_profile(profile)
    if normalized != "axi":
        return MarginModel(
            profile=normalized,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            symbol_min_lot=dict(symbol_min_lot or {}),
            # Sin esto, `contract_size_for` caía a la aproximación por grupo
            # (acciones 100, resto 1) y un lote de forex se valoraba en
            # `0.01 x 1 x precio` en vez de sus 100.000 unidades reales.
            symbol_contract_size=dict(symbol_contract_size or {}),
            # Y el nocional medido, que además viene convertido a divisa de
            # cuenta: con el contrato real, la fórmula por precio se descuadra
            # por el tipo de cambio en todo lo que no cotice en la divisa de
            # la cuenta.
            symbol_notional=dict(symbol_notional or {}),
            notional_source=notional_source,
        )
    return MarginModel(
        profile=normalized,
        account_leverage=float(account_leverage) if account_leverage else None,
        reference_account_leverage=float(reference_account_leverage) if reference_account_leverage else None,
        max_product_leverage=dict(max_product_leverage or {}),
        group_leverage=dict(AXI_FALLBACK_GROUP_LEVERAGE),
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
        symbol_margin=dict(symbol_margin or {}),
        symbol_min_lot=dict(symbol_min_lot or {}),
        symbol_contract_size=dict(symbol_contract_size or {}),
        symbol_notional=dict(symbol_notional or {}),
        group_notional=dict(group_notional or {}),
        unmeasured_symbols=frozenset(
            portfolio_symbol_key(name) for name in (unmeasured_symbols or ())
        ),
        margin_source=margin_source,
        notional_source=notional_source,
    )


def resolve_margin_model(
    margin_profile: str | MarginModel | None,
    *,
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> MarginModel:
    """Devuelve el modelo de margen efectivo de un ``margin_profile``.

    ``margin_profile`` viaja ya por todas las firmas del optimizador, asi que
    admitir ahi un ``MarginModel`` completo evita anadir un parametro paralelo a
    medio centenar de llamadas. Un string sigue significando lo de siempre: el
    perfil por nombre con los escalares heredados.
    """
    if isinstance(margin_profile, MarginModel):
        return margin_profile
    return MarginModel(
        profile=normalize_margin_profile(margin_profile),
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
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


def normalize_margin_profile(profile: str | MarginModel | None) -> str:
    if isinstance(profile, MarginModel):
        return profile.profile
    value = str(profile or "roboforex").strip().lower()
    if value in {"ttp", "thetradingpit", "tradingpit", "the_trading_pit"}:
        return "ttp"
    if value in {"axi", "axi trading", "axitrading", "axi_select", "axiselect"}:
        return "axi"
    if value in {"ictrading", "ic trading", "ic", "icmarkets", "ic markets"}:
        return "ictrading"
    return "roboforex"


#: Perfiles financieros que ofrece el formulario, con su nombre visible. Un
#: solo sitio para los cuatro: el selector de generacion, el de la mejora y la
#: etiqueta de la auditoria no pueden discrepar sobre cuales existen.
MARGIN_PROFILE_LABELS: dict[str, str] = {
    "ictrading": "ICTrading",
    "axi": "AXI",
    "roboforex": "RoboForex",
    "ttp": "TTP",
}


MARGIN_PROFILES: tuple[str, ...] = tuple(MARGIN_PROFILE_LABELS)


def margin_profile_label(profile: str | MarginModel | None) -> str:
    return MARGIN_PROFILE_LABELS.get(normalize_margin_profile(profile), "RoboForex")


def margin_leverage_for_profile(
    symbol: str,
    *,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
) -> float:
    if isinstance(margin_profile, MarginModel):
        return margin_profile.leverage_for(symbol)
    profile = normalize_margin_profile(margin_profile)
    if profile == "ttp":
        return ttp_leverage_for(symbol)
    return stock_leverage if portfolio_group_key(symbol) == "Stocks" else default_leverage


def margin_contract_size_for_profile(
    symbol: str,
    *,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    # Sigue siendo una aproximacion por grupo (acciones 100, resto 1). Solo se usa
    # cuando el perfil no aporta nocional medido; con ``symbol_notional`` presente
    # el margen no pasa por aqui.
    if isinstance(margin_profile, MarginModel):
        return margin_profile.contract_size_for(symbol)
    return stock_contract_size if portfolio_group_key(symbol) == "Stocks" else default_contract_size


def strategy_reference_price(strategy: RobustStrategySet) -> float:
    """Conservative price estimate from parsed MT5 closed trades."""
    prices: list[float] = []
    for report in (strategy.report_2020_2024, strategy.report_2025_2026):
        for trade in report.closed_trades:
            for price in (trade.open_price, trade.close_price):
                if price is not None and price > 0:
                    prices.append(float(price))
    if prices:
        return max(prices)
    return 1.0


def allocation_notional(
    strategy: RobustStrategySet,
    units: int,
    model: MarginModel,
) -> float:
    """Exposicion en divisa de cuenta de ``units`` unidades de una estrategia.

    Una unidad es una posicion al lote minimo ejecutable. Con nocional medido se
    multiplica directamente; si no, se recae en la estimacion antigua
    ``lote x contrato x precio`` con el precio maximo visto en los reportes.
    """
    measured = model.notional_for(strategy.symbol)
    if measured is not None:
        return units * measured
    return (
        model.lot_size_for(strategy.symbol, units)
        * model.contract_size_for(strategy.symbol)
        * strategy_reference_price(strategy)
    )


def allocation_margin_required(
    strategy: RobustStrategySet,
    units: int,
    *,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    units = max(int(units), 0)
    if units <= 0:
        return 0.0
    model = resolve_margin_model(
        margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    measured = model.margin_for_one(strategy.symbol)
    if measured is not None:
        return units * measured
    leverage = model.leverage_for(strategy.symbol)
    if leverage <= 0:
        return float("inf")
    return allocation_notional(strategy, units, model) / leverage


def portfolio_margin_summary(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float,
    max_margin_pct: float,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> dict[str, object]:
    model = resolve_margin_model(
        margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    by_set: dict[str, dict[str, float | str | int]] = {}
    total = 0.0
    total_notional = 0.0
    unmeasured: list[str] = []
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        leverage = model.leverage_for(strategy.symbol)
        measured = model.margin_for_one(strategy.symbol)
        notional = allocation_notional(strategy, units, model)
        margin = allocation_margin_required(strategy, units, margin_profile=model)
        if measured is None and strategy.symbol not in unmeasured:
            unmeasured.append(strategy.symbol)
        total += margin
        total_notional += notional
        by_set[strategy.set_id] = {
            "symbol": strategy.symbol,
            "group": portfolio_group_key(strategy.symbol),
            "units": units,
            "lot": model.lot_size_for(strategy.symbol, units),
            "min_lot": model.min_lot_for(strategy.symbol),
            "leverage": leverage,
            "contract_size": model.contract_size_for(strategy.symbol),
            "price": strategy_reference_price(strategy),
            "notional": notional,
            "margin_measured": measured is not None,
            "notional_measured": model.notional_for(strategy.symbol) is not None,
            "margin": margin,
        }
    limit = float(balance) * float(max_margin_pct) / 100.0 if balance > 0 else 0.0
    # Tramos y tamanos de contrato REALMENTE aplicados, para que el aviso al
    # usuario se redacte con estos numeros en vez de con un texto paralelo que
    # puede prometer una tabla que el modelo no usa.
    applied_leverage: dict[str, float] = {}
    for entry in by_set.values():
        applied_leverage.setdefault(str(entry["group"]), float(entry["leverage"]))
    measured_contract_sizes = sum(
        1 for strategy_symbol in
        {str(entry["symbol"]) for entry in by_set.values()}
        if model.symbol_contract_size.get(portfolio_symbol_key(strategy_symbol))
    )
    return {
        "enabled": True,
        "balance": float(balance),
        "max_margin_pct": float(max_margin_pct),
        "limit": limit,
        "total": total,
        "notional": total_notional,
        "group_leverage_applied": applied_leverage,
        "contract_size_measured": measured_contract_sizes,
        "symbol_count": len({str(entry["symbol"]) for entry in by_set.values()}),
        "usage_pct": total / limit * 100.0 if limit > 0 else 0.0,
        "profile": model.profile,
        "profile_label": margin_profile_label(model.profile),
        "account_leverage": float(model.account_leverage or 0.0),
        "reference_account_leverage": float(model.reference_account_leverage or 0.0),
        "margin_source": model.margin_source,
        "notional_source": model.notional_source,
        "unmeasured_symbols": unmeasured,
        "stock_leverage": float(model.stock_leverage),
        "default_leverage": float(model.default_leverage),
        "stock_contract_size": float(model.stock_contract_size),
        "default_contract_size": float(model.default_contract_size),
        "by_set": by_set,
    }


def allocations_respect_margin_limit(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> bool:
    if balance is None or max_margin_pct is None:
        return True
    summary = portfolio_margin_summary(
        sets,
        allocations,
        balance=float(balance),
        max_margin_pct=float(max_margin_pct),
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    return float(summary["total"]) <= float(summary["limit"]) + 1e-9
