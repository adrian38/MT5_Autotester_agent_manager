"""Los topes de una busqueda, que se aplican juntos y por eso viajan juntos."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .models import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_BOOTSTRAP_SIMULATIONS,
    PortfolioType,
    group_limits_for_portfolio_type,
)
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
    #: ``None`` significa el tope del tipo de cartera; ver with_group_defaults.
    group_unit_cap_bootstrap: int | None = None
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

    def with_group_defaults(self, portfolio_type: PortfolioType) -> SearchLimits:
        """Rellena los topes por grupo no fijados con los del tipo de cartera.

        Conservador diversifica mas que Agresivo, asi que el tope por grupo es
        una propiedad del tipo, no del llamante. Lo que el llamante fija a mano
        manda; lo que deja en ``None`` lo decide el tipo.
        """
        group = group_limits_for_portfolio_type(portfolio_type)
        return replace(
            self,
            max_units_per_group_pct=(
                group.max_units_pct
                if self.max_units_per_group_pct is None
                else self.max_units_per_group_pct
            ),
            max_sets_per_group=(
                group.max_sets if self.max_sets_per_group is None else self.max_sets_per_group
            ),
            group_unit_cap_bootstrap=(
                group.bootstrap_units
                if self.group_unit_cap_bootstrap is None
                else self.group_unit_cap_bootstrap
            ),
        )


@dataclass(frozen=True)
class CandidateFunnel:
    """A quien se invita al portafolio, antes de repartir una sola unidad.

    El embudo decide a quien se INVITA, no a quien se expulsa: las obligatorias
    vuelven al pool aunque hoy no lo pasen.
    """

    min_trades_2020_2026: int = 100
    top_k_per_symbol: int = 3
    max_total_candidates: int | None = 30
    required_set_ids: Sequence[str] | None = None
    required_initial_allocations: dict[str, int] | None = None
    preserve_required_allocations: bool = False


@dataclass(frozen=True)
class SearchPlan:
    """Cuanto se busca, con que criterio y con cuanta reserva de DD."""

    run_local_search: bool = True
    search_restarts: int = 0
    use_deep_refinement: bool = False
    prefer_breadth_below_minimum: bool = False
    minimum_active_strategies: int | None = None
    maximum_active_strategies: int | None = None
    #: Margen que se resta a los limites de DD antes de buscar.
    dd_reserve_pct: float = 0.0
    bootstrap_simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS
    bootstrap_block_size: int | None = None
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED

    def with_deep_refinement(self, enabled: bool) -> SearchPlan:
        """El mismo plan con la optimizacion profunda activada o no.

        Cada llamante decide por su cuenta si la activa: el mensual y las
        mejoras la encienden en unas pasadas y la apagan en otras.
        """
        return replace(self, use_deep_refinement=bool(enabled))
