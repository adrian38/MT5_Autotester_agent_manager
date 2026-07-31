from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from portfolio_manager.ubs_portfolio import (
    ACCOUNT_LEVERAGE_CHOICES,
    AXI_FALLBACK_GROUP_LEVERAGE,
    DEFAULT_ACCOUNT_LEVERAGE,
    ClosedTrade,
    PeriodReport,
    allocation_margin_required,
    build_robust_strategy_set,
    load_max_product_leverage,
    load_symbol_specs,
    load_symbol_notional,
    margin_model_for_profile,
    portfolio_margin_summary,
    resolve_margin_model,
)


def period(symbol: str, name: str, start_year: int, end_year: int, price: float) -> PeriodReport:
    trade = ClosedTrade(
        open_time=datetime(start_year, 1, 1),
        close_time=datetime(start_year, 1, 2),
        symbol=symbol,
        volume=0.01,
        profit=10.0,
        open_price=price,
        close_price=price,
    )
    return replace(
        PeriodReport(
            period_name=name,
            start_year=start_year,
            end_year=end_year,
            symbol=symbol,
            timeframe="H1",
            pnl_curve_001=[0.0, 100.0],
            net_profit_001=100.0,
            valley_dd_001=0.0,
            point_dd_001=0.0,
            profit_factor=2.0,
            return_dd_ratio=100.0,
            trades=100,
            balance_dd_metric_001=10.0,
            equity_dd_metric_001=10.0,
        ),
        closed_trades=[trade],
    )


def strategy(symbol: str, price: float):
    return build_robust_strategy_set(
        set_id=f"{symbol}.set",
        candidate_id=symbol,
        symbol=symbol,
        timeframe="H1",
        strategy_family="test",
        robustness_status="accepted",
        already_used=False,
        report_2020_2024=period(symbol, "2020_2024", 2020, 2024, price),
        report_2025_2026=period(symbol, "2025_2026", 2025, 2026, price),
    )


