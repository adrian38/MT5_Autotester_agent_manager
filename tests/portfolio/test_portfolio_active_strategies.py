from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.portfolio_service import (
    PortfolioCoordinator,
    filter_rows_by_active_portfolios,
    normalize_settings,
)
from mt5_manager.portfolio_improvement_common import (
    improvement_inherited_overrides,
    improvement_options,
)


class ActivePortfolioStrategyTests(unittest.TestCase):
    def test_filter_keeps_only_live_member_sets(self) -> None:
        rows = [
            {"set_path": r"C:\pool\active.set"},
            {"set_path": r"C:\pool\inactive.set"},
        ]
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [{"set_path": "C:/pool/active.set"}],
        }

        self.assertEqual(filter_rows_by_active_portfolios(rows, inputs), [rows[0]])

    def test_a_manual_live_member_matches_its_candidate_in_the_pool(self) -> None:
        """Un uso en real puede ser un portafolio manual, y entonces su miembro
        apunta a la copia del nodo mientras el candidato vive en `outputs/`.

        Medido el 2026-11-08 sobre AXI: tres de sus seis usos en real son
        manuales y, comparando rutas, el filtro se quedaba sin un solo
        candidato aunque los cuatro sets de uno de ellos están aceptados.
        """
        candidate = {
            "set_path": r"F:\agente\outputs\ubs_agent\AXI\STANDARD\run_1\gen_002"
                        r"\BTCUSD\H1\BTCUSD_H1_MIX_f4f272a8_g001_s004_v008.set",
        }
        rows = [candidate, {"set_path": r"F:\agente\outputs\otro.set"}]
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [{
                "set_path": r"F:\agente\runtime\axi\manual_portfolios\abc\sets"
                            r"\btcusd_h1_mix_f4f272a8_g001_s004_v008.set",
                "set_id": "btcusd_h1_mix_f4f272a8_g001_s004_v008.set",
            }],
        }

        self.assertEqual(filter_rows_by_active_portfolios(rows, inputs), [candidate])

    def test_every_row_of_the_same_set_file_counts_as_the_same_strategy(self) -> None:
        """El mismo `.set` regenerado en varios runs son varias filas. Lo que
        el usuario marcó en real es la estrategia, no la fila que la halló."""
        rows = [
            {"set_path": r"C:\pool\run_1\dup.set"},
            {"set_path": r"C:\pool\run_2\dup.set"},
            {"set_path": r"C:\pool\run_2\otra.set"},
        ]
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [{"set_path": r"C:\pool\run_9\dup.set"}],
        }

        self.assertEqual(filter_rows_by_active_portfolios(rows, inputs), rows[:2])

    def test_a_live_member_that_never_passed_the_funnel_enters_the_pool(self) -> None:
        """Marcar una estrategia en real ya es el veredicto.

        Los sets subidos a mano a un portafolio manual nunca fueron candidatos:
        de los 57 nombres en real de AXI medidos el 2026-11-08, 39 son de ésos.
        Su fila se construye con lo que guardó la asignación.
        """
        member = {
            "set_path": r"F:\agente\runtime\axi\manual_portfolios\abc\sets\a_mano.set",
            "symbol": "XAUUSD.sa", "timeframe": "H1",
            "candidate_id": "AXI:42",
            "is_report_path": r"F:\agente\reports\a_mano_is.htm",
            "oos_report_path": r"F:\agente\reports\a_mano_oos.htm",
            "final_tick_report_path": r"F:\agente\reports\a_mano_ft.htm",
            "max_balance_dd_001": 12.5,
        }
        rows = [{"set_path": r"F:\agente\outputs\aceptado.set"}]
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [member, {"set_path": r"F:\x\aceptado.set"}],
        }

        result = filter_rows_by_active_portfolios(rows, inputs)

        self.assertEqual(result[0], rows[0])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["set_path"], member["set_path"])
        self.assertEqual(result[1]["symbol"], "XAUUSD.sa")
        self.assertEqual(result[1]["target_symbol"], "XAUUSD.sa")
        self.assertEqual(result[1]["period"], "H1")
        self.assertEqual(result[1]["oos_report_path"], member["oos_report_path"])
        self.assertEqual(result[1]["max_balance_dd_001"], 12.5)

    def test_the_same_strategy_does_not_arrive_twice(self) -> None:
        """Dos usos en real pueden traer la misma estrategia con dos nombres:
        el candidato `robust_AAAAMM_` y la copia a mano sin prefijo.

        `load_robust_sets_from_rows` agrupa por `account_type:tronco` y la fila
        de un miembro no trae `account_type`, así que comparando por nombre de
        fichero llegarían como dos estrategias distintas.
        """
        rows = [{"set_path": r"F:\agente\outputs\robust_202503_base.set"}]
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [
                {"set_path": r"F:\agente\outputs\robust_202503_base.set"},
                {"set_path": r"F:\agente\runtime\axi\manual_portfolios\abc\sets\base.set"},
            ],
        }

        self.assertEqual(filter_rows_by_active_portfolios(rows, inputs), rows)

    def test_an_excluded_member_does_not_return_through_this_door(self) -> None:
        """La cuarentena ya sacó esa estrategia del pool aceptado, y es una
        decisión del usuario sobre ella, no un veredicto del embudo."""
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [{
                "set_path": r"F:\agente\sets\vetada.set", "excluded": True,
                "is_report_path": "is.htm", "oos_report_path": "oos.htm",
            }],
        }

        with self.assertRaisesRegex(ValueError, "portafolios activos"):
            filter_rows_by_active_portfolios([{"set_path": "otro.set"}], inputs)

    def test_a_member_without_reports_is_named_instead_of_vanishing(self) -> None:
        """Un set subido a mano sólo tiene la pasada OHLC completa 2020→2026:
        un informe, no el par IS/OOS. Partirlo inventaría un fuera de muestra
        que nunca lo fue, así que se dice cuáles son y por qué no entran.

        Medido el 2026-11-08: los 39 miembros de los tres portafolios manuales
        en real de AXI están en este caso.
        """
        warnings: list[str] = []
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [
                {"set_path": r"F:\agente\sets\a_mano.set"},
                {"set_path": r"F:\agente\outputs\aceptado.set",
                 "is_report_path": "is.htm", "oos_report_path": "oos.htm"},
            ],
        }
        rows = [{"set_path": r"F:\agente\outputs\aceptado.set"}]

        result = filter_rows_by_active_portfolios(rows, inputs, warnings)

        self.assertEqual(result, rows)
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 estrategia(s)", warnings[0])
        self.assertIn("a_mano.set", warnings[0])

    def test_the_empty_pool_error_carries_the_reason(self) -> None:
        """Un error de configuración disfrazado de fallo de búsqueda costaba
        abrir la memoria del agente para saber qué pasaba."""
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [{"set_path": r"F:\agente\sets\a_mano.set"}],
        }

        with self.assertRaises(ValueError) as caught:
            filter_rows_by_active_portfolios([{"set_path": "ajeno.set"}], inputs)

        self.assertIn("informe base ni de robustez", str(caught.exception))
        self.assertIn("a_mano.set", str(caught.exception))

    def test_the_pool_says_how_many_entered_without_being_candidates(self) -> None:
        warnings: list[str] = []
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [
                {"set_path": r"F:\agente\sets\una.set",
                 "is_report_path": "is.htm", "oos_report_path": "oos.htm"},
                {"set_path": r"F:\agente\sets\otra.set",
                 "is_report_path": "is.htm", "oos_report_path": "oos.htm"},
            ],
        }

        filter_rows_by_active_portfolios([{"set_path": "ajeno.set"}], inputs, warnings)

        self.assertEqual(len(warnings), 1)
        self.assertIn("2 estrategia(s)", warnings[0])

    def test_filter_reports_an_empty_live_pool(self) -> None:
        with self.assertRaisesRegex(ValueError, "portafolios activos"):
            filter_rows_by_active_portfolios(
                [{"set_path": "candidate.set"}],
                {"active_portfolios_only": True, "_active_portfolio_members": []},
            )

    def test_setting_disables_the_conflicting_used_set_filter(self) -> None:
        self.assertFalse(normalize_settings("full_history", {})["active_portfolios_only"])
        enabled = normalize_settings(
            "full_history", {"active_portfolios_only": "yes", "exclude_used_sets": True},
        )
        self.assertTrue(enabled["active_portfolios_only"])
        self.assertFalse(enabled["exclude_used_sets"])
        self.assertFalse(normalize_settings(
            "monthly", {"active_portfolios_only": True},
        )["active_portfolios_only"])

    def test_improvement_accepts_the_inherited_value_and_keeps_used_candidates(self) -> None:
        requested = {
            "active_portfolios_only": True,
            "improvement_exclude_used_sets": True,
            "improvement_inherited_overrides": {"active_portfolios_only": True},
        }

        self.assertEqual(
            improvement_inherited_overrides(requested),
            {"active_portfolios_only": True},
        )
        self.assertFalse(improvement_options(requested).exclude_used_sets)

    def _resolve_live_members(self, settings: dict, members: list | None = None) -> dict:
        """Lo que hace el trabajo al arrancar, con dos usos en real de ejemplo."""
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator([], Path(temp_dir) / "settings.json")
            coordinator.live_audit_settings = SimpleNamespace(
                state=lambda _node_id: {
                    "live_audit_ids": ["use-12", "use-19"],
                    "profiles": {
                        "use-12": {"portfolio_id": 12, "portfolio_type": "balanced"},
                        "use-19": {"portfolio_id": 19, "portfolio_type": "aggressive"},
                    },
                }
            )
            source = SimpleNamespace(
                project=Path(temp_dir),
                saved_portfolio_detail=lambda portfolio_id, _scope: {
                    "portfolio": {
                        "portfolio_type": "bundle",
                        "members": members if members is not None else [
                            {"set_path": f"{portfolio_id}-a.set", "variant_key": "aggressive"},
                            {"set_path": f"{portfolio_id}-m.set", "variant_key": "balanced"},
                        ],
                    }
                },
            )
            self.temp_dir = Path(temp_dir)
            return coordinator._with_active_portfolio_members("axi", source, settings)

    def test_live_members_are_resolved_for_the_same_node(self) -> None:
        settings = self._resolve_live_members({"active_portfolios_only": True})

        self.assertEqual(
            [member["set_path"] for member in settings["_active_portfolio_members"]],
            [str(self.temp_dir / "12-m.set"), str(self.temp_dir / "19-a.set")],
        )

    def test_live_members_are_resolved_when_only_the_improvement_asks(self) -> None:
        """`Mejorar base` manda la casilla dentro de los heredados, no en la raíz.

        El motor sólo los mezcla al preparar cada intento, mucho después de
        resolver los usos en real. Mirando sólo la raíz, el trabajo salía con
        la casilla activa y sin miembros, y el filtro abortaba aunque hubiera
        candidatos: medido el 2026-11-08 sobre la mejora del #67 de AXI.
        """
        settings = self._resolve_live_members({
            "active_portfolios_only": False,
            "improvement_inherited_overrides": {"active_portfolios_only": True},
        })

        self.assertEqual(len(settings["_active_portfolio_members"]), 2)

    def test_live_members_bring_their_reports_to_this_deployment(self) -> None:
        """Sus informes se leen de verdad desde que una estrategia en real entra
        al pool sin haber pasado el embudo, y un portafolio guardado puede
        traerlos con la raíz de otro despliegue."""
        settings = self._resolve_live_members(
            {"active_portfolios_only": True},
            [{
                "set_path": "/data/viejo/sets/a_mano.set",
                "variant_key": "balanced",
                "is_report_path": "/data/viejo/reports/a_mano_is.htm",
                "oos_report_path": "/data/viejo/reports/a_mano_oos.htm",
                "final_tick_report_path": "",
            }],
        )

        member = settings["_active_portfolio_members"][0]
        self.assertEqual(member["set_path"], str(self.temp_dir / "sets" / "a_mano.set"))
        self.assertEqual(
            member["is_report_path"], str(self.temp_dir / "reports" / "a_mano_is.htm"),
        )
        self.assertEqual(
            member["oos_report_path"], str(self.temp_dir / "reports" / "a_mano_oos.htm"),
        )
        self.assertEqual(member["final_tick_report_path"], "")

    def test_the_improvement_may_also_turn_the_filter_off(self) -> None:
        """Desmarcarla en el diálogo gana al ajuste guardado del nodo: si no,
        una mejora sin filtro fallaría por no haber portafolios activos."""
        settings = self._resolve_live_members({
            "active_portfolios_only": True,
            "improvement_inherited_overrides": {"active_portfolios_only": False},
        })

        self.assertNotIn("_active_portfolio_members", settings)

    def test_improvement_inherits_and_edits_the_filter(self) -> None:
        static = Path(__file__).parents[2] / "mt5_manager" / "static"
        script = (static / "portfolio_improvement.js").read_text(encoding="utf-8")
        monthly = (static / "portfolio_monthly_improvement.js").read_text(encoding="utf-8")

        self.assertIn('name="active_portfolios_only" type="checkbox"', script)
        self.assertIn(
            "setValue('active_portfolios_only', inherited.active_portfolios_only)", script,
        )
        self.assertIn(
            "active_portfolios_only: fields.active_portfolios_only.checked", script,
        )
        self.assertIn("fields.improvement_exclude_used_sets.disabled", script)
        self.assertNotIn("active_portfolios_only", monthly)


if __name__ == "__main__":
    unittest.main()
