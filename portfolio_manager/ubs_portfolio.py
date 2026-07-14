"""UBS discrete DD-constrained portfolio builder.

This module is intentionally pure: no Tkinter and no SQLite. It receives
robustness-accepted strategy sets, merges their 2020-2024 and 2025-2026 reports
into one 2020-2026 curve, then allocates lots in integer 0.01-lot units. Every
possible increment is evaluated against the complete portfolio curve before it
can be accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
import math
from pathlib import Path
import random
import re
import unicodedata
from typing import Callable, Iterable, Sequence

from .mt5_report import StrategyReport, parse_report
from ubs.path_utils import resolve_workspace_path
from ubs.universe import load_asset_universe


ProgressCallback = Callable[[str], None]


DEFAULT_BOOTSTRAP_SIMULATIONS = 1_000
DEFAULT_BOOTSTRAP_SEED = 20260624
BOOTSTRAP_METHOD = "circular_moving_block"
MIN_RECENT_EQUITY_RECOVERY = 1.0


PORTFOLIO_SYMBOL_ALIASES = {
    "US30": ".US30CASH",
    ".US30CASH": ".US30CASH",
    "US500": ".US500CASH",
    ".US500CASH": ".US500CASH",
    "USTEC": ".USTECHCASH",
    "US100": ".USTECHCASH",
    "NAS100": ".USTECHCASH",
    ".USTECHCASH": ".USTECHCASH",
    "DAX": ".DE40CASH",
    "DE40": ".DE40CASH",
    "GER40": ".DE40CASH",
    ".DE40CASH": ".DE40CASH",
    "XTIUSD": "WTI",
    "USOIL": "WTI",
    "CRUDEOIL": "WTI",
    "WTI": "WTI",
}

PORTFOLIO_GROUP_BY_SYMBOL = {
    **{
        symbol: "Forex"
        for symbol in (
            "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "CHFJPY",
            "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "EURAUD", "EURCAD",
            "EURCHF", "EURGBP", "EURJPY", "EURNZD", "EURUSD", "NZDCAD", "NZDCHF", "NZDJPY",
            "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
        )
    },
    **{symbol: "Metals" for symbol in ("XAGUSD", "XAUUSD", "XAUEUR")},
    **{
        symbol: "Indices"
        for symbol in (".DE40CASH", ".JP225CASH", ".US500CASH", ".USTECHCASH", ".US30CASH")
    },
    **{symbol: "Energies" for symbol in ("BRENT", "WTI")},
    **{symbol: "Crypto" for symbol in ("BTCUSD", "ETHUSD")},
    **{
        symbol: "Stocks"
        for symbol in (
            "GOOGL", "MSFT", "IBM", "VZ", "INTC", "LLY", "HPE", "PFE", "JNJ", "EA", "BA",
            "ORCL", "NVDA", "CAT", "CSCO", "MMM", "ADBE", "GE", "TSLA", "NKE", "CMCSA",
            "GM", "DIS", "PM", "PG", "PEP", "FOXA", "KO", "AAPL", "AMZN", "UPS", "NFLX",
            "BRK.B", "MCD", "PRU", "SBUX", "PYPL", "GS", "WMT", "V", "DAL", "WFC", "C",
            "XOM", "CVX", "NEM", "JPM", "BAC", "EBAY", "META",
        )
    },
}

PORTFOLIO_UNIVERSE_FILES = (
    "assets/roboforex_assets.ini",
    "assets/axi_assets.ini",
    "assets/ictrading_assets.ini",
)


def _normalized_universe_group(group: str, symbol_key: str) -> str:
    if group == "Commodities":
        return "Softs"
    if group != "IndicesEnergies":
        return group
    energy_tokens = ("BRENT", "WTI", "OIL", "XTI", "XBR", "XNG", "GAS")
    return "Energies" if any(token in symbol_key for token in energy_tokens) else "Indices"


def _portfolio_universe_files_key(universe_files: Iterable[str | Path] | None = None) -> tuple[str, ...]:
    files = universe_files if universe_files is not None else PORTFOLIO_UNIVERSE_FILES
    return tuple(str(path) for path in files)


@lru_cache(maxsize=16)
def _portfolio_universe_group_maps_for_files(
    universe_files: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the portfolio classifier from the broker universe files."""
    canonical: dict[str, str] = {}
    exact: dict[str, str] = {}
    alias_rows: list[tuple[str, str]] = []
    for relative_path in universe_files:
        groups, aliases = load_asset_universe(
            resolve_workspace_path(relative_path),
            include_disabled=True,
        )
        for group, symbols in groups.items():
            for symbol in symbols:
                raw_key = str(symbol).strip().upper()
                symbol_key = portfolio_symbol_key(symbol)
                normalized_group = _normalized_universe_group(group, symbol_key)
                exact[raw_key] = normalized_group
                if group == "Stocks" and "." in raw_key:
                    canonical.setdefault(symbol_key, normalized_group)
                else:
                    canonical[symbol_key] = normalized_group
        alias_rows.extend(aliases.items())
    for alias, target in alias_rows:
        target_group = exact.get(str(target).strip().upper()) or canonical.get(portfolio_symbol_key(target))
        if target_group:
            exact[str(alias).strip().upper()] = target_group
            canonical[portfolio_symbol_key(alias)] = target_group
    return canonical, exact