class MarginProfileTests(unittest.TestCase):
    def axi(self, **kwargs):
        """Modelo AXI con el margen medido de una cuenta 1:100, como la real."""
        defaults = dict(
            reference_account_leverage=100.0,
            symbol_margin={"EURUSD": 11.47, "XAUUSD": 40.25, "AIRBUS+": 11.66, "BTCUSD": 6.32},
            max_product_leverage={
                "EURUSD": 1000.0, "XAUUSD": 1000.0, "AIRBUS+": 25.0, "BTCUSD": 200.0,
            },
            margin_source="axi_symbol_specs.json",
        )
        defaults.update(kwargs)
        return margin_model_for_profile("axi", **defaults)

    def test_the_account_leverage_applies_to_every_group(self) -> None:
        # Al apalancamiento de medida no cambia nada.
        same = self.axi(account_leverage=100.0)
        self.assertAlmostEqual(same.margin_for_one("EURUSD"), 11.47)
        self.assertAlmostEqual(same.margin_for_one("BTCUSD"), 6.32)
        self.assertAlmostEqual(same.margin_for_one("AIRBUS+"), 11.66)

        wide = self.axi(account_leverage=1000.0)
        # Forex: tope 1000, medido a 100 -> el margen baja diez veces.
        self.assertAlmostEqual(wide.margin_for_one("EURUSD"), 11.47 / 10)
        self.assertAlmostEqual(wide.margin_for_one("XAUUSD"), 40.25 / 10)
        # Cripto: tope 200. La cuenta ya no ata, pero el producto sí -> la mitad,
        # no la décima parte. Antes se quedaba clavado y era el error.
        self.assertAlmostEqual(wide.margin_for_one("BTCUSD"), 6.32 / 2)
        # Acción con tope 1:25: el producto ataba ya a 1:100, así que no se mueve.
        self.assertAlmostEqual(wide.margin_for_one("AIRBUS+"), 11.66)

    def test_a_tighter_account_raises_the_margin_of_everything_it_binds(self) -> None:
        # Bajar a 1:100 desde una medida a 1:1000 encarece lo que la cuenta ate.
        model = self.axi(
            account_leverage=100.0,
            reference_account_leverage=1000.0,
            symbol_margin={"EURUSD": 1.147, "AIRBUS+": 11.66},
        )
        self.assertAlmostEqual(model.margin_for_one("EURUSD"), 1.147 * 10)
        # La acción seguía topada por producto en ambos casos.
        self.assertAlmostEqual(model.margin_for_one("AIRBUS+"), 11.66)

    def test_the_product_cap_stops_the_account_leverage(self) -> None:
        # XPTUSD tope 500: pedir 1:1000 no baja el margen más allá de 500.
        model = self.axi(
            account_leverage=1000.0,
            symbol_margin={"XPTUSD": 50.0},
            max_product_leverage={"XPTUSD": 500.0},
        )
        self.assertAlmostEqual(model.margin_for_one("XPTUSD"), 50.0 / 5)

        # Un símbolo sin tope publicado usa el de su grupo, no el infinito.
        fallback = self.axi(
            account_leverage=1000.0, symbol_margin={"AEDUSD": 10.0}, max_product_leverage={},
        )
        expected = 100.0 / AXI_FALLBACK_GROUP_LEVERAGE["Forex"]
        self.assertAlmostEqual(fallback.margin_for_one("AEDUSD"), 10.0 * expected)

    def test_other_profiles_keep_the_legacy_margin_exactly(self) -> None:
        for profile in ("ictrading", "roboforex"):
            model = margin_model_for_profile(profile, account_leverage=100.0)
            self.assertEqual(model.leverage_for("EURUSD"), 500.0)
            self.assertEqual(model.leverage_for("Airbus+"), 20.0)
            self.assertEqual(model.contract_size_for("EURUSD"), 1.0)
            self.assertEqual(model.contract_size_for("Airbus+"), 100.0)
            # Ni el apalancamiento de cuenta ni el margen medido se cuelan.
            self.assertIsNone(model.account_leverage)
            self.assertEqual(model.symbol_margin, {})

    def test_ttp_still_answers_through_the_string_profile(self) -> None:
        model = resolve_margin_model("ttp")
        self.assertEqual(model.profile, "ttp")
        # resolve_margin_model no reimplementa TTP: se sigue leyendo por nombre.
        from portfolio_manager.ubs_portfolio import margin_leverage_for_profile

        self.assertEqual(margin_leverage_for_profile("XAUUSD", margin_profile="ttp"), 10.0)

    def test_measured_margin_replaces_the_price_estimate(self) -> None:
        eurusd = strategy("EURUSD", 1.10)
        # Sin datos: 0.01 lotes x contrato 1 x precio 1.10 / 500 -> ridículo.
        legacy = allocation_margin_required(eurusd, 1, margin_profile="axi")
        self.assertAlmostEqual(legacy, 0.01 * 1.0 * 1.10 / 500.0)

        # Con el margen del terminal, tres unidades son tres veces esa cifra.
        model = self.axi(account_leverage=100.0)
        measured = allocation_margin_required(eurusd, 3, margin_profile=model)
        self.assertAlmostEqual(measured, 3 * 11.47)
        self.assertGreater(measured, legacy * 1000)

    def test_notional_file_is_the_fallback_when_the_terminal_has_no_margin(self) -> None:
        eurusd = strategy("EURUSD", 1.10)
        model = margin_model_for_profile(
            "axi", account_leverage=500.0, symbol_notional={"EURUSD": 1000.0},
            max_product_leverage={"EURUSD": 1000.0},
        )
        # Sin symbol_margin cae al nocional dividido por el apalancamiento efectivo.
        self.assertIsNone(model.margin_for_one("EURUSD"))
        self.assertAlmostEqual(
            allocation_margin_required(eurusd, 2, margin_profile=model), 2 * 1000.0 / 500.0
        )

    def test_group_notional_covers_symbols_that_mt5_could_not_measure(self) -> None:
        model = margin_model_for_profile(
            "axi", symbol_notional={"EURUSD": 1000.0}, group_notional={"Metals": 4000.0}
        )
        self.assertEqual(model.notional_for("EURUSD"), 1000.0)
        self.assertEqual(model.notional_for("XAUUSD"), 4000.0)
        self.assertIsNone(model.notional_for("NAS100"))

    def test_summary_reports_the_profile_and_flags_unmeasured_symbols(self) -> None:
        sets = [strategy("EURUSD", 1.10), strategy("NAS100", 20000.0)]
        model = self.axi(account_leverage=100.0)
        summary = portfolio_margin_summary(
            sets,
            {"EURUSD.set": 2, "NAS100.set": 1},
            balance=10000.0,
            max_margin_pct=100.0,
            margin_profile=model,
        )
        self.assertEqual(summary["profile"], "axi")
        self.assertEqual(summary["account_leverage"], 100.0)
        self.assertEqual(summary["reference_account_leverage"], 100.0)
        self.assertEqual(summary["margin_source"], "axi_symbol_specs.json")
        # NAS100 no está en el volcado: se avisa en vez de fingir una cifra.
        self.assertEqual(summary["unmeasured_symbols"], ["NAS100"])
        self.assertTrue(summary["by_set"]["EURUSD.set"]["margin_measured"])
        self.assertFalse(summary["by_set"]["NAS100.set"]["margin_measured"])
        self.assertAlmostEqual(summary["by_set"]["EURUSD.set"]["margin"], 2 * 11.47)


