"""Reintentos de la comprobación manual: lo que el manager manda al agente.

Vive aparte de `test_portfolio_manual_check.py`, que ya roza las 600 líneas.
Quien reintenta es el agente —es el único que ve el código de salida de cada
corrida del tester—, así que aquí sólo se comprueba que el número viaja, que
se recuerda por agente y que el modal lo ofrece editable.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mt5_manager import portfolio_manual_check
from mt5_manager.portfolio_manual_check import DEFAULT_RETRIES


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "mt5_manager" / "static"


def _source() -> SimpleNamespace:
    member = {
        "candidate_id": "IC:3", "set_name": "tres.set",
        "set_path": "/data/ic/outputs/tres.set",
        "symbol": "XAUUSD", "timeframe": "H1", "units": 1,
    }
    return SimpleNamespace(
        project=Path("/data/ic"),
        saved_portfolio_detail=lambda portfolio_id, scope: {
            "portfolio": {"id": 62, "members": [member]},
        },
    )


class RetryRequestTests(unittest.TestCase):
    def test_three_is_what_the_modal_proposes(self) -> None:
        self.assertEqual(DEFAULT_RETRIES, 3)
        preview = portfolio_manual_check.new_strategies(_source(), 62, "full_history")
        self.assertEqual(preview["retries"], 3)

    def test_an_edited_number_travels_to_the_node(self) -> None:
        request = portfolio_manual_check.start_request(
            _source(), 62, "full_history", {"retries": 5},
        )
        self.assertEqual(request["retries"], 5)

    def test_nothing_sent_keeps_the_default(self) -> None:
        request = portfolio_manual_check.start_request(_source(), 62, "full_history", {})
        self.assertEqual(request["retries"], DEFAULT_RETRIES)

    def test_a_nonsense_number_does_not_reach_the_agent(self) -> None:
        # El rango lo vuelve a comprobar el agente; aquí se acota para no
        # apuntar un disparate en los ajustes recordados de ese broker.
        # `None` no es un disparate: es «no me lo mandes», y cae al de fábrica.
        for sent, expected in (
            (99, 10), (-4, 0), ("muchos", DEFAULT_RETRIES), (None, DEFAULT_RETRIES),
        ):
            with self.subTest(sent=sent):
                request = portfolio_manual_check.start_request(
                    _source(), 62, "full_history", {"retries": sent},
                )
                self.assertEqual(request["retries"], expected)

    def test_the_number_is_remembered_for_that_agent(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "manual_check_settings.json"
            request = portfolio_manual_check.start_request(
                _source(), 62, "full_history", {"retries": 7},
            )
            portfolio_manual_check.remember_settings(path, "ic", request)

            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["ic"]["retries"], 7)
            self.assertEqual(
                portfolio_manual_check.saved_settings(path, "ic")["retries"], 7,
            )
            # Y es lo que el modal siguiente de ese agente abre, no el de fábrica.
            preview = portfolio_manual_check.new_strategies(
                _source(), 62, "full_history",
                portfolio_manual_check.saved_settings(path, "ic"),
            )
            self.assertEqual(preview["retries"], 7)

    def test_another_agent_keeps_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "manual_check_settings.json"
            portfolio_manual_check.remember_settings(
                path, "ic", {"retries": 7},
            )
            self.assertEqual(portfolio_manual_check.saved_settings(path, "axi"), {})


class RetryScreenTests(unittest.TestCase):
    def test_the_modal_offers_the_number_editable(self) -> None:
        script = (STATIC / "portfolio_manual_check.js").read_text(encoding="utf-8")

        self.assertIn('name="retries"', script)
        self.assertIn('value="3"', script)
        self.assertIn('min="0" max="10"', script)
        # Se envía y se repone con lo que el agente recuerde.
        self.assertIn("retries: Number(fields.retries.value)", script)
        self.assertIn("fields.retries.value = preview.retries", script)


if __name__ == "__main__":
    unittest.main()