def _portfolio_universe_group_maps(
    universe_files: Iterable[str | Path] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    return _portfolio_universe_group_maps_for_files(_portfolio_universe_files_key(universe_files))


def _portfolio_universe_group_by_symbol(
    universe_files: Iterable[str | Path] | None = None,
) -> dict[str, str]:
    return _portfolio_universe_group_maps(universe_files)[0]


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


@dataclass(frozen=True)
class PortfolioAvailability:
    robust_accepted: int
    already_used: int
    available: int
    symbols_available: int
    by_symbol: dict[str, int]


def merge_accumulated_curves(
    curve_2020_2024: list[float],
    curve_2025_2026: list[float],
) -> list[float]:
    if not curve_2020_2024:
        raise ValueError("2020-2024 curve is empty")
    if not curve_2025_2026:
        raise ValueError("2025-2026 curve is empty")
    last_value = curve_2020_2024[-1]
    return curve_2020_2024 + [last_value + value for value in curve_2025_2026[1:]]


def merge_incremental_curves(
    increments_2020_2024: list[float],
    increments_2025_2026: list[float],
) -> list[float]:
    return increments_2020_2024 + increments_2025_2026


def to_accumulated_curve(increments: list[float]) -> list[float]:
    curve = [0.0]
    total = 0.0
    for change in increments:
        total += change
        curve.append(total)
    return curve


def daily_pnl_series(strategy: RobustStrategySet) -> dict[str, float]:
    if strategy.curve_points_2020_2026_001:
        previous = 0.0
        series: dict[str, float] = {}
        for timestamp, value in strategy.curve_points_2020_2026_001:
            day = timestamp.date().isoformat()
            series[day] = series.get(day, 0.0) + (value - previous)
            previous = value
        return series

    increments = [
        current - previous
        for previous, current in zip(strategy.curve_2020_2026_001, strategy.curve_2020_2026_001[1:])
    ]
    return {str(index): value for index, value in enumerate(increments)}


def strategy_daily_closed_floating_dd(
    strategy: RobustStrategySet,
    *,
    full_history: bool = False,
) -> dict[str, float]:
    """Estimate per-day closed + floating DD for one 0.01-lot strategy unit.

    MT5 HTML reports parsed by this project expose closed deals/trades but do
    not expose a timestamped equity/floating-PnL series.  The closed component
    is the worst intraday closed-trade drawdown.  The floating component is a
    conservative proxy: the absolute final loss of each open losing trade is
    counted on every calendar day where the trade was open.  Winning trades do
    not add floating risk because their MAE is not present in the HTML.

    Monthly portfolios normally check only the selected target month.  When
    ``full_history`` is enabled, the same daily cap scans all historical days
    from the base + OOS reports.
    """
    month = 0 if full_history else int(strategy.target_month or 0)
    closed_by_day: dict[str, list[ClosedTrade]] = {}
    floating_by_day: dict[str, float] = {}
    for report in (strategy.report_2020_2024, strategy.report_2025_2026):
        for trade in report.closed_trades:
            close_time = trade.close_time
            if not month or close_time.month == month:
                closed_by_day.setdefault(close_time.date().isoformat(), []).append(trade)

            floating_risk = max(-float(trade.net_profit), 0.0)
            if floating_risk <= 0:
                continue
            open_time = trade.open_time or trade.close_time
            start_day = min(open_time.date(), trade.close_time.date())
            end_day = max(open_time.date(), trade.close_time.date())
            day = start_day
            while day <= end_day:
                if not month or day.month == month:
                    day_key = day.isoformat()
                    floating_by_day[day_key] = floating_by_day.get(day_key, 0.0) + floating_risk
                day += timedelta(days=1)

    closed_dd_by_day: dict[str, float] = {}
    for day_key, trades in closed_by_day.items():
        cumulative = 0.0
        trough = 0.0
        for trade in sorted(trades, key=lambda item: item.close_time):
            cumulative += float(trade.net_profit)
            trough = min(trough, cumulative)
        closed_dd_by_day[day_key] = max(-trough, 0.0)

    all_days = set(closed_dd_by_day) | set(floating_by_day)
    return {
        day: closed_dd_by_day.get(day, 0.0) + floating_by_day.get(day, 0.0)
        for day in all_days
    }


def portfolio_daily_closed_floating_dd(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    full_history: bool = False,
) -> tuple[float, dict[str, object]]:
    totals_by_day: dict[str, float] = {}
    by_set: dict[str, dict[str, object]] = {}
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        series = strategy_daily_closed_floating_dd(strategy, full_history=full_history)
        if not series:
            continue
        worst_day, worst_unit_dd = max(series.items(), key=lambda item: item[1])
        by_set[strategy.set_id] = {
            "symbol": strategy.symbol,
            "units": units,
            "worst_day": worst_day,
            "worst_unit_dd": float(worst_unit_dd),
            "worst_allocated_dd": float(worst_unit_dd) * units,
        }
        for day, value in series.items():
            totals_by_day[day] = totals_by_day.get(day, 0.0) + float(value) * units

    if not totals_by_day:
        return 0.0, {
            "enabled": False,
            "full_history": bool(full_history),
            "worst_day": None,
            "by_day": {},
            "by_set": by_set,
        }
    worst_day, worst_dd = max(totals_by_day.items(), key=lambda item: item[1])
    return float(worst_dd), {
        "enabled": True,
        "full_history": bool(full_history),
        "worst_day": worst_day,
        "worst_dd": float(worst_dd),
        "by_day": totals_by_day,
        "by_set": by_set,
    }


def pearson_correlation(values_a: Sequence[float], values_b: Sequence[float]) -> float:
    if len(values_a) < 2 or len(values_b) < 2 or len(values_a) != len(values_b):
        return 0.0
    mean_a = sum(values_a) / len(values_a)
    mean_b = sum(values_b) / len(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    denom_a = math.sqrt(sum(value * value for value in centered_a))
    denom_b = math.sqrt(sum(value * value for value in centered_b))
    denom = denom_a * denom_b
    if denom <= 0:
        return 0.0
    return float(sum(a * b for a, b in zip(centered_a, centered_b)) / denom)


def curve_increment_correlation(curve_a: Sequence[float], curve_b: Sequence[float]) -> float:
    increments_a = [current - previous for previous, current in zip(curve_a, curve_a[1:])]
    increments_b = [current - previous for previous, current in zip(curve_b, curve_b[1:])]
    length = max(len(increments_a), len(increments_b))
    if length < 2:
        return 0.0
    padded_a = increments_a + [0.0] * (length - len(increments_a))
    padded_b = increments_b + [0.0] * (length - len(increments_b))
    return pearson_correlation(padded_a, padded_b)


def strategy_correlation_pair(strategy_a: RobustStrategySet, strategy_b: RobustStrategySet) -> CorrelationPair:
    series_a = daily_pnl_series(strategy_a)
    series_b = daily_pnl_series(strategy_b)
    keys = sorted(set(series_a) | set(series_b))
    values_a = [series_a.get(key, 0.0) for key in keys]
    values_b = [series_b.get(key, 0.0) for key in keys]
    pearson = pearson_correlation(values_a, values_b)

    downside_a: list[float] = []
    downside_b: list[float] = []
    overlap_losses = 0
    loss_days = 0
    for value_a, value_b in zip(values_a, values_b):
        if value_a < 0 or value_b < 0:
            downside_a.append(min(value_a, 0.0))
            downside_b.append(min(value_b, 0.0))
            loss_days += 1
            if value_a < 0 and value_b < 0:
                overlap_losses += 1

    downside = pearson_correlation(downside_a, downside_b)
    dd_overlap = overlap_losses / loss_days if loss_days else 0.0
    return CorrelationPair(
        set_id_a=strategy_a.set_id,
        set_id_b=strategy_b.set_id,
        symbol_a=strategy_a.symbol,
        symbol_b=strategy_b.symbol,
        pearson_corr=pearson,
        downside_corr=downside,
        dd_overlap=dd_overlap,
        observations=len(keys),
    )


def build_correlation_pairs(sets: Sequence[RobustStrategySet]) -> list[CorrelationPair]:
    pairs: list[CorrelationPair] = []
    for left_index, strategy_a in enumerate(sets):
        for strategy_b in sets[left_index + 1:]:
            pairs.append(strategy_correlation_pair(strategy_a, strategy_b))
    return pairs


def calc_valley_dd(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        max_dd = max(max_dd, peak - value)
    return float(max_dd)


def calc_point_dd(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    worst_loss = 0.0
    for previous, current in zip(equity_curve, equity_curve[1:]):
        change = current - previous
        if change < worst_loss:
            worst_loss = change
    return abs(float(worst_loss))


def bootstrap_valley_drawdown(
    equity_curve: Sequence[float],
    *,
    nominal_valley_dd_limit: float,
    effective_valley_dd_limit: float,
    simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS,
    block_size: int | None = None,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> BootstrapDrawdownAnalysis:
    """Estimate valley-DD risk with a deterministic circular block bootstrap.

    Consecutive P/L increments are sampled in blocks, preserving local loss
    streaks instead of independently shuffling every trade. A fixed seed makes
    proposal comparisons and saved audits reproducible.
    """
    if simulations <= 0:
        raise ValueError("Bootstrap simulations must be positive")
    increments = [
        float(current) - float(previous)
        for previous, current in zip(equity_curve, equity_curve[1:])
    ]
    observation_count = len(increments)
    if observation_count == 0:
        return BootstrapDrawdownAnalysis(
            method=BOOTSTRAP_METHOD,
            simulations=int(simulations),
            seed=int(seed),
            observations=0,
            block_size=0,
            valley_dd_p50=0.0,
            valley_dd_p95=0.0,
            nominal_valley_dd_limit=float(nominal_valley_dd_limit),
            effective_valley_dd_limit=float(effective_valley_dd_limit),
            probability_exceed_nominal_pct=0.0,
            probability_exceed_effective_pct=0.0,
            alert=False,
        )

    if block_size is None:
        block_size = min(20, max(5, int(round(math.sqrt(observation_count)))))
    block_size = min(max(int(block_size), 1), observation_count)
    rng = random.Random(int(seed))
    drawdowns: list[float] = []
    for _simulation in range(int(simulations)):
        sampled = 0
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        while sampled < observation_count:
            start = rng.randrange(observation_count)
            take = min(block_size, observation_count - sampled)
            for offset in range(take):
                equity += increments[(start + offset) % observation_count]
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, peak - equity)
            sampled += take
        drawdowns.append(float(max_drawdown))

    drawdowns.sort()
    p50 = _linear_percentile(drawdowns, 0.50)
    p95 = _linear_percentile(drawdowns, 0.95)
    nominal_limit = float(nominal_valley_dd_limit)
    effective_limit = float(effective_valley_dd_limit)
    exceed_nominal = sum(value > nominal_limit + 1e-9 for value in drawdowns)
    exceed_effective = sum(value > effective_limit + 1e-9 for value in drawdowns)
    return BootstrapDrawdownAnalysis(
        method=BOOTSTRAP_METHOD,
        simulations=int(simulations),
        seed=int(seed),
        observations=observation_count,
        block_size=block_size,
        valley_dd_p50=p50,
        valley_dd_p95=p95,
        nominal_valley_dd_limit=nominal_limit,
        effective_valley_dd_limit=effective_limit,
        probability_exceed_nominal_pct=exceed_nominal / simulations * 100.0,
        probability_exceed_effective_pct=exceed_effective / simulations * 100.0,
        alert=p95 > effective_limit + 1e-9,
    )


def _linear_percentile(sorted_values: Sequence[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    position = min(max(float(quantile), 0.0), 1.0) * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def extract_period_info(text: str) -> tuple[str, str, str]:
    match = re.search(
        r"([A-Z0-9]+)\s+\((\d{4}\.\d{2}\.\d{2})\s+-\s+(\d{4}\.\d{2}\.\d{2})\)",
        text,
    )
    if not match:
        raise ValueError("Period info not found")
    return match.group(1), match.group(2), match.group(3)


def build_equity_curve_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[float]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    curve = [0.0]
    total = 0.0
    for trade in ordered:
        total += trade.net_profit
        curve.append(total)
    return curve


def parse_mt5_html_report(html_path: str | Path, period_name: str) -> PeriodReport:
    report = parse_report(Path(html_path))
    return period_report_from_strategy_report(report, period_name)


def period_report_from_strategy_report(report: StrategyReport, period_name: str) -> PeriodReport:
    closed_trades = [
        ClosedTrade(
            open_time=trade.open_time,
            close_time=trade.close_time,
            symbol=report.symbol,
            volume=trade.size,
            profit=trade.profit_loss,
            open_price=trade.open_price,
            close_price=trade.close_price,
        )
        for trade in report.trades
    ]
    curve = build_equity_curve_from_closed_trades(closed_trades)
    pnl_points = _curve_points_from_closed_trades(closed_trades)
    metric_net = _metric_amount(report, "Total Net Profit", "Beneficio Neto")
    net_profit = curve[-1] if metric_net is None else metric_net
    _validate_curve_against_net(curve, net_profit)

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    gross_profit = _metric_amount(report, "Gross Profit", "Beneficio Bruto")
    gross_loss_amount = _metric_amount(report, "Gross Loss", "Perdidas Brutas", "Perdidas Brutas")
    if gross_profit is None or gross_loss_amount is None:
        profits = [trade.net_profit for trade in closed_trades]
        gross_profit = sum(value for value in profits if value > 0)
        gross_loss = sum(value for value in profits if value < 0)
    else:
        gross_loss = -abs(gross_loss_amount)
    profit_factor = _metric_amount(report, "Profit Factor", "Factor de Beneficio")
    if profit_factor is None:
        profit_factor = gross_profit / abs(gross_loss) if gross_loss else (float("inf") if gross_profit else 0.0)

    start_year, end_year = _period_years(report, period_name)
    balance_dd_metric, equity_dd_metric = maximal_drawdowns_from_report(report)
    return PeriodReport(
        period_name=period_name,
        start_year=start_year,
        end_year=end_year,
        symbol=report.symbol,
        timeframe=report.timeframe,
        pnl_curve_001=curve,
        net_profit_001=net_profit,
        valley_dd_001=valley_dd,
        point_dd_001=point_dd,
        profit_factor=float(profit_factor),
        return_dd_ratio=net_profit / max(valley_dd, 1.0),
        trades=len(closed_trades),
        gross_profit=float(gross_profit) if gross_profit is not None else None,
        gross_loss=float(gross_loss) if gross_loss is not None else None,
        closed_trades=closed_trades,
        pnl_points_001=pnl_points,
        source_path=str(report.path),
        start_date=report.period_start,
        end_date=report.period_end,
        balance_dd_metric_001=balance_dd_metric,
        equity_dd_metric_001=equity_dd_metric,
    )


def calc_combined_profit_factor(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> float:
    if (
        report_2020_2024.gross_profit is not None
        and report_2020_2024.gross_loss is not None
        and report_2025_2026.gross_profit is not None
        and report_2025_2026.gross_loss is not None
    ):
        gross_profit = report_2020_2024.gross_profit + report_2025_2026.gross_profit
        gross_loss = report_2020_2024.gross_loss + report_2025_2026.gross_loss
        if gross_loss == 0:
            return float("inf")
        return gross_profit / abs(gross_loss)
    return min(report_2020_2024.profit_factor, report_2025_2026.profit_factor)


def build_robust_strategy_set(
    set_id: str,
    candidate_id: str,
    symbol: str,
    timeframe: str | None,
    strategy_family: str | None,
    robustness_status: str,
    already_used: bool,
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    *,
    set_path: str = "",
    is_report_path: str = "",
    oos_report_path: str = "",
    final_tick_balance_dd_001: float = 0.0,
    final_tick_equity_dd_001: float = 0.0,
    final_tick_net_profit_001: float = 0.0,
    recent_equity_dd_001: float | None = None,
    has_final_tick_performance: bool = False,
    final_tick_source: str = "Final Tick 6M",
) -> RobustStrategySet:
    if _normalize_symbol(report_2020_2024.symbol) != _normalize_symbol(report_2025_2026.symbol):
        raise ValueError("Cannot merge reports with different symbols")
    _validate_period_order(report_2020_2024, report_2025_2026)

    curve_2020_2026_001 = merge_accumulated_curves(
        report_2020_2024.pnl_curve_001,
        report_2025_2026.pnl_curve_001,
    )
    curve_points = _merge_curve_points(report_2020_2024, report_2025_2026)
    if curve_points:
        curve_2020_2026_001 = [0.0] + [value for _time, value in curve_points]

    net_profit_2020_2026_001 = curve_2020_2026_001[-1]
    valley_dd_2020_2026_001 = calc_valley_dd(curve_2020_2026_001)
    point_dd_2020_2026_001 = calc_point_dd(curve_2020_2026_001)
    return_dd_2020_2026 = net_profit_2020_2026_001 / max(valley_dd_2020_2026_001, 1.0)
    trades_2020_2026 = report_2020_2024.trades + report_2025_2026.trades
    profit_factor_2020_2026 = calc_combined_profit_factor(report_2020_2024, report_2025_2026)
    drawdown_observations = [
        ("2020-2024", report_2020_2024.balance_dd_metric_001, report_2020_2024.equity_dd_metric_001),
        ("2025-2026", report_2025_2026.balance_dd_metric_001, report_2025_2026.equity_dd_metric_001),
    ]
    if final_tick_balance_dd_001 > 0 or final_tick_equity_dd_001 > 0:
        drawdown_observations.append(
            (final_tick_source, float(final_tick_balance_dd_001), float(final_tick_equity_dd_001))
        )
    floating_source, max_balance_dd, max_equity_dd = max(
        drawdown_observations,
        key=lambda item: max(float(item[2]) - float(item[1]), 0.0),
    )
    max_floating_dd = max(float(max_equity_dd) - float(max_balance_dd), 0.0)

    return RobustStrategySet(
        set_id=str(set_id),
        candidate_id=str(candidate_id),
        symbol=_normalize_symbol(symbol or report_2020_2024.symbol),
        timeframe=timeframe or report_2020_2024.timeframe,
        strategy_family=strategy_family,
        robustness_status=robustness_status,
        already_used=already_used,
        report_2020_2024=report_2020_2024,
        report_2025_2026=report_2025_2026,
        curve_2020_2026_001=curve_2020_2026_001,
        net_profit_2020_2026_001=net_profit_2020_2026_001,
        valley_dd_2020_2026_001=valley_dd_2020_2026_001,
        point_dd_2020_2026_001=point_dd_2020_2026_001,
        profit_factor_2020_2026=profit_factor_2020_2026,
        return_dd_2020_2026=return_dd_2020_2026,
        trades_2020_2026=trades_2020_2026,
        set_path=set_path,
        is_report_path=is_report_path,
        oos_report_path=oos_report_path,
        curve_points_2020_2026_001=curve_points,
        max_balance_dd_001=max(float(max_balance_dd), 0.0),
        max_equity_dd_001=max(float(max_equity_dd), 0.0),
        max_floating_dd_001=max_floating_dd,
        floating_dd_source=floating_source,
        recent_net_profit_001=float(final_tick_net_profit_001),
        recent_equity_dd_001=max(float(
            final_tick_equity_dd_001 if recent_equity_dd_001 is None else recent_equity_dd_001
        ), 0.0),
        has_recent_performance=bool(has_final_tick_performance),
    )


def slice_strategy_set_to_month(
    strategy: RobustStrategySet,
    target_month: int,
) -> RobustStrategySet:
    """Return the strategy curve restricted to one calendar month across all years.

    The source points are accumulated trade P/L values.  We first recover each
    closed-trade increment, then keep only trades whose close timestamp belongs
    to ``target_month``.  Concatenating those increments chronologically gives a
    seasonal history such as every January available in the base + OOS reports.
    """
    if not 1 <= int(target_month) <= 12:
        raise ValueError("target_month must be between 1 and 12")
    if not strategy.curve_points_2020_2026_001:
        raise ValueError("Strategy has no timestamped trade curve")

    selected: list[tuple[datetime, float]] = []
    previous_value = 0.0
    for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
        increment = float(accumulated_value) - previous_value
        previous_value = float(accumulated_value)
        if timestamp.month == int(target_month):
            selected.append((timestamp, increment))

    total = 0.0
    curve = [0.0]
    points: list[tuple[datetime, float]] = []
    pnl_by_year: dict[int, float] = {}
    gross_profit = 0.0
    gross_loss = 0.0
    for timestamp, increment in selected:
        total += increment
        curve.append(total)
        points.append((timestamp, total))
        pnl_by_year[timestamp.year] = pnl_by_year.get(timestamp.year, 0.0) + increment
        if increment >= 0:
            gross_profit += increment
        else:
            gross_loss += increment

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    years = tuple(sorted(pnl_by_year))
    positive_years = tuple(year for year in years if pnl_by_year[year] > 0)

    return RobustStrategySet(
        set_id=strategy.set_id,
        candidate_id=strategy.candidate_id,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        strategy_family=strategy.strategy_family,
        robustness_status=strategy.robustness_status,
        already_used=strategy.already_used,
        report_2020_2024=strategy.report_2020_2024,
        report_2025_2026=strategy.report_2025_2026,
        curve_2020_2026_001=curve,
        net_profit_2020_2026_001=total,
        valley_dd_2020_2026_001=valley_dd,
        point_dd_2020_2026_001=point_dd,
        profit_factor_2020_2026=profit_factor,
        return_dd_2020_2026=total / max(valley_dd, 1.0),
        trades_2020_2026=len(selected),
        set_path=strategy.set_path,
        is_report_path=strategy.is_report_path,
        oos_report_path=strategy.oos_report_path,
        curve_points_2020_2026_001=points,
        target_month=int(target_month),
        month_years=years,
        positive_month_years=positive_years,
        max_balance_dd_001=strategy.max_balance_dd_001,
        max_equity_dd_001=strategy.max_equity_dd_001,
        max_floating_dd_001=strategy.max_floating_dd_001,
        floating_dd_source=strategy.floating_dd_source,
        recent_net_profit_001=strategy.recent_net_profit_001,
        recent_equity_dd_001=strategy.recent_equity_dd_001,
        has_recent_performance=strategy.has_recent_performance,
    )


def slice_strategy_sets_to_month(
    strategies: Sequence[RobustStrategySet],
    target_month: int,
) -> tuple[list[RobustStrategySet], list[str]]:
    """Build seasonal curves and report candidates without timestamped history."""
    sliced: list[RobustStrategySet] = []
    skipped = 0
    for strategy in strategies:
        try:
            sliced.append(slice_strategy_set_to_month(strategy, target_month))
        except ValueError:
            skipped += 1
    warnings = []
    if skipped:
        warnings.append(
            f"{skipped} candidato(s) omitido(s): no tienen curva historica con fechas para el mes objetivo."
        )
    return sliced, warnings


def set_file_has_enabled_grid(set_path: str | Path) -> bool:
    """Return True only when a .set file explicitly has EnableGrid=true."""
    path = resolve_workspace_path(set_path)
    if not path.is_file():
        return False
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() != "enablegrid":
            continue
        first_value = value.split("||", 1)[0].strip().lower()
        return first_value in {"true", "1", "yes", "y", "si", "sí"}
    return False


def filter_rows_grid_off(rows: Sequence[object]) -> tuple[list[object], list[str]]:
    """Remove candidate rows whose .set explicitly enables grid trading."""
    filtered: list[object] = []
    skipped_grid = 0
    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if set_path and set_file_has_enabled_grid(set_path):
            skipped_grid += 1
            continue
        filtered.append(row)
    warnings = []
    if skipped_grid:
        warnings.append(f"Grid OFF: {skipped_grid} candidato(s) omitido(s) por EnableGrid=true.")
    return filtered, warnings


def validate_strict_monthly_portfolio(
    strategies: Sequence[RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    lookback_years: int = 5,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    """Validate a monthly portfolio year-by-year, by DD caps, and by dominance.

    The optimizer builds the portfolio on the selected month aggregated across
    history.  This stricter audit checks the fixed final allocation against each
    selected-month year in the latest ``lookback_years`` available years,
    verifies that every calendar month in that same window respects the DD caps,
    then verifies that the selected month is the best aggregate month by net.
    """
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")
    years_back = max(int(lookback_years), 1)

    increments: list[tuple[datetime, float]] = []
    for strategy in strategies:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0 or not strategy.curve_points_2020_2026_001:
            continue
        previous_value = 0.0
        for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
            increment = (float(accumulated_value) - previous_value) * units
            previous_value = float(accumulated_value)
            increments.append((timestamp, increment))

    if not increments:
        return {
            "passed": False,
            "target_month": month,
            "lookback_years": years_back,
            "years": [],
            "yearly": [],
            "monthly_dd": {},
            "month_net_by_month": {},
            "best_month": None,
            "best_month_net": 0.0,
            "target_month_net": 0.0,
            "reasons": ["sin trades fechados para validar el portafolio mensual"],
            "enforce_point_dd": bool(enforce_point_dd),
        }

    target_month_years = [
        timestamp.year
        for timestamp, _increment in increments
        if timestamp.month == month
    ]
    latest_year = max(target_month_years) if target_month_years else max(
        timestamp.year for timestamp, _increment in increments
    )
    earliest_year = latest_year - years_back + 1
    years = list(range(earliest_year, latest_year + 1))
    increments = [
        (timestamp, increment)
        for timestamp, increment in increments
        if earliest_year <= timestamp.year <= latest_year
    ]

    reasons: list[str] = []
    yearly: list[dict[str, object]] = []
    for year in years:
        year_month_increments = [
            (timestamp, increment)
            for timestamp, increment in increments
            if timestamp.year == year and timestamp.month == month
        ]
        year_month_increments.sort(key=lambda item: item[0])
        total = 0.0
        curve = [0.0]
        for _timestamp, increment in year_month_increments:
            total += increment
            curve.append(total)
        valley_dd = calc_valley_dd(curve)
        point_dd = calc_point_dd(curve)
        passed_year = (
            bool(year_month_increments)
            and total > 0
            and valley_dd <= float(target_valley_dd) + 1e-9
            and (
                not enforce_point_dd
                or point_dd <= float(target_point_dd) + 1e-9
            )
        )
        if not passed_year:
            if not year_month_increments:
                reasons.append(f"{year}: sin trades en mes {month:02d}")
            elif total <= 0:
                reasons.append(f"{year}: net {total:,.2f} <= 0 en mes {month:02d}")
            elif valley_dd > float(target_valley_dd) + 1e-9:
                reasons.append(
                    f"{year}: DD valle {valley_dd:,.2f} > {float(target_valley_dd):,.2f}"
                )
            elif enforce_point_dd and point_dd > float(target_point_dd) + 1e-9:
                reasons.append(
                    f"{year}: DD puntual {point_dd:,.2f} > {float(target_point_dd):,.2f}"
                )
        yearly.append(
            {
                "year": year,
                "trades": len(year_month_increments),
                "net": total,
                "valley_dd": valley_dd,
                "point_dd": point_dd,
                "passed": passed_year,
            }
        )

    month_net_by_month = {item: 0.0 for item in range(1, 13)}
    for timestamp, increment in increments:
        month_net_by_month[timestamp.month] += increment

    monthly_dd: dict[str, dict[str, object]] = {}
    for month_no in range(1, 13):
        month_increments = [
            (timestamp, increment)
            for timestamp, increment in increments
            if timestamp.month == month_no
        ]
        month_increments.sort(key=lambda item: item[0])
        curve = [0.0]
        total = 0.0
        for _timestamp, increment in month_increments:
            total += increment
            curve.append(total)
        valley_dd = calc_valley_dd(curve)
        point_dd = calc_point_dd(curve)
        passed_dd = (
            valley_dd <= float(target_valley_dd) + 1e-9
            and (
                not enforce_point_dd
                or point_dd <= float(target_point_dd) + 1e-9
            )
        )
        if not passed_dd:
            label = f"mes {month_no:02d}"
            if valley_dd > float(target_valley_dd) + 1e-9:
                reasons.append(
                    f"{label}: DD valle {valley_dd:,.2f} > {float(target_valley_dd):,.2f}"
                )
            if enforce_point_dd and point_dd > float(target_point_dd) + 1e-9:
                reasons.append(
                    f"{label}: DD puntual {point_dd:,.2f} > {float(target_point_dd):,.2f}"
                )
        monthly_dd[f"{month_no:02d}"] = {
            "trades": len(month_increments),
            "net": total,
            "valley_dd": valley_dd,
            "point_dd": point_dd,
            "passed_dd": passed_dd,
        }

    best_month, best_month_net = max(
        month_net_by_month.items(),
        key=lambda item: (item[1], -abs(item[0] - month)),
    )
    target_month_net = month_net_by_month.get(month, 0.0)
    if best_month != month:
        reasons.append(
            f"mes {month:02d} no es el mejor de los ultimos {years_back} años "
            f"(mejor {best_month:02d}: {best_month_net:,.2f} vs {target_month_net:,.2f})"
        )

    return {
        "passed": not reasons,
        "target_month": month,
        "lookback_years": years_back,
        "years": years,
        "yearly": yearly,
        "monthly_dd": monthly_dd,
        "month_net_by_month": {
            f"{key:02d}": value for key, value in sorted(month_net_by_month.items())
        },
        "best_month": best_month,
        "best_month_net": best_month_net,
        "target_month_net": target_month_net,
        "target_valley_dd": float(target_valley_dd),
        "target_point_dd": float(target_point_dd),
        "enforce_point_dd": bool(enforce_point_dd),
        "reasons": reasons,
    }


def summarize_robust_rows(rows: Iterable[object], used_set_paths: Iterable[str]) -> PortfolioAvailability:
    used = {_norm_path(path) for path in used_set_paths}
    robust_accepted = 0
    already_used = 0
    by_symbol: dict[str, int] = {}
    seen: set[str] = set()
    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if not set_path or set_path in seen:
            continue
        seen.add(set_path)
        robust_accepted += 1
        symbol = portfolio_display_symbol(str(_row_value(row, "target_symbol", "symbol", default="")))
        if _norm_path(set_path) in used:
            already_used += 1
            continue
        by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
    available = sum(by_symbol.values())
    return PortfolioAvailability(
        robust_accepted=robust_accepted,
        already_used=already_used,
        available=available,
        symbols_available=len(by_symbol),
        by_symbol=dict(sorted(by_symbol.items())),
    )


def load_robust_sets_from_rows(
    rows: Sequence[object],
    used_set_paths: Iterable[str],
    *,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[RobustStrategySet], list[str]]:
    warnings: list[str] = []
    used = {_norm_path(path) for path in used_set_paths}
    latest_by_stem: dict[str, object] = {}

    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if not set_path:
            continue
        account_type = str(_row_value(row, "account_type", default="")).strip().upper()
        stem = _logical_stem(set_path)
        if account_type:
            stem = f"{account_type}:{stem}"
        current = latest_by_stem.get(stem)
        if current is None or _row_int(row, "source_candidate_id", "candidate_id") > _row_int(
            current, "source_candidate_id", "candidate_id"
        ):
            latest_by_stem[stem] = row

    loaded: list[RobustStrategySet] = []
    skipped_missing = 0
    skipped_parse = 0
    missing_examples: list[str] = []
    parse_examples: list[str] = []
    candidates = list(latest_by_stem.values())
    for index, row in enumerate(candidates, start=1):
        set_path = str(_row_value(row, "set_path", default=""))
        if _norm_path(set_path) in used:
            continue
        if progress:
            progress(f"Analizando set Final Tick OK {index}/{len(candidates)}")
        is_path = resolve_workspace_path(str(_row_value(row, "is_report_path", "report_path", default="")))
        oos_path = resolve_workspace_path(str(_row_value(row, "oos_report_path", "robust_report_path", default="")))
        if not is_path.is_file() or not oos_path.is_file():
            skipped_missing += 1
            if len(missing_examples) < 5:
                missing_parts = []
                if not is_path.is_file():
                    missing_parts.append(f"base={is_path.name or '-'}")
                if not oos_path.is_file():
                    missing_parts.append(f"robustez={oos_path.name or '-'}")
                missing_examples.append(
                    f"{Path(set_path).name}: " + ", ".join(missing_parts)
                )
            continue
        try:
            is_period = period_report_from_strategy_report(parse(is_path), "2020_2024")
            oos_period = period_report_from_strategy_report(parse(oos_path), "2025_2026")
            final_tick_balance_dd = float(_row_value(row, "max_balance_dd_001", default=0.0) or 0.0)
            final_tick_equity_dd = float(_row_value(row, "max_equity_dd_001", default=0.0) or 0.0)
            final_tick_source = str(_row_value(row, "floating_dd_source", default="guardado") or "guardado")
            final_tick_net_profit = float(_row_value(row, "recent_net_profit_001", default=0.0) or 0.0)
            recent_equity_dd = float(_row_value(row, "recent_equity_dd_001", default=0.0) or 0.0)
            has_final_tick_performance = bool(
                _row_value(row, "has_recent_performance", default=False)
            )
            recent_report_text = str(
                _row_value(row, "final_tick_report_path", "real_tick_report_path", default="") or ""
            ).strip()
            if recent_report_text:
                recent_report_path = resolve_workspace_path(recent_report_text)
                if not recent_report_path.is_file():
                    raise FileNotFoundError(f"Final Tick 6M report not found: {recent_report_path}")
                recent_report = parse(recent_report_path)
                final_tick_balance_dd, final_tick_equity_dd = maximal_drawdowns_from_report(recent_report)
                metric_net_profit = _metric_amount(recent_report, "Total Net Profit", "Beneficio Neto")
                if metric_net_profit is None:
                    raise ValueError("Final Tick 6M report has no total net profit metric")
                final_tick_net_profit = float(metric_net_profit)
                recent_equity_dd = final_tick_equity_dd
                has_final_tick_performance = True
                final_tick_source = "Final Tick 6M"
            target_symbol = str(_row_value(row, "target_symbol", "symbol", default=is_period.symbol))
            loaded.append(
                build_robust_strategy_set(
                    set_id=set_path,
                    candidate_id=str(_row_value(row, "candidate_id", "id", default=set_path)),
                    symbol=target_symbol,
                    timeframe=str(_row_value(row, "period", "timeframe", default=is_period.timeframe)),
                    strategy_family=str(_row_value(row, "family", "strategy_family", default="")),
                    robustness_status="accepted",
                    already_used=False,
                    report_2020_2024=is_period,
                    report_2025_2026=oos_period,
                    set_path=set_path,
                    is_report_path=str(is_path),
                    oos_report_path=str(oos_path),
                    final_tick_balance_dd_001=final_tick_balance_dd,
                    final_tick_equity_dd_001=final_tick_equity_dd,
                    final_tick_net_profit_001=final_tick_net_profit,
                    recent_equity_dd_001=recent_equity_dd,
                    has_final_tick_performance=has_final_tick_performance,
                    final_tick_source=final_tick_source,
                )
            )
        except Exception as exc:
            skipped_parse += 1
            if len(parse_examples) < 5:
                message = str(exc).strip() or "sin detalle"
                parse_examples.append(
                    f"{Path(set_path).name}: {type(exc).__name__}: {message}"
                )
            continue

    if skipped_missing:
        warnings.append(f"{skipped_missing} candidato(s) omitido(s): faltan reportes base o robustez.")
        warnings.append("Ejemplos de reportes ausentes: " + " | ".join(missing_examples))
    if skipped_parse:
        warnings.append(f"{skipped_parse} candidato(s) omitido(s): reporte ilegible o curva invalida.")
        warnings.append("Ejemplos de errores de carga: " + " | ".join(parse_examples))
    return loaded, warnings


def filter_eligible_sets(
    sets: list[RobustStrategySet],
    min_trades_2020_2026: int = 100,
) -> list[RobustStrategySet]:
    eligible: list[RobustStrategySet] = []
    for strategy in sets:
        if strategy.robustness_status != "accepted":
            continue
        if strategy.already_used:
            continue
        if not strategy.curve_2020_2026_001:
            continue
        if strategy.trades_2020_2026 < min_trades_2020_2026:
            continue
        if strategy.net_profit_2020_2026_001 <= 0:
            continue
        if strategy.has_recent_performance:
            recent_recovery = strategy.recent_net_profit_001 / max(strategy.recent_equity_dd_001, 1.0)
            if recent_recovery < MIN_RECENT_EQUITY_RECOVERY:
                continue
        eligible.append(strategy)
    return eligible


def recent_positive_month_count(
    monthly: dict[int, dict[int, float]],
    end_date: str | datetime | None = None,
    *,
    window_months: int = 6,
) -> int:
    end = _coerce_month_end(end_date)
    if end is None:
        end = _latest_month_from_monthly(monthly)
    if end is None or window_months <= 0:
        return 0
    count = 0
    for year, month in _month_window(end.year, end.month, window_months):
        if float(monthly.get(year, {}).get(month, 0.0)) > 0:
            count += 1
    return count


def filter_rows_by_recent_positive_months(
    rows: Sequence[object],
    *,
    min_positive_months: int = 3,
    window_months: int = 6,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[object], list[str]]:
    filtered: list[object] = []
    skipped_no_report = 0
    skipped_parse = 0
    skipped_months = 0

    for index, row in enumerate(rows, start=1):
        if progress:
            progress(f"Filtrando meses positivos {index}/{len(rows)}")
        report_path = _first_existing_report_path(
            row,
            "final_tick_report_path",
            "real_tick_report_path",
            "final_ohlc_report_path",
            "ohlc_report_path",
        )
        if report_path is None:
            skipped_no_report += 1
            continue
        try:
            report = parse(report_path)
            end_date = str(_row_value(row, "final_tick_to_date", "to_date", default=""))
            positives = recent_positive_month_count(
                report.monthly,
                end_date or report.period_end,
                window_months=window_months,
            )
        except Exception:
            skipped_parse += 1
            continue
        if positives >= min_positive_months:
            filtered.append(row)
        else:
            skipped_months += 1

    warnings: list[str] = []
    if skipped_months or skipped_no_report or skipped_parse:
        warnings.append(
            f"Filtro {min_positive_months}/{window_months} meses positivos: "
            f"{skipped_months} omitido(s) por meses insuficientes"
            + (f", {skipped_no_report} sin reporte Final Tick 6M" if skipped_no_report else "")
            + (f", {skipped_parse} con reporte ilegible" if skipped_parse else "")
            + "."
        )
    return filtered, warnings


def score_set_for_portfolio(
    strategy: RobustStrategySet,
    min_trades_2020_2026: int = 100,
) -> float:
    profit_score = max(strategy.net_profit_2020_2026_001, 0.0)
    pf_score = min(max(strategy.profit_factor_2020_2026, 1.0), 3.0)
    return_dd_score = max(strategy.return_dd_2020_2026, 0.1)
    trades_confidence = min(1.0, strategy.trades_2020_2026 / max(min_trades_2020_2026, 1))
    floating_buffer = strategy.max_floating_dd_001
    dd_penalty = max(strategy.valley_dd_2020_2026_001 + floating_buffer, 1.0)
    recent_factor = 1.0
    if strategy.has_recent_performance:
        recent_factor = min(
            max(strategy.recent_net_profit_001 / max(strategy.recent_equity_dd_001, 1.0), 0.1),
            3.0,
        )
    return float((profit_score * pf_score * return_dd_score * trades_confidence * recent_factor) / dd_penalty)


def select_top_k_per_symbol(
    sets: list[RobustStrategySet],
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    *,
    min_trades_2020_2026: int = 100,
) -> list[RobustStrategySet]:
    grouped: dict[str, list[RobustStrategySet]] = {}
    for strategy in sets:
        grouped.setdefault(portfolio_symbol_key(strategy.symbol), []).append(strategy)

    selected: list[RobustStrategySet] = []
    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
            reverse=True,
        )
        selected.extend(ordered[:top_k_per_symbol])

    selected = sorted(
        selected,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )
    if max_total_candidates is not None:
        selected = _limit_candidates_with_group_reserve(
            selected,
            max_total_candidates,
            min_trades_2020_2026,
        )
    return selected


def _limit_candidates_with_group_reserve(
    candidates: list[RobustStrategySet],
    max_total_candidates: int,
    min_trades_2020_2026: int,
) -> list[RobustStrategySet]:
    if max_total_candidates <= 0:
        return []
    if len(candidates) <= max_total_candidates:
        return candidates

    ordered = sorted(
        candidates,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )
    symbols: dict[str, list[RobustStrategySet]] = {}
    for candidate in ordered:
        symbols.setdefault(portfolio_symbol_key(candidate.symbol), []).append(candidate)

    selected: list[RobustStrategySet] = []
    selected_ids: set[str] = set()
    ordered_symbols = sorted(
        symbols.values(),
        key=lambda group: score_set_for_portfolio(group[0], min_trades_2020_2026),
        reverse=True,
    )
    for group in ordered_symbols:
        if len(selected) >= max_total_candidates:
            break
        candidate = group[0]
        selected.append(candidate)
        selected_ids.add(candidate.set_id)

    for candidate in ordered:
        if len(selected) >= max_total_candidates:
            break
        if candidate.set_id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.set_id)
    return selected


def evaluate_portfolio(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
    target_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> PortfolioEvaluation:
    active_sets = [strategy for strategy in sets if allocations.get(strategy.set_id, 0) > 0]
    if not active_sets:
        return PortfolioEvaluation(
            allocations=allocations.copy(),
            equity_curve_2020_2026=[0.0],
            total_net_profit=0.0,
            valley_dd=0.0,
            point_dd=0.0,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            valley_usage_pct=0.0,
            point_usage_pct=0.0,
            total_units=0,
            total_lot=0.0,
            active_strategies=0,
            daily_dd=0.0,
            target_daily_dd=target_daily_dd,
            daily_usage_pct=0.0,
            daily_dd_full_history=bool(daily_dd_full_history),
            enforce_point_dd=bool(enforce_point_dd),
            closed_valley_dd=0.0,
            floating_dd_buffer=0.0,
        )

    if all(strategy.curve_points_2020_2026_001 for strategy in active_sets):
        portfolio_curve = _evaluate_portfolio_on_time_axis(active_sets, allocations)
    else:
        length = len(active_sets[0].curve_2020_2026_001)
        for strategy in active_sets:
            if len(strategy.curve_2020_2026_001) != length:
                raise ValueError("All 2020-2026 curves must have the same length")
        portfolio_curve = [0.0] * length
        for strategy in active_sets:
            units = allocations[strategy.set_id]
            for index, value in enumerate(strategy.curve_2020_2026_001):
                portfolio_curve[index] += value * units

    total_net_profit = portfolio_curve[-1]
    closed_valley_dd = calc_valley_dd(portfolio_curve)
    floating_dd_buffer = sum(
        strategy.max_floating_dd_001 * allocations[strategy.set_id]
        for strategy in active_sets
    )
    valley_dd = closed_valley_dd + floating_dd_buffer
    point_dd = calc_point_dd(portfolio_curve)
    daily_dd = 0.0
    if target_daily_dd is not None:
        daily_dd, _daily_summary = portfolio_daily_closed_floating_dd(
            active_sets,
            allocations,
            full_history=bool(daily_dd_full_history),
        )
    return PortfolioEvaluation(
        allocations=allocations.copy(),
        equity_curve_2020_2026=portfolio_curve,
        total_net_profit=total_net_profit,
        valley_dd=valley_dd,
        point_dd=point_dd,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=valley_dd / target_valley_dd * 100 if target_valley_dd > 0 else 0.0,
        point_usage_pct=point_dd / target_point_dd * 100 if target_point_dd > 0 else 0.0,
        total_units=sum(max(value, 0) for value in allocations.values()),
        total_lot=sum(max(value, 0) for value in allocations.values()) * 0.01,
        active_strategies=sum(1 for value in allocations.values() if value > 0),
        daily_dd=daily_dd,
        target_daily_dd=target_daily_dd,
        daily_usage_pct=daily_dd / target_daily_dd * 100 if target_daily_dd and target_daily_dd > 0 else 0.0,
        daily_dd_full_history=bool(daily_dd_full_history),
        enforce_point_dd=bool(enforce_point_dd),
        closed_valley_dd=closed_valley_dd,
        floating_dd_buffer=floating_dd_buffer,
    )


def portfolio_group_summary(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
) -> dict[str, dict[str, float | int]]:
    stats: dict[str, dict[str, float | int]] = {}
    total_units = 0
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        group = portfolio_group_key(strategy.symbol)
        row = stats.setdefault(group, {"units": 0, "sets": 0, "unit_pct": 0.0})
        row["units"] = int(row["units"]) + units
        row["sets"] = int(row["sets"]) + 1
        total_units += units
    if total_units > 0:
        for row in stats.values():
            row["unit_pct"] = float(row["units"]) / total_units * 100.0
    return dict(sorted(stats.items(), key=lambda item: (-float(item[1]["units"]), item[0])))


def _evaluation_violates_dd_limits(evaluation: PortfolioEvaluation) -> bool:
    if evaluation.valley_dd > evaluation.target_valley_dd + 1e-9:
        return True
    if evaluation.enforce_point_dd and evaluation.point_dd > evaluation.target_point_dd + 1e-9:
        return True
    if (
        evaluation.target_daily_dd is not None
        and evaluation.daily_dd > float(evaluation.target_daily_dd) + 1e-9
    ):
        return True
    return False


def _evaluation_violation_ratio(evaluation: PortfolioEvaluation) -> float:
    ratios = [
        evaluation.valley_dd / max(evaluation.target_valley_dd, 1e-9),
    ]
    if evaluation.enforce_point_dd:
        ratios.append(evaluation.point_dd / max(evaluation.target_point_dd, 1e-9))
    if evaluation.target_daily_dd is not None:
        ratios.append(evaluation.daily_dd / max(float(evaluation.target_daily_dd), 1e-9))
    return max(ratios)


def roboforex_margin_leverage(symbol: str) -> float:
    """Portfolio leverage rule requested for RoboForex portfolios."""
    return 20.0 if portfolio_group_key(symbol) == "Stocks" else 500.0


def roboforex_contract_size(symbol: str) -> float:
    """Contract-size rule requested for the portfolio margin guard."""
    return 100.0 if portfolio_group_key(symbol) == "Stocks" else 1.0


def normalize_margin_profile(profile: str | None) -> str:
    value = str(profile or "roboforex").strip().lower()
    if value in {"ttp", "thetradingpit", "tradingpit", "the_trading_pit"}:
        return "ttp"
    if value in {"axi", "axi trading", "axitrading", "axi_select", "axiselect"}:
        return "axi"
    if value in {"ictrading", "ic trading", "ic", "icmarkets", "ic markets"}:
        return "ictrading"
    return "roboforex"


def margin_profile_label(profile: str | None) -> str:
    return {
        "ttp": "TTP",
        "axi": "AXI",
        "ictrading": "ICTrading",
        "roboforex": "RoboForex",
    }.get(normalize_margin_profile(profile), "RoboForex")


def margin_leverage_for_profile(
    symbol: str,
    *,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
) -> float:
    profile = normalize_margin_profile(margin_profile)
    if profile == "ttp":
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
    return stock_leverage if portfolio_group_key(symbol) == "Stocks" else default_leverage


def margin_contract_size_for_profile(
    symbol: str,
    *,
    margin_profile: str | None = "roboforex",
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    # Contract size is kept explicit and broker-independent for now: stocks use
    # 100 and every other group uses 1, matching the portfolio margin model.
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


def allocation_margin_required(
    strategy: RobustStrategySet,
    units: int,
    *,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    units = max(int(units), 0)
    if units <= 0:
        return 0.0
    lot = units * 0.01
    leverage = margin_leverage_for_profile(
        strategy.symbol,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
    )
    contract_size = margin_contract_size_for_profile(
        strategy.symbol,
        margin_profile=margin_profile,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    if leverage <= 0:
        return float("inf")
    return lot * contract_size * strategy_reference_price(strategy) / leverage


def portfolio_margin_summary(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float,
    max_margin_pct: float,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> dict[str, object]:
    by_set: dict[str, dict[str, float | str | int]] = {}
    total = 0.0
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        leverage = margin_leverage_for_profile(
            strategy.symbol,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
        )
        contract_size = margin_contract_size_for_profile(
            strategy.symbol,
            margin_profile=margin_profile,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
        )
        price = strategy_reference_price(strategy)
        margin = allocation_margin_required(
            strategy,
            units,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
        )
        total += margin
        by_set[strategy.set_id] = {
            "symbol": strategy.symbol,
            "group": portfolio_group_key(strategy.symbol),
            "units": units,
            "lot": units * 0.01,
            "leverage": leverage,
            "contract_size": contract_size,
            "price": price,
            "margin": margin,
        }
    limit = float(balance) * float(max_margin_pct) / 100.0 if balance > 0 else 0.0
    return {
        "enabled": True,
        "balance": float(balance),
        "max_margin_pct": float(max_margin_pct),
        "limit": limit,
        "total": total,
        "usage_pct": total / limit * 100.0 if limit > 0 else 0.0,
        "profile": normalize_margin_profile(margin_profile),
        "profile_label": margin_profile_label(margin_profile),
        "stock_leverage": float(stock_leverage),
        "default_leverage": float(default_leverage),
        "stock_contract_size": float(stock_contract_size),
        "default_contract_size": float(default_contract_size),
        "by_set": by_set,
    }


def allocations_respect_margin_limit(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | None = "roboforex",
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


def _candidate_group_count(sets: list[RobustStrategySet]) -> int:
    return len({portfolio_group_key(strategy.symbol) for strategy in sets})


def _target_group_units_pct_allowed(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
) -> bool:
    if max_units_per_group_pct is None:
        return True
    if _candidate_group_count(sets) <= 1:
        return True
    after_total_units = sum(max(int(value), 0) for value in allocations.values())
    after_target_units = max(int(allocations.get(target_set.set_id, 0)), 0)
    before_total_units = max(after_total_units - 1, 0)
    before_target_units = max(after_target_units - 1, 0)
    if before_total_units <= 0:
        return True

    target_group = portfolio_group_key(target_set.symbol)
    after_group_units = sum(
        max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
        if portfolio_group_key(strategy.symbol) == target_group
    )
    before_group_units = max(after_group_units - 1, 0)
    if before_group_units <= 0:
        return True

    before_active_groups: set[str] = set()
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if strategy.set_id == target_set.set_id:
            units = before_target_units
        if units > 0:
            before_active_groups.add(portfolio_group_key(strategy.symbol))
    if len(before_active_groups) < min(2, _candidate_group_count(sets)):
        return before_group_units < group_unit_cap_bootstrap

    before_group_pct = before_group_units / max(before_total_units, 1)
    if before_group_pct > max_units_per_group_pct + 1e-9:
        return False
    max_units_with_one_step_slack = math.floor(after_total_units * max_units_per_group_pct) + 1
    return after_group_units <= max_units_with_one_step_slack


def can_add_unit(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> bool:
    current_units = allocations.get(target_set.set_id, 0)
    if max_units_per_set is not None and current_units >= max_units_per_set:
        return False
    if max_total_units is not None and sum(allocations.values()) + 1 > max_total_units:
        return False
    if max_units_per_symbol is not None:
        target_symbol = portfolio_symbol_key(target_set.symbol)
        symbol_units = sum(
            allocations.get(strategy.set_id, 0)
            for strategy in sets
            if portfolio_symbol_key(strategy.symbol) == target_symbol
        )
        if symbol_units + 1 > max_units_per_symbol:
            return False
    if max_sets_per_symbol is not None:
        target_symbol = portfolio_symbol_key(target_set.symbol)
        active_same_symbol = sum(
            1
            for strategy in sets
            if portfolio_symbol_key(strategy.symbol) == target_symbol and allocations.get(strategy.set_id, 0) > 0
        )
        if current_units == 0 and active_same_symbol >= max_sets_per_symbol:
            return False
    if max_sets_per_group is not None:
        target_group = portfolio_group_key(target_set.symbol)
        active_same_group = sum(
            1
            for strategy in sets
            if portfolio_group_key(strategy.symbol) == target_group and allocations.get(strategy.set_id, 0) > 0
        )
        if current_units == 0 and active_same_group >= max_sets_per_group:
            return False
    temp_allocations = allocations.copy()
    temp_allocations[target_set.set_id] = current_units + 1
    if not _target_group_units_pct_allowed(
        target_set,
        sets,
        temp_allocations,
        max_units_per_group_pct,
        group_unit_cap_bootstrap,
    ):
        return False
    if not allocations_respect_margin_limit(
        sets,
        temp_allocations,
        balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    ):
        return False
    return True


def violates_correlation_limits(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
) -> tuple[bool, str]:
    if allocations.get(target_set.set_id, 0) > 0:
        return False, ""
    if max_pair_corr is None and max_downside_corr is None and max_dd_overlap is None:
        return False, ""

    for active in sets:
        if active.set_id == target_set.set_id or allocations.get(active.set_id, 0) <= 0:
            continue
        pair = strategy_correlation_pair(target_set, active)
        if max_pair_corr is not None and pair.pearson_corr > max_pair_corr:
            return True, f"pair_corr>{max_pair_corr:.2f} vs {Path(active.set_id).name}"
        if max_downside_corr is not None and pair.downside_corr > max_downside_corr:
            return True, f"downside_corr>{max_downside_corr:.2f} vs {Path(active.set_id).name}"
        if max_dd_overlap is not None and pair.dd_overlap > max_dd_overlap:
            return True, f"dd_overlap>{max_dd_overlap:.2f} vs {Path(active.set_id).name}"
    return False, ""


def _allocations_respect_constraints(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None = None,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> bool:
    total_units = 0
    units_by_symbol: dict[str, int] = {}
    active_sets_by_symbol: dict[str, int] = {}
    active_sets_by_group: dict[str, int] = {}

    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        total_units += units
        if max_units_per_set is not None and units > max_units_per_set:
            return False
        if units <= 0:
            continue
        symbol_key = portfolio_symbol_key(strategy.symbol)
        units_by_symbol[symbol_key] = units_by_symbol.get(symbol_key, 0) + units
        active_sets_by_symbol[symbol_key] = active_sets_by_symbol.get(symbol_key, 0) + 1
        group_key = portfolio_group_key(strategy.symbol)
        active_sets_by_group[group_key] = active_sets_by_group.get(group_key, 0) + 1

    if max_total_units is not None and total_units > max_total_units:
        return False
    if max_units_per_symbol is not None:
        for units in units_by_symbol.values():
            if units > max_units_per_symbol:
                return False
    if max_sets_per_symbol is not None:
        for count in active_sets_by_symbol.values():
            if count > max_sets_per_symbol:
                return False
    if max_sets_per_group is not None:
        for count in active_sets_by_group.values():
            if count > max_sets_per_group:
                return False
    if not allocations_respect_margin_limit(
        sets,
        allocations,
        balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    ):
        return False
    return True


def score_increment(
    current: PortfolioEvaluation,
    temp: PortfolioEvaluation,
    current_units_for_set: int,
    portfolio_type: PortfolioType,
) -> float:
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 0:
        return float("-inf")

    valley_cost = temp.valley_dd - current.valley_dd
    point_cost = temp.point_dd - current.point_dd
    epsilon = 1e-9
    valley_cost_pct = max(valley_cost, 0.0) / max(temp.target_valley_dd, epsilon)
    point_cost_pct = (
        max(point_cost, 0.0) / max(temp.target_point_dd, epsilon)
        if temp.enforce_point_dd
        else 0.0
    )
    risk_cost = max(valley_cost_pct, point_cost_pct, epsilon)

    if valley_cost < 0 and (point_cost <= 0 or not temp.enforce_point_dd):
        base_score = gain * 10.0 + abs(valley_cost)
    elif valley_cost <= 0 and (point_cost <= 0 or not temp.enforce_point_dd):
        base_score = gain * 5.0
    else:
        if portfolio_type == PortfolioType.CONSERVATIVE:
            concentration_penalty = 1.0 + current_units_for_set * 0.30
            base_score = gain / risk_cost
        elif portfolio_type == PortfolioType.BALANCED:
            concentration_penalty = 1.0 + current_units_for_set * 0.15
            base_score = gain / risk_cost
        elif portfolio_type == PortfolioType.AGGRESSIVE:
            concentration_penalty = 1.0 + current_units_for_set * 0.05
            base_score = gain * 0.70 + (gain / risk_cost) * 0.30
        else:
            concentration_penalty = 1.0 + current_units_for_set * 0.15
            base_score = gain / risk_cost
        base_score = base_score / concentration_penalty

    if temp.enforce_point_dd and temp.point_usage_pct > 95:
        base_score *= 0.70
    if temp.valley_usage_pct > 98:
        base_score *= 0.85
    return float(base_score)


def build_portfolio_greedy(
    sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    initial_allocations: dict[str, int] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    fixed_set_ids: Sequence[str] | None = None,
    allow_fixed_reductions_for_repair: bool = False,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
    target_valley_dd = capital * valley_dd_pct / 100.0
    target_point_dd = capital * point_dd_pct / 100.0
    allocations = {
        strategy.set_id: max(int((initial_allocations or {}).get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    if not _allocations_respect_constraints(
        sets,
        allocations,
        max_units_per_set,
        max_total_units,
        max_units_per_symbol,
        max_sets_per_symbol,
        max_sets_per_group,
        margin_balance,
        max_margin_pct,
        margin_profile,
        stock_leverage,
        default_leverage,
        stock_contract_size,
        default_contract_size,
    ):
        raise ValueError("Initial portfolio allocations violate configured limits")
    current = evaluate_portfolio(
        sets,
        allocations,
        target_valley_dd,
        target_point_dd,
        max_daily_dd,
        enforce_point_dd,
        daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(current) and not allow_fixed_reductions_for_repair:
        raise ValueError("Initial portfolio allocations violate DD limits")
    decision_log: list[OptimizationDecision] = []
    step = sum(allocations.values())
    max_steps = max_total_units if max_total_units is not None else 10000
    correlation_rejections = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    fixed_ids = {str(set_id) for set_id in (fixed_set_ids or ())}

    while step < max_steps:
        best_candidate: dict[str, object] | None = None
        best_repair_candidate: dict[str, object] | None = None
        blocked_by_risk = False
        for strategy in sets:
            if strategy.set_id in fixed_ids:
                continue
            if (
                maximum_active_strategies is not None
                and current.active_strategies >= maximum_active_strategies
                and allocations.get(strategy.set_id, 0) <= 0
            ):
                continue
            if (
                minimum_active_strategies is not None
                and current.active_strategies < minimum_active_strategies
                and allocations.get(strategy.set_id, 0) > 0
            ):
                # During repair, fill the missing strategy slots before adding
                # more risk to strategies that are already active.
                continue
            if not can_add_unit(
                target_set=strategy,
                sets=sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                continue
            rejected_by_corr, corr_reason = violates_correlation_limits(
                strategy,
                sets,
                allocations,
                max_pair_corr,
                max_downside_corr,
                max_dd_overlap,
            )
            if rejected_by_corr:
                correlation_rejections += 1
                decision_log.append(
                    OptimizationDecision(
                        step=step + 1,
                        action="reject_corr",
                        set_id=strategy.set_id,
                        from_set_id=None,
                        to_set_id=None,
                        gain=0.0,
                        valley_cost=0.0,
                        point_cost=0.0,
                        score=float("-inf"),
                        portfolio_net_profit_after=current.total_net_profit,
                        portfolio_valley_dd_after=current.valley_dd,
                        portfolio_point_dd_after=current.point_dd,
                        reason=corr_reason,
                    )
                )
                continue
            temp_allocations = allocations.copy()
            temp_allocations[strategy.set_id] += 1
            temp = evaluate_portfolio(
                sets,
                temp_allocations,
                target_valley_dd,
                target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            )
            if _evaluation_violates_dd_limits(temp):
                blocked_by_risk = True
                if allow_fixed_reductions_for_repair:
                    current_violation = _evaluation_violation_ratio(current)
                    temp_violation = _evaluation_violation_ratio(temp)
                    if temp_violation < current_violation - 1e-9:
                        repair_score = (current_violation - temp_violation) * 1_000_000_000.0
                        repair_score += max(temp.total_net_profit - current.total_net_profit, 0.0)
                        if (
                            best_repair_candidate is None
                            or repair_score > float(best_repair_candidate["score"])
                        ):
                            best_repair_candidate = {
                                "set": strategy,
                                "allocations": temp_allocations,
                                "evaluation": temp,
                                "score": repair_score,
                                "reason": "Replacement increment reduced the DD violation",
                            }
                continue
            if max_portfolio_corr is not None and portfolio_curves:
                worst_portfolio_corr = max(
                    curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                    for curve in portfolio_curves
                )
                if worst_portfolio_corr > max_portfolio_corr:
                    blocked_by_risk = True
                    correlation_rejections += 1
                    decision_log.append(
                        OptimizationDecision(
                            step=step + 1,
                            action="reject_portfolio_corr",
                            set_id=strategy.set_id,
                            from_set_id=None,
                            to_set_id=None,
                            gain=0.0,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=float("-inf"),
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason=f"portfolio_corr>{max_portfolio_corr:.2f}",
                        )
                    )
                    continue
            score = score_increment(current, temp, allocations[strategy.set_id], portfolio_type)
            if score == float("-inf"):
                continue
            if best_candidate is None or score > float(best_candidate["score"]):
                best_candidate = {
                    "set": strategy,
                    "allocations": temp_allocations,
                    "evaluation": temp,
                    "score": score,
                    "reason": "Best valid +0.01 increment",
                }

        if best_candidate is None and best_repair_candidate is not None:
            best_candidate = best_repair_candidate

        if best_candidate is None and allow_fixed_reductions_for_repair:
            current_violation = _evaluation_violation_ratio(current)
            missing_required_strategy = (
                minimum_active_strategies is not None
                and current.active_strategies < minimum_active_strategies
            )
            if current_violation > 1.0 or (missing_required_strategy and blocked_by_risk):
                best_reduction: tuple[float, float, RobustStrategySet, dict[str, int], PortfolioEvaluation] | None = None
                for strategy in sets:
                    if strategy.set_id not in fixed_ids or allocations.get(strategy.set_id, 0) <= 1:
                        continue
                    temp_allocations = allocations.copy()
                    temp_allocations[strategy.set_id] -= 1
                    temp = evaluate_portfolio(
                        sets,
                        temp_allocations,
                        target_valley_dd,
                        target_point_dd,
                        max_daily_dd,
                        enforce_point_dd,
                        daily_dd_full_history,
                    )
                    temp_violation = _evaluation_violation_ratio(temp)
                    if temp_violation >= current_violation - 1e-9:
                        continue
                    choice = (temp_violation, -temp.total_net_profit, strategy, temp_allocations, temp)
                    if best_reduction is None or choice[:2] < best_reduction[:2]:
                        best_reduction = choice
                if best_reduction is not None:
                    _violation, _negative_net, reduced_set, allocations, current = best_reduction
                    step = sum(allocations.values())
                    decision_log.append(
                        OptimizationDecision(
                            step=len(decision_log) + 1,
                            action="reduce_unit_for_repair",
                            set_id=reduced_set.set_id,
                            from_set_id=reduced_set.set_id,
                            to_set_id=None,
                            gain=-reduced_set.net_profit_2020_2026_001,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=-current_violation,
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason="Minimum existing-lot reduction required to make portfolio repair feasible",
                        )
                    )
                    continue

        if best_candidate is None:
            stop_reason = "No valid +0.01 increment found without breaking DD constraints"
            break

        selected_set = best_candidate["set"]
        assert isinstance(selected_set, RobustStrategySet)
        previous = current
        allocations = best_candidate["allocations"]  # type: ignore[assignment]
        current = best_candidate["evaluation"]  # type: ignore[assignment]
        step += 1
        decision_log.append(
            OptimizationDecision(
                step=step,
                action="add_unit",
                set_id=selected_set.set_id,
                from_set_id=None,
                to_set_id=None,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_candidate["score"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason=str(best_candidate.get("reason") or "Best valid +0.01 increment"),
            )
        )
    else:
        stop_reason = "Max optimizer iterations reached"

    return allocations, current, decision_log, stop_reason, correlation_rejections


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    max_iterations: int = 1000,
    protected_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    decision_log: list[OptimizationDecision] = []
    iteration = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    protected_ids = {str(set_id) for set_id in (protected_set_ids or ())}
    while iteration < max_iterations:
        iteration += 1
        best_move: dict[str, object] | None = None
        for from_set in sets:
            if allocations.get(from_set.set_id, 0) <= 0:
                continue
            if from_set.set_id in protected_ids and allocations.get(from_set.set_id, 0) <= 1:
                continue
            for to_set in sets:
                if from_set.set_id == to_set.set_id:
                    continue
                temp_allocations = allocations.copy()
                temp_allocations[from_set.set_id] -= 1
                temp_allocations[to_set.set_id] += 1
                if minimum_active_strategies is not None:
                    active_count = sum(1 for units in temp_allocations.values() if units > 0)
                    if active_count < minimum_active_strategies:
                        continue
                if not _allocations_respect_constraints(
                    sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    to_set,
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(to_set.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[to_set.set_id] = 0
                    rejected_by_corr, _corr_reason = violates_correlation_limits(
                        to_set,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    target_valley_dd,
                    target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    worst_portfolio_corr = max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    )
                    if worst_portfolio_corr > max_portfolio_corr:
                        continue
                gain = temp.total_net_profit - current.total_net_profit
                if gain <= 0:
                    continue
                if best_move is None or gain > float(best_move["gain"]):
                    best_move = {
                        "from_set": from_set,
                        "to_set": to_set,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert isinstance(from_set, RobustStrategySet)
        assert isinstance(to_set, RobustStrategySet)
        previous = current
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action="swap_unit",
                set_id=None,
                from_set_id=from_set.set_id,
                to_set_id=to_set.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=current.total_net_profit - previous.total_net_profit,
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Local search improved total net profit",
            )
        )
    return allocations, current, decision_log


def improve_with_multi_start_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    *,
    restarts: int,
    perturbations: int = 2,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    if restarts <= 0 or perturbations <= 0 or len(sets) < 2:
        return allocations, current, [], 0

    best_allocations = allocations.copy()
    best = current
    best_log: list[OptimizationDecision] = []
    valid_restarts = 0
    portfolio_curves = list(existing_portfolio_curves or [])

    for restart in range(restarts):
        rng = random.Random(104729 + restart * 7919 + len(sets) * 17)
        trial_allocations = allocations.copy()
        trial = current
        perturb_log: list[OptimizationDecision] = []

        for perturbation in range(perturbations):
            active = [item for item in sets if trial_allocations.get(item.set_id, 0) > 0]
            targets = list(sets)
            moves = [(source, target) for source in active for target in targets if source.set_id != target.set_id]
            rng.shuffle(moves)
            accepted_move = False
            for source, target in moves:
                temp_allocations = trial_allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] += 1
                if not _allocations_respect_constraints(
                    sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    target,
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if trial_allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected, _reason = violates_correlation_limits(
                        target,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    target_valley_dd,
                    target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    if max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    ) > max_portfolio_corr:
                        continue
                perturb_log.append(
                    OptimizationDecision(
                        step=perturbation + 1,
                        action="multi_start_perturb",
                        set_id=None,
                        from_set_id=source.set_id,
                        to_set_id=target.set_id,
                        gain=temp.total_net_profit - trial.total_net_profit,
                        valley_cost=temp.valley_dd - trial.valley_dd,
                        point_cost=temp.point_dd - trial.point_dd,
                        score=temp.total_net_profit - trial.total_net_profit,
                        portfolio_net_profit_after=temp.total_net_profit,
                        portfolio_valley_dd_after=temp.valley_dd,
                        portfolio_point_dd_after=temp.point_dd,
                        reason=f"Multi-start perturbation {restart + 1}",
                    )
                )
                trial_allocations = temp_allocations
                trial = temp
                accepted_move = True
                break
            if not accepted_move:
                break

        if not perturb_log:
            continue
        valid_restarts += 1
        trial_allocations, trial, local_log = improve_with_local_search(
            sets=sets,
            allocations=trial_allocations,
            current=trial,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            max_iterations=200,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        if trial.total_net_profit > best.total_net_profit + 1e-9:
            best_allocations = trial_allocations
            best = trial
            best_log = perturb_log + local_log

    return best_allocations, best, best_log, valid_restarts


def _deep_refine_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    minimum_active_strategies: int | None,
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None,
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
    margin_balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | None,
    stock_leverage: float,
    default_leverage: float,
    stock_contract_size: float,
    default_contract_size: float,
    max_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
    max_iterations: int = 160,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    working_sets = list({strategy.set_id: strategy for strategy in sets}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in working_sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0

    for iteration in range(1, max_iterations + 1):
        best_move: dict[str, object] | None = None
        ordered_targets = sorted(
            working_sets,
            key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
            reverse=True,
        )

        for target in ordered_targets:
            attempts += 1
            if can_add_unit(
                target_set=target,
                sets=working_sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                if allocations.get(target.set_id, 0) <= 0:
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        working_sets,
                        allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp_allocations = allocations.copy()
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                temp = evaluate_portfolio(
                    working_sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and not _evaluation_violates_dd_limits(temp)
                    and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_add_unit",
                        "from_set": None,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

            active_sources = [
                source for source in working_sets if allocations.get(source.set_id, 0) > 0
            ]
            for source in active_sources:
                if source.set_id == target.set_id:
                    continue
                attempts += 1
                temp_allocations = allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                if (
                    minimum_active_strategies is not None
                    and _portfolio_active_count(temp_allocations) < minimum_active_strategies
                ):
                    continue
                if not _allocations_respect_constraints(
                    working_sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    target,
                    working_sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        working_sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    working_sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and not _evaluation_violates_dd_limits(temp)
                    and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_swap_unit",
                        "from_set": source,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        previous = current
        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert to_set is not None and isinstance(to_set, RobustStrategySet)
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action=str(best_move["action"]),
                set_id=to_set.set_id,
                from_set_id=from_set.set_id if isinstance(from_set, RobustStrategySet) else None,
                to_set_id=to_set.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_move["gain"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Optimizacion profunda: movimiento validado contra DD, margen y correlacion",
            )
        )

    return allocations, current, decision_log, attempts


def _active_unit_allocations(allocations: dict[str, int]) -> dict[str, int]:
    return {str(set_id): int(units) for set_id, units in allocations.items() if int(units) > 0}


def _strict_validation_for_allocations(
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    active_units = _active_unit_allocations(allocations)
    return validate_strict_monthly_portfolio(
        [full_by_id[set_id] for set_id in active_units if set_id in full_by_id],
        active_units,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        lookback_years=5,
        enforce_point_dd=enforce_point_dd,
    )


def _strict_monthly_candidate_validation(
    strategy: RobustStrategySet,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    return validate_strict_monthly_portfolio(
        [strategy],
        {strategy.set_id: 1},
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        lookback_years=5,
        enforce_point_dd=enforce_point_dd,
    )


def _strict_monthly_candidate_score(
    monthly_strategy: RobustStrategySet,
    full_strategy: RobustStrategySet,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
    enforce_point_dd: bool = True,
) -> float:
    validation = _strict_monthly_candidate_validation(
        full_strategy,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    target_net = float(validation.get("target_month_net") or 0.0)
    best_net = float(validation.get("best_month_net") or 0.0)
    best_month = int(validation.get("best_month") or 0)
    best_gap = max(best_net - target_net, 0.0) if best_month != target_month else 0.0
    yearly = validation.get("yearly") if isinstance(validation.get("yearly"), list) else []
    positive_years = 0
    dd_over = 0.0
    for item in yearly:
        if not isinstance(item, dict):
            continue
        net = float(item.get("net") or 0.0)
        if int(item.get("trades") or 0) > 0 and net > 0:
            positive_years += 1
        dd_over += max(float(item.get("valley_dd") or 0.0) - target_valley_dd, 0.0)
        if enforce_point_dd:
            dd_over += max(float(item.get("point_dd") or 0.0) - target_point_dd, 0.0)
    base_score = score_set_for_portfolio(monthly_strategy, min_trades_2020_2026)
    return (
        target_net * 4.0
        - best_gap * 6.0
        + positive_years * 10_000.0
        - dd_over * 25.0
        + base_score * 0.05
    )


def _limit_sorted_candidates_with_symbol_reserve(
    ordered: Sequence[RobustStrategySet],
    limit: int | None,
) -> list[RobustStrategySet]:
    unique: list[RobustStrategySet] = []
    seen_ids: set[str] = set()
    for strategy in ordered:
        if strategy.set_id in seen_ids:
            continue
        unique.append(strategy)
        seen_ids.add(strategy.set_id)
    if limit is None or limit <= 0 or len(unique) <= limit:
        return unique

    selected: list[RobustStrategySet] = []
    selected_ids: set[str] = set()
    by_symbol: dict[str, list[RobustStrategySet]] = {}
    for strategy in unique:
        by_symbol.setdefault(portfolio_symbol_key(strategy.symbol), []).append(strategy)
    for group in by_symbol.values():
        if len(selected) >= limit:
            break
        strategy = group[0]
        selected.append(strategy)
        selected_ids.add(strategy.set_id)
    for strategy in unique:
        if len(selected) >= limit:
            break
        if strategy.set_id in selected_ids:
            continue
        selected.append(strategy)
        selected_ids.add(strategy.set_id)
    return selected


def _strict_monthly_candidate_variants(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    enforce_point_dd: bool = True,
) -> list[tuple[str, list[RobustStrategySet]]]:
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    eligible = [
        strategy
        for strategy in filter_eligible_sets(monthly_sets, min_trades_2020_2026)
        if strategy.set_id in full_by_id
    ]
    if not eligible:
        return []

    symbol_count = len({portfolio_symbol_key(strategy.symbol) for strategy in eligible})
    configured_limit = max_total_candidates if max_total_candidates is not None else len(eligible)
    if configured_limit is None or configured_limit <= 0:
        configured_limit = len(eligible)
    strict_limit = min(len(eligible), max(symbol_count, min(int(configured_limit), 40)))

    normal = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=strict_limit,
        min_trades_2020_2026=min_trades_2020_2026,
    )

    candidate_validations = {
        strategy.set_id: _strict_monthly_candidate_validation(
            full_by_id[strategy.set_id],
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            enforce_point_dd=enforce_point_dd,
        )
        for strategy in eligible
    }
    individual_target_best = [
        strategy
        for strategy in eligible
        if int(candidate_validations[strategy.set_id].get("best_month") or 0) == target_month
        and float(candidate_validations[strategy.set_id].get("target_month_net") or 0.0) > 0
    ]
    individual_target_best = sorted(
        individual_target_best,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )

    seasonal = sorted(
        eligible,
        key=lambda item: _strict_monthly_candidate_score(
            item,
            full_by_id[item.set_id],
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            min_trades_2020_2026=min_trades_2020_2026,
            enforce_point_dd=enforce_point_dd,
        ),
        reverse=True,
    )
    target_net = sorted(
        eligible,
        key=lambda item: (
            float(candidate_validations[item.set_id].get("target_month_net") or 0.0),
            score_set_for_portfolio(item, min_trades_2020_2026),
        ),
        reverse=True,
    )

    ordered_variant_sources: list[tuple[str, list[RobustStrategySet]]] = []
    if individual_target_best:
        ordered_variant_sources.append(("mejor_mes_individual", individual_target_best))
    ordered_variant_sources.extend(
        [
            ("estacionalidad", seasonal),
            ("net_mes_objetivo", target_net),
        ]
    )
    if not individual_target_best:
        ordered_variant_sources.append(("normal", normal))

    variants: list[tuple[str, list[RobustStrategySet]]] = []
    seen_signatures: set[tuple[str, ...]] = set()
    for label, ordered in ordered_variant_sources:
        limited = _limit_sorted_candidates_with_symbol_reserve(ordered, strict_limit)
        signature = tuple(strategy.set_id for strategy in limited)
        if not limited or signature in seen_signatures:
            continue
        variants.append((label, limited))
        seen_signatures.add(signature)
    return variants


def _portfolio_active_count(allocations: dict[str, int]) -> int:
    return sum(1 for units in allocations.values() if int(units) > 0)


def _portfolio_corr_allowed(
    evaluation: PortfolioEvaluation,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
) -> bool:
    if max_portfolio_corr is None:
        return True
    curves = list(existing_portfolio_curves or [])
    if not curves:
        return True
    worst_corr = max(
        curve_increment_correlation(evaluation.equity_curve_2020_2026, curve)
        for curve in curves
    )
    return worst_corr <= max_portfolio_corr + 1e-9


def _strict_monthly_violation_score(validation: dict[str, object]) -> float:
    if bool(validation.get("passed")):
        return 0.0
    score = 0.0
    enforce_point_dd = bool(validation.get("enforce_point_dd", True))
    target_valley = float(validation.get("target_valley_dd") or 0.0)
    target_point = float(validation.get("target_point_dd") or 0.0)
    monthly_dd = validation.get("monthly_dd")
    if isinstance(monthly_dd, dict):
        for item in monthly_dd.values():
            if not isinstance(item, dict):
                continue
            score += max(float(item.get("valley_dd") or 0.0) - target_valley, 0.0) * 10.0
            if enforce_point_dd:
                score += max(float(item.get("point_dd") or 0.0) - target_point, 0.0) * 10.0
    yearly = validation.get("yearly")
    if isinstance(yearly, list):
        for item in yearly:
            if not isinstance(item, dict):
                continue
            if int(item.get("trades") or 0) <= 0:
                score += 1_000_000.0
            if float(item.get("net") or 0.0) <= 0.0:
                score += 1_000_000.0 + abs(float(item.get("net") or 0.0)) * 100.0
            score += max(float(item.get("valley_dd") or 0.0) - target_valley, 0.0) * 20.0
            if enforce_point_dd:
                score += max(float(item.get("point_dd") or 0.0) - target_point, 0.0) * 20.0
    best_month = int(validation.get("best_month") or 0)
    target_month = int(validation.get("target_month") or 0)
    if best_month != target_month:
        best_net = float(validation.get("best_month_net") or 0.0)
        target_net = float(validation.get("target_month_net") or 0.0)
        score += 1_000_000.0 + max(best_net - target_net, 0.0) * 100.0
    return score + len(validation.get("reasons") or []) * 1_000.0


def _repair_allocations_to_strict_monthly(
    monthly_sets: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, dict[str, object], list[OptimizationDecision]]:
    current_allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in monthly_sets
    }
    current_eval = evaluate_portfolio(
        monthly_sets,
        current_allocations,
        target_valley_dd,
        target_point_dd,
        max_daily_dd,
        enforce_point_dd,
        daily_dd_full_history,
    )
    current_validation = _strict_validation_for_allocations(
        full_by_id,
        current_allocations,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    decision_log: list[OptimizationDecision] = []
    if bool(current_validation.get("passed")):
        return current_allocations, current_eval, current_validation, decision_log

    step = 0
    while sum(current_allocations.values()) > 0:
        current_score = _strict_monthly_violation_score(current_validation)
        best_choice: tuple[
            float,
            float,
            float,
            str,
            RobustStrategySet,
            dict[str, int],
            PortfolioEvaluation,
            dict[str, object],
        ] | None = None
        for strategy in monthly_sets:
            if current_allocations.get(strategy.set_id, 0) <= 0:
                continue
            trial_allocations = current_allocations.copy()
            trial_allocations[strategy.set_id] -= 1
            trial_eval = evaluate_portfolio(
                monthly_sets,
                trial_allocations,
                target_valley_dd,
                target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            )
            trial_validation = _strict_validation_for_allocations(
                full_by_id,
                trial_allocations,
                target_month=target_month,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
                enforce_point_dd=enforce_point_dd,
            )
            trial_score = _strict_monthly_violation_score(trial_validation)
            choice = (
                trial_score,
                -trial_eval.total_net_profit,
                -trial_eval.active_strategies,
                strategy.set_id,
                strategy,
                trial_allocations,
                trial_eval,
                trial_validation,
            )
            if best_choice is None or choice[:4] < best_choice[:4]:
                best_choice = choice
        if best_choice is None or best_choice[0] >= current_score - 1e-9:
            break
        (
            _score,
            _negative_net,
            _negative_active,
            _set_id,
            reduced_set,
            next_allocations,
            next_eval,
            next_validation,
        ) = best_choice
        previous_eval = current_eval
        current_allocations = next_allocations
        current_eval = next_eval
        current_validation = next_validation
        step += 1
        decision_log.append(
            OptimizationDecision(
                step=step,
                action="strict_monthly_reduce_unit",
                set_id=reduced_set.set_id,
                from_set_id=reduced_set.set_id,
                to_set_id=None,
                gain=-reduced_set.net_profit_2020_2026_001,
                valley_cost=current_eval.valley_dd - previous_eval.valley_dd,
                point_cost=current_eval.point_dd - previous_eval.point_dd,
                score=-float(best_choice[0]),
                portfolio_net_profit_after=current_eval.total_net_profit,
                portfolio_valley_dd_after=current_eval.valley_dd,
                portfolio_point_dd_after=current_eval.point_dd,
                reason="Reduccion necesaria para cumplir validacion mensual estricta 5A/DD",
            )
        )
        if bool(current_validation.get("passed")):
            break
    return current_allocations, current_eval, current_validation, decision_log


def _strict_monthly_safe_refill_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None,
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
    margin_balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | None,
    stock_leverage: float,
    default_leverage: float,
    stock_contract_size: float,
    default_contract_size: float,
    max_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
    max_iterations: int = 160,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0

    for iteration in range(1, max_iterations + 1):
        best_move: dict[str, object] | None = None
        ordered_targets = sorted(
            sets,
            key=lambda item: score_set_for_portfolio(item, 1),
            reverse=True,
        )
        for target in ordered_targets:
            attempts += 1
            if not can_add_unit(
                target_set=target,
                sets=sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                continue
            if allocations.get(target.set_id, 0) <= 0:
                rejected_by_corr, _reason = violates_correlation_limits(
                    target,
                    sets,
                    allocations,
                    max_pair_corr,
                    max_downside_corr,
                    max_dd_overlap,
                )
                if rejected_by_corr:
                    continue
            trial_allocations = allocations.copy()
            trial_allocations[target.set_id] = trial_allocations.get(target.set_id, 0) + 1
            trial = evaluate_portfolio(
                sets,
                trial_allocations,
                current.target_valley_dd,
                current.target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            )
            if _evaluation_violates_dd_limits(trial):
                continue
            if not _portfolio_corr_allowed(trial, existing_portfolio_curves, max_portfolio_corr):
                continue
            validation = _strict_validation_for_allocations(
                full_by_id,
                trial_allocations,
                target_month=target_month,
                target_valley_dd=current.target_valley_dd,
                target_point_dd=current.target_point_dd,
                enforce_point_dd=enforce_point_dd,
            )
            if not bool(validation.get("passed")):
                continue
            gain = trial.total_net_profit - current.total_net_profit
            if gain <= 1e-9:
                continue
            choice = {
                "target": target,
                "allocations": trial_allocations,
                "evaluation": trial,
                "gain": gain,
            }
            if best_move is None or gain > float(best_move["gain"]):
                best_move = choice

        if best_move is None:
            break

        previous = current
        target = best_move["target"]
        assert isinstance(target, RobustStrategySet)
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action="strict_monthly_safe_add_unit",
                set_id=target.set_id,
                from_set_id=None,
                to_set_id=target.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_move["gain"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Relleno seguro: unidad anadida sin romper DD, margen, correlacion ni 5A",
            )
        )

    return allocations, current, decision_log, attempts


def _strict_monthly_deep_refine_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
    minimum_active_strategies: int,
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None,
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
    margin_balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | None,
    stock_leverage: float,
    default_leverage: float,
    stock_contract_size: float,
    default_contract_size: float,
    max_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
    max_iterations: int = 120,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0

    for iteration in range(1, max_iterations + 1):
        best_move: dict[str, object] | None = None
        ordered_targets = sorted(
            sets,
            key=lambda item: score_set_for_portfolio(item, 1),
            reverse=True,
        )

        for target in ordered_targets:
            attempts += 1
            if can_add_unit(
                target_set=target,
                sets=sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                if allocations.get(target.set_id, 0) <= 0:
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        sets,
                        allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp_allocations = allocations.copy()
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if not _evaluation_violates_dd_limits(temp):
                    validation = _strict_validation_for_allocations(
                        full_by_id,
                        temp_allocations,
                        target_month=target_month,
                        target_valley_dd=current.target_valley_dd,
                        target_point_dd=current.target_point_dd,
                        enforce_point_dd=enforce_point_dd,
                    )
                    gain = temp.total_net_profit - current.total_net_profit
                    if (
                        gain > 1e-9
                        and bool(validation.get("passed"))
                        and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                        and (best_move is None or gain > float(best_move["gain"]))
                    ):
                        best_move = {
                            "action": "deep_add_unit",
                            "from_set": None,
                            "to_set": target,
                            "allocations": temp_allocations,
                            "evaluation": temp,
                            "gain": gain,
                        }

            active_sources = [source for source in sets if allocations.get(source.set_id, 0) > 0]
            for source in active_sources:
                if source.set_id == target.set_id:
                    continue
                attempts += 1
                temp_allocations = allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                if _portfolio_active_count(temp_allocations) < minimum_active_strategies:
                    continue
                if not _allocations_respect_constraints(
                    sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    target,
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if not _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr):
                    continue
                validation = _strict_validation_for_allocations(
                    full_by_id,
                    temp_allocations,
                    target_month=target_month,
                    target_valley_dd=current.target_valley_dd,
                    target_point_dd=current.target_point_dd,
                    enforce_point_dd=enforce_point_dd,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and bool(validation.get("passed"))
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_swap_unit",
                        "from_set": source,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        previous = current
        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert to_set is not None and isinstance(to_set, RobustStrategySet)
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action=str(best_move["action"]),
                set_id=to_set.set_id,
                from_set_id=from_set.set_id if isinstance(from_set, RobustStrategySet) else None,
                to_set_id=to_set.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_move["gain"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Optimizacion profunda: movimiento validado contra DD, margen, correlacion y 5A",
            )
        )

    return allocations, current, decision_log, attempts


def optimize_strict_monthly_portfolio(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    min_trades_2020_2026: int = 100,
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    run_local_search: bool = True,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    dd_reserve_pct: float = 0.0,
    search_restarts: int = 0,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    use_deep_refinement: bool = True,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> PortfolioResult:
    """Optimize a monthly portfolio with the 5-year seasonal test in the loop.

    This is a bounded, deterministic deep search.  It builds several candidate
    pools ranked by monthly profit and seasonal dominance, optimizes each pool
    with the normal DD/margin/correlation engine, and keeps only portfolios that
    pass the strict year-by-year and "best month in 5Y" audit.
    """
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")

    reserve_factor = 1.0 - min(max(float(dd_reserve_pct), 0.0), 99.0) / 100.0
    target_valley_dd = float(capital) * float(valley_dd_pct) * reserve_factor / 100.0
    target_point_dd = float(capital) * float(point_dd_pct) * reserve_factor / 100.0
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    variants = _strict_monthly_candidate_variants(
        monthly_sets,
        full_sets,
        target_month=month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        min_trades_2020_2026=min_trades_2020_2026,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        enforce_point_dd=enforce_point_dd,
    )
    if not variants:
        raise ValueError("No hay candidatos mensuales elegibles para la busqueda estricta.")

    base_result: PortfolioResult | None = None
    base_label = ""
    errors: list[str] = []
    for label, candidate_pool in variants:
        try:
            base_result = optimize_portfolio(
                raw_sets=candidate_pool,
                capital=capital,
                valley_dd_pct=valley_dd_pct,
                point_dd_pct=point_dd_pct,
                portfolio_type=portfolio_type,
                min_trades_2020_2026=min_trades_2020_2026,
                top_k_per_symbol=max(top_k_per_symbol, len(candidate_pool)),
                max_total_candidates=None,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                run_local_search=run_local_search,
                max_pair_corr=max_pair_corr,
                max_downside_corr=max_downside_corr,
                max_dd_overlap=max_dd_overlap,
                existing_portfolio_curves=existing_portfolio_curves,
                max_portfolio_corr=max_portfolio_corr,
                dd_reserve_pct=dd_reserve_pct,
                search_restarts=int(search_restarts),
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
                max_daily_dd=max_daily_dd,
                enforce_point_dd=enforce_point_dd,
                daily_dd_full_history=daily_dd_full_history,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        base_units = {
            allocation.set_id: allocation.units
            for allocation in base_result.allocations
            if allocation.units > 0
        }
        repaired_units, repaired_eval, validation, repair_log = _repair_allocations_to_strict_monthly(
            candidate_pool,
            full_by_id,
            base_units,
            target_month=month,
            target_valley_dd=base_result.target_valley_dd,
            target_point_dd=base_result.target_point_dd,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        if bool(validation.get("passed")):
            active_repaired = _active_unit_allocations(repaired_units)
            if not active_repaired:
                errors.append(f"{label}: reparacion estricta dejo el portafolio sin estrategias")
                continue
            if active_repaired != base_units:
                repaired_sets = [
                    strategy for strategy in candidate_pool if strategy.set_id in active_repaired
                ]
                try:
                    base_result = optimize_portfolio(
                        raw_sets=repaired_sets,
                        capital=capital,
                        valley_dd_pct=valley_dd_pct,
                        point_dd_pct=point_dd_pct,
                        portfolio_type=portfolio_type,
                        min_trades_2020_2026=min_trades_2020_2026,
                        top_k_per_symbol=max(1, len(repaired_sets)),
                        max_total_candidates=None,
                        max_units_per_set=max_units_per_set,
                        max_total_units=sum(active_repaired.values()),
                        max_units_per_symbol=max_units_per_symbol,
                        max_sets_per_symbol=max_sets_per_symbol,
                        run_local_search=False,
                        max_pair_corr=max_pair_corr,
                        max_downside_corr=max_downside_corr,
                        max_dd_overlap=max_dd_overlap,
                        existing_portfolio_curves=existing_portfolio_curves,
                        max_portfolio_corr=max_portfolio_corr,
                        dd_reserve_pct=dd_reserve_pct,
                        search_restarts=0,
                        margin_balance=margin_balance,
                        max_margin_pct=max_margin_pct,
                        margin_profile=margin_profile,
                        stock_leverage=stock_leverage,
                        default_leverage=default_leverage,
                        stock_contract_size=stock_contract_size,
                        default_contract_size=default_contract_size,
                        max_daily_dd=max_daily_dd,
                        enforce_point_dd=enforce_point_dd,
                        daily_dd_full_history=daily_dd_full_history,
                        required_initial_allocations=active_repaired,
                        preserve_required_allocations=True,
                    )
                except Exception as exc:
                    errors.append(f"{label}: reparacion estricta no pudo reconstruirse: {exc}")
                    continue
                base_result.warnings = [
                    warning for warning in base_result.warnings
                    if not warning.startswith("Existing portfolio strategies and units were preserved")
                ]
                base_result.decision_log.extend(repair_log)
                base_result.warnings.append(
                    "Reparacion estricta mensual: se redujeron "
                    f"{len(repair_log)} unidad(es) para cumplir DD de todos los meses y mejor mes 5A."
                )
            base_result.seasonal_validation = validation
            base_label = label
            break
        reasons = validation.get("reasons") or []
        errors.append(f"{label}: " + "; ".join(str(item) for item in list(reasons)[:3]))

    if base_result is None or not bool(base_result.seasonal_validation.get("passed")):
        detail = " | ".join(errors[:6])
        raise ValueError(
            "Ninguna variante de busqueda estricta mensual fue viable."
            + (f" {detail}" if detail else "")
        )

    monthly_by_id = {strategy.set_id: strategy for strategy in monthly_sets}
    candidate_pool_by_id: dict[str, RobustStrategySet] = {}
    for _label, variant_pool in variants:
        for strategy in variant_pool:
            candidate_pool_by_id[strategy.set_id] = strategy
    for allocation in base_result.allocations:
        strategy = monthly_by_id.get(allocation.set_id)
        if strategy is not None:
            candidate_pool_by_id[allocation.set_id] = strategy

    if not use_deep_refinement:
        group_limits = group_limits_for_portfolio_type(portfolio_type)
        safe_allocations, safe_eval, safe_log, attempts = _strict_monthly_safe_refill_allocations(
            list(candidate_pool_by_id.values()),
            full_by_id,
            {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
            evaluate_portfolio(
                list(candidate_pool_by_id.values()),
                {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
                base_result.target_valley_dd,
                base_result.target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            ),
            target_month=month,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_sets_per_group=group_limits.max_sets,
            max_units_per_group_pct=group_limits.max_units_pct,
            group_unit_cap_bootstrap=group_limits.bootstrap_units,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        active_safe = _active_unit_allocations(safe_allocations)
        validation = _strict_validation_for_allocations(
            full_by_id,
            active_safe,
            target_month=month,
            target_valley_dd=base_result.target_valley_dd,
            target_point_dd=base_result.target_point_dd,
            enforce_point_dd=enforce_point_dd,
        )
        if safe_eval.total_net_profit > base_result.total_net_profit + 1e-9 and bool(validation.get("passed")):
            safe_sets = [
                monthly_by_id[set_id]
                for set_id in active_safe
                if set_id in monthly_by_id
            ]
            safe_result = optimize_portfolio(
                raw_sets=safe_sets,
                capital=capital,
                valley_dd_pct=valley_dd_pct,
                point_dd_pct=point_dd_pct,
                portfolio_type=portfolio_type,
                min_trades_2020_2026=min_trades_2020_2026,
                top_k_per_symbol=max(1, len(safe_sets)),
                max_total_candidates=None,
                max_units_per_set=max_units_per_set,
                max_total_units=sum(active_safe.values()),
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                run_local_search=False,
                max_pair_corr=max_pair_corr,
                max_downside_corr=max_downside_corr,
                max_dd_overlap=max_dd_overlap,
                existing_portfolio_curves=existing_portfolio_curves,
                max_portfolio_corr=max_portfolio_corr,
                required_initial_allocations=active_safe,
                preserve_required_allocations=True,
                dd_reserve_pct=dd_reserve_pct,
                search_restarts=0,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
                max_daily_dd=max_daily_dd,
                enforce_point_dd=enforce_point_dd,
                daily_dd_full_history=daily_dd_full_history,
            )
            safe_result.seasonal_validation = validation
            safe_result.warnings = [
                warning for warning in safe_result.warnings
                if not warning.startswith("Existing portfolio strategies and units were preserved")
            ]
            safe_result.decision_log.extend(base_result.decision_log)
            safe_result.decision_log.extend(safe_log)
            safe_result.warnings.extend(base_result.warnings)
            safe_result.warnings.append(
                "Relleno seguro mensual aplicado sin optimizacion profunda: "
                f"net {base_result.total_net_profit:,.2f} -> {safe_result.total_net_profit:,.2f}; "
                f"unidades {base_result.total_units} -> {safe_result.total_units}; "
                f"base '{base_label}', {attempts} intentos evaluados."
            )
            return safe_result
        base_result.warnings.append(
            "Relleno seguro mensual: no encontro unidades adicionales validas "
            f"sin romper DD/margen/correlacion/5A ({attempts} intentos evaluados)."
        )
        base_result.warnings.append(
            f"Generacion estricta mensual OK sin optimizacion profunda; base '{base_label}'."
        )
        return base_result

    group_limits = group_limits_for_portfolio_type(portfolio_type)
    refined_allocations, refined_eval, refinement_log, attempts = _strict_monthly_deep_refine_allocations(
        list(candidate_pool_by_id.values()),
        full_by_id,
        {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
        evaluate_portfolio(
            list(candidate_pool_by_id.values()),
            {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
            base_result.target_valley_dd,
            base_result.target_point_dd,
            max_daily_dd,
            enforce_point_dd,
            daily_dd_full_history,
        ),
        target_month=month,
        minimum_active_strategies=base_result.active_strategies,
        max_units_per_set=max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        max_sets_per_group=group_limits.max_sets,
        max_units_per_group_pct=group_limits.max_units_pct,
        group_unit_cap_bootstrap=group_limits.bootstrap_units,
        max_pair_corr=max_pair_corr,
        max_downside_corr=max_downside_corr,
        max_dd_overlap=max_dd_overlap,
        existing_portfolio_curves=existing_portfolio_curves,
        max_portfolio_corr=max_portfolio_corr,
        margin_balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
    )
    if refined_eval.total_net_profit <= base_result.total_net_profit + 1e-9:
        base_result.warnings.append(
            "Optimizacion profunda: no encontro mejora valida sobre la base estricta "
            f"({attempts} movimientos evaluados)."
        )
        return base_result

    active_refined = _active_unit_allocations(refined_allocations)
    validation = _strict_validation_for_allocations(
        full_by_id,
        active_refined,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    if _portfolio_active_count(active_refined) < base_result.active_strategies or not bool(validation.get("passed")):
        base_result.warnings.append(
            "Optimizacion profunda: mejora descartada por diversificacion o validacion 5A."
        )
        return base_result

    refined_sets = [
        monthly_by_id[set_id]
        for set_id in active_refined
        if set_id in monthly_by_id
    ]
    refined_result = optimize_portfolio(
        raw_sets=refined_sets,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
        portfolio_type=portfolio_type,
        min_trades_2020_2026=min_trades_2020_2026,
        top_k_per_symbol=max(1, len(refined_sets)),
        max_total_candidates=None,
        max_units_per_set=max_units_per_set,
        max_total_units=sum(active_refined.values()),
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        run_local_search=False,
        max_pair_corr=max_pair_corr,
        max_downside_corr=max_downside_corr,
        max_dd_overlap=max_dd_overlap,
        existing_portfolio_curves=existing_portfolio_curves,
        max_portfolio_corr=max_portfolio_corr,
        required_initial_allocations=active_refined,
        preserve_required_allocations=True,
        dd_reserve_pct=dd_reserve_pct,
        search_restarts=0,
        margin_balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
    )
    refined_result.seasonal_validation = validation
    refined_result.warnings = [
        warning for warning in refined_result.warnings
        if not warning.startswith("Existing portfolio strategies and units were preserved")
    ]
    refined_result.decision_log.extend(refinement_log)
    refined_result.warnings.append(
        "Optimizacion profunda aplicada: "
        f"net {base_result.total_net_profit:,.2f} -> {refined_result.total_net_profit:,.2f}; "
        f"estrategias {base_result.active_strategies} -> {refined_result.active_strategies}; "
        f"base '{base_label}', {attempts} movimientos evaluados."
    )
    return refined_result


def optimize_portfolio(
    raw_sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    min_trades_2020_2026: int = 100,
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    run_local_search: bool = True,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int | None = None,
    required_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    required_initial_allocations: dict[str, int] | None = None,
    preserve_required_allocations: bool = False,
    dd_reserve_pct: float = 0.0,
    search_restarts: int = 0,
    bootstrap_simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS,
    bootstrap_block_size: int | None = None,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
    use_deep_refinement: bool = False,
) -> PortfolioResult:
    reserve_factor = 1.0 - min(max(float(dd_reserve_pct), 0.0), 99.0) / 100.0
    effective_valley_dd_pct = valley_dd_pct * reserve_factor
    effective_point_dd_pct = point_dd_pct * reserve_factor
    target_valley_dd = capital * effective_valley_dd_pct / 100.0
    target_point_dd = capital * effective_point_dd_pct / 100.0
    group_limits = group_limits_for_portfolio_type(portfolio_type)
    if max_units_per_group_pct is None:
        max_units_per_group_pct = group_limits.max_units_pct
    if max_sets_per_group is None:
        max_sets_per_group = group_limits.max_sets
    if group_unit_cap_bootstrap is None:
        group_unit_cap_bootstrap = group_limits.bootstrap_units
    eligible = filter_eligible_sets(raw_sets, min_trades_2020_2026)
    if not eligible:
        raise ValueError("No eligible robust sets found")

    selected = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    base_selected_count = len(selected)
    required_ids = {str(set_id) for set_id in (required_set_ids or ())}
    required_ids.update(str(set_id) for set_id in (required_initial_allocations or {}))
    eligible_by_id = {strategy.set_id: strategy for strategy in eligible}
    missing_required = sorted(required_ids - set(eligible_by_id))
    if missing_required:
        raise ValueError(
            "Required portfolio sets are no longer eligible: "
            + ", ".join(Path(set_id).name for set_id in missing_required)
        )
    selected_ids = {strategy.set_id for strategy in selected}
    selected.extend(eligible_by_id[set_id] for set_id in required_ids - selected_ids)
    configured_group_units_pct = max_units_per_group_pct
    candidate_group_count = _candidate_group_count(selected)
    group_units_pct_feasibility_floor: float | None = None
    if max_units_per_group_pct is not None and candidate_group_count > 1:
        # A cap below 1/N is impossible when only N asset groups are in the
        # candidate pool (for example, 40% with Forex + Metals only). Use the
        # smallest feasible cap instead of leaving the greedy allocator stuck.
        group_units_pct_feasibility_floor = 1.0 / candidate_group_count
        max_units_per_group_pct = max(
            float(max_units_per_group_pct),
            group_units_pct_feasibility_floor,
        )
    initial_allocations = {
        set_id: max(int((required_initial_allocations or {}).get(set_id, 1)), 1)
        for set_id in required_ids
    }
    fixed_set_ids = required_ids if preserve_required_allocations else set()
    allocations, current, greedy_log, stop_reason, correlation_rejections = build_portfolio_greedy(
        sets=selected,
        capital=capital,
        valley_dd_pct=effective_valley_dd_pct,
        point_dd_pct=effective_point_dd_pct,
        portfolio_type=portfolio_type,
        max_units_per_set=max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        max_pair_corr=max_pair_corr,
        max_downside_corr=max_downside_corr,
        max_dd_overlap=max_dd_overlap,
        existing_portfolio_curves=existing_portfolio_curves,
        max_portfolio_corr=max_portfolio_corr,
        max_units_per_group_pct=max_units_per_group_pct,
        max_sets_per_group=max_sets_per_group,
        group_unit_cap_bootstrap=group_unit_cap_bootstrap,
        initial_allocations=initial_allocations,
        minimum_active_strategies=minimum_active_strategies,
        maximum_active_strategies=maximum_active_strategies,
        fixed_set_ids=fixed_set_ids,
        allow_fixed_reductions_for_repair=preserve_required_allocations,
        margin_balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
    )

    local_log: list[OptimizationDecision] = []
    if run_local_search and not preserve_required_allocations:
        allocations, current, local_log = improve_with_local_search(
            sets=selected,
            allocations=allocations,
            current=current,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            protected_set_ids=required_ids,
            minimum_active_strategies=minimum_active_strategies,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )

    group_cap_relaxed = False
    if (
        portfolio_type == PortfolioType.BALANCED
        and max_units_per_group_pct is not None
        and _candidate_group_count(selected) > 1
        and current.valley_usage_pct < 70
    ):
        (
            relaxed_allocations,
            relaxed_current,
            relaxed_greedy_log,
            relaxed_stop_reason,
            relaxed_rejections,
        ) = build_portfolio_greedy(
            sets=selected,
            capital=capital,
            valley_dd_pct=effective_valley_dd_pct,
            point_dd_pct=effective_point_dd_pct,
            portfolio_type=portfolio_type,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=None,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            initial_allocations=initial_allocations,
            minimum_active_strategies=minimum_active_strategies,
            maximum_active_strategies=maximum_active_strategies,
            fixed_set_ids=fixed_set_ids,
            allow_fixed_reductions_for_repair=preserve_required_allocations,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        relaxed_local_log: list[OptimizationDecision] = []
        if run_local_search and not preserve_required_allocations:
            relaxed_allocations, relaxed_current, relaxed_local_log = improve_with_local_search(
                sets=selected,
                allocations=relaxed_allocations,
                current=relaxed_current,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_pair_corr=max_pair_corr,
                max_downside_corr=max_downside_corr,
                max_dd_overlap=max_dd_overlap,
                existing_portfolio_curves=existing_portfolio_curves,
                max_portfolio_corr=max_portfolio_corr,
                max_units_per_group_pct=None,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                protected_set_ids=required_ids,
                minimum_active_strategies=minimum_active_strategies,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
                max_daily_dd=max_daily_dd,
                enforce_point_dd=enforce_point_dd,
                daily_dd_full_history=daily_dd_full_history,
            )
        if relaxed_current.total_net_profit > current.total_net_profit and relaxed_current.total_units > current.total_units:
            allocations = relaxed_allocations
            current = relaxed_current
            greedy_log = relaxed_greedy_log
            local_log = relaxed_local_log
            correlation_rejections = relaxed_rejections
            group_cap_relaxed = True
            stop_reason = f"{relaxed_stop_reason}; group unit cap relaxed after strict Balanced allocation underused DD"

    multi_start_log: list[OptimizationDecision] = []
    valid_restarts = 0
    if search_restarts > 0 and not preserve_required_allocations:
        allocations, current, multi_start_log, valid_restarts = improve_with_multi_start_search(
            sets=selected,
            allocations=allocations,
            current=current,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            restarts=int(search_restarts),
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=None if group_cap_relaxed else max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
    if multi_start_log:
        stop_reason += "; multi-start search improved the local solution"

    deep_log: list[OptimizationDecision] = []
    deep_attempts = 0
    deep_pool_expanded = False
    deep_pool_count = len(selected)
    if use_deep_refinement and not preserve_required_allocations:
        deep_top_k = max(int(top_k_per_symbol), min(20, int(top_k_per_symbol) * 2))
        if max_total_candidates is None:
            deep_max_candidates = None
        else:
            deep_max_candidates = min(len(eligible), max(int(max_total_candidates), int(max_total_candidates) * 2))
        deep_selected = select_top_k_per_symbol(
            eligible,
            top_k_per_symbol=deep_top_k,
            max_total_candidates=deep_max_candidates,
            min_trades_2020_2026=min_trades_2020_2026,
        )
        deep_selected_by_id = {strategy.set_id: strategy for strategy in deep_selected}
        for set_id in required_ids:
            if set_id in eligible_by_id:
                deep_selected_by_id.setdefault(set_id, eligible_by_id[set_id])
        for strategy in selected:
            deep_selected_by_id.setdefault(strategy.set_id, strategy)
        deep_selected = list(deep_selected_by_id.values())
        deep_pool_count = len(deep_selected)
        deep_pool_expanded = len(deep_selected) > len(selected)
        refined_allocations, refined_current, deep_log, deep_attempts = _deep_refine_allocations(
            deep_selected,
            allocations,
            current,
            minimum_active_strategies=minimum_active_strategies,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_sets_per_group=max_sets_per_group,
            max_units_per_group_pct=None if group_cap_relaxed else max_units_per_group_pct,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        if refined_current.total_net_profit > current.total_net_profit + 1e-9:
            selected = deep_selected
            allocations = refined_allocations
            current = refined_current
            stop_reason += "; deep optimization refined the solution"

    executable_allocations, executable_steps = _execution_plan_allocations(selected, allocations, capital)
    execution_adjustments = {
        set_id: executable_allocations[set_id]
        for set_id, units in allocations.items()
        if units > 0 and executable_allocations.get(set_id, 0) != units
    }
    if execution_adjustments:
        current = evaluate_portfolio(
            selected,
            executable_allocations,
            target_valley_dd,
            target_point_dd,
            max_daily_dd,
            enforce_point_dd,
            daily_dd_full_history,
        )
        allocations = executable_allocations

    if current.valley_dd > target_valley_dd:
        raise ValueError("Final portfolio violates valley DD")
    if enforce_point_dd and current.point_dd > target_point_dd:
        raise ValueError("Final portfolio violates point DD")
    if max_daily_dd is not None and current.daily_dd > float(max_daily_dd) + 1e-9:
        raise ValueError("Final portfolio violates daily DD")

    margin_summary = (
        portfolio_margin_summary(
            selected,
            allocations,
            balance=float(margin_balance),
            max_margin_pct=float(max_margin_pct),
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
        )
        if margin_balance is not None and max_margin_pct is not None
        else {}
    )
    margin_by_set = margin_summary.get("by_set", {}) if isinstance(margin_summary, dict) else {}
    daily_dd_summary: dict[str, object] = {}
    if max_daily_dd is not None:
        _daily_dd, daily_dd_summary = portfolio_daily_closed_floating_dd(
            selected,
            allocations,
            full_history=bool(daily_dd_full_history),
        )
        daily_dd_summary["limit"] = float(max_daily_dd)
        daily_dd_summary["usage_pct"] = current.daily_dd / float(max_daily_dd) * 100.0 if float(max_daily_dd) > 0 else 0.0

    result_allocations: list[StrategyAllocation] = []
    for strategy in selected:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            continue
        margin_row = margin_by_set.get(strategy.set_id, {}) if isinstance(margin_by_set, dict) else {}
        result_allocations.append(
            StrategyAllocation(
                set_id=strategy.set_id,
                candidate_id=strategy.candidate_id,
                symbol=strategy.symbol,
                units=units,
                lot=round(units * 0.01, 2),
                net_profit_contribution=strategy.net_profit_2020_2026_001 * units,
                standalone_valley_dd=(
                    strategy.valley_dd_2020_2026_001
                    + strategy.max_floating_dd_001
                ) * units,
                standalone_point_dd=strategy.point_dd_2020_2026_001 * units,
                timeframe=strategy.timeframe,
                set_path=strategy.set_path,
                is_report_path=strategy.is_report_path,
                oos_report_path=strategy.oos_report_path,
                lot_size_step=float(executable_steps.get(strategy.set_id, _lot_size_step(capital, units) or 0)),
                margin_required=float(margin_row.get("margin", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                margin_pct=(
                    float(margin_row.get("margin", 0.0) or 0.0) / max(float(margin_balance or 0.0), 1e-9) * 100.0
                    if margin_balance is not None and isinstance(margin_row, dict)
                    else 0.0
                ),
                margin_leverage=float(margin_row.get("leverage", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                margin_contract_size=float(margin_row.get("contract_size", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                margin_price=float(margin_row.get("price", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                max_balance_dd_001=strategy.max_balance_dd_001,
                max_equity_dd_001=strategy.max_equity_dd_001,
                floating_dd_source=strategy.floating_dd_source,
                standalone_floating_dd=(
                    strategy.max_floating_dd_001 * units
                ),
                recent_net_profit_001=strategy.recent_net_profit_001,
                recent_equity_dd_001=strategy.recent_equity_dd_001,
                has_recent_performance=strategy.has_recent_performance,
            )
        )
    result_allocations.sort(key=lambda item: (item.units, item.net_profit_contribution), reverse=True)

    group_summary = portfolio_group_summary(selected, allocations)
    eligible_groups = {portfolio_group_key(strategy.symbol) for strategy in eligible}
    group_limit_overages: list[str] = []
    if max_units_per_group_pct is not None and len(eligible_groups) > 1:
        limit_pct = max_units_per_group_pct * 100.0
        group_limit_overages = [
            f"{group} {float(stats['unit_pct']):.1f}%"
            for group, stats in group_summary.items()
            if float(stats["unit_pct"]) > limit_pct + 0.1
        ]

    warnings: list[str] = []
    if (
        configured_group_units_pct is not None
        and group_units_pct_feasibility_floor is not None
        and float(max_units_per_group_pct) > float(configured_group_units_pct) + 1e-9
    ):
        warnings.append(
            "Group unit cap adjusted to the feasible diversification floor: "
            f"{float(configured_group_units_pct) * 100.0:.1f}% -> "
            f"{float(max_units_per_group_pct) * 100.0:.1f}% for "
            f"{candidate_group_count} available asset groups."
        )
    if dd_reserve_pct > 0:
        warnings.append(
            f"DD reserve {float(dd_reserve_pct):.1f}% applied; optimizer used reduced effective DD targets."
        )
    if current.floating_dd_buffer > 0:
        warnings.append(
            "DD equity historico aplicado (2020-hoy + Final Tick 6M): DD cerrado "
            f"{current.closed_valley_dd:.2f} + buffer flotante conservador "
            f"{current.floating_dd_buffer:.2f} = {current.valley_dd:.2f}."
        )
    recent_recovery_rejections = sum(
        1
        for strategy in raw_sets
        if strategy.has_recent_performance
        and strategy.recent_net_profit_001 / max(strategy.recent_equity_dd_001, 1.0)
        < MIN_RECENT_EQUITY_RECOVERY
    )
    if recent_recovery_rejections:
        warnings.append(
            f"{recent_recovery_rejections} estrategia(s) excluida(s): recuperacion 6M "
            f"sobre DD de equity < {MIN_RECENT_EQUITY_RECOVERY:.1f}."
        )
    if search_restarts > 0:
        warnings.append(
            f"Multi-start search evaluated {valid_restarts}/{int(search_restarts)} valid restart(s)."
        )
    if use_deep_refinement:
        if deep_log:
            warnings.append(
                "Optimizacion profunda aplicada: "
                f"{len(deep_log)} movimiento(s), {deep_attempts} intento(s), "
                f"pool {base_selected_count}->{len(selected)} candidato(s)."
            )
        else:
            pool_text = (
                f" pool {base_selected_count}->{deep_pool_count} candidato(s)"
                if deep_pool_expanded
                else f" pool {len(selected)} candidato(s)"
            )
            warnings.append(
                "Optimizacion profunda: no encontro mejora valida "
                f"tras {deep_attempts} intento(s),{pool_text}."
            )
    if preserve_required_allocations and required_ids:
        repair_reductions = sum(
            1 for decision in greedy_log if decision.action == "reduce_unit_for_repair"
        )
        if repair_reductions:
            warnings.append(
                f"Repair preserved every existing strategy and changed only {repair_reductions} existing unit(s) required by DD limits."
            )
        else:
            warnings.append(
                "Existing portfolio strategies and units were preserved; only replacement allocations were optimized."
            )
    if group_cap_relaxed:
        warnings.append(
            "Balanced relajo el limite porcentual por grupo porque la asignacion estricta dejaba el portfolio infrautilizado."
        )
    if portfolio_type != PortfolioType.AGGRESSIVE and len(eligible_groups) <= 1:
        only_group = next(iter(eligible_groups), "none")
        warnings.append(
            f"Solo un grupo de activo tuvo curvas elegibles ({only_group}); "
            "no fue posible diversificar por grupo en Balanced/Conservative."
        )
    if current.valley_usage_pct < 70:
        warnings.append(
            "Valley DD usage is below 70%. This can be acceptable if no efficient increments remained."
        )
    if enforce_point_dd and current.point_usage_pct > 95:
        warnings.append("Point DD usage is above 95%. Portfolio is close to point DD limit.")
    if not result_allocations:
        warnings.append("No eligible robust sets found.")
    if execution_adjustments:
        warnings.append(
            "Lots were rounded down to match integer LotPerBalance_step export values."
        )
    if correlation_rejections:
        warnings.append(f"{correlation_rejections} increment candidate(s) rejected by correlation limits.")
    if group_limit_overages:
        limit_pct = max_units_per_group_pct * 100.0
        warnings.append(
            f"Concentracion por grupo sobre {limit_pct:.0f}% tras optimizar: "
            + ", ".join(group_limit_overages)
        )
    if margin_summary:
        profile_label = str(margin_summary.get("profile_label") or margin_profile_label(margin_profile))
        if normalize_margin_profile(str(margin_summary.get("profile") or margin_profile)) == "ttp":
            rule_text = (
                "Forex 1:50; indices 1:15; commodities/metales/energias 1:10; "
                "stocks/crypto 1:2; contract_size stocks 100/resto 1."
            )
        else:
            rule_text = "Stocks 1:20 contract_size 100; resto 1:500 contract_size 1."
        warnings.append(
            f"Margen {profile_label} aplicado: {rule_text} "
            f"Uso estimado {float(margin_summary['total']):.2f}/"
            f"{float(margin_summary['limit']):.2f} "
            f"({float(margin_summary['usage_pct']):.1f}% del limite)."
        )
    if max_daily_dd is not None:
        worst_day = str(daily_dd_summary.get("worst_day") or "-")
        warnings.append(
            "DD diario max aplicado: cerrado + flotante estimado "
            f"({'historico completo' if daily_dd_full_history else 'mes objetivo'}) "
            f"{current.daily_dd:.2f}/{float(max_daily_dd):.2f}"
            + (f" en {worst_day}." if worst_day != "-" else ".")
        )

    unused_sets = _build_unused_sets(raw_sets, eligible, selected, allocations, min_trades_2020_2026)
    stress_bootstrap = bootstrap_valley_drawdown(
        current.equity_curve_2020_2026,
        nominal_valley_dd_limit=capital * valley_dd_pct / 100.0,
        effective_valley_dd_limit=target_valley_dd,
        simulations=bootstrap_simulations,
        block_size=bootstrap_block_size,
        seed=bootstrap_seed,
    )
    if stress_bootstrap.alert:
        warnings.append(
            f"ALERTA bootstrap: DD valle P95 {stress_bootstrap.valley_dd_p95:.2f} "
            f"supera el limite efectivo {stress_bootstrap.effective_valley_dd_limit:.2f}."
        )
    return PortfolioResult(
        allocations=result_allocations,
        equity_curve_2020_2026=current.equity_curve_2020_2026,
        total_net_profit=current.total_net_profit,
        actual_valley_dd=current.valley_dd,
        actual_point_dd=current.point_dd,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=current.valley_usage_pct,
        point_usage_pct=current.point_usage_pct,
        total_lot=current.total_lot,
        total_units=current.total_units,
        active_strategies=current.active_strategies,
        stop_reason=stop_reason,
        warnings=warnings,
        decision_log=greedy_log + local_log + multi_start_log + deep_log,
        unused_sets=unused_sets,
        correlation_rejections=correlation_rejections,
        group_summary=group_summary,
        stress_bootstrap=stress_bootstrap,
        margin_summary=margin_summary,
        max_daily_dd=current.daily_dd,
        target_daily_dd=float(max_daily_dd) if max_daily_dd is not None else None,
        daily_dd_summary=daily_dd_summary,
        daily_dd_full_history=bool(daily_dd_full_history),
        enforce_point_dd=bool(enforce_point_dd),
        actual_closed_valley_dd=current.closed_valley_dd,
        floating_dd_buffer=current.floating_dd_buffer,
    )


def set_current_value(text: str, key: str, value: object) -> tuple[str, bool]:
    out: list[str] = []
    found = False
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(";"):
            lhs, rhs = line.split("=", 1)
            if lhs.strip() == key:
                if "||" in rhs:
                    parts = rhs.split("||")
                    parts[0] = str(value)
                    rhs = "||".join(parts)
                else:
                    rhs = str(value)
                line = f"{lhs}={rhs}"
                found = True
        out.append(line)
    return "\n".join(out), found


def apply_portfolio_lot_text(text: str, lot_size_step: float) -> tuple[str, int, bool]:
    step_int = max(1, int(math.ceil(lot_size_step)))
    text, _ = set_current_value(text, "Risk", 2)
    text, found_step = set_current_value(text, "LotPerBalance_step", step_int)
    return text, step_int, found_step


def execution_units_from_step(capital: float, lot_size_step: float | int | None) -> int:
    if lot_size_step is None:
        return 0
    step_int = max(1, int(math.ceil(float(lot_size_step))))
    return int(math.floor(capital / step_int)) if capital > 0 else 0


def _curve_points_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[tuple[datetime, float]]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    total = 0.0
    points: list[tuple[datetime, float]] = []
    for trade in ordered:
        total += trade.net_profit
        points.append((trade.close_time, total))
    return points


def _merge_curve_points(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> list[tuple[datetime, float]]:
    if not report_2020_2024.pnl_points_001 and not report_2025_2026.pnl_points_001:
        return []
    last_value = report_2020_2024.pnl_curve_001[-1] if report_2020_2024.pnl_curve_001 else 0.0
    points = list(report_2020_2024.pnl_points_001)
    points.extend((timestamp, last_value + value) for timestamp, value in report_2025_2026.pnl_points_001)
    return sorted(points, key=lambda item: item[0])


def _evaluate_portfolio_on_time_axis(
    active_sets: list[RobustStrategySet],
    allocations: dict[str, int],
) -> list[float]:
    events: list[tuple[datetime, str, int, float]] = []
    for strategy in active_sets:
        previous_value = 0.0
        for index, (timestamp, value) in enumerate(strategy.curve_points_2020_2026_001):
            events.append((timestamp, strategy.set_id, index, (value - previous_value) * allocations[strategy.set_id]))
            previous_value = value

    if not events:
        return [0.0]
    curve = [0.0]
    total = 0.0
    for _timestamp, _set_id, _index, change in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        total += change
        curve.append(total)
    return curve


def _metric_amount(report: StrategyReport, *keys: str) -> float | None:
    value = _first_metric(report, *keys)
    if value == "":
        return None
    return _to_float(value)


def maximal_drawdowns_from_report(report: StrategyReport) -> tuple[float, float]:
    """Return maximal balance/equity DD amounts from an MT5 report.

    The closed-trade curve only observes balance changes.  The maximal equity
    drawdown is therefore required explicitly to account for adverse floating
    P/L that recovered before the position was closed.
    """
    balance_dd = _metric_amount(
        report,
        "Balance Drawdown Maximal",
        "Reduccion maxima del balance",
    )
    equity_dd = _metric_amount(
        report,
        "Equity Drawdown Maximal",
        "Reduccion maxima de la equidad",
    )
    if equity_dd is None:
        raise ValueError("Final Tick 6M report has no maximal equity drawdown metric")
    return max(float(balance_dd or 0.0), 0.0), max(float(equity_dd), 0.0)


def _first_metric(report: StrategyReport, *keys: str) -> str:
    normalized = {_ascii_text(key): value for key, value in report.metrics.items()}
    for key in keys:
        value = report.metrics.get(key)
        if value:
            return value
        value = normalized.get(_ascii_text(key))
        if value:
            return value
    return ""


def _validate_curve_against_net(curve: list[float], html_net_profit: float) -> None:
    curve_net_profit = curve[-1] if curve else 0.0
    difference = abs(curve_net_profit - html_net_profit)
    tolerance = max(1.0, abs(html_net_profit) * 0.01)
    if difference > tolerance:
        raise ValueError("Parsed trade curve net profit differs from HTML net profit")


def _period_years(report: StrategyReport, period_name: str) -> tuple[int, int]:
    dates = [_parse_report_date(report.period_start), _parse_report_date(report.period_end)]
    if dates[0] and dates[1]:
        return dates[0].year, dates[1].year
    match = re.search(r"(\d{4})[_-](\d{4})", period_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    year = dates[0].year if dates[0] else 0
    return year, year


def _validate_period_order(report_2020_2024: PeriodReport, report_2025_2026: PeriodReport) -> None:
    first_end = _parse_report_date(report_2020_2024.end_date)
    second_start = _parse_report_date(report_2025_2026.start_date)
    if first_end and second_start and first_end >= second_start:
        raise ValueError("First report period must end before second report period starts")


def _parse_report_date(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_month_end(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value:
        return _parse_report_date(str(value))
    return None


def _latest_month_from_monthly(monthly: dict[int, dict[int, float]]) -> datetime | None:
    pairs = [
        (int(year), int(month))
        for year, months in monthly.items()
        for month in months
    ]
    if not pairs:
        return None
    year, month = max(pairs)
    return datetime(year, month, 1)


def _month_window(end_year: int, end_month: int, window_months: int) -> list[tuple[int, int]]:
    end_index = end_year * 12 + end_month - 1
    start_index = end_index - window_months + 1
    result: list[tuple[int, int]] = []
    for month_index in range(start_index, end_index + 1):
        year = month_index // 12
        month = month_index % 12 + 1
        result.append((year, month))
    return result


def _first_existing_report_path(row: object, *keys: str) -> Path | None:
    for key in keys:
        value = str(_row_value(row, key, default="") or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return path
    return None


def _to_float(value: str) -> float:
    text = str(value or "").split("(")[0].strip()
    text = text.replace(" ", "").replace("%", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def _ascii_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_symbol(symbol: str) -> str:
    value = (symbol or "").strip()
    if value.startswith("."):
        return value.upper()
    return re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", value).upper()


def portfolio_symbol_key(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    return PORTFOLIO_SYMBOL_ALIASES.get(normalized, normalized)


def portfolio_display_symbol(symbol: str) -> str:
    return str(symbol or "").strip() or portfolio_symbol_key(symbol)


def portfolio_group_key(
    symbol: str,
    *,
    universe_files: Iterable[str | Path] | None = None,
) -> str:
    raw_symbol = str(symbol or "").strip().upper()
    exact_group = _portfolio_universe_group_maps(universe_files)[1].get(raw_symbol)
    if exact_group:
        return exact_group
    symbol_key = portfolio_symbol_key(symbol)
    universe_group = _portfolio_universe_group_by_symbol(universe_files).get(symbol_key)
    if universe_group:
        return universe_group
    if symbol_key in PORTFOLIO_GROUP_BY_SYMBOL:
        return PORTFOLIO_GROUP_BY_SYMBOL[symbol_key]
    if _looks_like_forex_pair(symbol_key):
        return "Forex"
    return "Other"


def group_limits_for_portfolio_type(portfolio_type: PortfolioType) -> PortfolioGroupLimits:
    return DEFAULT_GROUP_LIMITS.get(portfolio_type, DEFAULT_GROUP_LIMITS[PortfolioType.BALANCED])


def _looks_like_forex_pair(symbol: str) -> bool:
    currencies = {"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"}
    return len(symbol) == 6 and symbol[:3] in currencies and symbol[3:] in currencies


def _logical_stem(set_path: str) -> str:
    stem = Path(set_path).stem
    return re.sub(r"^robust_\d{6}_", "", stem)


def _norm_path(value: str) -> str:
    try:
        return str(Path(value)).casefold()
    except (TypeError, ValueError):
        return str(value or "").casefold()


def _row_value(row: object, *keys: str, default: object = "") -> object:
    row_keys: set[str] | None = None
    try:
        row_keys = {str(key) for key in row.keys()}  # type: ignore[attr-defined]
    except Exception:
        row_keys = None
    for key in keys:
        if row_keys is not None and key not in row_keys:
            continue
        try:
            return row[key]  # type: ignore[index]
        except Exception:
            pass
        try:
            return getattr(row, key)
        except Exception:
            pass
    return default


def _row_int(row: object, *keys: str) -> int:
    try:
        return int(_row_value(row, *keys, default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _lot_size_step(capital: float, units: int) -> float | None:
    if units <= 0:
        return None
    return float(_step_for_max_units(capital, units))


def _execution_plan_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    capital: float,
) -> tuple[dict[str, int], dict[str, int]]:
    executable = allocations.copy()
    steps: dict[str, int] = {}
    for strategy in sets:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            executable[strategy.set_id] = 0
            continue
        step = _step_for_max_units(capital, units)
        executable[strategy.set_id] = execution_units_from_step(capital, step)
        steps[strategy.set_id] = step
    return executable, steps


def _step_for_max_units(capital: float, units: int) -> int:
    if capital <= 0 or units <= 0:
        return 1
    return max(1, int(math.floor(capital / (units + 1))) + 1)


def _build_unused_sets(
    raw_sets: list[RobustStrategySet],
    eligible: list[RobustStrategySet],
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    min_trades_2020_2026: int,
) -> list[UnusedSetInfo]:
    eligible_ids = {strategy.set_id for strategy in eligible}
    selected_ids = {strategy.set_id for strategy in selected}
    unused: list[UnusedSetInfo] = []
    for strategy in raw_sets:
        reason = ""
        if strategy.robustness_status != "accepted":
            reason = "not_accepted"
        elif strategy.already_used:
            reason = "already_used"
        elif strategy.trades_2020_2026 < min_trades_2020_2026:
            reason = "below_min_trades"
        elif strategy.net_profit_2020_2026_001 <= 0:
            reason = "non_positive_net_profit"
        elif strategy.has_recent_performance and (
            strategy.recent_net_profit_001 / max(strategy.recent_equity_dd_001, 1.0)
            < MIN_RECENT_EQUITY_RECOVERY
        ):
            reason = "recent_equity_recovery_below_1"
        elif strategy.set_id not in eligible_ids:
            reason = "not_eligible"
        elif strategy.set_id not in selected_ids:
            reason = "not_selected_top_k"
        elif allocations.get(strategy.set_id, 0) <= 0:
            reason = "received_zero_units"
        if reason:
            unused.append(
                UnusedSetInfo(
                    set_id=strategy.set_id,
                    symbol=strategy.symbol,
                    score=score_set_for_portfolio(strategy, min_trades_2020_2026),
                    reason=reason,
                )
            )
    return sorted(unused, key=lambda item: (item.reason, -item.score, item.symbol))
