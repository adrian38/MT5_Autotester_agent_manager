from __future__ import annotations

import unittest
from datetime import datetime, timezone

from mt5_manager.live_audit_analysis import (
    PayloadError, analyze, portfolio_lot_bands, strategy_set_names,
)


def profile(**overrides) -> dict:
    base = {
        "trade_time_tolerance_seconds": 120,
        "price_tolerance_points": 15.0,
        "volume_tolerance_pct": 1.0,
        "pnl_deviation_warning_pct": 10.0,
        "drawdown_deviation_warning_pct": 15.0,
        "min_tick_history_quality_pct": 80.0,
        "real_strategy_lots": {},
    }
    base.update(overrides)
    return base


def payload(**overrides) -> dict:
    opened = datetime(2026, 9, 22, 9, 19, 46, tzinfo=timezone.utc)
    closed = datetime(2026, 9, 22, 10, 32, 58, tzinfo=timezone.utc)
    trade = {
        "strategy": "ROBOFOREX/ECN:27672", "symbol": "EURUSD", "side": "sell",
        "open_time": opened.isoformat(), "close_time": closed.isoformat(),
        "open_price": 1.14621, "close_price": 1.14, "volume": 0.08, "profit": 5.97,
    }
    base = {
        "audit_id": "20261001_012810_063790",
        "completed_at": "2026-09-30T23:31:04+00:00",
        "period_start": "2026-09-21T00:00:00+00:00",
        "period_end": "2026-09-27T23:59:59+00:00",
        "request": {
            "audit_key": "audit-148", "portfolio_id": 148, "portfolio_type": "aggressive",
            "period_mode": "fixed_dates", "period_days": 7,
            "period_start_date": "2026-09-21", "period_end_date": "2026-09-27",
        },
        "tester_trades": [trade],
        "real_trades": [{**trade, "volume": 0.04, "profit": 3.06}],
        "symbol_points": {"EURUSD": 0.00001},
        "strategies": {"ROBOFOREX/ECN:27672": 1},
        "qualities": [100.0],
        "strategy_artifacts": [],
        "selected_members": [],
        "volume_rules": {},
        "account": {"login": "77049426"},
        "real_history_detail": {"period_raw_deals": 70},
        "tester_execution": {},
        "real_account_report": {},
        "terminal_restore": [],
        "open_positions_at_period_end": [],
    }
    base.update(overrides)
    return base


