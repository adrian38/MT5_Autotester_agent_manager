"""Motor de mejora en cadena: despacho, umbral 6M propio y reintento.

La mejora sobre base vive en otro fichero y no debe cambiar de comportamiento;
`ChainForkParityTests` vigila que la parte copiada no se separe en silencio.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from mt5_manager import portfolio_improvement_chain_service as chain
from mt5_manager import portfolio_improvement_dispatch as dispatch
from mt5_manager import portfolio_improvement_service as base
from mt5_manager.portfolio_service import normalize_settings
from portfolio_manager.ubs_portfolio import PortfolioResult, StrategyAllocation


def allocation(name: str, symbol: str, recent: float, units: int = 1):
    return StrategyAllocation(
        name, "1", symbol, units, .01, 65, 5, 5,
        set_path=name, has_recent_performance=True, recent_net_profit_001=recent,
    )


def result_for(allocations: list[StrategyAllocation], gain: float = 30) -> PortfolioResult:
    result = PortfolioResult(allocations, [0, 130], 130, 10, 10, 100, 100,
                             10, 10, .02, 2, 2, "ok", [], [])
    result.seasonal_validation = {"portfolio_improvement": {"efficiency_gain_pct": gain}}
    return result


OLD = str(Path("old.set").resolve())
BAD = str(Path("new_bad.set").resolve())
GOOD = str(Path("new_good.set").resolve())


def chain_inputs(**extra):
    inputs = normalize_settings("full_history", {"portfolio_type": "balanced"})
    inputs.update({
        "improvement_additions": 1,
        "_improvement_exact_additions": True,
        "_improvement_portfolio_uid": "uid-chain",
        **extra,
    })
    return inputs


def single_mode_source(detail_extra: dict | None = None):
    detail = {
        "portfolio_type": "balanced",
        "name": "Mejora del portafolio #73 | modo Moderado",
        "members": [{"set_path": OLD, "units": 1, "lot": .01}],
        "metrics": {"inputs": {"improvement_source_portfolio_id": 73}},
        **(detail_extra or {}),
    }
    return NS(
        project=Path.cwd(),
        saved_portfolio_detail=Mock(return_value={"portfolio": detail}),
        saved_curves=Mock(return_value=[]),
    )


class ChainDetectionTests(unittest.TestCase):
    """La genealogía decide el motor, nunca el nombre del portafolio."""

    def test_each_persisted_fingerprint_is_enough(self):
        cases = [
            {"metrics": {"inputs": {"improvement_source_portfolio_id": 73}}},
            {"metrics": {"seasonal_validation": {"portfolio_improvement": {"source_portfolio_id": 73}}}},
            {"improvement_origin": {"source_id": 73}},
            {"metrics": {"inputs": {"improvement_depth": 1}}},
            {"metrics": {"seasonal_validation": {"portfolio_improvement": {"depth": 2}}}},
        ]
        for detail in cases:
            with self.subTest(detail=detail):
                self.assertTrue(dispatch.is_chain_improvement(detail))

    def test_a_plain_base_is_not_a_chain(self):
        # El nombre genérico A/M/C de una cartera antigua no puede confundirse
        # con una mejora: sin metadatos de origen, es base.
        for detail in (
            {},
            None,
            {"name": "Mejora del portafolio #9"},
            {"metrics": {"inputs": {"improvement_source_portfolio_id": 0}}},
            {"improvement_origin": {"source_id": None}},
        ):
            with self.subTest(detail=detail):
                self.assertFalse(dispatch.is_chain_improvement(detail))


class ChainDispatchTests(unittest.TestCase):
    def test_base_portfolio_keeps_using_the_untouched_engine(self):
        source = NS(saved_portfolio_detail=Mock(return_value={"portfolio": {"members": []}}))
        with patch.object(base, "generate_full_history_improvement", return_value=("a", [])) as engine, \
             patch.object(chain, "generate_full_history_chain_improvement") as chain_engine:
            self.assertEqual(dispatch.run_full_history_improvement(source, 9, {}), ("a", []))
        engine.assert_called_once()
        chain_engine.assert_not_called()

    def test_an_improved_portfolio_goes_to_the_chain_engine(self):
        detail = {"metrics": {"inputs": {"improvement_source_portfolio_id": 73}}}
        source = NS(saved_portfolio_detail=Mock(return_value={"portfolio": detail}))
        with patch.object(chain, "generate_full_history_chain_improvement", return_value=("b", [])) as chain_engine, \
             patch.object(base, "generate_full_history_improvement") as engine:
            self.assertEqual(dispatch.run_full_history_improvement(source, 82, {}), ("b", []))
        chain_engine.assert_called_once()
        engine.assert_not_called()


class ChainRecentThresholdTests(unittest.TestCase):
    """La clave propia existe porque el merge sólo respeta `improvement_*`."""

    def test_absent_key_inherits_the_saved_value(self):
        self.assertEqual(
            chain.improvement_min_recent_contribution_pct(
                {"min_strategy_recent_contribution_pct": 5}
            ),
            5.0,
        )

    def test_explicit_zero_disables_the_gate_instead_of_inheriting(self):
        self.assertEqual(
            chain.improvement_min_recent_contribution_pct({
                "improvement_min_recent_contribution_pct": 0,
                "min_strategy_recent_contribution_pct": 5,
            }),
            0.0,
        )

    def test_the_dialog_value_wins_over_the_saved_one(self):
        self.assertEqual(
            chain.improvement_min_recent_contribution_pct({
                "improvement_min_recent_contribution_pct": 1.5,
                "min_strategy_recent_contribution_pct": 5,
            }),
            1.5,
        )

    def test_out_of_range_and_garbage_are_rejected(self):
        for value in (-1, 101, "bad", True, float("nan")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "entre 0 y 100"):
                chain.improvement_min_recent_contribution_pct(
                    {"improvement_min_recent_contribution_pct": value}
                )

    def test_the_outer_search_validates_before_looping(self):
        with self.assertRaisesRegex(ValueError, "entre 0 y 100"):
            chain.generate_full_history_chain_improvement(
                object(), 82, {"improvement_min_recent_contribution_pct": 200},
            )


class ChainFillerRetryTests(unittest.TestCase):
    """Vetar la candidata de relleno y volver a seleccionar, no abortar."""

    def pool(self):
        return [NS(set_id=OLD), NS(set_id=BAD), NS(set_id=GOOD)]

    def run_attempt(self, optimize_results, **extra):
        source = single_mode_source()
        sets = self.pool()
        with patch.object(chain, "_load_full_history_improvement_pool",
                          return_value=(sets[:1], sets, [], [], [])), \
             patch.object(chain, "build_margin_model", return_value=None), \
             patch.object(chain, "optimize_portfolio", side_effect=optimize_results) as optimize, \
             patch.object(chain, "evaluate_portfolio") as baseline, \
             patch.object(chain, "validate_and_attach_improvement_audit",
                          return_value={"added_count": 1}), \
             patch.object(chain, "_seasonal_coverage"):
            availability, proposals = chain._generate_full_history_improvement_attempt(
                source, 82, chain_inputs(**extra),
            )
        return availability, proposals, optimize, baseline

    def test_a_filler_is_banned_and_the_next_composition_is_tried(self):
        old, bad, good = allocation(OLD, "EURUSD", 1000), allocation(BAD, "USDJPY", 1), allocation(GOOD, "GBPUSD", 900)
        availability, proposals, optimize, baseline = self.run_attempt([
            result_for([old, bad]),   # selección, primer intento
            result_for([old, bad]),   # lotaje final: BAD es relleno
            result_for([old, good]),  # selección tras vetar BAD
            result_for([old, good]),  # lotaje final: aceptable
        ], min_strategy_recent_contribution_pct=5)
        self.assertEqual(optimize.call_count, 4)
        baseline.assert_called_once()
        improvement = availability["improvement"]
        self.assertEqual(improvement["recent_contribution_rejections"], ["new_bad.set"])
        self.assertEqual(improvement["min_recent_contribution_pct"], 5.0)
        self.assertEqual(improvement["engine"], "chain")
        # El pool vetado no puede reaparecer en la propuesta aceptada.
        self.assertEqual(improvement["selected_set_names"], ["old.set", "new_good.set"])
        self.assertEqual(
            proposals[0]["inputs"]["improvement_min_recent_contribution_pct"], 5.0
        )

    def test_retries_are_bounded_and_the_error_names_the_way_out(self):
        # Una candidata de relleno por intento, y una más de las que caben en el
        # tope: así se agotan los reintentos sin agotar antes el pool.
        old = allocation(OLD, "EURUSD", 1000)
        bad_ids = [
            str(Path(f"filler_{index}.set").resolve())
            for index in range(chain.MAX_FILLER_RETRIES + 1)
        ]
        source = single_mode_source()
        sets = [NS(set_id=OLD)] + [NS(set_id=set_id) for set_id in bad_ids]
        optimize_results = []
        for set_id in bad_ids:
            bad = allocation(set_id, "USDJPY", 1)
            optimize_results.extend([result_for([old, bad]), result_for([old, bad])])
        with patch.object(chain, "_load_full_history_improvement_pool",
                          return_value=(sets[:1], sets, [], [], [])), \
             patch.object(chain, "build_margin_model", return_value=None), \
             patch.object(chain, "optimize_portfolio", side_effect=optimize_results) as optimize, \
             patch.object(chain, "evaluate_portfolio") as baseline:
            with self.assertRaisesRegex(ValueError, "tras 3 reintento\\(s\\).*Baja ese mínimo"):
                chain._generate_full_history_improvement_attempt(
                    source, 82, chain_inputs(min_strategy_recent_contribution_pct=5),
                )
        self.assertEqual(optimize.call_count, 2 * (chain.MAX_FILLER_RETRIES + 1))
        baseline.assert_not_called()

    def test_zero_threshold_accepts_the_small_addition_without_retrying(self):
        old, bad = allocation(OLD, "EURUSD", 1000), allocation(BAD, "USDJPY", 1)
        availability, _proposals, optimize, baseline = self.run_attempt([
            result_for([old, bad]), result_for([old, bad]),
        ], improvement_min_recent_contribution_pct=0, min_strategy_recent_contribution_pct=5)
        self.assertEqual(optimize.call_count, 2)
        baseline.assert_called_once()
        self.assertEqual(availability["improvement"]["min_recent_contribution_pct"], 0.0)
        self.assertEqual(availability["improvement"]["recent_contribution_rejections"], [])

    def test_exhausting_the_pool_by_banning_blames_the_threshold(self):
        old, bad = allocation(OLD, "EURUSD", 1000), allocation(BAD, "USDJPY", 1)
        source = single_mode_source()
        sets = [NS(set_id=OLD), NS(set_id=BAD)]
        with patch.object(chain, "_load_full_history_improvement_pool",
                          return_value=(sets[:1], sets, [], [], [])), \
             patch.object(chain, "build_margin_model", return_value=None), \
             patch.object(chain, "optimize_portfolio", return_value=result_for([old, bad])), \
             patch.object(chain, "evaluate_portfolio"):
            # La causa es el umbral, no que falten candidatas en el pool.
            with self.assertRaisesRegex(
                ValueError, "aporte mínimo Final Tick 6M de 5.0%.*agotaron.*tras vetar 1"
            ):
                chain._generate_full_history_improvement_attempt(
                    source, 82, chain_inputs(min_strategy_recent_contribution_pct=5),
                )


class BaseEngineThresholdTests(unittest.TestCase):
    """El motor de base tiene el mismo umbral elegible y el mismo reintento."""

    def base_source(self):
        detail = {"portfolio_type": "balanced", "members": [{"set_path": OLD, "units": 1}]}
        return NS(project=Path.cwd(),
                  saved_portfolio_detail=Mock(return_value={"portfolio": detail}),
                  saved_curves=Mock(return_value=[]))

    def run_attempt(self, optimize_results, sets, **extra):
        with patch.object(base, "_load_full_history_improvement_pool",
                          return_value=(sets[:1], sets, [], [], [])), \
             patch.object(base, "build_margin_model", return_value=None), \
             patch.object(base, "optimize_portfolio", side_effect=optimize_results) as optimize, \
             patch.object(base, "evaluate_portfolio") as baseline, \
             patch.object(base, "validate_and_attach_improvement_audit",
                          return_value={"added_count": 1}), \
             patch.object(base, "_seasonal_coverage"):
            availability, proposals = base._generate_full_history_improvement_attempt(
                self.base_source(), 9, chain_inputs(**extra),
            )
        return availability, proposals, optimize, baseline

    def test_the_dialog_value_is_honoured_and_recorded(self):
        old, bad = allocation(OLD, "EURUSD", 1000), allocation(BAD, "USDJPY", 1)
        availability, proposals, optimize, baseline = self.run_attempt(
            [result_for([old, bad]), result_for([old, bad])],
            [NS(set_id=OLD), NS(set_id=BAD)],
            improvement_min_recent_contribution_pct=0,
            min_strategy_recent_contribution_pct=5,
        )
        self.assertEqual(optimize.call_count, 2)
        baseline.assert_called_once()
        self.assertEqual(availability["improvement"]["min_recent_contribution_pct"], 0.0)
        self.assertEqual(availability["improvement"]["engine"], "base")
        self.assertEqual(availability["improvement"]["recent_contribution_rejections"], [])
        self.assertEqual(
            proposals[0]["inputs"]["improvement_min_recent_contribution_pct"], 0.0
        )

    def test_absent_key_still_inherits_the_saved_threshold(self):
        old, bad = allocation(OLD, "EURUSD", 1000), allocation(BAD, "USDJPY", 1)
        with self.assertRaisesRegex(ValueError, "Final Tick 6M de 5.0%"):
            self.run_attempt(
                [result_for([old, bad]) for _ in range(2)],
                [NS(set_id=OLD), NS(set_id=BAD)],
                min_strategy_recent_contribution_pct=5,
            )

    def test_a_filler_is_banned_and_the_next_composition_is_tried(self):
        old = allocation(OLD, "EURUSD", 1000)
        bad, good = allocation(BAD, "USDJPY", 1), allocation(GOOD, "GBPUSD", 900)
        availability, _proposals, optimize, baseline = self.run_attempt(
            [result_for([old, bad]), result_for([old, bad]),
             result_for([old, good]), result_for([old, good])],
            [NS(set_id=OLD), NS(set_id=BAD), NS(set_id=GOOD)],
            min_strategy_recent_contribution_pct=5,
        )
        self.assertEqual(optimize.call_count, 4)
        baseline.assert_called_once()
        improvement = availability["improvement"]
        self.assertEqual(improvement["recent_contribution_rejections"], ["new_bad.set"])
        self.assertEqual(improvement["selected_set_names"], ["old.set", "new_good.set"])

    def test_the_outer_search_validates_before_looping(self):
        with self.assertRaisesRegex(ValueError, "entre 0 y 100"):
            base.generate_full_history_improvement(
                object(), 9, {"improvement_min_recent_contribution_pct": 200},
            )


class ChainForkParityTests(unittest.TestCase):
    """El fork es deliberado; la deriva silenciosa no.

    Estas funciones son copia literal y deben seguir siéndolo. Si una corrección
    entra en el motor de base, esta prueba obliga a decidir explícitamente si
    también entra en la cadena, en vez de descubrirlo meses después.
    """

    SHARED = (
        "_lineage_from_parent",
        "minimum_additions",
        "improvement_options",
        "improvement_allowed_groups",
        "improvement_selection_priority",
        "improvement_account_leverage",
        "improvement_grid_off",
        "improvement_min_recent_contribution_pct",
        "_attach_stress_comparison",
        "_improvement_rank",
        "_selected_variant_detail",
        "_saved_single_mode",
        "_load_full_history_improvement_pool",
    )

    def test_both_engines_filter_disabled_symbols_only_from_new_candidates(self):
        original = NS(set_id=OLD, symbol="EURUSD", target_symbol="EURUSD")
        candidate = NS(set_id=GOOD, symbol="USDJPY", target_symbol="USDJPY")
        rows = [
            {"set_path": OLD, "symbol": "EURUSD", "target_symbol": "EURUSD"},
            {"set_path": BAD, "symbol": "GBPUSD", "target_symbol": "GBPUSD"},
            {"set_path": GOOD, "symbol": "USDJPY", "target_symbol": "USDJPY"},
        ]
        detail = {"portfolio_type": "balanced", "members": [
            {"variant_key": "balanced", "set_path": OLD, "units": 1},
        ]}
        source = NS(
            project=Path.cwd(), universe=Path("universe.ini"),
            candidate_rows=Mock(return_value=rows), used_set_paths=Mock(return_value=[]),
        )
        inputs = {
            "portfolio_type": "balanced",
            "improvement_allowed_asset_groups": ["Forex"],
            "improvement_disabled_symbols": ["EURUSD", "GBPUSD"],
        }

        for engine in (base, chain):
            with self.subTest(engine=engine.__name__), \
                    patch.object(engine, "load_robust_sets_from_rows") as loader, \
                    patch.object(engine, "recent_positive_candidates", side_effect=lambda sets, ids: sets), \
                    patch.object(engine, "member_rows", return_value=[{"set_path": OLD}]):
                loader.side_effect = [([original], []), ([candidate], [])]
                originals, pool, kept_rows, _used, _warnings = (
                    engine._load_full_history_improvement_pool(
                        source, detail, 1, inputs, None,
                    )
                )

            self.assertEqual([row["symbol"] for row in kept_rows], ["USDJPY"])
            self.assertEqual([item.set_id for item in originals], [OLD])
            self.assertEqual({item.set_id for item in pool}, {OLD, GOOD})

    @staticmethod
    def bodies(path: Path) -> dict[str, str]:
        """Código normalizado, sin docstrings.

        El texto explicativo sí puede diferir: la cadena documenta por qué está
        bifurcada. Lo que no puede diferir es lo que se ejecuta.
        """
        def stripped(node: ast.FunctionDef) -> ast.FunctionDef:
            copy = ast.parse(ast.unparse(node)).body[0]
            if ast.get_docstring(copy) is not None:
                copy.body = copy.body[1:]
            return copy

        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.name: ast.dump(stripped(node))
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def test_shared_helpers_are_identical_in_both_engines(self):
        root = Path(__file__).resolve().parents[1] / "mt5_manager"
        left = self.bodies(root / "portfolio_improvement_service.py")
        right = self.bodies(root / "portfolio_improvement_chain_service.py")
        for name in self.SHARED:
            with self.subTest(name=name):
                self.assertIn(name, left)
                self.assertIn(name, right)
                self.assertEqual(
                    left[name], right[name],
                    f"{name} divergió entre el motor de base y el de cadena; "
                    "decide si el cambio debe portarse o si deja de ser compartida",
                )

    def test_the_attempt_differs_only_in_the_engine_label(self):
        """Hoy los dos intentos son el mismo algoritmo.

        La bifurcación existe para poder cambiar la cadena sin tocar la base, no
        porque ya diverjan. Mientras no diverjan, cualquier arreglo tiene que
        entrar en las dos; el día que una se separe de verdad, esta prueba se
        quita a conciencia y se documenta por qué.
        """
        root = Path(__file__).resolve().parents[1] / "mt5_manager"
        name = "_generate_full_history_improvement_attempt"
        left = self.bodies(root / "portfolio_improvement_service.py")[name]
        right = self.bodies(root / "portfolio_improvement_chain_service.py")[name]
        self.assertEqual(
            left.replace("'base'", "'engine'"),
            right.replace("'chain'", "'engine'"),
            "los dos intentos divergieron; porta el arreglo o documenta la separación",
        )

    def test_the_constants_match(self):
        self.assertEqual(base.MAX_IMPROVEMENT_ADDITIONS, chain.MAX_IMPROVEMENT_ADDITIONS)
        self.assertEqual(base.MAX_FILLER_RETRIES, chain.MAX_FILLER_RETRIES)


if __name__ == "__main__":
    unittest.main()
