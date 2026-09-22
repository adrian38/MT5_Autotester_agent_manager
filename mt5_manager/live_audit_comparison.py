from __future__ import annotations

from .live_audit_core import *  # noqa: F403


class _ComparisonMixin:
    @staticmethod
    def _comparison_state(real: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "unused": set(range(len(real))), "matched": 0, "within_tolerance": 0,
            "deviations": 0, "matched_by_strategy": {},
            "within_tolerance_by_strategy": {}, "deviating_by_strategy": {},
            "missing_by_strategy": {},
            "deviation_reasons": {
                "close_time": 0, "open_price": 0, "volume": 0, "pnl": 0,
                "drawdown": 0,
            },
            "tester_data_issues": {}, "operation_comparisons": [],
        }

    @staticmethod
    def _candidate_matches(
        expected: dict[str, Any], real: list[dict[str, Any]], unused: set[int],
        time_limit: int,
    ) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
        candidates, same_market = [], []
        for index in unused:
            actual = real[index]
            if (
                actual["symbol"].casefold() != expected["symbol"].casefold()
                or actual["side"] != expected["side"]
            ):
                continue
            delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
            same_market.append((delta, index))
            if delta <= time_limit:
                candidates.append((delta, index))
        return candidates, same_market

    @staticmethod
    def _missing_comparison(
        expected: dict[str, Any], tester_index: int, strategy: str,
        same_market: list[tuple[float, int]], real: list[dict[str, Any]],
        time_limit: int, data_issues: list[str],
    ) -> dict[str, Any]:
        nearest = min(same_market) if same_market else None
        nearest_trade = real[nearest[1]] if nearest else None
        return {
            "tester_index": tester_index, "status": "missing", "strategy": strategy,
            "tester": _trade_view(expected), "real": None,
            "nearest_unused_real": _trade_view(nearest_trade),
            "measurements": {
                "nearest_open_time_delta_seconds": round(nearest[0], 3) if nearest else None,
            },
            "limits": {"open_time_seconds": time_limit}, "data_issues": data_issues,
            "reasons": [
                "open_time_outside_tolerance" if nearest else "no_real_same_symbol_and_side"
            ],
        }

    @staticmethod
    def _matched_measurements(
        expected: dict[str, Any], open_time_delta: float, point: float,
        close_delta: float, price_delta: float, volume_delta: float, pnl: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "open_time_delta_seconds": round(open_time_delta, 3),
            "close_time_delta_seconds": round(close_delta, 3),
            "open_price_delta": round(price_delta, 10),
            "open_price_delta_points": round(price_delta / point, 3) if point > 0 else None,
            "volume_delta": round(volume_delta, 8),
            "volume_delta_pct": round(volume_delta / max(abs(float(expected["volume"])), 1e-9) * 100, 3),
            "pnl_delta": round(float(pnl["delta"]), 2),
            "pnl_delta_pct": round(float(pnl["delta"]) / max(abs(float(expected["profit"])), 1.0) * 100, 3),
            "pnl_change": round(float(pnl["change"]), 2),
            "pnl_change_pct": round(float(pnl["change_pct"]), 3),
            "pnl_adverse_delta": round(float(pnl["adverse_delta"]), 2),
            "pnl_adverse_delta_pct": round(float(pnl["adverse_delta_pct"]), 3),
            "pnl_direction": pnl["direction"],
        }

    @staticmethod
    def _matched_limits(
        request: dict[str, Any], price_limit: float | None,
        price_limit_points: float | None, price_limit_rule: str,
        volume_limit: float, pnl_limit: float,
    ) -> dict[str, Any]:
        return {
            "open_time_seconds": request["trade_time_tolerance_seconds"],
            "close_time_seconds": request["trade_time_tolerance_seconds"],
            "open_price_points": round(price_limit_points, 3) if price_limit_points is not None else None,
            "open_price_absolute": round(price_limit, 10) if price_limit is not None else None,
            "open_price_configured_points": request["price_tolerance_points"],
            "open_price_rule": price_limit_rule, "volume_pct": request["volume_tolerance_pct"],
            "volume_absolute": round(volume_limit, 8), "pnl_pct": request["pnl_deviation_warning_pct"],
            "pnl_absolute": round(pnl_limit, 2),
        }

    @staticmethod
    def _matched_comparison(
        expected: dict[str, Any], actual: dict[str, Any], tester_index: int,
        real_index: int, open_time_delta: float, point: float,
        request: dict[str, Any], data_issues: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        price_limit, price_limit_points, price_limit_rule = _effective_price_tolerance(
            actual["symbol"], point, request["price_tolerance_points"],
        )
        volume_limit = max(expected["volume"], 1e-9) * request["volume_tolerance_pct"] / 100
        pnl = _pnl_comparison(
            actual["profit"], expected["profit"], request["pnl_deviation_warning_pct"],
        )
        close_delta = abs((actual["close_time"] - expected["close_time"]).total_seconds())
        price_delta = abs(float(actual["open_price"]) - float(expected["open_price"]))
        volume_delta = abs(float(actual["volume"]) - float(expected["volume"]))
        reasons = []
        if close_delta > request["trade_time_tolerance_seconds"]:
            reasons.append("close_time")
        epsilon = max(point * 1e-6, 1e-12)
        if (
            price_limit is not None and price_delta > price_limit
            and not math.isclose(price_delta, price_limit, rel_tol=0.0, abs_tol=epsilon)
        ):
            reasons.append("open_price")
        if volume_delta > volume_limit:
            reasons.append("volume")
        if pnl["outside_tolerance"]:
            reasons.append("pnl")
        measurements = _ComparisonMixin._matched_measurements(
            expected, open_time_delta, point, close_delta, price_delta, volume_delta, pnl,
        )
        limits = _ComparisonMixin._matched_limits(
            request, price_limit, price_limit_points, price_limit_rule,
            volume_limit, float(pnl["limit"]),
        )
        return ({
            "tester_index": tester_index, "real_index": real_index + 1,
            "status": "deviation" if reasons else "matched",
            "strategy": str(expected["strategy"]), "tester": _trade_view(expected),
            "real": _trade_view(actual), "nearest_unused_real": None,
            "measurements": measurements, "limits": limits,
            "data_issues": data_issues, "reasons": reasons,
        }, reasons)

    @staticmethod
    def _increment(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    @staticmethod
    def _compare_one_operation(
        real: list[dict[str, Any]], points: dict[str, float], request: dict[str, Any],
        state: dict[str, Any], tester_index: int, expected: dict[str, Any],
    ) -> None:
        strategy = str(expected["strategy"])
        data_issues = []
        if expected["close_time"] < expected["open_time"]:
            data_issues.append("close_before_open")
            _ComparisonMixin._increment(state["tester_data_issues"], "close_before_open")
        candidates, same_market = _ComparisonMixin._candidate_matches(
            expected, real, state["unused"], request["trade_time_tolerance_seconds"],
        )
        if not candidates:
            _ComparisonMixin._increment(state["missing_by_strategy"], strategy)
            state["operation_comparisons"].append(_ComparisonMixin._missing_comparison(
                expected, tester_index, strategy, same_market, real,
                request["trade_time_tolerance_seconds"], data_issues,
            ))
            return
        open_time_delta, index = min(candidates)
        state["unused"].remove(index)
        actual = real[index]
        state["matched"] += 1
        _ComparisonMixin._increment(state["matched_by_strategy"], strategy)
        row, reasons = _ComparisonMixin._matched_comparison(
            expected, actual, tester_index, index, open_time_delta,
            points.get(actual["symbol"], 0.0), request, data_issues,
        )
        if reasons:
            state["deviations"] += 1
            _ComparisonMixin._increment(state["deviating_by_strategy"], strategy)
            for reason in reasons:
                state["deviation_reasons"][reason] += 1
        else:
            state["within_tolerance"] += 1
            _ComparisonMixin._increment(state["within_tolerance_by_strategy"], strategy)
        state["operation_comparisons"].append(row)

    @staticmethod
    def _unmatched_real_operations(
        real: list[dict[str, Any]], unused: set[int],
    ) -> tuple[dict[str, int], list[dict[str, Any]]]:
        unmatched, operations = {}, []
        for index in unused:
            trade = real[index]
            key = f"{trade.get('symbol') or '?'} / lote {float(trade.get('volume') or 0):g}"
            unmatched[key] = unmatched.get(key, 0) + 1
            operations.append({
                "real_index": index + 1, "status": "extra", "real": _trade_view(trade),
                "reason": "not_used_by_any_tester_operation",
            })
        return unmatched, operations

    @staticmethod
    def _strategy_comparison_summary(
        strategies: dict[str, int], state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [{
            "strategy": strategy, "tester_trades": int(strategies.get(strategy) or 0),
            "aligned": state["matched_by_strategy"].get(strategy, 0),
            "within_tolerance": state["within_tolerance_by_strategy"].get(strategy, 0),
            "with_deviations": state["deviating_by_strategy"].get(strategy, 0),
            "missing_real": state["missing_by_strategy"].get(strategy, 0),
        } for strategy in sorted(strategies)]

    @staticmethod
    def _finalize_comparison(
        real: list[dict[str, Any]], tester: list[dict[str, Any]],
        request: dict[str, Any], strategies: dict[str, int], state: dict[str, Any],
    ) -> dict[str, Any]:
        real_dd, tester_dd = _drawdown(real), _drawdown(tester)
        dd_deviation = abs(real_dd - tester_dd) / max(tester_dd, 1.0) * 100
        if dd_deviation > request["drawdown_deviation_warning_pct"]:
            state["deviations"] += 1
            state["deviation_reasons"]["drawdown"] += 1
        stalled = sum(
            1 for strategy, count in strategies.items()
            if count and not state["matched_by_strategy"].get(strategy)
        )
        unmatched, unmatched_operations = _ComparisonMixin._unmatched_real_operations(
            real, state["unused"],
        )
        strategy_summary = _ComparisonMixin._strategy_comparison_summary(strategies, state)
        detail = _ComparisonMixin._comparison_detail(
            request, state, unmatched, unmatched_operations, strategy_summary,
            real_dd, tester_dd, dd_deviation,
        )
        missing = len(tester) - state["matched"]
        extra = len(state["unused"])
        return {
            "matched_trades": state["matched"],
            "within_tolerance_trades": state["within_tolerance"],
            "missing_real_trades": missing, "extra_real_trades": extra,
            "deviating_pairs": sum(state["deviating_by_strategy"].values()),
            "deviating_trades": state["deviations"],
            "discrepancies": missing + extra + state["deviations"],
            "stalled_strategies": stalled, "real_drawdown": round(real_dd, 2),
            "tester_drawdown": round(tester_dd, 2),
            "drawdown_deviation_pct": round(dd_deviation, 2), "comparison_detail": detail,
        }

    @staticmethod
    def _comparison_detail(
        request: dict[str, Any], state: dict[str, Any], unmatched: dict[str, int],
        unmatched_operations: list[dict[str, Any]], strategy_summary: list[dict[str, Any]],
        real_dd: float, tester_dd: float, dd_deviation: float,
    ) -> dict[str, Any]:
        time_limit = request["trade_time_tolerance_seconds"]
        return {
            "matched_by_strategy": state["matched_by_strategy"],
            "within_tolerance_by_strategy": state["within_tolerance_by_strategy"],
            "deviating_by_strategy": state["deviating_by_strategy"],
            "missing_by_strategy": state["missing_by_strategy"], "unmatched_real": unmatched,
            "deviation_reasons": {
                key: value for key, value in state["deviation_reasons"].items() if value
            },
            "tester_data_issues": state["tester_data_issues"],
            "time_tolerance_seconds": time_limit,
            "methodology": {
                "alignment": "Mismo símbolo y lado; apertura dentro de tolerancia; se elige el menor delta y cada real se usa una vez.",
                "validation": "Después se validan cierre, precio de apertura y volumen. El PnL solo alerta si el resultado real empeora frente al tester; una mejora es admisible. El drawdown se valida sobre el conjunto.",
                "tolerances": {
                    "time_seconds": time_limit, "price_points": request["price_tolerance_points"],
                    "price_policy": "adaptive_by_instrument",
                    "price_absolute_floors": ADAPTIVE_PRICE_TOLERANCE_FLOORS,
                    "volume_pct": request["volume_tolerance_pct"],
                    "pnl_pct": request["pnl_deviation_warning_pct"],
                    "pnl_policy": "adverse_shortfall_only",
                    "drawdown_pct": request["drawdown_deviation_warning_pct"],
                },
            },
            "strategy_summary": strategy_summary,
            "operation_comparisons": state["operation_comparisons"],
            "unmatched_real_operations": unmatched_operations,
            "drawdown": {
                "real": round(real_dd, 2), "tester": round(tester_dd, 2),
                "deviation_pct": round(dd_deviation, 2),
                "limit_pct": request["drawdown_deviation_warning_pct"],
                "outside_tolerance": dd_deviation > request["drawdown_deviation_warning_pct"],
            },
        }

    @staticmethod
    def _compare(
        real: list[dict[str, Any]], tester: list[dict[str, Any]], points: dict[str, float],
        request: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        state = _ComparisonMixin._comparison_state(real)
        for tester_index, expected in enumerate(tester, 1):
            _ComparisonMixin._compare_one_operation(
                real, points, request, state, tester_index, expected,
            )
        return _ComparisonMixin._finalize_comparison(
            real, tester, request, strategies, state,
        )

    @staticmethod
    def _result_base(
        request: dict[str, Any], period_start: datetime, period_end: datetime,
        real: list[dict[str, Any]], tester: list[dict[str, Any]], quality: float | None,
    ) -> dict[str, Any]:
        return {
            "audit_key": request["audit_key"], "portfolio_id": request["portfolio_id"],
            "portfolio_type": request["portfolio_type"], "completed_at": utc_now(),
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "period_mode": request.get("period_mode", "rolling_days"),
            "period_days": request["period_days"],
            "period_start_date": request.get("period_start_date", ""),
            "period_end_date": request.get("period_end_date", ""),
            "history_quality_pct": round(quality, 2) if quality is not None else None,
            "real_trades": len(real), "tester_trades": len(tester),
        }