class LiveAuditAnalysisTests(unittest.TestCase):
    def test_the_verdict_uses_the_tolerances_configured_now(self) -> None:
        # Es el motivo del cambio: cambiar una tolerancia y volver a aplicarla
        # sobre una ejecución guardada, sin abrir un terminal ni repetir nada.
        strict = analyze(payload(), profile())
        self.assertEqual(strict["within_tolerance_trades"], 0)

        with_real_lot = analyze(
            payload(), profile(real_strategy_lots={"ROBOFOREX/ECN:27672": 0.04}),
        )
        row = with_real_lot["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(with_real_lot["within_tolerance_trades"], 1)
        self.assertEqual(row["limits"]["volume_expected_real"], 0.04)
        self.assertEqual(row["measurements"]["pnl_scale"], 0.5)

    def test_an_open_real_ticket_is_aligned_instead_of_counted_as_missing(self) -> None:
        base = payload()
        tester = dict(base["tester_trades"][0])
        open_position = {
            "strategy": tester["strategy"], "symbol": tester["symbol"],
            "side": tester["side"], "open_time": tester["open_time"],
            "open_price": tester["open_price"], "volume": 0.04,
            "position_id": 760842306,
        }
        result = analyze(
            payload(real_trades=[], open_positions_at_period_end=[open_position]),
            profile(
                real_strategy_lots={tester["strategy"]: 0.04},
                drawdown_deviation_warning_pct=1000.0,
            ),
        )

        row = result["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["real"]["position_id"], 760842306)
        self.assertEqual(result["matched_trades"], 1)
        self.assertEqual(result["open_real_trades"], 1)
        self.assertEqual(result["missing_real_trades"], 0)
        self.assertIn("1 posición(es) real(es) aún abierta(s)", result["summary"])

    def test_the_period_comes_from_the_execution_and_not_from_the_profile(self) -> None:
        # El periodo decide qué ejecutó el Strategy Tester. Dejar que lo
        # reescribiese la configuración actual produciría un resultado que dice
        # auditar unas fechas sobre operaciones de otras.
        result = analyze(
            payload(),
            profile(period_start_date="2026-01-01", period_end_date="2026-01-07", period_days=99),
        )
        self.assertEqual(result["period_start_date"], "2026-09-21")
        self.assertEqual(result["period_end_date"], "2026-09-27")
        self.assertEqual(result["period_days"], 7)
        self.assertEqual(result["period_start"], "2026-09-21T00:00:00+00:00")

    def test_the_quality_gate_still_blocks_the_comparison(self) -> None:
        result = analyze(payload(qualities=[42.0]), profile())
        self.assertEqual(result["status"], "not_comparable")
        self.assertEqual(result["matched_trades"], 0)
        self.assertIn("42.00%", result["summary"])

        missing = analyze(payload(qualities=[]), profile())
        self.assertEqual(missing["status"], "not_comparable")
        self.assertIn("no informó History Quality", missing["summary"])

    def test_the_portfolio_filter_discards_closures_of_another_lot(self) -> None:
        members = [{"candidate_id": "ROBOFOREX/ECN:27672", "symbol": "EURUSD", "lot": 0.08}]
        result = analyze(
            payload(
                selected_members=members,
                volume_rules={"eurusd": {"volume_min": 0.01, "volume_step": 0.01}},
                real_trades=[
                    {
                        "strategy": "ROBOFOREX/ECN:27672", "symbol": "EURUSD", "side": "sell",
                        "open_time": "2026-09-22T09:19:46+00:00",
                        "close_time": "2026-09-22T10:32:58+00:00",
                        "open_price": 1.14621, "close_price": 1.14, "volume": volume, "profit": 3.06,
                    }
                    for volume in (0.04, 0.33)
                ],
            ),
            profile(real_strategy_lots={"ROBOFOREX/ECN:27672": 0.04}),
        )
        self.assertEqual(result["portfolio_filter"]["portfolio_closures"], 1)
        self.assertEqual(result["portfolio_filter"]["foreign_closures_ignored"], 1)
        self.assertEqual(result["real_trades"], 1)

    def test_the_band_goes_from_the_tester_lot_to_the_configured_real_one(self) -> None:
        # Entre el lote con el que se probó y el configurado para la cuenta real
        # hay redondeos del broker y reajustes a mano: un cierre intermedio
        # sigue siendo de la estrategia. Sin lote configurado, la banda es un
        # punto y el filtro se comporta como antes.
        members = [{"candidate_id": "s1", "symbol": "EURUSD", "lot": 0.08}]
        rules = {"eurusd": (0.01, 0.01)}

        configured = portfolio_lot_bands(members, rules, {}, {"s1": 0.04})
        fallback = portfolio_lot_bands(members, rules, {}, {})

        self.assertEqual(configured, {"eurusd": (0.04, 0.08)})
        self.assertEqual(fallback, {"eurusd": (0.08, 0.08)})

    def test_a_saved_lot_below_the_broker_minimum_uses_the_effective_one(self) -> None:
        # Portafolios ICTrading guardados antes de que la construcción consumiese
        # `volume_min`: 0,03 con tres unidades y mínimo 0,1 se ejecuta a 0,1, no
        # a 0,3. Las unidades son metadato de asignación, no multiplican.
        members = [{"candidate_id": "de40", "symbol": "DE40", "lot": 0.03, "units": 3}]
        bands = portfolio_lot_bands(members, {"de40": (0.1, 0.1)}, {}, {})
        self.assertEqual(bands, {"de40": (0.1, 0.1)})

    def test_the_broker_symbol_of_the_report_is_what_the_filter_matches(self) -> None:
        # El portafolio guarda `NAS100` y el broker ejecuta `NAS100.fs`.
        members = [{"candidate_id": "s1", "symbol": "NAS100", "lot": 0.01}]
        bands = portfolio_lot_bands(members, {}, {"s1": {"nas100.fs"}}, {"s1": 0.01})
        self.assertEqual(bands, {"nas100.fs": (0.01, 0.01)})

    def test_every_operation_names_the_set_file_of_its_strategy(self) -> None:
        # El magic no dice nada a quien lee el informe; el fichero `.set` sí.
        members = [
            {"candidate_id": "s1", "symbol": "EURUSD", "lot": 0.04, "set_name": "EURUSD_H1_a.set"},
            {"candidate_id": "s2", "symbol": "EURUSD", "set_id": "/data/x/EURUSD_H4_b.set"},
        ]
        self.assertEqual(strategy_set_names(members), {
            "s1": "EURUSD_H1_a.set", "s2": "EURUSD_H4_b.set",
        })

        result = analyze(
            payload(selected_members=[{
                "candidate_id": "ROBOFOREX/ECN:27672", "symbol": "EURUSD", "lot": 0.08,
                "set_name": "EURUSD_H1_a.set",
            }]),
            profile(),
        )
        detail = result["comparison_detail"]
        self.assertEqual(
            [row["strategy_set"] for row in detail["operation_comparisons"]],
            ["EURUSD_H1_a.set"],
        )
        self.assertEqual(detail["strategy_summary"][0]["strategy_set"], "EURUSD_H1_a.set")

    def test_a_run_without_raw_material_is_rejected_instead_of_invented(self) -> None:
        for broken in ({}, {"audit_id": ""}, {"real_trades": []}):
            with self.subTest(payload=broken):
                with self.assertRaises(PayloadError):
                    analyze(broken, profile())

    def test_the_result_says_who_analysed_it_and_with_what(self) -> None:
        result = analyze(payload(), profile(volume_tolerance_pct=60.0))
        self.assertEqual(result["analysis"]["analysed_by"], "manager")
        self.assertEqual(result["analysis"]["executed_at"], "2026-09-30T23:31:04+00:00")
        self.assertEqual(result["analysis"]["tolerances"]["volume_tolerance_pct"], 60.0)
        self.assertEqual(result["audit_id"], "20261001_012810_063790")


if __name__ == "__main__":
    unittest.main()
