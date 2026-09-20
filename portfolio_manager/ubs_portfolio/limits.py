"""Los topes de una busqueda, que se aplican juntos y por eso viajan juntos."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .margin import MarginModel


@dataclass(frozen=True)
class SearchLimits:
    """Cuanto puede crecer una cartera y contra que se mide cada incremento.

    Todas las fases de la busqueda -greedy, busqueda local, multi-start y
    refinamiento profundo- comprueban exactamente estos topes. Antes viajaban
    como veintidos argumentos sueltos repetidos en cada firma y en cada llamada,
    y lo unico que de verdad cambiaba entre fases -el tope por grupo- se perdia
    en el ruido.
    """

    max_units_per_set: int | None = None
    max_total_units: int | None = None
    max_units_per_symbol: int | None = None
    max_sets_per_symbol: int | None = 1
    max_units_per_group_pct: float | None = None
    max_sets_per_group: int | None = None
    group_unit_cap_bootstrap: int = 10
    max_pair_corr: float | None = None
    max_downside_corr: float | None = None
    max_dd_overlap: float | None = None
    max_portfolio_corr: float | None = None
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None
    margin_balance: float | None = None
    max_margin_pct: float | None = None
    margin_profile: str | MarginModel | None = "roboforex"
    stock_leverage: float = 20.0
    default_leverage: float = 500.0
    stock_contract_size: float = 100.0
    default_contract_size: float = 1.0
    max_daily_dd: float | None = None
    enforce_point_dd: bool = True
    daily_dd_full_history: bool = False

    def without_group_cap(self) -> SearchLimits:
        """Los mismos topes sin el porcentaje por grupo.

        Es la unica variacion que la busqueda hace sobre si misma: Balanced
        repite la pasada sin ese tope cuando la estricta dejo el DD ocioso.
        """
        return replace(self, max_units_per_group_pct=None)
