"""Convierte la materia prima que observó el nodo en el veredicto de la auditoría.

El reparto es el que siempre debió ser: el nodo **ejecuta** —pausa el pipeline,
extrae el historial con la API de MT5, lanza el Strategy Tester, exporta los
informes nativos y restaura las cuentas— y publica lo que vio. Todo el
**criterio** vive aquí: filtro de pertenencia al portafolio, puerta de History
Quality, tolerancias y comparación operación por operación.

Dos consecuencias, que son el motivo del cambio:

- Una tolerancia nueva se aplica a una ejecución ya guardada sin volver a abrir
  un terminal ni parar el pipeline del agente.
- La regla existe una sola vez. Antes había que portarla a mano a la copia de
  cada broker, y bastaba olvidar una para que ese agente auditase con un
  criterio distinto sin que nada lo dijese.

Límite deliberado: se puede reanalizar con otras tolerancias, **no** con otro
periodo ni otros lotes de tester. Eso cambia lo que el Strategy Tester ejecutó,
así que exige repetir la auditoría. `analyze` toma de la configuración actual
solo lo que es criterio, y de la ejecución todo lo que es hecho observado.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .live_audit_attribution import (
    annotate_unmatched, attribution_by_ticket, owned_position_ids,
)
from .live_audit_autolot import dynamic_lot_bands, prepare_request, rules_by_strategy
from .live_audit_comment_links import (
    attribution_from_comments, links_by_comment, merged_attribution,
)
from .live_audit_engine import (
    LiveAuditController, _lot_bands, _member_set_name, _member_strategy_id,
    _trade_view, _within_lot_bands,
)
from .live_audit_lot_schedule import normalize_lot_schedule, scheduled_lots
from .live_audit_manual_pairs import (
    normalize_manual_pairs, real_pair_id, rescued_position_ids,
)

# Lo que el usuario puede cambiar y volver a aplicar sobre una ejecución guardada.
REANALYSABLE_TOLERANCES = (
    "trade_time_tolerance_seconds",
    "price_tolerance_points",
    "volume_tolerance_pct",
    "pnl_deviation_warning_pct",
    "drawdown_deviation_warning_pct",
    "min_tick_history_quality_pct",
    "real_strategy_lots",
    # El lote que el usuario subió a mano a mitad del periodo, con la fecha
    # desde la que rige. No sustituye a `real_strategy_lots`: lo continúa, para
    # que las operaciones anteriores al cambio sigan midiéndose con el suyo.
    "real_lot_schedule",
    "autolot",
    "real_attribution",
    # Las parejas que decidió el usuario. Es criterio, no hecho observado: se
    # aplica sobre la materia prima guardada sin repetir la auditoría, igual
    # que una tolerancia nueva o la atribución.
    "manual_pairs",
    # La tabla `comentario del EA → .set`. También es criterio: el comentario
    # lo trae la materia prima y esto sólo dice cómo leerlo.
    "comment_links",
)
# Lo que describe la ejecución y no se puede reinterpretar sin repetirla.
EXECUTION_FACTS = (
    "audit_key", "portfolio_id", "portfolio_type",
    "period_mode", "period_days", "period_start_date", "period_end_date",
)


class PayloadError(ValueError):
    """La materia prima del nodo no permite analizar la ejecución."""


def _node_state(value: Any) -> dict[str, Any] | None:
    """El estado del nodo, venga envuelto en `audit` o plano."""
    if not isinstance(value, dict):
        return None
    nested = value.get("audit")
    return nested if isinstance(nested, dict) else value


def has_raw_material(value: Any) -> bool:
    """¿Publicó este nodo materia prima analizable?

    Un agente al que todavía no se le ha portado el reparto nuevo devuelve su
    propio `last_result` y ningún `last_payload`. Distinguirlo aquí evita que el
    manager pida credenciales y perfiles para nada.
    """
    state = _node_state(value)
    payload = state.get("last_payload") if state else None
    return isinstance(payload, dict) and bool(payload.get("audit_id"))


def analyse_node_state(value: Any, profile: dict[str, Any]) -> Any:
    """Sustituye la materia prima del estado del nodo por el veredicto.

    La materia prima no viaja al navegador: son miles de operaciones y el único
    consumidor es este análisis.
    """
    state = _node_state(value)
    if state is None:
        return value
    payload = state.get("last_payload")
    if not isinstance(payload, dict) or not payload.get("audit_id"):
        return value
    try:
        state["last_result"] = analyze(payload, profile)
    except (PayloadError, KeyError, TypeError, ValueError) as exc:
        # Nunca inventar un veredicto a partir de materia prima que no se puede
        # interpretar: se dice que el análisis falló y por qué.
        state["last_result"] = None
        state["analysis_error"] = str(exc)
    state.pop("last_payload", None)
    return value


def _revive(trade: dict[str, Any] | None) -> dict[str, Any] | None:
    """Devuelve las marcas de tiempo a `datetime`; el transporte es JSON."""
    if not trade:
        return None
    revived = dict(trade)
    for key in ("open_time", "close_time"):
        value = revived.get(key)
        if isinstance(value, str) and value:
            revived[key] = datetime.fromisoformat(value)
    return revived


def _revive_all(trades: Any) -> list[dict[str, Any]]:
    return [revived for revived in (_revive(trade) for trade in (trades or [])) if revived]


def portfolio_lot_bands(
    members: list[dict[str, Any]],
    volume_rules: dict[str, tuple[float, float]],
    symbols_by_strategy: dict[str, set[str]],
    real_strategy_lots: dict[str, float],
    real_lot_schedule: dict[str, Any] | None = None,
) -> dict[str, tuple[float, float]]:
    """Banda `símbolo → (lote mínimo, lote máximo)` de los cierres del portafolio.

    El magic del terminal real puede no coincidir con el del `.set` importado,
    así que la pertenencia se decide por símbolo y lote. Exigir el lote exacto
    configurado descartaba cierres que sí eran de la estrategia —el auditado
    del 2026-10-04 perdió así un USDJPY a 0,03 y un XAGUSD a 0,06—, de modo que
    se admite cualquier lote entre el que probó el tester y el configurado.
    """
    entries: list[tuple[set[str], float, float]] = []
    for member in members:
        strategy = _member_strategy_id(member)
        try:
            _configured, effective_lot, _minimum, _step, _units = LiveAuditController._tester_lot(
                member, volume_rules,
            )
        except (TypeError, ValueError):
            continue
        try:
            real_lot = float(real_strategy_lots.get(strategy, effective_lot))
        except (TypeError, ValueError):
            real_lot = float(effective_lot)
        symbols = symbols_by_strategy.get(strategy) or {
            str(member.get("symbol") or "").casefold()
        }
        entries.append((symbols, float(effective_lot), real_lot))
        # La banda es un prefiltro del periodo entero y no sabe de qué instante
        # es cada cierre, así que admite **todos** los lotes que la estrategia
        # llegó a operar. Quién se desvía lo decide después la comparación, que
        # sí mira la apertura y usa el tramo vigente en ella.
        entries.extend(
            (symbols, float(effective_lot), lot)
            for lot in scheduled_lots(real_lot_schedule, strategy)
        )
    return _lot_bands(entries)


def strategy_set_names(members: list[dict[str, Any]]) -> dict[str, str]:
    """Fichero `.set` de cada estrategia, para nombrarla en cada operación."""
    names: dict[str, str] = {}
    for member in members:
        strategy = _member_strategy_id(member)
        name = _member_set_name(member)
        if strategy and name:
            names[strategy] = name
    return names


def _symbols_by_strategy(
    tester_trades: list[dict[str, Any]], strategy_artifacts: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """El símbolo efectivo del broker sale del reporte, no del portafolio.

    El portafolio guarda `NAS100` y el broker ejecuta `NAS100.fs`; sin esta
    traducción el filtro de pertenencia no encontraría ningún cierre.
    """
    symbols: dict[str, set[str]] = {}
    for trade in tester_trades:
        strategy = str(trade.get("strategy") or "")
        symbol = str(trade.get("symbol") or "").casefold()
        if strategy and symbol:
            symbols.setdefault(strategy, set()).add(symbol)
    for artifact in strategy_artifacts or []:
        strategy = str(artifact.get("strategy") or "")
        symbol = str(artifact.get("report_symbol") or "").casefold()
        if strategy and symbol:
            symbols.setdefault(strategy, set()).add(symbol)
    return symbols


def _symbol_aliases(
    members: list[dict[str, Any]], tester_trades: list[dict[str, Any]],
    strategy_artifacts: list[dict[str, Any]],
) -> dict[str, str]:
    """Traduce el símbolo del portfolio al que observó el tester.

    Las cuentas real y tester pueden pertenecer a grupos de servidor distintos
    del mismo broker: AXI-US50 informa `BTCUSD`, mientras AXI-US51 ejecuta
    `BTCUSD.sa`. El miembro guardado une ambos nombres sin adivinar sufijos.
    """
    effective: dict[str, dict[str, str]] = {}
    for trade in tester_trades:
        strategy = str(trade.get("strategy") or "")
        symbol = str(trade.get("symbol") or "")
        if strategy and symbol:
            effective.setdefault(strategy, {}).setdefault(symbol.casefold(), symbol)
    for artifact in strategy_artifacts or []:
        strategy = str(artifact.get("strategy") or "")
        symbol = str(artifact.get("report_symbol") or "")
        if strategy and symbol:
            effective.setdefault(strategy, {}).setdefault(symbol.casefold(), symbol)
    candidates: dict[str, set[str]] = {}
    spelling: dict[str, str] = {}
    for member in members:
        strategy = _member_strategy_id(member)
        targets = effective.get(strategy) or {}
        if len(targets) != 1:
            continue
        target_key, target = next(iter(targets.items()))
        source = str(member.get("symbol") or "").casefold()
        for alias in (source, target_key):
            if alias:
                candidates.setdefault(alias, set()).add(target_key)
                spelling[target_key] = target
    return {
        alias: spelling[next(iter(targets))]
        for alias, targets in candidates.items() if len(targets) == 1
    }


def _apply_symbol_aliases(
    trades: list[dict[str, Any]], aliases: dict[str, str],
) -> list[dict[str, Any]]:
    """Canoniza la copia de análisis; la materia prima observada queda intacta."""
    for trade in trades:
        reported = str(trade.get("symbol") or "")
        canonical = aliases.get(reported.casefold())
        if canonical and canonical.casefold() != reported.casefold():
            trade["symbol"] = canonical
    return trades


def _volume_rules(payload: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Normaliza las especificaciones del broker que publicó el nodo."""
    rules: dict[str, tuple[float, float]] = {}
    for symbol, raw in (payload.get("volume_rules") or {}).items():
        try:
            if isinstance(raw, dict):
                minimum = float(raw.get("volume_min") or 0.0)
                step = float(raw.get("volume_step") or minimum or 0.0)
            else:
                minimum, step = (float(value) for value in raw)
        except (TypeError, ValueError):
            continue
        if minimum > 0:
            rules[str(symbol).casefold()] = (minimum, step if step > 0 else minimum)
    return rules


