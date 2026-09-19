from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mt5_manager.portfolio_improvement_common import (
    ImprovementOptions,
    allocation_units,
    improvement_options,
    member_rows,
    unique_original_members,
    validate_and_attach_improvement_audit,
)
from mt5_manager.portfolio_service import PortfolioCoordinator
from mt5_manager import portfolio_improvement_service as full_improvement
from mt5_manager import portfolio_monthly_improvement_service as monthly_improvement


class ImprovementOptionsTests(unittest.TestCase):
    def test_excluding_sets_used_by_other_portfolios_is_on_by_default(self) -> None:
        options = improvement_options({})

        self.assertTrue(options.exclude_used_sets)
        self.assertTrue(options.allow_same_symbol)
        self.assertEqual(options.max_additions, 2)
        self.assertEqual(options.min_efficiency_gain_pct, 3.0)

    def test_a_single_run_cannot_grow_the_base_without_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "entre 1 y 5"):
            improvement_options({"improvement_additions": 6})

    def test_efficiency_threshold_rejects_overfit_seeking_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "entre 0 y 25"):
            improvement_options({"improvement_min_efficiency_gain_pct": 30})

    def test_bundle_reconstruction_prefers_the_saved_base_variant(self) -> None:
        detail = {
            "members": [
                {"set_path": "one.set", "variant_key": "aggressive", "units": 7},
                {"set_path": "one.set", "variant_key": "balanced", "units": 3},
                {"set_path": "two.set", "variant_key": "balanced", "units": 2},
            ]
        }

        members = unique_original_members(detail, "balanced")

        self.assertEqual({row["set_path"] for row in members}, {"one.set", "two.set"})
        self.assertEqual(next(row for row in members if row["set_path"] == "one.set")["units"], 3)

    def test_saved_member_paths_are_relocated_before_reconstruction(self) -> None:
        member = {
            "set_path": r"C:\old-agent\outputs\run_1\one.set",
            "is_report_path": r"C:\old-agent\reports\one.htm",
            "oos_report_path": r"C:\old-agent\reports\robust_one.htm",
        }

        rows = member_rows(
            [member],
            resolve_path=lambda value: str(value).replace(
                r"C:\old-agent", "/data/agent"
            ).replace("\\", "/"),
        )

        self.assertEqual(rows[0]["set_path"], "/data/agent/outputs/run_1/one.set")
        self.assertEqual(rows[0]["is_report_path"], "/data/agent/reports/one.htm")
        self.assertEqual(
            rows[0]["oos_report_path"], "/data/agent/reports/robust_one.htm"
        )

    def test_a_changed_verdict_does_not_erase_an_original_with_reports_on_disk(self) -> None:
        """Reproduce el #123 de RoboForex: XAUUSD sin ruta de robustez guardada.

        El agente rechazó el candidato 4348 después de guardar el portafolio y
        borró su fila de robustez, así que la asignación quedó con
        ``oos_report_path`` vacío. El informe sigue en ``reports/``. Sin
        recuperarlo, la mejora aborta con «no se pudieron reconstruir todas las
        estrategias originales», que es retirar un original por un cambio de
        veredicto.
        """
        stem = "XAUUSD_H4_GOLD_XAUUSD_H4_GOLD_5e988f6b_g002_s013_v008"
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            reports = project / "reports"
            reports.mkdir()
            (reports / f"{stem}.htm").write_text("base", encoding="utf-8")
            (reports / f"robust_004348_{stem}.htm").write_text("oos", encoding="utf-8")
            member = {
                "candidate_id": "ROBOFOREX/ECN:4348",
                "set_path": f"/data/roboforex/outputs/ubs_agent/ECN/{stem}.set",
                "is_report_path": f"/data/roboforex/reports/{stem}.htm",
                "oos_report_path": "",
            }
            resolve = lambda value: str(value or "").replace(
                "/data/roboforex", str(project)
            ).replace("/", "\\") if value else ""

            without = member_rows([member], resolve_path=resolve)
            with_project = member_rows([member], resolve_path=resolve, project=project)

        self.assertEqual(without[0]["oos_report_path"], "")
        self.assertFalse(without[0]["historical_reports_recovered"])
        self.assertEqual(
            with_project[0]["oos_report_path"],
            str(reports / f"robust_004348_{stem}.htm"),
        )
        self.assertTrue(with_project[0]["historical_reports_recovered"])

    def test_recovery_never_invents_the_optional_final_tick_reports(self) -> None:
        """Final Tick continuo y 6M son opcionales: recuperarlos cambiaría el
        riesgo y el aporte reciente con los que se evaluó la base guardada."""
        stem = "EURUSD_M30_Advanced_Scalper_a57fa43e_g002_s010_v007"
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            reports = project / "reports"
            reports.mkdir()
            for name in (
                f"{stem}.htm",
                f"robust_019917_{stem}.htm",
                f"tick_019917_{stem}.htm",
                f"tick6m_019917_{stem}.htm",
            ):
                (reports / name).write_text("report", encoding="utf-8")

            rows = member_rows(
                [{
                    "candidate_id": "ROBOFOREX/ECN:19917",
                    "set_path": f"{project}\\outputs\\{stem}.set",
                    "is_report_path": "",
                    "oos_report_path": "",
                    "final_tick_report_path": "",
                    "full_history_report_path": "",
                }],
                project=project,
            )

        self.assertTrue(rows[0]["is_report_path"].endswith(f"{stem}.htm"))
        self.assertTrue(rows[0]["oos_report_path"].endswith(f"robust_019917_{stem}.htm"))
        self.assertEqual(rows[0]["final_tick_report_path"], "")
        self.assertEqual(rows[0]["full_history_report_path"], "")

    def test_recovery_keeps_the_saved_paths_when_they_are_present(self) -> None:
        stem = "AMZN_M30_GOLD_b882b6a6_g002_s014_v009"
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "reports").mkdir()
            (project / "reports" / f"robust_016659_{stem}.htm").write_text("x", encoding="utf-8")

            rows = member_rows(
                [{
                    "candidate_id": "ROBOFOREX/ECN:16659",
                    "set_path": f"{project}\\outputs\\{stem}.set",
                    "is_report_path": r"X:\saved\base.htm",
                    "oos_report_path": r"X:\saved\robust.htm",
                }],
                project=project,
            )

        self.assertEqual(rows[0]["is_report_path"], r"X:\saved\base.htm")
        self.assertEqual(rows[0]["oos_report_path"], r"X:\saved\robust.htm")
        self.assertFalse(rows[0]["historical_reports_recovered"])

    def test_saved_allocation_keys_use_the_same_relocated_ids_as_curves(self) -> None:
        detail = {
            "members": [
                {
                    "variant_key": "balanced",
                    "set_path": r"C:\old-agent\outputs\run_1\one.set",
                    "units": 4,
                }
            ]
        }

        units = allocation_units(
            detail,
            "balanced",
            resolve_path=lambda value: str(value).replace(
                r"C:\old-agent", "/data/agent"
            ).replace("\\", "/"),
        )

        self.assertEqual(units, {"/data/agent/outputs/run_1/one.set": 4})
        self.assertNotIn(r"C:\old-agent\outputs\run_1\one.set", units)


