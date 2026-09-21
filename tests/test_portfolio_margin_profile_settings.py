from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portfolio_manager.ubs_portfolio import (
    ACCOUNT_LEVERAGE_CHOICES,
    DEFAULT_ACCOUNT_LEVERAGE,
    allocation_margin_required,
    margin_model_for_profile,
    portfolio_margin_summary,
    resolve_margin_model,
)

try:
    from .portfolio_margin_profile_fixtures import strategy
except ImportError:
    from portfolio_margin_profile_fixtures import strategy


class AccountLeverageSettingTests(unittest.TestCase):
    def test_setting_accepts_only_the_offered_choices(self) -> None:
        from mt5_manager.portfolio_service import normalize_settings

        base = {"capital": 10000, "valley_dd_pct": 10}
        for choice in ACCOUNT_LEVERAGE_CHOICES:
            settings = normalize_settings("full_history", {**base, "account_leverage": choice}, "AXI")
            self.assertEqual(settings["account_leverage"], choice)
        # Un valor fuera de la lista no revienta el formulario: cae al defecto.
        for bogus in (777, 0, -1, "", None, "mucho"):
            settings = normalize_settings("full_history", {**base, "account_leverage": bogus}, "AXI")
            self.assertEqual(settings["account_leverage"], DEFAULT_ACCOUNT_LEVERAGE)
        # Sin indicar nada, AXI arranca en 1:1000.
        self.assertEqual(
            normalize_settings("full_history", base, "AXI")["account_leverage"],
            DEFAULT_ACCOUNT_LEVERAGE,
        )

    def test_ttp_tiers_apply_through_the_model_not_only_by_name(self) -> None:
        # El tramo vivía sólo en `margin_leverage_for_profile`. Al pasar el
        # cálculo a `MarginModel`, TTP se quedó en `default_leverage` y el
        # margen del #27 salió en 51.29 donde la tabla publicada pide 4973.51.
        from portfolio_manager.ubs_portfolio import margin_leverage_for_profile

        models = (
            margin_model_for_profile("ttp"),
            resolve_margin_model("ttp"),
            # El tramo ES el requisito: el apalancamiento de cuenta no lo mueve.
            margin_model_for_profile("ttp", account_leverage=1000.0),
        )
        for model in models:
            self.assertEqual(model.leverage_for("EURUSD"), 50.0)
            self.assertEqual(model.leverage_for("US500"), 15.0)
            self.assertEqual(model.leverage_for("XAUUSD"), 10.0)
            self.assertEqual(model.leverage_for("BRENT"), 10.0)
            self.assertEqual(model.leverage_for("BTCUSD"), 2.0)
            self.assertEqual(model.leverage_for("Airbus+"), 2.0)
            # Una sola implementación: el modelo y la función por nombre no
            # pueden volver a discrepar.
            for symbol in ("EURUSD", "US500", "XAUUSD", "BRENT", "BTCUSD", "Airbus+"):
                self.assertEqual(
                    model.leverage_for(symbol),
                    margin_leverage_for_profile(symbol, margin_profile="ttp"),
                    symbol,
                )

    def test_measured_contract_size_reaches_every_profile(self) -> None:
        # El tamaño de contrato es del instrumento, no del perfil financiero.
        for profile in ("ttp", "ictrading", "roboforex", "axi"):
            model = margin_model_for_profile(
                profile, symbol_contract_size={"EURUSD": 100000.0, "XAUUSD": 100.0},
            )
            self.assertEqual(model.contract_size_for("EURUSD"), 100000.0, profile)
            self.assertEqual(model.contract_size_for("XAUUSD"), 100.0, profile)
            # Lo no medido sigue cayendo a la aproximación por grupo.
            self.assertEqual(model.contract_size_for("US500"), 1.0, profile)

    def test_ttp_forex_margin_stops_being_a_millionth_of_the_real_one(self) -> None:
        eurusd = strategy("EURUSD", 1.10)
        blind = margin_model_for_profile("ttp")
        real = margin_model_for_profile("ttp", symbol_contract_size={"EURUSD": 100000.0})

        # 0.01 lotes x 100.000 x 1.10 / 50 = 22.00
        self.assertAlmostEqual(allocation_margin_required(eurusd, 1, margin_profile=real), 22.0)
        # Sin tamaño de contrato medido queda en 0.01 x 1 x 1.10 / 50.
        self.assertAlmostEqual(
            allocation_margin_required(eurusd, 1, margin_profile=blind), 0.01 * 1.10 / 50,
        )

        summary = portfolio_margin_summary(
            [eurusd], {"EURUSD.set": 1},
            balance=5000.0, max_margin_pct=100.0, margin_profile=real,
        )
        # El aviso al usuario se redacta con estos campos, no con un texto
        # paralelo que pueda prometer una tabla que el modelo no aplica.
        self.assertEqual(summary["group_leverage_applied"], {"Forex": 50.0})
        self.assertEqual(summary["contract_size_measured"], 1)
        self.assertEqual(summary["symbol_count"], 1)
        self.assertAlmostEqual(float(summary["total"]), 22.0)
        self.assertAlmostEqual(float(summary["notional"]), 1100.0)

    def test_measured_notional_keeps_a_foreign_quote_out_of_the_margin(self) -> None:
        # USDJPY cotiza en JPY: `lote x contrato x precio` da yenes. Con el
        # contrato real eso multiplicaba el margen por el tipo de cambio.
        usdjpy = strategy("USDJPY", 161.677)
        blind = margin_model_for_profile("ttp", symbol_contract_size={"USDJPY": 100000.0})
        measured = margin_model_for_profile(
            "ttp",
            symbol_contract_size={"USDJPY": 100000.0},
            symbol_notional={"USDJPY": 865.91},
            notional_source="ictrading_symbol_specs.json",
        )

        self.assertAlmostEqual(
            allocation_margin_required(usdjpy, 3, margin_profile=blind),
            0.03 * 100000.0 * 161.677 / 50,
        )
        self.assertAlmostEqual(
            allocation_margin_required(usdjpy, 3, margin_profile=measured),
            3 * 865.91 / 50,
        )
        # Tres ordenes de magnitud entre una cifra y la otra.
        self.assertGreater(
            allocation_margin_required(usdjpy, 3, margin_profile=blind),
            allocation_margin_required(usdjpy, 3, margin_profile=measured) * 100,
        )

    def test_build_margin_model_only_measures_notional_for_axi(self) -> None:
        import sqlite3
        import contextlib

        from mt5_manager.portfolio_service import build_margin_model, PortfolioSource

        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            with contextlib.closing(
                sqlite3.connect(project / "outputs" / "ubs_memory_AXI_STANDARD.sqlite")
            ) as conn:
                conn.execute("create table candidates(id integer primary key)")
                conn.commit()
            (project / "assets" / "axi_normalization.json").write_text(
                json.dumps({"reference_notional": 1000.0, "symbol_net_profit_factors": {"EURUSD": 1.0}}),
                encoding="utf-8",
            )
            (project / "assets" / "axi_symbol_specs.json").write_text(
                json.dumps({"account_leverage": 100, "symbols": {"EURUSD.sa": {"margin_min_lot": 11.47}}}),
                encoding="utf-8",
            )
            (project / "assets" / "axi_max_product_leverage.json").write_text(
                json.dumps({"max_product_leverage": {"EURUSD.sa": 1000}}), encoding="utf-8",
            )
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "AXI",
                "portfolio_account_type": "STANDARD",
            })

            axi = build_margin_model(source, {"margin_profile": "axi", "account_leverage": 500.0})
            self.assertEqual(axi.reference_account_leverage, 100.0)
            self.assertEqual(axi.account_leverage, 500.0)
            self.assertEqual(axi.max_product_leverage["EURUSD"], 1000.0)
            # Medido a 1:100 y pedido 1:500 -> una quinta parte de margen.
            self.assertAlmostEqual(axi.margin_for_one("EURUSD"), 11.47 / 5)

            # Mismos ficheros, otro perfil: nada cambia para él.
            legacy = build_margin_model(source, {"margin_profile": "roboforex", "account_leverage": 500.0})
            self.assertIsNone(legacy.margin_for_one("EURUSD"))
            self.assertIsNone(legacy.notional_for("EURUSD"))
            self.assertIsNone(legacy.account_leverage)
            self.assertEqual(legacy.leverage_for("EURUSD"), 500.0)

    @staticmethod
    def _ictrading_source(project: Path):
        import contextlib
        import sqlite3

        from mt5_manager.portfolio_service import PortfolioSource

        (project / "outputs").mkdir()
        (project / "assets").mkdir()
        with contextlib.closing(
            sqlite3.connect(project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite")
        ) as conn:
            conn.execute("create table candidates(id integer primary key)")
            conn.commit()
        (project / "assets" / "ictrading_symbol_specs.json").write_text(
            json.dumps({"symbols": {
                "USTEC": {
                    "volume_min": 0.1, "volume_step": 0.1,
                    "margin_min_lot": 12.86, "contract_size": 1.0,
                },
                "JP225": {"volume_min": 1.0, "volume_step": 1.0},
                "GBPUSD": {"contract_size": 100000.0},
                "USDJPY": {
                    "volume_min": 0.01, "contract_size": 100000.0,
                    "notional_min_lot": 865.91,
                },
            }}),
            encoding="utf-8",
        )
        return PortfolioSource({
            "portfolio_project_dir": str(project),
            "portfolio_broker": "ICTRADING",
            "portfolio_account_type": "STANDARD",
        })

    def _assert_ictrading_profile(self, source, profile: str, scope: str) -> None:
        from portfolio_manager.ubs_portfolio import (
            _execution_plan_allocations, execution_units_from_step,
        )
        from mt5_manager.portfolio_service import (
            _optimizer_kwargs, build_margin_model, normalize_settings,
        )

        inputs = normalize_settings(
            scope, {"capital": 5000, "margin_profile": profile}, "ICTRADING",
        )
        model = build_margin_model(source, inputs)
        inputs["margin_model"] = model
        self.assertIs(
            _optimizer_kwargs(inputs, "balanced", [], 10)["limits"].margin_profile,
            model,
        )
        self.assertEqual(model.profile, profile)
        self.assertEqual(model.min_lot_for("USTEC"), 0.1)
        self.assertEqual(model.lot_size_for("USTEC", 2), 0.2)
        self.assertEqual(model.lot_increments_for("USTEC"), 10)
        self.assertEqual(model.lot_size_for("JP225", 2), 2.0)
        self.assertIsNone(model.margin_for_one("USTEC"))
        self.assertIsNone(model.notional_for("USTEC"))
        self.assertEqual(model.contract_size_for("GBPUSD"), 100000.0)
        self.assertEqual(model.contract_size_for("USTEC"), 1.0)
        self.assertAlmostEqual(model.notional_for("USDJPY"), 865.91)
        self.assertIsNone(model.notional_for("USTEC"))
        sets = [
            strategy("USTEC", 20000), strategy("EURUSD", 1.1),
            strategy("JP225", 40000),
        ]
        units, steps = _execution_plan_allocations(
            sets, {"USTEC.set": 3, "EURUSD.set": 3, "JP225.set": 1}, 5000, model,
        )
        self.assertEqual(units["USTEC.set"], 3)
        expected = {"USTEC.set": .3, "EURUSD.set": .03, "JP225.set": 1.0}
        for key, lot in expected.items():
            self.assertAlmostEqual(
                execution_units_from_step(5000, steps[key]) * .01, lot,
            )

    def test_ictrading_uses_terminal_volume_min_without_enabling_axi_margin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._ictrading_source(Path(temp_dir))
            for profile in ("ictrading", "ttp", "roboforex"):
                for scope in ("full_history", "monthly"):
                    with self.subTest(profile=profile, scope=scope):
                        self._assert_ictrading_profile(source, profile, scope)


    def test_every_broker_keeps_its_minimum_lot_under_every_margin_profile(self) -> None:
        import contextlib
        import sqlite3

        from mt5_manager.portfolio_service import PortfolioSource, build_margin_model
        from portfolio_manager.ubs_portfolio import _execution_plan_allocations, execution_units_from_step

        # Deliberately different minima for the same symbol: selecting a margin
        # profile must never select another broker's execution specifications.
        minima = {"ICTRADING": 0.1, "AXI": 1.0, "ROBOFOREX": 0.01}
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            for broker, minimum in minima.items():
                with contextlib.closing(sqlite3.connect(
                    project / "outputs" / f"ubs_memory_{broker}_STANDARD.sqlite"
                )) as conn:
                    conn.execute("create table candidates(id integer primary key)")
                    conn.commit()
                (project / "assets" / f"{broker.lower()}_symbol_specs.json").write_text(
                    json.dumps({"symbols": {"TEST": {"volume_min": minimum}}}),
                    encoding="utf-8",
                )

            for broker, minimum in minima.items():
                source = PortfolioSource({
                    "portfolio_project_dir": str(project),
                    "portfolio_broker": broker,
                    "portfolio_account_type": "STANDARD",
                })
                for profile in ("ictrading", "axi", "roboforex", "ttp"):
                    with self.subTest(broker=broker, profile=profile):
                        model = build_margin_model(source, {"margin_profile": profile})
                        self.assertEqual(model.profile, profile)
                        self.assertEqual(model.min_lot_for("TEST"), minimum)
                        executable, steps = _execution_plan_allocations(
                            [strategy("TEST", 100)], {"TEST.set": 2}, 100000, model,
                        )
                        self.assertEqual(executable["TEST.set"], 2)
                        self.assertAlmostEqual(model.lot_size_for("TEST", 2), 2 * minimum)
                        self.assertAlmostEqual(
                            execution_units_from_step(100000, steps["TEST.set"]) * .01,
                            2 * minimum,
                        )

    def test_choices_and_default_match_the_form(self) -> None:
        self.assertEqual(ACCOUNT_LEVERAGE_CHOICES, (1000.0, 500.0, 100.0))
        self.assertEqual(DEFAULT_ACCOUNT_LEVERAGE, 1000.0)

        # Los tres ambitos construyen ya el modelo de margen medido, asi que los
        # tres tienen que poder decir con que apalancamiento se mide la cuenta:
        # si uno se queda con el valor por defecto, la misma cuenta valida el
        # margen con dos numeros distintos segun la pantalla.
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        for name in ("portfolios.html", "portfolios_monthly.html", "portfolios_grid.html"):
            page = (static_dir / name).read_text(encoding="utf-8")
            self.assertIn('name="account_leverage"', page, name)
            for choice in ACCOUNT_LEVERAGE_CHOICES:
                self.assertIn(f'<option value="{int(choice)}">1:{int(choice)}</option>', page, name)
        for name in ("portfolios.js", "portfolios_monthly.js", "portfolios_grid.js"):
            script = (static_dir / name).read_text(encoding="utf-8")
            self.assertIn("'account_leverage'", script, name)
