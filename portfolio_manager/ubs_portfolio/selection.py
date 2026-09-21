"""Embudo de candidatos: validacion, carga, filtros y ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..mt5_report import StrategyReport, parse_report
from ubs.path_utils import resolve_workspace_path
from ..grid_set import (
    filter_rows_grid_off as _filter_rows_grid_off,
    set_file_has_enabled_grid as _set_file_has_enabled_grid,
)

from .symbols import (
    portfolio_display_symbol,
    portfolio_symbol_key,
)
from .models import (
    MIN_RECENT_EQUITY_RECOVERY,
    PeriodReport,
    PortfolioAvailability,
    ProgressCallback,
    RobustStrategySet,
)
from .rows import (
    _coerce_month_end,
    _first_existing_report_path,
    _latest_month_from_monthly,
    _logical_stem,
    _month_window,
    _norm_path,
    _parse_report_date,
    _row_int,
    _row_value,
)
from .monthly_validation import validate_strict_monthly_portfolio
from .reports import (
    _full_history_report_covers_segmented_history,
    _metric_amount,
    _report_period_bounds,
    build_robust_strategy_set,
    maximal_drawdowns_from_report,
    period_report_from_strategy_report,
)


def set_file_has_enabled_grid(set_path: str | Path) -> bool:
    """Compatibility export; grid parsing lives in ``grid_set``."""
    return _set_file_has_enabled_grid(set_path)


def filter_rows_grid_off(rows: Sequence[object]) -> tuple[list[object], list[str]]:
    """Compatibility export; grid filtering lives in ``grid_set``."""
    return _filter_rows_grid_off(rows)




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


@dataclass
class _LoadStats:
    skipped_missing: int = 0
    skipped_parse: int = 0
    continuous_fallbacks: int = 0
    missing_examples: list[str] = field(default_factory=list)
    parse_examples: list[str] = field(default_factory=list)
    continuous_fallback_examples: list[str] = field(default_factory=list)


@dataclass
class _CandidateReports:
    is_period: PeriodReport
    oos_period: PeriodReport
    final_tick_balance_dd: float
    final_tick_equity_dd: float
    final_tick_source: str
    final_tick_net_profit: float
    recent_equity_dd: float
    has_final_tick_performance: bool
    full_history_period: PeriodReport | None = None
    full_history_report_path: str = ""
    final_tick_period: PeriodReport | None = None
    final_tick_report_path: str = ""


class _RobustSetLoader:
    def _latest_candidate_rows(rows: Sequence[object]) -> list[object]:
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
        return list(latest_by_stem.values())


    def _required_report_paths(row: object) -> tuple[Path, Path]:
        is_path = resolve_workspace_path(
            str(_row_value(row, "is_report_path", "report_path", default=""))
        )
        oos_path = resolve_workspace_path(
            str(_row_value(row, "oos_report_path", "robust_report_path", default=""))
        )
        return is_path, oos_path


    def _record_missing_reports(
        stats: _LoadStats, set_path: str, is_path: Path, oos_path: Path
    ) -> None:
        stats.skipped_missing += 1
        if len(stats.missing_examples) >= 5:
            return
        missing_parts = []
        if not is_path.is_file():
            missing_parts.append(f"base={is_path.name or '-'}")
        if not oos_path.is_file():
            missing_parts.append(f"robustez={oos_path.name or '-'}")
        stats.missing_examples.append(f"{Path(set_path).name}: " + ", ".join(missing_parts))


    def _initial_candidate_reports(
        row: object,
        is_path: Path,
        oos_path: Path,
        parse: Callable[[Path], StrategyReport],
    ) -> _CandidateReports:
        return _CandidateReports(
            is_period=period_report_from_strategy_report(parse(is_path), "2020_2024"),
            oos_period=period_report_from_strategy_report(parse(oos_path), "2025_2026"),
            final_tick_balance_dd=float(_row_value(row, "max_balance_dd_001", default=0.0) or 0.0),
            final_tick_equity_dd=float(_row_value(row, "max_equity_dd_001", default=0.0) or 0.0),
            final_tick_source=str(_row_value(row, "floating_dd_source", default="guardado") or "guardado"),
            final_tick_net_profit=float(_row_value(row, "recent_net_profit_001", default=0.0) or 0.0),
            recent_equity_dd=float(_row_value(row, "recent_equity_dd_001", default=0.0) or 0.0),
            has_final_tick_performance=bool(_row_value(row, "has_recent_performance", default=False)),
        )


    def _load_full_history_report(
        row: object,
        set_path: str,
        reports: _CandidateReports,
        parse: Callable[[Path], StrategyReport],
        stats: _LoadStats,
    ) -> None:
        full_history_text = str(
            _row_value(row, "full_history_report_path", default="") or ""
        ).strip()
        require_full_history = bool(_row_value(row, "require_full_history", default=False))
        if not full_history_text:
            if require_full_history:
                raise ValueError("Falta el reporte Final Tick continuo 2020-hoy")
            return
        continuous_path = resolve_workspace_path(full_history_text)
        if not continuous_path.is_file():
            raise FileNotFoundError(
                f"Final Tick continuo 2020-hoy report not found: {continuous_path}"
            )
        full_history_period = period_report_from_strategy_report(
            parse(continuous_path), "final_tick_continuous_2020_today"
        )
        required_to = str(_row_value(row, "final_tick_to_date", default="") or "")
        continuous_bounds = _report_period_bounds(full_history_period)
        required_end = _parse_report_date(required_to)
        covers_history = _full_history_report_covers_segmented_history(
            reports.is_period, reports.oos_period, full_history_period
        )
        covers_recent_cutoff = required_end is None or (
            continuous_bounds is not None and continuous_bounds[1] >= required_end
        )
        if covers_history and covers_recent_cutoff:
            reports.full_history_period = full_history_period
            reports.full_history_report_path = str(continuous_path)
            return
        if require_full_history:
            raise ValueError("El supuesto Final Tick continuo no cubre todo IS + OOS + corte reciente")
        stats.continuous_fallbacks += 1
        if len(stats.continuous_fallback_examples) < 5:
            stats.continuous_fallback_examples.append(
                f"{Path(set_path).name}: {full_history_period.start_date or '?'} -> "
                f"{full_history_period.end_date or '?'}"
            )


    def _load_recent_report(
        row: object,
        reports: _CandidateReports,
        parse: Callable[[Path], StrategyReport],
    ) -> None:
        recent_report_text = str(
            _row_value(row, "final_tick_report_path", "real_tick_report_path", default="") or ""
        ).strip()
        if not recent_report_text:
            return
        recent_report_path = resolve_workspace_path(recent_report_text)
        if not recent_report_path.is_file():
            raise FileNotFoundError(f"Final Tick 6M report not found: {recent_report_path}")
        recent_report = parse(recent_report_path)
        reports.final_tick_period = period_report_from_strategy_report(
            recent_report, "final_tick_6m"
        )
        reports.final_tick_report_path = str(recent_report_path)
        reports.final_tick_balance_dd, reports.final_tick_equity_dd = maximal_drawdowns_from_report(
            recent_report
        )
        metric_net_profit = _metric_amount(recent_report, "Total Net Profit", "Beneficio Neto")
        if metric_net_profit is None:
            raise ValueError("Final Tick 6M report has no total net profit metric")
        reports.final_tick_net_profit = float(metric_net_profit)
        reports.recent_equity_dd = reports.final_tick_equity_dd
        reports.has_final_tick_performance = True
        reports.final_tick_source = "Final Tick 6M"


    def _build_loaded_strategy(
        row: object,
        set_path: str,
        is_path: Path,
        oos_path: Path,
        reports: _CandidateReports,
    ) -> RobustStrategySet:
        return build_robust_strategy_set(
            set_id=set_path,
            candidate_id=str(_row_value(row, "candidate_id", "id", default=set_path)),
            symbol=str(_row_value(row, "target_symbol", "symbol", default=reports.is_period.symbol)),
            timeframe=str(_row_value(row, "period", "timeframe", default=reports.is_period.timeframe)),
            strategy_family=str(_row_value(row, "family", "strategy_family", default="")),
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=reports.is_period,
            report_2025_2026=reports.oos_period,
            set_path=set_path,
            is_report_path=str(is_path),
            oos_report_path=str(oos_path),
            final_tick_balance_dd_001=reports.final_tick_balance_dd,
            final_tick_equity_dd_001=reports.final_tick_equity_dd,
            final_tick_net_profit_001=reports.final_tick_net_profit,
            recent_equity_dd_001=reports.recent_equity_dd,
            has_final_tick_performance=reports.has_final_tick_performance,
            final_tick_source=reports.final_tick_source,
            final_tick_report=reports.final_tick_period,
            final_tick_report_path=reports.final_tick_report_path,
            full_history_report=reports.full_history_period,
            full_history_report_path=reports.full_history_report_path,
        )


    def _load_candidate(
        row: object,
        parse: Callable[[Path], StrategyReport],
        stats: _LoadStats,
    ) -> RobustStrategySet | None:
        set_path = str(_row_value(row, "set_path", default=""))
        is_path, oos_path = _RobustSetLoader._required_report_paths(row)
        if not is_path.is_file() or not oos_path.is_file():
            _RobustSetLoader._record_missing_reports(stats, set_path, is_path, oos_path)
            return None
        try:
            reports = _RobustSetLoader._initial_candidate_reports(row, is_path, oos_path, parse)
            _RobustSetLoader._load_full_history_report(row, set_path, reports, parse, stats)
            _RobustSetLoader._load_recent_report(row, reports, parse)
            return _RobustSetLoader._build_loaded_strategy(row, set_path, is_path, oos_path, reports)
        except Exception as exc:
            stats.skipped_parse += 1
            if len(stats.parse_examples) < 5:
                message = str(exc).strip() or "sin detalle"
                stats.parse_examples.append(
                    f"{Path(set_path).name}: {type(exc).__name__}: {message}"
                )
            return None


    def _load_warnings(stats: _LoadStats) -> list[str]:
        warnings: list[str] = []
        if stats.skipped_missing:
            warnings.append(
                f"{stats.skipped_missing} candidato(s) omitido(s): faltan reportes base o robustez."
            )
            warnings.append("Ejemplos de reportes ausentes: " + " | ".join(stats.missing_examples))
        if stats.skipped_parse:
            warnings.append(
                f"{stats.skipped_parse} candidato(s) omitido(s): reporte ilegible o curva invalida."
            )
            warnings.append("Ejemplos de errores de carga: " + " | ".join(stats.parse_examples))
        if stats.continuous_fallbacks:
            warnings.append(
                f"{stats.continuous_fallbacks} reporte(s) Final Tick no eran continuos; "
                "se conservó la curva IS + OOS y Final Tick 6M sólo extendió la cola/riesgo."
            )
            warnings.append(
                "Ejemplos de coberturas no continuas: "
                + " | ".join(stats.continuous_fallback_examples)
            )
        return warnings


def load_robust_sets_from_rows(
    rows: Sequence[object],
    used_set_paths: Iterable[str],
    *,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[RobustStrategySet], list[str]]:
    used = {_norm_path(path) for path in used_set_paths}
    candidates = _RobustSetLoader._latest_candidate_rows(rows)
    stats = _LoadStats()

    loaded: list[RobustStrategySet] = []
    for index, row in enumerate(candidates, start=1):
        set_path = str(_row_value(row, "set_path", default=""))
        if _norm_path(set_path) in used:
            continue
        if progress:
            progress(f"Analizando set Final Tick OK {index}/{len(candidates)}")
        candidate = _RobustSetLoader._load_candidate(row, parse, stats)
        if candidate is not None:
            loaded.append(candidate)
    return loaded, _RobustSetLoader._load_warnings(stats)


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