class ImprovementAuditTests(unittest.TestCase):
    @staticmethod
    def strategy(set_id: str, symbol: str, recent: float = 10.0) -> SimpleNamespace:
        return SimpleNamespace(
            set_id=set_id,
            symbol=symbol,
            has_recent_performance=True,
            recent_net_profit_001=recent,
        )

    @staticmethod
    def result(ids: list[str], net: float = 130.0, dd: float = 10.0) -> SimpleNamespace:
        return SimpleNamespace(
            allocations=[SimpleNamespace(set_id=set_id, units=1) for set_id in ids],
            total_net_profit=net,
            actual_valley_dd=dd,
            target_valley_dd=20.0,
            seasonal_validation={},
            warnings=[],
        )

    def test_originals_are_never_removed(self) -> None:
        with self.assertRaisesRegex(ValueError, "retirar estrategias originales"):
            validate_and_attach_improvement_audit(
                result=self.result(["new.set"]),
                baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "USDJPY")],
                original_ids=["old.set"],
                options=ImprovementOptions(),
                inputs={},
                scope="full_history",
            )

    def test_same_symbol_is_accepted_only_with_recorded_low_dependence(self) -> None:
        pair = SimpleNamespace(pearson_corr=0.10, downside_corr=0.08, dd_overlap=0.12)
        result = self.result(["old.set", "new.set"])

        with mock.patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            audit = validate_and_attach_improvement_audit(
                result=result,
                baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "EURUSD")],
                original_ids=["old.set"],
                options=ImprovementOptions(min_efficiency_gain_pct=1.0),
                inputs={"max_pair_corr": 0.35, "max_downside_corr": 0.25, "max_dd_overlap": 0.35},
                scope="full_history",
            )

        self.assertEqual(audit["removed_original_ids"], [])
        self.assertTrue(audit["candidates"][0]["same_symbol_as_original"])
        self.assertIn("baja dependencia", audit["candidates"][0]["justification"])
        self.assertIs(result.seasonal_validation["portfolio_improvement"], audit)

    def test_historical_growth_without_better_profit_dd_is_rejected(self) -> None:
        pair = SimpleNamespace(pearson_corr=0.0, downside_corr=0.0, dd_overlap=0.0)
        with mock.patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            with self.assertRaisesRegex(ValueError, "no mejora suficientemente"):
                validate_and_attach_improvement_audit(
                    result=self.result(["old.set", "new.set"], net=105.0, dd=12.0),
                    baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                    all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "USDJPY")],
                    original_ids=["old.set"],
                    options=ImprovementOptions(min_efficiency_gain_pct=1.0),
                    inputs={},
                    scope="full_history",
                )

    def test_requested_additions_are_a_maximum_not_an_exact_quota(self) -> None:
        pair = SimpleNamespace(pearson_corr=0.0, downside_corr=0.0, dd_overlap=0.0)
        result = self.result(["old.set", "new.set"])
        with mock.patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            audit = validate_and_attach_improvement_audit(
                result=result,
                baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "USDJPY")],
                original_ids=["old.set"],
                options=ImprovementOptions(max_additions=2, min_efficiency_gain_pct=3.0),
                inputs={},
                scope="full_history",
            )

        self.assertEqual(audit["added_count"], 1)
        self.assertEqual(audit["maximum_additions"], 2)