def _analysis_request(payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload.get("audit_id"):
        raise PayloadError("La ejecución no publicó materia prima analizable")
    executed = dict(payload.get("request") or {})
    request: dict[str, Any] = {
        **executed,
        **{key: profile[key] for key in REANALYSABLE_TOLERANCES if key in profile},
    }
    for key in EXECUTION_FACTS:
        if key in executed:
            request[key] = executed[key]
    request["strategy_set_names"] = strategy_set_names(
        list(payload.get("selected_members") or []),
    )
    request["_autolot_rules_by_strategy"] = rules_by_strategy(
        request.get("autolot"), list(payload.get("selected_members") or []),
    )
    request["_autolot_balance_history"] = profile.get("_autolot_balance_history") or []
    request["_autolot_balance_error"] = profile.get("_autolot_balance_error") or ""
    request["_comment_links_supported"] = bool(profile.get("_comment_links_supported"))
    request["_real_attribution_by_ticket"] = attribution_by_ticket(
        request.get("real_attribution"), list(payload.get("selected_members") or []),
    )
    request["_comment_links_by_comment"] = links_by_comment(
        request.get("comment_links"), list(payload.get("selected_members") or []),
    )
    request["manual_pairs"] = normalize_manual_pairs(request.get("manual_pairs"))
    request["real_lot_schedule"] = normalize_lot_schedule(request.get("real_lot_schedule"))
    return request


def tester_trades_in_window(
    trades: list[dict[str, Any]], period_start: datetime, period_end: datetime,
) -> list[dict[str, Any]]:
    """Las operaciones del tester que pertenecen a la ventana auditada.

    El lado real son los cierres de la ventana más las posiciones abiertas al
    final; el lado tester tiene que ser lo mismo, o no se comparan las mismas
    operaciones. Con el reporte que ejecuta el propio nodo da igual -MT5 corre
    exactamente la ventana: en las 444 operaciones de las once ejecuciones
    guardadas en los tres agentes no había ni una fuera-, pero es lo que
    permite aprovechar un backtest **más
    ancho** que la ventana: el usuario no tiene que rehacerlo cada noche, basta
    con que cubra las fechas, porque al final se aterriza en operaciones.
    """
    kept = []
    for trade in trades:
        opened, closed = trade.get("open_time"), trade.get("close_time")
        if not isinstance(opened, datetime) or not isinstance(closed, datetime):
            continue
        closed_inside = period_start <= closed <= period_end
        open_at_end = opened <= period_end < closed
        if closed_inside or open_at_end:
            kept.append(trade)
    return kept


def _counted_by_strategy(
    counted: Counter[str], declared: dict[str, Any],
) -> dict[str, int]:
    """Las cuentas del payload, recontadas sobre lo que queda en la ventana."""
    return {
        **{str(strategy): int(counted.get(str(strategy), 0)) for strategy in declared},
        **{strategy: count for strategy, count in counted.items() if strategy not in declared},
    }


def _analysis_inputs(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    request["_autolot_volume_rules"] = _volume_rules(payload)
    period_start = datetime.fromisoformat(str(payload["period_start"]))
    period_end = datetime.fromisoformat(str(payload["period_end"]))
    # La ventana es criterio para `attributed_assignment`: una real que abrió
    # antes del periodo cerró dentro, pero su tester está en el backtest previo.
    request["_period_start"] = period_start
    tester_trades = tester_trades_in_window(
        _revive_all(payload.get("tester_trades")), period_start, period_end,
    )
    counted = Counter(str(trade.get("strategy") or "") for trade in tester_trades)
    artifacts = [
        {**artifact, "tester_trades": int(counted.get(str(artifact.get("strategy") or ""), 0))}
        if isinstance(artifact, dict) and artifact.get("strategy") else artifact
        for artifact in payload.get("strategy_artifacts") or []
    ]
    aliases = _symbol_aliases(
        list(payload.get("selected_members") or []), tester_trades, artifacts,
    )
    real_trades = _apply_symbol_aliases(_revive_all(payload.get("real_trades")), aliases)
    # El comentario del EA resuelve a `ticket → estrategia`, igual que el Excel,
    # y entra por la misma puerta. Manda sobre él —lo escribió el programa que
    # operó— salvo sobre una exclusión, que es una afirmación sobre pertenencia
    # al portafolio y el comentario sólo nombra el `.set`.
    request["_real_attribution_by_ticket"] = merged_attribution(
        request.get("_real_attribution_by_ticket") or {},
        attribution_from_comments(
            real_trades, request.get("_comment_links_by_comment") or {},
        ),
    )
    open_positions = _apply_symbol_aliases(
        _revive_all(payload.get("open_positions_at_period_end")), aliases,
    )
    request["real_positions_open_at_period_end"] = open_positions
    history_detail = dict(payload.get("real_history_detail") or {})
    history_detail["open_positions_at_period_end"] = [
        _trade_view(position) for position in open_positions
    ]
    return {
        "period_start": period_start,
        "period_end": period_end,
        "real_trades": real_trades,
        "tester_trades": tester_trades,
        "symbol_points": {
            str(symbol): float(point or 0.0)
            for symbol, point in (payload.get("symbol_points") or {}).items()
        },
        "strategies": _counted_by_strategy(counted, payload.get("strategies") or {}),
        "strategy_artifacts": artifacts,
        "symbol_aliases": aliases,
        "history_detail": history_detail,
    }


def _lot_filter_exemptions(request: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Las reales que la banda de lote ya no puede apartar, y por qué.

    La banda es una **conjetura** de pertenencia: «este lote no lo opera el
    portafolio». Las dos exenciones son respuestas del usuario a esa misma
    pregunta, así que la conjetura deja de decidir.

    - La que emparejó a mano señala esa real una por una.
    - La que atribuyó en el Excel —o que el comentario del EA reclama— dice de
      qué estrategia del uso es. En `audit-170` de RoboForex las dos `EURUSD`
      de 0,06 estaban atribuidas a `ROBOFOREX/ECN:32807`, cuya banda es
      0,08–0,12, así que el filtro las apartaba antes de comparar y sus dos
      filas del tester seguían «SIN REAL» con la respuesta ya escrita: la
      portada pedía como trabajo pendiente algo que el usuario ya había hecho.

    Se devuelven aparte porque el cuadre cuenta cada procedencia por su lado, y
    sin la resta una real emparejada a mano **y** atribuida se contaría dos
    veces.
    """
    paired = rescued_position_ids(request.get("manual_pairs"))
    owned = owned_position_ids(request.get("_real_attribution_by_ticket") or {})
    return paired, owned - paired


def _filter_portfolio_trades(
    payload: dict[str, Any], request: dict[str, Any], inputs: dict[str, Any],
) -> dict[str, Any]:
    real_trades = inputs["real_trades"]
    bands = portfolio_lot_bands(
        list(payload.get("selected_members") or []),
        _volume_rules(payload),
        _symbols_by_strategy(inputs["tester_trades"], inputs["strategy_artifacts"]),
        request.get("real_strategy_lots") or {},
        request.get("real_lot_schedule") or {},
    )
    for symbol, dynamic in dynamic_lot_bands(inputs["tester_trades"], request).items():
        previous = bands.get(symbol)
        bands[symbol] = (
            min(previous[0], dynamic[0]), max(previous[1], dynamic[1])
        ) if previous else dynamic
    filter_detail: dict[str, Any] = {
        "applied": bool(bands), "symbol_aliases": dict(inputs["symbol_aliases"]),
    }
    discarded: list[dict[str, Any]] = []
    paired, owned = _lot_filter_exemptions(request)
    rescued = paired | owned
    if bands:
        before = len(real_trades)
        kept: list[dict[str, Any]] = []
        for trade in real_trades:
            belongs = _within_lot_bands(trade, bands) or real_pair_id(trade) in rescued
            (kept if belongs else discarded).append(trade)
        real_trades = kept
        inputs["history_detail"]["portfolio_closures"] = len(real_trades)
        inputs["history_detail"]["foreign_closures_ignored"] = before - len(real_trades)
        filter_detail.update(
            lot_bands={symbol: list(band) for symbol, band in sorted(bands.items())},
            closures_before=before,
            portfolio_closures=len(real_trades),
            foreign_closures_ignored=before - len(real_trades),
            manually_paired_kept=sum(
                1 for trade in real_trades
                if not _within_lot_bands(trade, bands) and real_pair_id(trade) in paired
            ),
            attributed_kept=sum(
                1 for trade in real_trades
                if not _within_lot_bands(trade, bands) and real_pair_id(trade) in owned
            ),
        )
    inputs["real_trades"] = real_trades
    inputs["discarded_real_trades"] = discarded
    inputs["filter_detail"] = filter_detail
    return inputs


def _attach_discarded_reals(
    result: dict[str, Any], request: dict[str, Any], inputs: dict[str, Any],
) -> None:
    """Dice las reales que el filtro de lote dejó fuera, en vez de perderlas.

    El filtro es una conjetura —«este lote no lo opera el portafolio»— y hasta
    ahora se las tragaba en silencio: no estaban emparejadas, ni sin pareja, ni
    en ninguna tabla. Ahora salen en el cuadre como pendientes, que es donde el
    Excel de atribución puede decir de quién son o que son ajenas.
    """
    detail = result.get("comparison_detail")
    if not isinstance(detail, dict):
        return
    bands = (inputs.get("filter_detail") or {}).get("lot_bands") or {}
    rows = [{
        "status": "discarded", "real": _trade_view(trade),
        "reason": "lot_outside_portfolio_bands",
        "portfolio_lot_band": bands.get(str(trade.get("symbol") or "").casefold()),
    } for trade in inputs.get("discarded_real_trades") or []]
    annotate_unmatched(rows, request.get("_real_attribution_by_ticket") or {})
    detail["discarded_real_operations"] = rows


def _analysis_result(
    payload: dict[str, Any], request: dict[str, Any], inputs: dict[str, Any],
) -> dict[str, Any]:
    qualities = [float(value) for value in (payload.get("qualities") or []) if value is not None]
    quality = min(qualities) if qualities else None
    minimum_quality = float(request["min_tick_history_quality_pct"])
    result = LiveAuditController._result_base(
        request, inputs["period_start"], inputs["period_end"],
        inputs["real_trades"], inputs["tester_trades"], quality,
    )
    comparison = LiveAuditController._compare(
        inputs["real_trades"], inputs["tester_trades"], inputs["symbol_points"],
        request, inputs["strategies"],
    )
    result.update(comparison)
    # La calidad ya no es una puerta: marca el set que no la acredita y la
    # auditoría sigue, porque el historial malo es de una terminal concreta.
    below = LiveAuditController._mark_quality(
        result, inputs["strategy_artifacts"], minimum_quality,
    )
    _attach_discarded_reals(result, request, inputs)
    invalid_tester = sum(
        (comparison.get("comparison_detail") or {}).get("tester_data_issues", {}).values()
    )
    result["summary"] = (
        f"{comparison['matched_trades']} parejas alineadas, "
        f"{comparison['within_tolerance_trades']} dentro de todas las tolerancias y "
        f"{comparison.get('open_real_trades', 0)} posición(es) real(es) aún abierta(s); "
        f"{comparison['discrepancies']} discrepancias; "
        f"{comparison['stalled_strategies']} estrategia(s) sin continuidad"
        + (f"; {invalid_tester} operación(es) tester con tiempos inválidos." if invalid_tester else ".")
        + LiveAuditController._quality_note(below, minimum_quality)
    )
    result["status"] = "completed"
    result["status_label"] = "COMPLETADA"
    return result


def _attach_evidence(
    result: dict[str, Any], payload: dict[str, Any], request: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    result["audit_id"] = payload["audit_id"]
    result["account"] = dict(payload.get("account") or {})
    result["real_history_detail"] = inputs["history_detail"]
    result["portfolio_filter"] = inputs["filter_detail"]
    result["strategy_artifacts"] = inputs["strategy_artifacts"]
    result["tester_execution"] = dict(payload.get("tester_execution") or {})
    result["real_account_report"] = dict(payload.get("real_account_report") or {})
    result["terminal_restore"] = list(payload.get("terminal_restore") or [])
    # Quien lee el resultado tiene que poder distinguir lo observado de lo
    # decidido ahora: si no, un reanálisis con otras tolerancias parece una
    # ejecución nueva.
    result["analysis"] = {
        "analysed_by": "manager",
        "executed_at": payload.get("completed_at"),
        "tolerances": {key: request.get(key) for key in REANALYSABLE_TOLERANCES},
        # Si este uso puede enlazar por el comentario del EA. Lo decide el
        # broker del nodo y la pantalla no lo conoce: sin esto tendría que
        # preguntar por el nodo aparte sólo para saber si enseña un botón.
        "comment_links_supported": bool(request.get("_comment_links_supported")),
    }
    return result


def analyze(payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Veredicto reanalizable a partir de hechos del nodo y criterio actual."""
    request = _analysis_request(payload, profile)
    prepare_request(request)
    inputs = _filter_portfolio_trades(
        payload, request, _analysis_inputs(payload, request),
    )
    result = _analysis_result(payload, request, inputs)
    return _attach_evidence(result, payload, request, inputs)
