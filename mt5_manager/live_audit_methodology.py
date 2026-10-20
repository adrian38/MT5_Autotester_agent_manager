"""Lo que el veredicto declara haber usado para medir.

No es maquinaria de comparación: es el texto y las constantes que el informe
publica para que quien lo lea sepa contra qué lote se midió cada volumen y con
qué tolerancias. Vive aparte porque `live_audit_comparison.py` llegó a 600
líneas y éste es el bloque que no mide nada.

Que esto se quede corto es un defecto silencioso: si el motor cambia de
criterio y el texto no, el informe describe un criterio que no usó. Pasó con
`volume_basis`, que decía `configured_real_lot` incluso con el autolote
activo.
"""
from __future__ import annotations

from typing import Any

from .live_audit_core import ADAPTIVE_PRICE_TOLERANCE_FLOORS
from .live_audit_lot_schedule import scheduled_steps


def methodology(request: dict[str, Any]) -> dict[str, Any]:
    """Qué criterio dice el veredicto que usó, y contra qué lote midió.

    Los tramos de lote declarados a mano se declaran aparte del
    autolote porque no son el mismo criterio: aquí el lote no sale de
    una fórmula sobre el saldo, lo dijo el usuario con su fecha. Quien
    lea el veredicto tiene que poder distinguirlo del lote único
    configurado para la estrategia.
    """
    time_limit = request["trade_time_tolerance_seconds"]
    autolot = request.get("autolot") or {}
    dynamic_lots = bool(autolot.get("enabled"))
    scheduled = scheduled_steps(request.get("real_lot_schedule"))
    return {
        "alignment": "Mismo símbolo y lado; el reparto se decide sobre todas las parejas a la vez y cada real se usa una vez, sea un cierre o una posición todavía abierta. Manda la apertura dentro de tolerancia; a igualdad, el cierre dentro de tolerancia; a igualdad, la menor distancia total. Una real que sólo encaja por el cierre se acepta y se marca la apertura como desviación.",
        "validation": (
            "En parejas cerradas se validan cierre, precio de apertura, volumen y PnL. Una posición real todavía abierta se considera alineada y valida solo apertura, precio y volumen: no se inventan cierre ni PnL. "
            + (
                "El volumen se compara contra el lote que el autolote calcula en la apertura de cada operación con el saldo de la cuenta real —saldo real × riesgo % × 0,01 ÷ DD_0.01, ajustado al mínimo y paso del broker— y el PnL del tester se escala por ese mismo ratio: dentro de una misma auditoría dos operaciones de la estrategia pueden esperar lotes distintos. El tester no tiene autolote: su lote es el fijo del backtest. Un cierre del tester sin pareja real lee ese mismo saldo real en la hora de su propia apertura."
                if dynamic_lots else
                f"El volumen se compara contra el lote real configurado para la estrategia y el PnL del tester se escala por ese mismo ratio. {scheduled} tramo(s) de lote declarados a mano cambian ese lote desde su fecha: una operación anterior al cambio se sigue midiendo con el lote de antes, y la banda de pertenencia admite todos los lotes del periodo para no apartar un cierre antes de compararlo."
                if scheduled else
                "El volumen se compara contra el lote real configurado para la estrategia y el PnL del tester se escala por ese mismo ratio."
            )
            + " El drawdown se valida sobre el conjunto."
        ),
        "tolerances": {
            "time_seconds": time_limit, "price_points": request["price_tolerance_points"],
            "price_policy": "adaptive_by_instrument",
            "price_absolute_floors": ADAPTIVE_PRICE_TOLERANCE_FLOORS,
            "volume_pct": request["volume_tolerance_pct"],
            "volume_basis": (
                "autolot_balance_risk" if dynamic_lots
                else "real_lot_schedule" if scheduled else "configured_real_lot"
            ),
            "real_lot_schedule_steps": scheduled,
            "autolot_source": str(autolot.get("source_name") or "") if dynamic_lots else "",
            "autolot_strategies": len(
                request.get("_autolot_rules_by_strategy") or autolot.get("rules") or {},
            ) if dynamic_lots else 0,
            "pnl_pct": request["pnl_deviation_warning_pct"],
            "pnl_policy": "adverse_shortfall_only",
            "pnl_basis": "tester_scaled_to_real_lot",
            "drawdown_pct": request["drawdown_deviation_warning_pct"],
        },
    }
