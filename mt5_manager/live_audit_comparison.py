from __future__ import annotations

from .live_audit_attribution import annotate_unmatched
from .live_audit_core import *  # noqa: F403
from .live_audit_manual_pairs import (
    attributed_assignment, forced_assignment, note_manual_pairs,
)
from .live_audit_methodology import methodology


class _ComparisonMixin:
    @staticmethod
    def _comparison_state(
        real: list[dict[str, Any]], request: dict[str, Any],
    ) -> dict[str, Any]:
        still_open = list(request.get("real_positions_open_at_period_end") or [])
        return {
            "unused": set(range(len(real))), "matched": 0, "within_tolerance": 0,
            "deviations": 0, "open_real": 0, "matched_by_strategy": {},
            "within_tolerance_by_strategy": {}, "deviating_by_strategy": {},
            "open_real_by_strategy": {}, "missing_by_strategy": {},
            "still_open": still_open, "unused_open": set(range(len(still_open))),
            "deviation_reasons": {
                "open_time": 0, "close_time": 0, "open_price": 0, "volume": 0,
                "pnl": 0, "drawdown": 0,
            },
            "tester_data_issues": {}, "operation_comparisons": [],
        }

    @staticmethod
    def _same_market(
        expected: dict[str, Any], real: list[dict[str, Any]], indices: Iterable[int],
    ) -> list[tuple[float, int]]:
        """Cierres reales del mismo símbolo y lado, por distancia de apertura."""
        return sorted(
            (abs((real[index]["open_time"] - expected["open_time"]).total_seconds()), index)
            for index in indices
            if real[index]["symbol"].casefold() == expected["symbol"].casefold()
            and real[index]["side"] == expected["side"]
        )

    @staticmethod
    def _stops_evidence(
        expected: dict[str, Any], actual: dict[str, Any], limit: float | None,
    ) -> int:
        """0 coinciden, 1 sin evidencia, 2 falla un nivel, 3 fallan los dos.

        El SL y el TP con que se coloca la orden son la huella de la operación:
        en el auditado del 2026-10-04 los dos XAUUSD que abren en el mismo
        segundo llevaban 4189,28/4133,78 y 4169,86/4123,54, y sólo uno de los
        dos cierres reales traía cada par. No siempre existen —una orden a
        mercado no declara niveles—, y entonces no dicen nada.

        **Cuántos fallan importa.** Era un sí/no, y con eso la huella dejaba de
        distinguir en cuanto un solo nivel se pasaba: en `audit-173` de
        RoboForex, `#T19` declaraba SL a 2,5 puntos del real —dentro del límite
        de 10,5— y TP a 14,7, fuera por 4,2; `#T21` declaraba 140,4 y 320,8.
        Las dos caían en el mismo cajón, una diferencia de 56× se perdía, y
        decidía el campo siguiente, el lote esperado, que con autolote es una
        simulación y no un hecho observado: se llevó la real la fila cuyo cierre
        estaba a 47.798 s en vez de la que coincidía al segundo. Un nivel que
        coincide y otro que no es evidencia más débil que las dos coincidiendo,
        pero más fuerte que ninguna.
        """
        levels = [
            (float(expected.get(key) or 0.0), float(actual.get(key) or 0.0))
            for key in ("sl", "tp")
        ]
        known = [(mine, theirs) for mine, theirs in levels if mine and theirs]
        if not known or limit is None:
            return 1
        apart = sum(1 for mine, theirs in known if abs(mine - theirs) > limit)
        return 1 + apart if apart else 0

    @staticmethod
    def _pair_cost(
        expected: dict[str, Any], actual: dict[str, Any], request: dict[str, Any],
        points: dict[str, float],
    ) -> tuple[int, int, int, int, float] | None:
        """Lo lejos que está esta real de ser la del tester, o `None` si no lo es.

        Manda la huella de SL y TP, cuando las dos partes la declaran. Después
        el lote, que identifica a la estrategia dentro de la cuenta igual que en
        el filtro de pertenencia. Después la apertura dentro de tolerancia,
        después el cierre, y a igualdad la menor distancia total.
        """
        if (
            actual["symbol"].casefold() != expected["symbol"].casefold()
            or actual["side"] != expected["side"]
        ):
            return None
        time_limit = request["trade_time_tolerance_seconds"]
        open_delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
        close_delta = abs((actual["close_time"] - expected["close_time"]).total_seconds())
        expected_volume, _tester_volume = _expected_real_volume(expected, request, actual)
        volume_limit = max(expected_volume, 1e-9) * request["volume_tolerance_pct"] / 100
        price_limit, _points, _rule = _effective_price_tolerance(
            actual["symbol"], points.get(actual["symbol"], 0.0),
            request["price_tolerance_points"],
        )
        stops_evidence = _ComparisonMixin._stops_evidence(expected, actual, price_limit)
        complete_stops = stops_evidence == 0 and all(
            float(expected.get(key) or 0.0) and float(actual.get(key) or 0.0)
            for key in ("sl", "tp")
        )
        volume_outside = abs(float(actual["volume"]) - expected_volume) > volume_limit
        if (
            open_delta > time_limit and close_delta > time_limit
            and (not complete_stops or volume_outside)
        ):
            return None
        return (
            stops_evidence, int(volume_outside),
            int(open_delta > time_limit), int(close_delta > time_limit),
            open_delta + close_delta,
        )

    @staticmethod
    def _assign_real_trades(
        tester: list[dict[str, Any]], real: list[dict[str, Any]], request: dict[str, Any],
        points: dict[str, float], forced: dict[int, int] | None = None,
    ) -> dict[int, int]:
        """Reparte los cierres reales entre las operaciones del tester.

        Elegir por tester, de uno en uno y sólo por la apertura, repartía mal
        dos operaciones abiertas en el mismo segundo: la primera se quedaba con
        el cierre de la segunda y ninguna de las dos quedaba dentro de
        tolerancia. El reparto se decide ahora sobre todas las parejas a la vez,
        de la mejor a la peor, y cada real se consume una sola vez.

        Las parejas que decidió el usuario entran antes que cualquier coste y
        consumen su real: lo que el auditor no supo ver no se vuelve a repartir.
        """
        costs = sorted(
            (*cost, tester_index, real_index)
            for tester_index, expected in enumerate(tester)
            for real_index, actual in enumerate(real)
            for cost in [_ComparisonMixin._pair_cost(expected, actual, request, points)]
            if cost is not None
        )
        assignment: dict[int, int] = dict(forced or {})
        taken: set[int] = set(assignment.values())
        for *_cost, tester_index, real_index in costs:
            if tester_index in assignment or real_index in taken:
                continue
            assignment[tester_index] = real_index
            taken.add(real_index)
        return assignment

    @staticmethod
    def _missing_comparison(
        expected: dict[str, Any], tester_index: int, strategy: str,
        same_market: list[tuple[float, int]], real: list[dict[str, Any]],
        request: dict[str, Any], data_issues: list[str],
    ) -> dict[str, Any]:
        """La fila de un cierre del tester que ninguna real reclamó.

        Publica igualmente el lote que se esperaba en real. El saldo es siempre
        el de la **cuenta real** —el tester no tiene autolote: su lote es el
        fijo con que se ejecutó el backtest—, y lo único que falta sin pareja es
        el instante en que leerlo. Se usa entonces la hora de apertura del
        tester, que da el mismo escalón de saldo que habría dado la real: una
        apertura real sólo puede caer dentro de la tolerancia de tiempo.

        Dejar la fila sin lote esperado la congelaba en el lote del backtest, y
        la simulación de la pantalla de análisis mezclaba dos escalas en una
        misma curva.
        """
        time_limit = request["trade_time_tolerance_seconds"]
        nearest = min(same_market) if same_market else None
        nearest_trade = real[nearest[1]] if nearest else None
        volume_detail = _expected_real_volume_detail(expected, request)
        return {
            "tester_index": tester_index, "status": "missing", "strategy": strategy,
            "tester": _trade_view(expected), "real": None,
            "nearest_unused_real": _trade_view(nearest_trade),
            "real_position_still_open": None,
            "measurements": {
                "nearest_open_time_delta_seconds": round(nearest[0], 3) if nearest else None,
            },
            "limits": {
                "open_time_seconds": time_limit,
                "volume_expected_basis": "real_balance_at_tester_open_time",
                **_volume_limits(volume_detail),
            },
            "data_issues": data_issues,
            "reasons": [
                "open_time_outside_tolerance" if nearest else "no_real_same_symbol_and_side"
            ],
        }

    @staticmethod
    def _consume_open_position(
        expected: dict[str, Any], tester_index: int, strategy: str,
        state: dict[str, Any], points: dict[str, float], request: dict[str, Any],
        data_issues: list[str],
    ) -> bool:
        match = _matching_open_position(
            expected, state["still_open"], state["unused_open"],
            request["trade_time_tolerance_seconds"],
        )
        if match is None:
            return False
        open_index, position = match
        state["unused_open"].remove(open_index)
        state["matched"] += 1
        state["open_real"] += 1
        _ComparisonMixin._increment(state["matched_by_strategy"], strategy)
        _ComparisonMixin._increment(state["open_real_by_strategy"], strategy)
        row, reasons = _open_position_comparison(
            expected, position, tester_index, open_index,
            points.get(str(position.get("symbol") or expected["symbol"]), 0.0),
            request, request.get("real_strategy_lots") or {}, data_issues,
        )
        if reasons:
            state["deviations"] += 1
            _ComparisonMixin._increment(state["deviating_by_strategy"], strategy)
            for reason in reasons:
                state["deviation_reasons"][reason] += 1
        state["operation_comparisons"].append(row)
        return True

    @staticmethod
    def _matched_measurements(
        expected: dict[str, Any], open_time_delta: float, point: float,
        close_delta: float, price_delta: float, volume_delta: float, pnl: dict[str, Any],
        expected_volume: float, scaled_tester_profit: float, pnl_scale: float,
    ) -> dict[str, Any]:
        return {
            "open_time_delta_seconds": round(open_time_delta, 3),
            "close_time_delta_seconds": round(close_delta, 3),
            "open_price_delta": round(price_delta, 10),
            "open_price_delta_points": round(price_delta / point, 3) if point > 0 else None,
            "volume_delta": round(volume_delta, 8),
            "volume_delta_pct": round(volume_delta / max(abs(expected_volume), 1e-9) * 100, 3),
            "pnl_delta": round(float(pnl["delta"]), 2),
            "pnl_delta_pct": round(float(pnl["delta"]) / max(abs(scaled_tester_profit), 1.0) * 100, 3),
            "pnl_scale": round(pnl_scale, 8),
            "tester_profit_scaled": round(scaled_tester_profit, 2),
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
        volume_limit: float, pnl_limit: float, volume_detail: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "open_time_seconds": request["trade_time_tolerance_seconds"],
            "close_time_seconds": request["trade_time_tolerance_seconds"],
            "open_price_points": round(price_limit_points, 3) if price_limit_points is not None else None,
            "open_price_absolute": round(price_limit, 10) if price_limit is not None else None,
            "open_price_configured_points": request["price_tolerance_points"],
            "open_price_rule": price_limit_rule, "volume_pct": request["volume_tolerance_pct"],
            "volume_absolute": round(volume_limit, 8),
            **_volume_limits(volume_detail),
            "pnl_pct": request["pnl_deviation_warning_pct"],
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
        strategy = str(expected["strategy"])
        volume_detail = _expected_real_volume_detail(expected, request, actual)
        expected_volume = float(volume_detail["expected"])
        tester_volume = float(volume_detail["tester"])
        pnl_scale = expected_volume / tester_volume if tester_volume > 0 else 1.0
        scaled_tester_profit = float(expected["profit"]) * pnl_scale
        volume_limit = max(expected_volume, 1e-9) * request["volume_tolerance_pct"] / 100
        pnl = _pnl_comparison(
            actual["profit"], scaled_tester_profit, request["pnl_deviation_warning_pct"],
        )
        close_delta = abs((actual["close_time"] - expected["close_time"]).total_seconds())
        price_delta = abs(float(actual["open_price"]) - float(expected["open_price"]))
        volume_delta = abs(float(actual["volume"]) - expected_volume)
        reasons = []
        if open_time_delta > request["trade_time_tolerance_seconds"]:
            reasons.append("open_time")
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
            expected_volume, scaled_tester_profit, pnl_scale,
        )
        limits = _ComparisonMixin._matched_limits(
            request, price_limit, price_limit_points, price_limit_rule,
            volume_limit, float(pnl["limit"]), volume_detail,
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
        index = state["assignment"].get(tester_index - 1)
        if index is None:
            if _ComparisonMixin._consume_open_position(
                expected, tester_index, strategy, state, points, request, data_issues,
            ):
                return
            _ComparisonMixin._increment(state["missing_by_strategy"], strategy)
            state["operation_comparisons"].append(_ComparisonMixin._missing_comparison(
                expected, tester_index, strategy,
                _ComparisonMixin._same_market(expected, real, state["unassigned"]), real,
                request, data_issues,
            ))
            return
        state["unused"].remove(index)
        actual = real[index]
        open_time_delta = abs(
            (actual["open_time"] - expected["open_time"]).total_seconds()
        )
        state["matched"] += 1
        _ComparisonMixin._increment(state["matched_by_strategy"], strategy)
        row, reasons = _ComparisonMixin._matched_comparison(
            expected, actual, tester_index, index, open_time_delta,
            points.get(actual["symbol"], 0.0), request, data_issues,
        )
        # La pareja forzada se mide igual que cualquier otra, pero la fila dice
        # quién la decidió: un lector tiene que poder distinguir lo que encontró
        # el auditor de lo que le dijeron.
        if tester_index - 1 in (state.get("forced") or {}):
            row["manual_pair"] = True
        elif tester_index - 1 in (state.get("attributed") or {}):
            # «manual» si lo dijo el Excel y «comment» si lo escribió el EA: son
            # dos pruebas distintas y la fila no puede presentarlas igual.
            row["attributed_pair"] = str((
                (request.get("_real_attribution_by_ticket") or {}).get(
                    str(actual.get("position_id") or actual.get("ticket") or ""),
                ) or {}
            ).get("source") or "manual")
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
    def _name_strategies(state: dict[str, Any], set_names: dict[str, str]) -> None:
        """Pone en cada operación el `.set` con el que se probó su estrategia."""
        for row in state["operation_comparisons"]:
            row["strategy_set"] = set_names.get(str(row.get("strategy") or ""), "")

    @staticmethod
    def _strategy_comparison_summary(
        strategies: dict[str, int], state: dict[str, Any],
        set_names: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        set_names = set_names or {}
        return [{
            "strategy": strategy, "strategy_set": set_names.get(strategy, ""),
            "tester_trades": int(strategies.get(strategy) or 0),
            "aligned": state["matched_by_strategy"].get(strategy, 0),
            "within_tolerance": state["within_tolerance_by_strategy"].get(strategy, 0),
            "open_real": state["open_real_by_strategy"].get(strategy, 0),
            "with_deviations": state["deviating_by_strategy"].get(strategy, 0),
            "missing_real": state["missing_by_strategy"].get(strategy, 0),
        } for strategy in sorted(strategies)]

    @staticmethod
    def _mark_quality(
        result: dict[str, Any], artifacts: list[dict[str, Any]], minimum: float,
    ) -> list[dict[str, Any]]:
        """Marca los sets sin la calidad exigida en vez de anular la auditoría.

        Una calidad insuficiente la causa el historial de ticks de la terminal
        que ejecutó ese set, no la cuenta auditada. Anular la comparación
        entera dejaba sin medir once sets al 100 % por culpa de dos al 66 %.
        """
        below = [{
            "strategy": str(row.get("strategy") or ""),
            "symbol": str(row.get("symbol") or ""),
            "history_quality_pct": (
                None if row.get("history_quality_pct") is None
                else float(row["history_quality_pct"])
            ),
        } for row in artifacts if (
            row.get("history_quality_pct") is None
            or float(row["history_quality_pct"]) < minimum
        )]
        quality_by_strategy = {
            str(row.get("strategy") or ""): row.get("history_quality_pct")
            for row in artifacts
        }
        flagged = {row["strategy"] for row in below}
        for summary in (result.get("comparison_detail") or {}).get("strategy_summary") or []:
            strategy = str(summary.get("strategy") or "")
            summary["history_quality_pct"] = quality_by_strategy.get(strategy)
            summary["quality_ok"] = strategy not in flagged
        result["minimum_tick_history_quality_pct"] = minimum
        result["strategies_below_quality"] = below
        return below

    @staticmethod
    def _quality_note(below: list[dict[str, Any]], minimum: float) -> str:
        """Lo que se añade al resumen para que el aviso llegue a la tarjeta."""
        if not below:
            return ""
        detail = ", ".join(
            f"{row['symbol'] or row['strategy']} "
            + ("sin calidad informada" if row["history_quality_pct"] is None
               else f"{row['history_quality_pct']:.2f}%")
            for row in below[:5]
        )
        return (
            f" {len(below)} set(s) por debajo de la calidad mínima {minimum:.2f}%"
            f" ({detail}): sus desviaciones pueden ser del historial de ticks."
        )

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
        # El usuario puede haber dicho de quién es cada real que el auditor no
        # pudo emparejar. No se inventa una pareja: la fila sigue siendo
        # `extra` y sólo queda anotada.
        state["attribution_summary"] = annotate_unmatched(
            unmatched_operations, request.get("_real_attribution_by_ticket") or {},
        )
        set_names = dict(request.get("strategy_set_names") or {})
        _ComparisonMixin._name_strategies(state, set_names)
        strategy_summary = _ComparisonMixin._strategy_comparison_summary(
            strategies, state, set_names,
        )
        detail = _ComparisonMixin._comparison_detail(
            request, state, unmatched, unmatched_operations, strategy_summary,
            real_dd, tester_dd, dd_deviation,
        )
        note_manual_pairs(
            detail, state.get("forced") or {}, state.get("attributed") or {},
        )
        missing = len(tester) - state["matched"]
        extra = len(state["unused"])
        return {
            "matched_trades": state["matched"], "open_real_trades": state["open_real"],
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
            "open_real_by_strategy": state["open_real_by_strategy"],
            "deviating_by_strategy": state["deviating_by_strategy"],
            "missing_by_strategy": state["missing_by_strategy"], "unmatched_real": unmatched,
            "deviation_reasons": {
                key: value for key, value in state["deviation_reasons"].items() if value
            },
            "tester_data_issues": state["tester_data_issues"],
            "time_tolerance_seconds": time_limit,
            "methodology": methodology(request),
            "strategy_summary": strategy_summary,
            "operation_comparisons": state["operation_comparisons"],
            "unmatched_real_operations": unmatched_operations,
            "real_attribution": state.get("attribution_summary") or {},
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
        state = _ComparisonMixin._comparison_state(real, request)
        state["forced"] = forced_assignment(
            tester, real, request.get("manual_pairs") or {},
        )
        state["assignment"] = _ComparisonMixin._assign_real_trades(
            tester, real, request, points, state["forced"],
        )
        # Segunda pasada: lo que el Excel ya había atribuido sólo se puede
        # emparejar sabiendo qué quedó suelto, y eso no se sabe hasta repartir.
        state["attributed"] = attributed_assignment(
            tester, real, request.get("_real_attribution_by_ticket") or {},
            state["assignment"], request.get("_period_start"),
        )
        if state["attributed"]:
            state["assignment"] = _ComparisonMixin._assign_real_trades(
                tester, real, request, points,
                {**state["forced"], **state["attributed"]},
            )
        state["unassigned"] = set(range(len(real))) - set(state["assignment"].values())
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
