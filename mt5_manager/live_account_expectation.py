"""Lo que el backtest promete para **ese** mes, no para un mes cualquiera.

La portada de cuentas compara el mes en curso con «la media de ese mes en el
histórico»: para octubre, la media de todos los octubres del backtest. Es una
media estacional a propósito; promediar los setenta y dos meses juntos
escondería que abril y octubre no se parecen.

De dónde sale, medido sobre datos reales el 21 de octubre de 2026 con el
portafolio #105 de AXI:

- La curva 2020-2026 de cada estrategia es la **concatenación de dos
  informes**, el IS (2020-2024) y el OOS (2025-2026): `reports._curve_fields`
  los suma. Leer sólo el IS dejaba la serie en 2024 y el total en 32.006 € de
  los 47.838 € que el portafolio declara.
- Con los dos informes el total sube a 46.731 €, y el 2,3 % que falta tiene
  explicación: la contribución oficial de cada miembro usa el «Total Net
  Profit» que imprime el informe, mientras que lo único con fecha son las
  operaciones emparejadas, y algún cierre no se empareja. Nueve de los trece
  miembros cuadraban al céntimo y los otros cuatro se quedaban cortos.

Por eso cada miembro se **reconcilia**: su serie se escala para sumar lo que el
portafolio declara de él. Sin eso la media mensual llevaría un sesgo a la baja
del tamaño del resto sin emparejar, y la tarjeta compararía la cuenta real con
una promesa que no es la del portafolio que el usuario ve.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

from .live_account_series import drawdown, month_key, monthly_rows

REPORT_FIELDS = ("is_report_path", "oos_report_path")
"""Los dos informes que forman la curva 2020-2026, en ese orden."""

CRITERION_VERSION = "autolot-portfolio-valley-v6"
"""Con qué criterio se midió esta media. Cambia cuando cambia lo que se mide.

Sin esto, cambiar el criterio no repinta nada. La media guardada se recalcula
**por edad** —`EXPECTATION_MAX_AGE_HOURS`— y la firma, que sí detectaría el
cambio, ni siquiera se consulta hasta entonces: calcularla exige abrir la
memoria del agente, que es justo lo que la edad evita. Medido el 2026-10-23: el
#67 se arregló a las 15:00 y su media era de 18 horas antes, así que la
pantalla siguió enseñando los 86,27 € viejos y habría seguido hasta la
medianoche, con sondeos de por medio que no cambiaban nada.

