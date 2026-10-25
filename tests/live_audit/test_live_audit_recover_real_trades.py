"""Rellenar con el informe nativo una ejecución ya guardada sin reales.

El uso #170 del 18→24/10 quedó guardado con `real_trades` vacío porque
`history_deals_get` devolvió una caché vacía, y su propio HTML traía 50
posiciones cerradas. El veredicto lo calcula el manager al leer, así que esas
ejecuciones se pueden arreglar sin relanzar la auditoría.
"""
from __future__ import annotations

import hashlib
import unittest
import unittest.mock

from mt5_manager import live_audit_executions, manager_http

CLOSED = (
    '<tr align="right"><td>2026.10.19 15:34:30</td><td>772438775</td><td>XOM</td>'
    '<td>buy</td><td class="hidden" colspan="8"></td><td>0.01</td><td>176.51</td>'
    "<td>177.11</td><td>187.00</td><td>2026.10.19 16:31:18</td><td>177.04</td>"
    '<td>-0.06</td><td>-0.08</td><td colspan="2">0.47</td></tr>'
)

REPORT = (
    "<html><head><title>77049426: Informe del historial de trading</title></head><body>"
    '<table><tr align="center"><th colspan="14"><b>Posiciones</b></th></tr>'
    '<tr align="center" bgcolor="#E5F0FC"><td><b>Fecha/Hora</b></td>'
    "<td><b>Posición</b></td><td><b>Símbolo</b></td><td><b>Tipo</b></td>"
    "<td><b>Volumen</b></td><td><b>Precio</b></td><td><b>S / L</b></td>"
    "<td><b>T / P</b></td><td><b>Fecha/Hora</b></td><td><b>Precio</b></td>"
    "<td><b>Comisión</b></td><td><b>Swap</b></td>"
    '<td colspan="2"><b>Beneficio</b></td></tr>' + CLOSED + "</table></body></html>"
)

CONTENT = REPORT.encode("utf-16")
NODE = {"id": "roboforex", "url": "http://node", "token": "t"}


def _state(real_trades=(), content: bytes = CONTENT) -> dict:
    return {
        "last_payload": {
            "audit_id": "20261025_003002_773621",
            "audit_key": "audit-170-mvdscovr-2",
            "period_start": "2026-10-18T00:00:00+00:00",
            "period_end": "2026-10-24T23:59:59.999999+00:00",
            "real_trades": list(real_trades),
            "real_account_report": {
                "filename": "real_account_mt5_report.html",
                "native_terminal_report": True,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        },
    }


class RecoverRealTradesTests(unittest.TestCase):
    def _recover(self, state: dict, body: bytes = CONTENT, status: int = 200):
        with unittest.mock.patch.object(
            manager_http, "node_artifact_request", return_value=(status, body, "text/html"),
        ) as artifact:
            return live_audit_executions.recover_real_trades(NODE, state), artifact

    def test_an_execution_without_reals_is_rebuilt_from_its_own_report(self) -> None:
        state = _state()
        recovered, _artifact = self._recover(state)

        self.assertEqual(recovered, 1)
        payload = state["last_payload"]
        trade = payload["real_trades"][0]
        self.assertEqual(trade["position_id"], 772438775)
        self.assertEqual(trade["symbol"], "XOM")
        self.assertEqual(trade["sl"], 177.11)
        # La materia prima viaja en JSON: las horas salen en ISO, como las
        # publica el nodo, y `_revive` las devuelve a datetime al analizar.
        self.assertEqual(trade["open_time"], "2026-10-19T15:34:30+00:00")
        self.assertEqual(trade["close_time"], "2026-10-19T16:31:18+00:00")
        detail = payload["real_history_detail"]
        self.assertEqual(detail["real_trades_source"], "native_terminal_report")
        self.assertEqual(detail["native_closed_positions"], 1)
        # `analyse` lee las posiciones vivas de esta clave del payload, no de
        # `real_history_detail` ni de `real_positions_open_at_period_end`.
        self.assertIn("open_positions_at_period_end", payload)

    def test_an_execution_that_already_has_reals_is_never_touched(self) -> None:
        state = _state([{"position_id": 1, "symbol": "EURUSD"}])
        recovered, artifact = self._recover(state)

        self.assertEqual(recovered, 0)
        self.assertEqual(state["last_payload"]["real_trades"], [{"position_id": 1, "symbol": "EURUSD"}])
        artifact.assert_not_called()

    def test_a_report_from_another_execution_is_refused_by_its_sha(self) -> None:
        state = _state()
        recovered, _artifact = self._recover(state, body=b"otro informe distinto")

        self.assertEqual(recovered, 0)
        self.assertEqual(state["last_payload"]["real_trades"], [])
        self.assertIn(
            "no coincide con la ejecución guardada",
            state["last_payload"]["real_history_detail"]["native_history_error"],
        )

    def test_an_agent_that_does_not_answer_still_lets_the_execution_be_read(self) -> None:
        state = _state()
        with unittest.mock.patch.object(
            manager_http, "node_artifact_request", side_effect=TimeoutError("apagado"),
        ):
            recovered = live_audit_executions.recover_real_trades(NODE, state)

        self.assertEqual(recovered, 0)
        self.assertIn(
            "apagado", state["last_payload"]["real_history_detail"]["native_history_error"],
        )

    def test_an_account_that_did_not_trade_keeps_its_empty_execution(self) -> None:
        empty = REPORT.replace(CLOSED, "").encode("utf-16")
        state = _state(content=empty)
        recovered, _artifact = self._recover(state, body=empty)

        self.assertEqual(recovered, 0)
        self.assertEqual(state["last_payload"]["real_trades"], [])
        self.assertNotIn("real_history_detail", state["last_payload"])


if __name__ == "__main__":
    unittest.main()
