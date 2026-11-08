"""El aborto de la base dice qué original falta y qué lo impidió.

El 2026-11-08 una mejora de AXI abortó con «no se pudieron reconstruir todas
las estrategias originales» y nada más. La causa estaba medida dentro del
propio motor —el histórico OHLC de un portafolio manual había sustituido el
informe base de cuatro candidatos por uno que solapa con su robustez—, pero los
avisos que la nombran se devolvían junto a los sets y se perdían al lanzar la
excepción.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from mt5_manager import portfolio_improvement_chain_support as chain_support
from mt5_manager import portfolio_improvement_support as support
from mt5_manager.portfolio_improvement_common import unreconstructed_originals


ORIGINALS = [
    {"set_path": "/data/axi/outputs/run_1/XAUUSD_H1_GOLD_v010.set", "units": 1},
    {"set_path": "/data/axi/outputs/run_2/USDJPY_H1_Scalper_v001.set", "units": 1},
]


class UnreconstructedOriginalsTests(unittest.TestCase):
    def test_the_error_names_the_missing_set_and_what_the_loader_said(self) -> None:
        rebuilt = [SimpleNamespace(set_path="C:/axi/outputs/run_1/XAUUSD_H1_GOLD_v010.set")]

        error = unreconstructed_originals(
            "la mejora no puede retirar ninguna sin evidencia",
            ORIGINALS,
            rebuilt,
            [
                "1 candidato(s) omitido(s): reporte ilegible o curva invalida.",
                "Ejemplos de errores de carga: USDJPY_H1_Scalper_v001.set: ValueError: "
                "First report period must end before second report period starts",
            ],
        )

        message = str(error)
        self.assertIn("No se pudieron reconstruir todas las estrategias originales", message)
        self.assertIn("la mejora no puede retirar ninguna sin evidencia", message)
        self.assertIn("Sin reconstruir: USDJPY_H1_Scalper_v001.set", message)
        self.assertIn("First report period must end before second report period starts", message)
        # El que sí se reconstruyó no aparece como ausente aunque su ruta venga
        # del host y la guardada del nodo: se comparan por nombre de fichero.
        self.assertNotIn("Sin reconstruir: XAUUSD", message)

    def test_a_set_id_without_path_still_gets_named(self) -> None:
        error = unreconstructed_originals(
            "la mejora mensual no retirará ninguna automáticamente",
            [{"set_id": "EURUSD_M30_a57fa43e.set"}],
            [],
            [],
        )

        self.assertIn("Sin reconstruir: EURUSD_M30_a57fa43e.set", str(error))

    def test_without_warnings_the_message_is_still_the_old_one_plus_the_names(self) -> None:
        error = unreconstructed_originals("la mejora no puede retirar ninguna", ORIGINALS, [], [])

        message = str(error)
        self.assertIn("XAUUSD_H1_GOLD_v010.set", message)
        self.assertIn("USDJPY_H1_Scalper_v001.set", message)


class EngineAbortTests(unittest.TestCase):
    """Los dos motores UBS normales abortan con el mensaje detallado."""

    MODULES = (support, chain_support)

    def _abort_message(self, module) -> str:
        detail = {"members": [dict(member, variant_key="balanced") for member in ORIGINALS]}
        source = SimpleNamespace(project="C:/axi")
        with (
            mock.patch.object(module, "member_rows", return_value=[{}, {}]),
            mock.patch.object(
                module, "load_robust_sets_from_rows",
                return_value=([], ["2 candidato(s) omitido(s): reporte ilegible o curva invalida."]),
            ),
        ):
            with self.assertRaises(ValueError) as caught:
                module._load_original_improvement_sets(
                    source, detail, {"portfolio_type": "balanced"}, None,
                )
        return str(caught.exception)

    def test_both_engines_report_the_missing_originals_and_the_reason(self) -> None:
        for module in self.MODULES:
            with self.subTest(module=module.__name__):
                message = self._abort_message(module)
                self.assertIn("Sin reconstruir: ", message)
                self.assertIn("XAUUSD_H1_GOLD_v010.set", message)
                self.assertIn("USDJPY_H1_Scalper_v001.set", message)
                self.assertIn("reporte ilegible o curva invalida", message)


if __name__ == "__main__":
    unittest.main()