class SavedPortfolioAuditTests(unittest.TestCase):
    """Defectos vistos auditando el portafolio #2 ya guardado."""

    def axi_measured(self):
        # Cifras reales del volcado del terminal AXI (cuenta 1:100).
        return margin_model_for_profile(
            "axi",
            account_leverage=1000.0,
            reference_account_leverage=100.0,
            symbol_margin={"XAUUSD": 40.25, "USDJPY": 10.0, "SPDR_SP500+": 37.11},
            symbol_min_lot={"XAUUSD": 0.01, "USDJPY": 0.01, "SPDR_SP500+": 1.0},
            symbol_contract_size={"XAUUSD": 100.0, "USDJPY": 100000.0, "SPDR_SP500+": 1.0},
            max_product_leverage={"XAUUSD": 1000.0, "USDJPY": 1000.0, "SPDR_SP500+": 20.0},
        )

    def test_reported_leverage_never_exceeds_the_product_cap(self) -> None:
        # Antes se derivaba de nocional/margen con el nocional de otra fecha y
        # salian 1:1013 en el oro o 1:205 en cripto, que no existen.
        model = self.axi_measured()
        self.assertEqual(model.leverage_for("XAUUSD"), 1000.0)
        self.assertEqual(model.leverage_for("USDJPY"), 1000.0)
        self.assertEqual(model.leverage_for("SPDR_SP500+"), 20.0)

    def test_contract_size_comes_from_the_terminal_not_from_the_group(self) -> None:
        model = self.axi_measured()
        # La aproximacion por grupo daba 1 para forex y metales, y 100 para acciones.
        self.assertEqual(model.contract_size_for("USDJPY"), 100000.0)
        self.assertEqual(model.contract_size_for("XAUUSD"), 100.0)
        self.assertEqual(model.contract_size_for("SPDR_SP500+"), 1.0)
        # Sin medir se mantiene la aproximacion por grupo: acciones 100, resto 1.
        self.assertEqual(model.contract_size_for("Airbus+"), 100.0)
        self.assertEqual(model.contract_size_for("EURUSD"), 1.0)

    def test_the_margin_warning_does_not_claim_a_rule_that_no_longer_applies(self) -> None:
        # El aviso se persiste en el portafolio. Decia "solo en forex y bullion",
        # de cuando el apalancamiento de cuenta estaba restringido a esos grupos,
        # y se quedo desactualizado tras corregir el modelo.
        source = (
            Path(__file__).parents[1] / "portfolio_manager" / "ubs_portfolio.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("solo en forex y bullion", source)
        self.assertIn("min(cuenta, tope)", source)

    def test_total_lot_adds_real_lots_not_units_times_one_cent(self) -> None:
        from dataclasses import fields as dc_fields

        from portfolio_manager.ubs_portfolio import StrategyAllocation

        model = self.axi_measured()
        # 4 uds de oro, 4 de USDJPY y 3 de una accion de lote minimo 1.0.
        lots = [
            model.lot_size_for("XAUUSD", 4),
            model.lot_size_for("USDJPY", 4),
            model.lot_size_for("SPDR_SP500+", 3),
        ]
        self.assertEqual(lots, [0.04, 0.04, 3.0])
        # El viejo calculo daba 11 x 0.01 = 0.11 para las mismas 11 unidades.
        self.assertAlmostEqual(round(sum(lots), 2), 3.08)
        self.assertNotAlmostEqual(round(sum(lots), 2), 11 * 0.01)
        # StrategyAllocation.lot es lo que suma el total, así que debe existir.
        self.assertIn("lot", [f.name for f in dc_fields(StrategyAllocation)])


class SavePayloadTests(unittest.TestCase):
    def test_the_margin_model_never_reaches_the_saved_payload(self) -> None:
        """El payload de guardado se serializa a JSON y viaja al nodo.

        Un ``MarginModel`` colado en ``inputs`` rompia el POST entero con
        "Object of type MarginModel is not JSON serializable", que en pantalla
        solo se veia como "failed to fetch".
        """
        from mt5_manager.portfolio_service import RUNTIME_ONLY_INPUT_KEYS, settings_inputs

        model = margin_model_for_profile("axi", account_leverage=1000.0)
        inputs = {"capital": 10000, "margin_profile": "axi", "account_leverage": 1000.0,
                  "margin_model": model}

        cleaned = settings_inputs(inputs)

        self.assertNotIn("margin_model", cleaned)
        self.assertEqual(cleaned["account_leverage"], 1000.0)
        self.assertEqual(cleaned["margin_profile"], "axi")
        # Lo que queda tiene que poder serializarse tal cual.
        json.dumps(cleaned)
        self.assertIn("margin_model", RUNTIME_ONLY_INPUT_KEYS)

    def test_every_proposal_builder_filters_the_runtime_keys(self) -> None:
        # Guardia contra volver a colar un dict(inputs) crudo en una propuesta.
        for module in ("portfolio_service", "portfolio_monthly_service"):
            source = (
                Path(__file__).parents[1] / "mt5_manager" / f"{module}.py"
            ).read_text(encoding="utf-8")
            self.assertNotIn("proposal_inputs = dict(inputs)", source, module)


class MinimumLotExecutionTests(unittest.TestCase):
    """El lote minimo real del simbolo, no 0.01, es la unidad del portafolio."""

    def model(self, **min_lots):
        return margin_model_for_profile("axi", symbol_min_lot=min_lots)

    def test_one_unit_is_one_minimum_position(self) -> None:
        model = self.model(EURUSD=0.01, AIRBUS=1.0, ETHUSD=0.1)
        self.assertEqual(model.lot_size_for("EURUSD", 3), 0.03)
        self.assertEqual(model.lot_size_for("AIRBUS", 3), 3.0)
        self.assertEqual(model.lot_size_for("ETHUSD", 3), 0.3)
        # Sin medir se mantiene el supuesto histórico de 0.01.
        self.assertEqual(model.lot_size_for("DESCONOCIDO", 3), 0.03)

    def test_increments_translate_units_into_ea_steps(self) -> None:
        model = self.model(EURUSD=0.01, AIRBUS=1.0, ETHUSD=0.1, BIGONE=100.0)
        self.assertEqual(model.lot_increments_for("EURUSD"), 1)
        self.assertEqual(model.lot_increments_for("AIRBUS"), 100)
        self.assertEqual(model.lot_increments_for("ETHUSD"), 10)
        self.assertEqual(model.lot_increments_for("BIGONE"), 10000)
        self.assertEqual(model.lot_increments_for("DESCONOCIDO"), 1)

    def test_exported_step_delivers_the_units_the_portfolio_counted(self) -> None:
        from portfolio_manager.ubs_portfolio import (
            _execution_plan_allocations,
            execution_units_from_step,
        )

        capital = 100000.0
        sets = [strategy("EURUSD", 1.10), strategy("AIRBUS", 200.0)]
        allocations = {"EURUSD.set": 4, "AIRBUS.set": 4}
        model = self.model(EURUSD=0.01, AIRBUS=1.0)

        executable, steps = _execution_plan_allocations(sets, allocations, capital, model)
        self.assertEqual(executable["EURUSD.set"], 4)
        self.assertEqual(executable["AIRBUS.set"], 4)

        # Lo que el EA acabará pidiendo con ese step, en lotes.
        eurusd_lot = execution_units_from_step(capital, steps["EURUSD.set"]) * 0.01
        airbus_lot = execution_units_from_step(capital, steps["AIRBUS.set"]) * 0.01
        self.assertAlmostEqual(eurusd_lot, 0.04)
        self.assertAlmostEqual(airbus_lot, 4.0)

        # Sin el modelo (perfiles que no son AXI) el step de Airbus pide 0.04
        # lotes, que MT5 sube a su mínimo de 1.0: cuatro unidades ejecutadas
        # como una. Ese era el fallo.
        _, legacy_steps = _execution_plan_allocations(sets, allocations, capital, None)
        legacy_lot = execution_units_from_step(capital, legacy_steps["AIRBUS.set"]) * 0.01
        self.assertAlmostEqual(legacy_lot, 0.04)

    def test_units_that_do_not_fit_the_capital_are_rounded_down_not_faked(self) -> None:
        from portfolio_manager.ubs_portfolio import _execution_plan_allocations

        # 10.000 de capital y un mínimo de 1.0 lote: el step entero del EA no
        # llega a expresar 3 posiciones completas, así que se bajan a 2.
        sets = [strategy("AIRBUS", 200.0)]
        model = self.model(AIRBUS=1.0)
        executable, _ = _execution_plan_allocations(sets, {"AIRBUS.set": 3}, 10000.0, model)
        self.assertEqual(executable["AIRBUS.set"], 2)


class SymbolMarginLoaderTests(unittest.TestCase):
    def test_terminal_dump_gives_margin_and_reference_leverage(self) -> None:
        payload = {
            "account_leverage": 100,
            "symbols": {
                "EURUSD.sa": {"margin_min_lot": 11.47, "volume_min": 0.01},
                "USTECH.sa": {"margin_min_lot": 282.53, "volume_min": 1.0},
                "EURUSD": {"margin_min_lot": 5.0},          # colapsa: gana el mayor
                "SINPRECIO.sa": {"margin_min_lot": None},   # el terminal no pudo
                "ROTO.sa": {"margin_min_lot": "x"},
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "axi_symbol_specs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            margins, min_lots, contract_sizes, reference, source = load_symbol_specs(path)

        self.assertEqual(reference, 100.0)
        self.assertEqual(source, str(path))
        self.assertAlmostEqual(margins["EURUSD"], 11.47)
        self.assertAlmostEqual(margins["USTECH"], 282.53)
        self.assertNotIn("SINPRECIO", margins)
        self.assertNotIn("ROTO", margins)
        # El lote mínimo se lee aunque el margen falte, y viceversa.
        self.assertAlmostEqual(min_lots["EURUSD"], 0.01)
        self.assertAlmostEqual(min_lots["USTECH"], 1.0)

    def test_product_leverage_keeps_the_lowest_cap_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "axi_max_product_leverage.json"
            path.write_text(
                json.dumps({"max_product_leverage": {
                    "EURUSD.sa": 1000, "EURUSD": 500, "XPTUSD.sa": 500, "MALO": "x", "CERO": 0,
                }}),
                encoding="utf-8",
            )
            caps = load_max_product_leverage(path)

        self.assertEqual(caps["EURUSD"], 500.0)
        self.assertEqual(caps["XPTUSD"], 500.0)
        self.assertNotIn("MALO", caps)
        self.assertNotIn("CERO", caps)

    def test_missing_files_degrade_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "no-existe.json"
            self.assertEqual(load_symbol_specs(missing), ({}, {}, {}, None, ""))
            self.assertEqual(load_max_product_leverage(missing), {})


class SymbolNotionalLoaderTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "reference_notional": 1000.0,
            "min_notional": 100.0,
            "symbol_net_profit_factors": {
                "USDJPY.SA": 1.0,        # 1.000 de nocional
                "XAUUSD.SA": 0.2453,     # ~4.077
                "USDJPY": 2.0,           # colapsa con USDJPY.SA -> gana el mayor
                "WYNNRESORT+": 10.0,     # topado: nocional <= min_notional
                "ROTO": "no-numerico",
                "CERO": 0.0,
            },
            "group_net_profit_factors": {"Metals": 0.2453, "Stocks": 10.0},
        }

    def test_factors_are_inverted_into_notional_per_minimum_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "axi_normalization.json"
            path.write_text(json.dumps(self.payload()), encoding="utf-8")
            by_symbol, by_group, source = load_symbol_notional(path)

        self.assertEqual(source, str(path))
        # Suffix stripping colapsa .SA sobre la clave del portafolio.
        self.assertAlmostEqual(by_symbol["USDJPY"], 1000.0)
        self.assertAlmostEqual(by_symbol["XAUUSD"], 1000.0 / 0.2453, places=2)
        # El tope del factor devuelve min_notional: sobreestima, que es el lado seguro.
        self.assertAlmostEqual(by_symbol["WYNNRESORT+"], 100.0)
        # Entradas rotas o a cero no entran.
        self.assertNotIn("ROTO", by_symbol)
        self.assertNotIn("CERO", by_symbol)
        self.assertAlmostEqual(by_group["Metals"], 1000.0 / 0.2453, places=2)

    def test_missing_or_broken_file_degrades_to_the_legacy_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "no-existe.json"
            self.assertEqual(load_symbol_notional(missing), ({}, {}, ""))

            broken = Path(temp_dir) / "roto.json"
            broken.write_text("{no es json", encoding="utf-8")
            self.assertEqual(load_symbol_notional(broken), ({}, {}, ""))

            no_reference = Path(temp_dir) / "sin_ref.json"
            no_reference.write_text(json.dumps({"symbol_net_profit_factors": {"A": 1}}), encoding="utf-8")
            self.assertEqual(load_symbol_notional(no_reference), ({}, {}, ""))


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

    def test_choices_and_default_match_the_form(self) -> None:
        self.assertEqual(ACCOUNT_LEVERAGE_CHOICES, (1000.0, 500.0, 100.0))
        self.assertEqual(DEFAULT_ACCOUNT_LEVERAGE, 1000.0)

        page = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.html"
        ).read_text(encoding="utf-8")
        for choice in ACCOUNT_LEVERAGE_CHOICES:
            self.assertIn(f'<option value="{int(choice)}">1:{int(choice)}</option>', page)

        # El mensual no expone el selector: el alcance de hoy es solo el UBS.
        monthly = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios_monthly.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("account_leverage", monthly)


if __name__ == "__main__":
    unittest.main()