class ImprovementWireTests(unittest.TestCase):
    def test_full_improvement_creates_a_new_single_mode_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordinator = PortfolioCoordinator(
                [{"id": "node-1"}], Path(folder) / "settings.json"
            )
            key = coordinator._key("node-1", "full_history")
            coordinator.jobs[key] = {
                "operation": "improve",
                "portfolio_id": 41,
            }
            coordinator.proposals[key] = [
                {"key": "balanced"},
            ]
            with mock.patch(
                "mt5_manager.portfolio_service.serialize_portfolio_proposals",
                return_value=[{"serialized": True}],
            ):
                payload = coordinator.prepare_save(
                    "node-1", "full_history", "balanced"
                )

        self.assertEqual(payload["operation"], "generate")
        self.assertEqual(payload["manager_operation"], "improve")
        self.assertIsNone(payload["portfolio_id"])


class ImprovementMaximumFallbackTests(unittest.TestCase):
    def test_full_history_never_reduces_the_requested_minimum(self) -> None:
        with mock.patch.object(
            full_improvement,
            "_generate_full_history_improvement_attempt",
            side_effect=ValueError("no cumplen"),
        ) as attempt:
            with self.assertRaisesRegex(ValueError, "al menos 2.*No se rebaja el mínimo"):
                full_improvement.generate_full_history_improvement(
                    object(), 7, {"improvement_additions": 2}
                )

        tried = [call.args[2]["improvement_additions"] for call in attempt.call_args_list]
        self.assertEqual(tried, [2, 3, 4, 5])

    def test_monthly_retries_with_one_when_two_do_not_pass(self) -> None:
        with mock.patch.object(
            monthly_improvement,
            "_generate_monthly_improvement_attempt",
            side_effect=[ValueError("dos no cumplen"), ({"improvement": {}}, [])],
        ) as attempt:
            availability, _proposals = monthly_improvement.generate_monthly_improvement(
                object(), 8, {"improvement_additions": 2}
            )

        tried = [call.args[2]["improvement_additions"] for call in attempt.call_args_list]
        self.assertEqual(tried, [2, 1])
        self.assertEqual(availability["improvement"]["maximum_additions"], 2)
        self.assertEqual(availability["improvement"]["actual_additions"], 1)


class ImprovementScreenTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1] / "mt5_manager" / "static"

    def test_full_and_monthly_keep_independent_improvement_scripts(self) -> None:
        full_page = (self.ROOT / "portfolios.html").read_text(encoding="utf-8")
        monthly_page = (self.ROOT / "portfolios_monthly.html").read_text(encoding="utf-8")

        self.assertIn('id="detail-improve"', full_page)
        self.assertIn('/portfolio_improvement.js', full_page)
        self.assertNotIn('/portfolio_monthly_improvement.js', full_page)
        self.assertIn('id="detail-improve"', monthly_page)
        self.assertIn('/portfolio_monthly_improvement.js', monthly_page)

    def test_dialog_makes_the_exclusion_explicit_and_checked(self) -> None:
        for name in ("portfolio_improvement.js", "portfolio_monthly_improvement.js"):
            script = (self.ROOT / name).read_text(encoding="utf-8")
            self.assertIn('name="improvement_exclude_used_sets" type="checkbox" checked', script)
            self.assertIn("originales quedarán bloqueadas", script)
            self.assertIn("improvement_allow_same_symbol", script)
            expected = "Mínimo" if name == "portfolio_improvement.js" else "Máximo"
            self.assertIn(f"{expected} de estrategias a añadir", script)
            self.assertIn('max="25" step="0.1" value="3"', script)

    def test_normal_dialog_sends_a_minimum_and_explains_acceptance(self) -> None:
        script = (self.ROOT / "portfolio_improvement.js").read_text(encoding="utf-8")
        self.assertIn("improvement_min_additions: Number(fields.improvement_min_additions.value)", script)
        self.assertIn("improvement_selection_priority: fields.improvement_selection_priority.value", script)
        self.assertIn('<option value="balanced" selected>Equilibrada</option>', script)
        self.assertIn("Si no se alcanza el mínimo con candidatas válidas, no habrá propuesta", script)
        self.assertIn("límite de cinco incorporaciones por búsqueda", script)
        self.assertIn("es una preferencia de selección, no una restricción adicional", script)

    def test_normal_dialog_offers_the_margin_profile_already_inherited(self) -> None:
        script = (self.ROOT / "portfolio_improvement.js").read_text(encoding="utf-8")
        self.assertIn('name="improvement_margin_profile"', script)
        self.assertIn("improvement_margin_profile: fields.improvement_margin_profile.value", script)
        self.assertIn('name="improvement_account_leverage"', script)
        self.assertIn("improvement_account_leverage: Number(fields.improvement_account_leverage.value)", script)
        self.assertIn("variantSaved.account_leverage", script)
        self.assertIn("las carteras antiguas pueden no conservar la elección original", script)
        # Llega puesto con el de la base, no con el del formulario central.
        self.assertIn("variantSaved.margin_profile || saved.margin_profile", script)
        self.assertIn("son siempre los del broker de origen", script)
        # El mensual sigue congelado: no gana selector.
        monthly = (self.ROOT / "portfolio_monthly_improvement.js").read_text(encoding="utf-8")
        self.assertNotIn("improvement_margin_profile", monthly)
        self.assertNotIn("improvement_account_leverage", monthly)

    def test_normal_dialog_controls_grid_off_for_new_candidates(self) -> None:
        script = (self.ROOT / "portfolio_improvement.js").read_text(encoding="utf-8")
        self.assertIn('name="improvement_grid_off" type="checkbox"', script)
        self.assertIn("variantSaved.grid_off ?? saved.grid_off", script)
        self.assertIn("improvement_grid_off: fields.improvement_grid_off.checked", script)
        self.assertIn("Las estrategias originales no se retiran", script)
        monthly = (self.ROOT / "portfolio_monthly_improvement.js").read_text(encoding="utf-8")
        self.assertNotIn("improvement_grid_off", monthly)

    def test_bundle_improvement_starts_from_the_variant_shown_in_the_detail(self) -> None:
        script = (self.ROOT / "portfolio_improvement.js").read_text(encoding="utf-8")

        self.assertIn("const displayed = typeof selectedDetailVariant", script)
        self.assertIn("bundle && portfolioModes.includes(displayed)", script)

    def test_improving_an_improvement_locks_its_original_mode(self) -> None:
        script = (self.ROOT / "portfolio_improvement.js").read_text(encoding="utf-8")

        self.assertIn("const inheritedImprovementMode", script)
        self.assertIn("lineage?.mode", script)
        self.assertIn("saved.improvement_portfolio_type", script)
        self.assertIn("option.value !== target", script)
        self.assertNotIn("option.value !== currentDetail.portfolio_type", script)

    def test_manager_serves_both_new_static_assets(self) -> None:
        manager = (self.ROOT.parent / "manager.py").read_text(encoding="utf-8")
        self.assertIn('"portfolio_improvement.js"', manager)
        self.assertIn('"portfolio_monthly_improvement.js"', manager)


if __name__ == "__main__":
    unittest.main()