Es el mismo gesto que `live_account_report.NORMALIZATION_VERSION` hace con la
serie real: un criterio nuevo fuerza una medición, una sola vez, sin esperar al
reloj. Se sube al cambiar **qué** se mide; no al corregir un comentario.
"""

RECONCILE_TOLERANCE = 0.25
"""Hasta dónde se corrige un miembro. Más que esto no es «algún cierre sin
emparejar»: es otro informe, y entonces no se escala nada y se dice."""


def audited_members(
    portfolio: dict[str, Any], portfolio_type: str = "",
) -> list[dict[str, Any]]:
    """Las filas de la variante que se audita, no las de sus hermanas.

    Un bundle guarda A/M/C en la **misma** fila de la memoria UBS: el #67 de
    AXI son cuatro estrategias en doce filas. La auditoría mide una sola
    variante —sus `selected_members` son cuatro— y la promesa tiene que medir
    esa misma, o promete el triple. Medido el 2026-10-23: la media de octubre
    salía 86,27 € donde la variante agresiva promete 28,76 €.

    Es el mismo gesto que `live_audit_lineage.member_strategies`, y por la
    misma razón. Sin `variant_key` no hay variantes que separar: una mejora
    guarda la fila entera, y entonces se miden todas.
    """
    members = [
        member for member in portfolio.get("members") or []
        if isinstance(member, dict) and not member.get("excluded")
    ]
    variants = {str(member.get("variant_key") or "") for member in members}
    if not portfolio_type or variants <= {""}:
        return members
    return [
        member for member in members
        if str(member.get("variant_key") or "") == portfolio_type
    ]


def manual_report_paths(portfolio: dict[str, Any]) -> dict[str, str]:
    """Los informes de una cartera manual, que no están en la fila del miembro.

    Una cartera manual no tiene candidatos de la memoria UBS: sus `.set` los
    subió el usuario y su histórico vive en `history.strategies`, indexado por
    el nombre del fichero.
    """
    history = portfolio.get("history") if isinstance(portfolio, dict) else None
    rows = (history or {}).get("strategies") or [] if isinstance(history, dict) else []
    found: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("report_path") or "").strip()
        for key in ("set_id", "set_name"):
            label = str(row.get(key) or "").strip()
            if label and path:
                found.setdefault(label, path)
    return found


def member_paths(member: dict[str, Any], manual: dict[str, str]) -> list[str]:
    """Los informes de ese miembro: IS y OOS, o el de la cartera manual."""
    found = [
        str(member.get(field) or "").strip() for field in REPORT_FIELDS
        if str(member.get(field) or "").strip()
    ]
    if found:
        return found
    for key in ("set_id", "set_name"):
        label = str(member.get(key) or "").strip()
        if label in manual:
            return [manual[label]]
    return []


def member_lot(member: dict[str, Any], real_lots: dict[str, Any]) -> tuple[float, str]:
    """El lote con el que se mide, y de dónde sale.

    Manda el lote que el manager sabe que la cuenta ejecuta de verdad
    (`real_strategy_lots` del perfil del uso): comparar la cuenta real con una
    promesa medida a otros lotes mide el volumen, no el comportamiento. Sin ese
    dato manda el lote guardado del portafolio.
    """
    candidate = str(member.get("candidate_id") or "").strip()
    try:
        declared = float(real_lots.get(candidate))
    except (TypeError, ValueError):
        declared = 0.0
    if declared > 0:
        return declared, "real"
    try:
        return max(float(member.get("lot") or 0.0), 0.0), "saved"
    except (TypeError, ValueError):
        return 0.0, "saved"


def scaled_trades(
    report: Any, lot: float,
) -> list[tuple[datetime, float]]:
    """Cada operación del informe llevada a ese lote, con su cierre.

    El mismo gesto que `live_audit_degradation_evidence.trade_series`: se
    normaliza por el volumen de cada operación y se multiplica por el lote.
    """
    found: list[tuple[datetime, float]] = []
    for trade in getattr(report, "trades", []) or []:
        size = float(getattr(trade, "size", 0.0) or 0.0)
        close = getattr(trade, "close_time", None)
        if size <= 0 or not isinstance(close, datetime):
            continue
        found.append((close, float(trade.profit_loss) / size * lot))
    return found


def reconcile(
    values: Sequence[tuple[datetime, float]], declared: float,
) -> tuple[list[tuple[datetime, float]], float]:
    """Escala la serie del miembro para sumar lo que el portafolio declara.

    Devuelve `(serie, factor)`. Factor 1.0 significa que no se tocó: sin
    contribución declarada —una cartera manual no la tiene—, con suma medida
    cero, o con una diferencia tan grande que ya no es resto sin emparejar.
    """
    measured = sum(amount for _moment, amount in values)
    if not declared or not measured or declared * measured <= 0:
        return list(values), 1.0
    factor = declared / measured
    if abs(factor - 1.0) > RECONCILE_TOLERANCE:
        return list(values), 1.0
    return [(moment, amount * factor) for moment, amount in values], factor


@dataclass(frozen=True)
class MemberCurve:
    """La serie de un miembro y **a qué lote está medida**.

    Guardar el lote al lado de la serie es lo que permite llevarla después a
    otro tamaño sin volver a abrir el informe: la curva es estrictamente
    lineal en el lote —`scaled_trades` divide por el volumen de cada operación
    y multiplica por él—, así que un factor basta.
    """

    candidate: str
    lot: float
    values: tuple[tuple[datetime, float], ...]


def seasonal_at(
    curves: Sequence[MemberCurve], lots: dict[str, float] | None = None,
) -> dict[str, Any]:
    """La media estacional de esas curvas, llevadas a los lotes que se digan.

    Un miembro que no aparezca en `lots` se queda con el suyo, y el factor es
    exactamente `1.0`: la media sin escenario tiene que salir idéntica, bit a
    bit, a la de antes de que esto existiera.

    El escalado **no** puede aplicarse al número final: con factores distintos
    por miembro la curva combinada cambia de forma, y el drawdown de una suma
    no es la suma de los drawdowns. Por eso se rehace la combinación.
    """
    chosen = dict(lots or {})
    combined: list[tuple[datetime, float]] = []
    for curve in curves:
        target = chosen.get(curve.candidate)
        factor = 1.0 if not target or curve.lot <= 0 else float(target) / curve.lot
        combined.extend(
            (moment, amount * factor) for moment, amount in curve.values
        )
    return _seasonal(combined)


def portfolio_valleys(
    portfolio: dict[str, Any], portfolio_type: str,
) -> dict[str, float | None]:
    """El máximo fijado al crear y el valle histórico de la variante servida."""
    metrics = portfolio.get("metrics") or {}
    variants = metrics.get("variants") or {}
    variant = variants.get(portfolio_type) or {}
    summary = variant.get("summary") or {}
    limit = float(summary.get("target_valley_dd") or portfolio.get("target_valley_dd") or 0)
    if limit <= 0:
        capital = float(portfolio.get("capital") or 0)
        limit = capital * float(portfolio.get("target_valley_dd_pct") or 0) / 100.0
    historical = float(
        summary.get("actual_valley_dd") or portfolio.get("actual_valley_dd") or 0
    )
    return {
        "portfolio_valley_limit": limit if limit > 0 else None,
        "portfolio_historical_valley": historical if historical > 0 else None,
    }


def curve_drawdown(
    curves: Sequence[MemberCurve], lots: dict[str, float] | None = None,
) -> float:
    """El valle de toda la curva a los lotes base o al escenario de autolote."""
    chosen = dict(lots or {})
    combined: list[tuple[datetime, float]] = []
    for curve in curves:
        target = chosen.get(curve.candidate)
        factor = 1.0 if not target or curve.lot <= 0 else float(target) / curve.lot
        combined.extend((moment, amount * factor) for moment, amount in curve.values)
    combined.sort(key=lambda item: item[0])
    return drawdown([amount for _moment, amount in combined])


def _seasonal(values: Sequence[tuple[datetime, float]]) -> dict[str, Any]:
    """De la serie combinada a la media de cada mes del año."""
    rows = monthly_rows(sorted(values, key=lambda item: item[0]))
    grouped: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for label, row in sorted(rows.items()):
        grouped.setdefault(int(label.split("-", 1)[1]), []).append((label, row))
    months: dict[str, Any] = {}
    for number, entries in grouped.items():
        months[f"{number:02d}"] = {
            "profit": sum(row["profit"] for _label, row in entries) / len(entries),
            "drawdown": sum(row["drawdown"] for _label, row in entries) / len(entries),
            "years": [int(label.split("-", 1)[0]) for label, _row in entries],
            "months": {label: dict(row) for label, row in entries},
        }
    return months


def signature(
    portfolio: dict[str, Any], real_lots: dict[str, Any], portfolio_type: str = "",
    scenarios: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Huella de lo que haría cambiar la medición, para no repetirla.

    Entran los miembros de la variante auditada con su lote y sus informes, la
    contribución declarada y los lotes reales: cambiar cualquiera de esas cosas
    cambia la media, y no cambiar ninguna la deja idéntica. La variante entra
    porque decide **qué filas** se miden, que es lo primero que cambia la media.

    Los escenarios entran igual, y por eso la media de una cuenta con autolote
    se vuelve a medir cuando el saldo mueve el lote: sin ellos la firma diría
    que nada ha cambiado mientras la cuenta opera a otro tamaño.
    """
    members = [
        [
            str(member.get("candidate_id") or ""), member.get("lot"),
            member.get("net_profit_contribution"), bool(member.get("excluded")),
            *[member.get(field) for field in REPORT_FIELDS],
        ]
        for member in audited_members(portfolio, portfolio_type)
    ]
    payload = json.dumps(
        [
            portfolio.get("id"), portfolio_type, members,
            sorted((real_lots or {}).items()),
            sorted(
                (label, sorted((scenario.get("lots") or {}).items()))
                for label, scenario in (scenarios or {}).items()
            ),
        ],
        sort_keys=True, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def build(
    portfolio: dict[str, Any], *, read: Callable[[str], Any],
    resolve: Callable[[str], str] | None = None,
    real_lots: dict[str, Any] | None = None,
    portfolio_type: str = "",
    scenarios: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """La media estacional del portafolio, o el motivo de que no haya.

    `read` parsea un informe y `resolve` traduce la ruta que guardó el nodo;
    se inyectan para que esto no dependa de la memoria del agente ni de la
    caché, y para poder medirlo sin ficheros. `portfolio_type` es la variante
    auditada: sin ella un bundle promete tantas veces como variantes guarda.

    `scenarios` son medidas **del mismo backtest a otros lotes**, una por mes
    objetivo: `{"2026-10": {"lots": {...}, …}}`. Salen aparte y no sustituyen
    a `months` porque sólo valen para su mes —el lote de una cuenta con
    autolote es el que tuvo **ese** mes— y porque un mes sin escenario tiene
    que poder seguir leyendo la media de siempre.
    """
    real_lots = dict(real_lots or {})
    manual = manual_report_paths(portfolio)
    warnings: list[str] = []
    sources: set[str] = set()
    members = audited_members(portfolio, portfolio_type)
    curves: list[MemberCurve] = []
    for member in members:
        curve = _member_curve(
            member, real_lots, manual, read, resolve, warnings, sources,
        )
        if curve is not None:
            curves.append(curve)
    combined = [
        (moment, amount) for curve in curves for moment, amount in curve.values
    ]
    return {
        "months": seasonal_at(curves),
        "months_at": _scenarios(curves, scenarios),
        **portfolio_valleys(portfolio, portfolio_type),
        "members_read": len(curves), "members_total": len(members),
        "measured_profit": sum(amount for _moment, amount in combined),
        "declared_profit": sum(
            _declared(member.get("net_profit_contribution")) for member in members
        ),
        "lots": "mixed" if len(sources) > 1 else (sources.pop() if sources else ""),
        "warnings": warnings,
        "signature": signature(portfolio, real_lots, portfolio_type, scenarios),
        "criterion": CRITERION_VERSION,
    }


def _scenarios(
    curves: Sequence[MemberCurve], scenarios: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """La media de cada mes objetivo, medida a los lotes de ese mes.

    Se guarda **sólo la fila de su mes**: el escenario de octubre no dice nada
    sobre mayo, y guardar la tabla estacional entera doce veces engordaría el
    fichero con once filas que nadie puede leer.
    """
    measured: dict[str, Any] = {}
    for label, scenario in (scenarios or {}).items():
        lots = {
            key: float(value) for key, value in (scenario.get("lots") or {}).items()
        }
        if not lots:
            continue
        row = seasonal_at(curves, lots).get(label.split("-", 1)[-1]) or {}
        if not row.get("years"):
            continue
        measured[label] = {
            "profit": float(row["profit"]), "drawdown": float(row["drawdown"]),
            "portfolio_historical_valley": curve_drawdown(curves, lots),
            "years": list(row["years"]), "lots": lots,
            **{
                key: scenario[key] for key in ("basis", "balance", "without_rule")
                if key in scenario
            },
        }
    return measured


def _declared(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _member_curve(
    member: dict[str, Any], real_lots: dict[str, Any], manual: dict[str, str],
    read: Callable[[str], Any], resolve: Callable[[str], str] | None,
    warnings: list[str], sources: set[str],
) -> MemberCurve | None:
    """La curva de un miembro ya reconciliada, o `None` si no se pudo medir.

    La reconciliación se decide **al lote con el que se midió**, que es el que
    `net_profit_contribution` describe; llevarla después a otro tamaño no la
    cambia, porque es una corrección adimensional.
    """
    lot, origin = member_lot(member, real_lots)
    paths = member_paths(member, manual)
    label = str(member.get("set_name") or member.get("candidate_id") or "?")
    if lot <= 0 or not paths:
        warnings.append(f"{label}: sin informe del backtest o sin lote.")
        return None
    values = _member_values(paths, lot, read, resolve, label, warnings)
    if values is None:
        return None
    sources.add(origin)
    values, _factor = reconcile(
        values, _declared(member.get("net_profit_contribution")),
    )
    return MemberCurve(
        candidate=str(member.get("candidate_id") or ""), lot=lot,
        values=tuple(values),
    )


def _member_values(
    paths: Iterable[str], lot: float, read: Callable[[str], Any],
    resolve: Callable[[str], str] | None, label: str, warnings: list[str],
) -> list[tuple[datetime, float]] | None:
    """Las operaciones de un miembro, o `None` si su informe no se deja leer.

    Un informe ilegible no inventa una media: deja aviso y ese miembro queda
    fuera, igual que `read_histories` con una memoria ilegible.
    """
    values: list[tuple[datetime, float]] = []
    for stored in paths:
        path = resolve(stored) if resolve is not None else stored
        try:
            values.extend(scaled_trades(read(path), lot))
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            warnings.append(f"{label}: no se pudo leer {stored} ({exc}).")
            return None
    return values


def month_expectation(expectation: dict[str, Any], label: str) -> dict[str, Any]:
    """La media de ese `YYYY-MM`, con cuántos años la promedian.

    Sin años no hay media y se dice: un octubre con dos años no es un octubre
    con seis, y la tarjeta tiene que poder enseñar la diferencia.

    Manda el escenario de **ese** mes si existe: con autolote la media de
    siempre está medida a un lote que la cuenta no corrió. Un mes sin
    escenario lee la de siempre, y `basis` dice cuál de las dos se devolvió
    para que la tarjeta no tenga que adivinarlo.
    """
    scenario = ((expectation or {}).get("months_at") or {}).get(label)
    if isinstance(scenario, dict) and scenario.get("years"):
        return _month_row(scenario, str(scenario.get("basis") or ""))
    months = (expectation or {}).get("months") or {}
    row = months.get(label.split("-", 1)[1] if "-" in label else label)
    if not isinstance(row, dict) or not row.get("years"):
        return {"profit": None, "drawdown": None, "years": [], "basis": ""}
    result = _month_row(row, "")
    result["portfolio_historical_valley"] = expectation.get(
        "portfolio_historical_valley",
    )
    return result


def _month_row(row: dict[str, Any], basis: str) -> dict[str, Any]:
    """Una fila de media, venga del escenario del mes o de la de siempre."""
    return {
        "profit": float(row.get("profit") or 0.0),
        "drawdown": float(row.get("drawdown") or 0.0),
        "portfolio_historical_valley": row.get("portfolio_historical_valley"),
        "years": list(row.get("years") or []),
        "basis": basis,
        "balance": float(row.get("balance") or 0.0),
        "lots": {
            key: float(value) for key, value in (row.get("lots") or {}).items()
        },
        "without_rule": list(row.get("without_rule") or []),
    }


def monthly_label(moment: datetime) -> str:
    """`YYYY-MM` del instante, en UTC: la clave con la que se compara."""
    return month_key(moment)
