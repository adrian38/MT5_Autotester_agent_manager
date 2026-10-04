"""Tipos del dominio: enums, dataclasses y la cancelacion cooperativa."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading
from typing import Callable


ProgressCallback = Callable[[str], None]


CancellationCheck = Callable[[], bool]


class PortfolioCalculationCancelled(RuntimeError):
    """Raised cooperatively when the active portfolio calculation is stopped."""


_CANCELLATION_STATE = threading.local()


def set_portfolio_cancellation_check(
    check: CancellationCheck | None,
) -> CancellationCheck | None:
    """Install a cancellation check for this worker thread and return the old one."""
    previous = getattr(_CANCELLATION_STATE, "check", None)
    if check is None:
        if hasattr(_CANCELLATION_STATE, "check"):
            delattr(_CANCELLATION_STATE, "check")
    else:
        _CANCELLATION_STATE.check = check
    return previous


def _raise_if_portfolio_cancelled() -> None:
    check = getattr(_CANCELLATION_STATE, "check", None)
    if check is not None and check():
        raise PortfolioCalculationCancelled("Cálculo de portafolio detenido por el usuario")


DEFAULT_BOOTSTRAP_SIMULATIONS = 1_000


DEFAULT_BOOTSTRAP_SEED = 20260624


BOOTSTRAP_METHOD = "circular_moving_block"


MIN_RECENT_EQUITY_RECOVERY = 1.0


class PortfolioType(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class PortfolioGroupLimits:
    max_units_pct: float | None
    max_sets: int | None
    bootstrap_units: int = 10


DEFAULT_GROUP_LIMITS = {
    PortfolioType.CONSERVATIVE: PortfolioGroupLimits(max_units_pct=0.40, max_sets=2, bootstrap_units=2),
    PortfolioType.BALANCED: PortfolioGroupLimits(max_units_pct=0.55, max_sets=3, bootstrap_units=2),
    PortfolioType.AGGRESSIVE: PortfolioGroupLimits(max_units_pct=0.70, max_sets=4, bootstrap_units=5),
}


@dataclass(frozen=True)
class ClosedTrade:
    open_time: datetime | None
    close_time: datetime
    symbol: str
    volume: float
    profit: float
    commission: float = 0.0
    swap: float = 0.0
    open_price: float | None = None
    close_price: float | None = None

    @property
    def net_profit(self) -> float:
        return self.profit + self.commission + self.swap


@dataclass
class PeriodReport:
    period_name: str
    start_year: int
    end_year: int
    symbol: str
    timeframe: str
    pnl_curve_001: list[float]
    net_profit_001: float
    valley_dd_001: float
    point_dd_001: float
    profit_factor: float
    return_dd_ratio: float
    trades: int
    gross_profit: float | None = None
    gross_loss: float | None = None
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    pnl_points_001: list[tuple[datetime, float]] = field(default_factory=list)
    source_path: str = ""
    start_date: str = ""
    end_date: str = ""
    balance_dd_metric_001: float = 0.0
    equity_dd_metric_001: float = 0.0


@dataclass
class RobustStrategySet:
    set_id: str
    candidate_id: str
    symbol: str
    timeframe: str | None
    strategy_family: str | None
    robustness_status: str
    already_used: bool
    report_2020_2024: PeriodReport
    report_2025_2026: PeriodReport
    curve_2020_2026_001: list[float]
    net_profit_2020_2026_001: float
    valley_dd_2020_2026_001: float
    point_dd_2020_2026_001: float
    profit_factor_2020_2026: float
    return_dd_2020_2026: float
    trades_2020_2026: int
    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    curve_points_2020_2026_001: list[tuple[datetime, float]] = field(default_factory=list)
    target_month: int | None = None
    month_years: tuple[int, ...] = ()
    positive_month_years: tuple[int, ...] = ()
    max_balance_dd_001: float = 0.0
    max_equity_dd_001: float = 0.0
    max_floating_dd_001: float = 0.0
    floating_dd_source: str = ""
    recent_net_profit_001: float = 0.0
    recent_equity_dd_001: float = 0.0
    has_recent_performance: bool = False
    final_tick_report_path: str = ""
    full_history_report_path: str = ""
    closed_trades_2020_2026: list[ClosedTrade] = field(default_factory=list)
    final_tick_tail_trades: int = 0


@dataclass
class PortfolioEvaluation:
    allocations: dict[str, int]
    equity_curve_2020_2026: list[float]
    total_net_profit: float
    valley_dd: float
    point_dd: float
    target_valley_dd: float
    target_point_dd: float
    valley_usage_pct: float
    point_usage_pct: float
    total_units: int
    total_lot: float
    active_strategies: int
    daily_dd: float = 0.0
    target_daily_dd: float | None = None
    daily_usage_pct: float = 0.0
    daily_dd_full_history: bool = False
    enforce_point_dd: bool = True
    closed_valley_dd: float = 0.0
    floating_dd_buffer: float = 0.0


@dataclass(frozen=True)
class BootstrapDrawdownAnalysis:
    method: str
    simulations: int
    seed: int
    observations: int
    block_size: int
    valley_dd_p50: float
    valley_dd_p95: float
    nominal_valley_dd_limit: float
    effective_valley_dd_limit: float
    probability_exceed_nominal_pct: float
    probability_exceed_effective_pct: float
    alert: bool


@dataclass
class StrategyAllocation:
    set_id: str
    candidate_id: str
    symbol: str
    units: int
    lot: float
    net_profit_contribution: float
    standalone_valley_dd: float
    standalone_point_dd: float
    timeframe: str | None = None
    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    lot_size_step: float | None = None
    margin_required: float = 0.0
    margin_pct: float = 0.0
    margin_leverage: float = 0.0
    margin_contract_size: float = 0.0
    margin_price: float = 0.0
    max_balance_dd_001: float = 0.0
    max_equity_dd_001: float = 0.0
    floating_dd_source: str = ""
    standalone_floating_dd: float = 0.0
    recent_net_profit_001: float = 0.0
    recent_equity_dd_001: float = 0.0
    has_recent_performance: bool = False
    final_tick_report_path: str = ""
    full_history_report_path: str = ""


@dataclass
class OptimizationDecision:
    step: int
    action: str
    set_id: str | None
    from_set_id: str | None
    to_set_id: str | None
    gain: float
    valley_cost: float
    point_cost: float
    score: float
    portfolio_net_profit_after: float
    portfolio_valley_dd_after: float
    portfolio_point_dd_after: float
    reason: str


@dataclass
class UnusedSetInfo:
    set_id: str
    symbol: str
    score: float
    reason: str


@dataclass(frozen=True)
class CorrelationPair:
    set_id_a: str
    set_id_b: str
    symbol_a: str
    symbol_b: str
    pearson_corr: float
    downside_corr: float
    dd_overlap: float
    observations: int


@dataclass
class PortfolioResult:
    allocations: list[StrategyAllocation]
    equity_curve_2020_2026: list[float]
    total_net_profit: float
    actual_valley_dd: float
    actual_point_dd: float
    target_valley_dd: float
    target_point_dd: float
    valley_usage_pct: float
    point_usage_pct: float
    total_lot: float
    total_units: int
    active_strategies: int
    stop_reason: str
    warnings: list[str]
    decision_log: list[OptimizationDecision]
    unused_sets: list[UnusedSetInfo] = field(default_factory=list)
    correlation_rejections: int = 0
    group_summary: dict[str, dict[str, float | int]] = field(default_factory=dict)
    stress_bootstrap: BootstrapDrawdownAnalysis | None = None
    seasonal_coverage: dict[str, dict[str, object]] = field(default_factory=dict)
    seasonal_validation: dict[str, object] = field(default_factory=dict)
    margin_summary: dict[str, object] = field(default_factory=dict)
    max_daily_dd: float = 0.0
    target_daily_dd: float | None = None
    daily_dd_summary: dict[str, object] = field(default_factory=dict)
    daily_dd_full_history: bool = False
    enforce_point_dd: bool = True
    actual_closed_valley_dd: float = 0.0
    floating_dd_buffer: float = 0.0
    #: Exposicion abierta agregada alineada en el tiempo. Informativa: mide lo
    #: que ``floating_dd_buffer`` no puede ver porque toma el maximo entre
    #: estrategias en vez de sumar las que coinciden bajo el agua.
    floating_overlap_audit: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioAvailability:
    robust_accepted: int
    already_used: int
    available: int
    symbols_available: int
    by_symbol: dict[str, int]


def group_limits_for_portfolio_type(portfolio_type: PortfolioType) -> PortfolioGroupLimits:
    return DEFAULT_GROUP_LIMITS.get(portfolio_type, DEFAULT_GROUP_LIMITS[PortfolioType.BALANCED])
