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


@dataclass(frozen=True)
class _MarginModelInputs:
    account_leverage: float | None
    reference_account_leverage: float | None
    stock_leverage: float
    default_leverage: float
    stock_contract_size: float
    default_contract_size: float
    symbol_margin: dict[str, float] | None
    symbol_min_lot: dict[str, float] | None
    symbol_contract_size: dict[str, float] | None
    max_product_leverage: dict[str, float] | None
    symbol_notional: dict[str, float] | None
    group_notional: dict[str, float] | None
    unmeasured_symbols: Iterable[str] | None
    margin_source: str
    notional_source: str


def _standard_margin_model(profile: str, inputs: _MarginModelInputs) -> MarginModel:
    return MarginModel(
        profile=profile,
        stock_leverage=inputs.stock_leverage,
        default_leverage=inputs.default_leverage,
        stock_contract_size=inputs.stock_contract_size,
        default_contract_size=inputs.default_contract_size,
        symbol_min_lot=dict(inputs.symbol_min_lot or {}),
        symbol_contract_size=dict(inputs.symbol_contract_size or {}),
        symbol_notional=dict(inputs.symbol_notional or {}),
        notional_source=inputs.notional_source,
    )


def _axi_margin_model(profile: str, inputs: _MarginModelInputs) -> MarginModel:
    return MarginModel(
        profile=profile,
        account_leverage=float(inputs.account_leverage) if inputs.account_leverage else None,
        reference_account_leverage=(
            float(inputs.reference_account_leverage)
            if inputs.reference_account_leverage else None
        ),
        max_product_leverage=dict(inputs.max_product_leverage or {}),
        group_leverage=dict(AXI_FALLBACK_GROUP_LEVERAGE),
        stock_leverage=inputs.stock_leverage,
        default_leverage=inputs.default_leverage,
        stock_contract_size=inputs.stock_contract_size,
        default_contract_size=inputs.default_contract_size,
        symbol_margin=dict(inputs.symbol_margin or {}),
        symbol_min_lot=dict(inputs.symbol_min_lot or {}),
        symbol_contract_size=dict(inputs.symbol_contract_size or {}),
        symbol_notional=dict(inputs.symbol_notional or {}),
        group_notional=dict(inputs.group_notional or {}),
        unmeasured_symbols=frozenset(
            portfolio_symbol_key(name) for name in (inputs.unmeasured_symbols or ())
        ),
        margin_source=inputs.margin_source,
        notional_source=inputs.notional_source,
    )


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
    inputs = _MarginModelInputs(
        account_leverage, reference_account_leverage, stock_leverage,
        default_leverage, stock_contract_size, default_contract_size,
        symbol_margin, symbol_min_lot, symbol_contract_size,
        max_product_leverage, symbol_notional, group_notional,
        unmeasured_symbols, margin_source, notional_source,
    )
    normalized = normalize_margin_profile(profile)
    if normalized != "axi":
        return _standard_margin_model(normalized, inputs)
    return _axi_margin_model(normalized, inputs)


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
