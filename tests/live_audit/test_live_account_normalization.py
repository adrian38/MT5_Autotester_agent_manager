"""A qué lote se presenta la cuenta real, y quién lo decide.

Antes lo decidía un mapa cableado por cuenta y símbolo. Ahora lo decide el
usuario en la simulación de lotes de Análisis, y estas pruebas comprueban lo
que no puede separarse: que las dos mitades de la tarjeta —el dinero de la
cuenta y la promesa del backtest— se midan siempre al mismo lote.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from portfolio_manager.mt5_report import RawDeal
from mt5_manager import (
    live_account_expectation, live_account_ingest, live_account_presentation,
    live_account_report, live_account_watch,
)
from mt5_manager.live_account_archive import LiveAccountArchive

# Medido el 2026-10-24 sobre `audit-67-muzhv8jq-1` de AXI, el uso de la cuenta
# 10010194: el tester corrió BTCUSD a 0,03 y la cuenta lo ejecutó a 0,10; en
# ETHUSD los dos lados corrieron 0,10.
MEMBERS = [
    {"candidate_id": "AXI/STANDARD:21175", "symbol": "BTCUSD", "lot": 0.03},
    {"candidate_id": "AXI/STANDARD:46978", "symbol": "ETHUSD", "lot": 0.1},
    {"candidate_id": "AXI/STANDARD:3813", "symbol": "XAUUSD", "lot": 0.01},
]


def comparison(strategy: str, symbol: str, tester: float, real: float) -> dict:
    return {
        "strategy": strategy,
        "tester": {"strategy": strategy, "symbol": symbol, "volume": tester},
        "real": {"symbol": symbol, "volume": real},
    }


DETAIL = {"operation_comparisons": [
    comparison("AXI/STANDARD:21175", "BTCUSD.sa", 0.03, 0.1),
    comparison("AXI/STANDARD:46978", "ETHUSD.sa", 0.1, 0.1),
    comparison("AXI/STANDARD:3813", "XAUUSD.sa", 0.01, 0.01),
]}


def deal(symbol: str, volume: float, profit: float) -> RawDeal:
    return RawDeal(
        timestamp=datetime(2026, 10, 20, 12), ticket="1", symbol=symbol,
        trade_type="buy", direction="out", volume=volume, price=1.0, order="1",
        commission=-1.0, swap=-2.0, profit=profit, balance=0.0, comment="",
    )


class PresentationLotsTests(unittest.TestCase):
    def test_with_the_tester_marked_each_close_drops_to_the_backtest_lot(self) -> None:
        use = live_account_presentation.use_lots(
            live_account_presentation.TESTER_LEADS, MEMBERS, DETAIL,
        )
        # El símbolo lo pone el miembro —`BTCUSD`, como el informe nativo— y el
        # número la auditoría, que lo nombra `BTCUSD.sa`.
        self.assertEqual(use.symbols, {"BTCUSD": 0.03, "ETHUSD": 0.1, "XAUUSD": 0.01})
        self.assertEqual(use.members, {
            "AXI/STANDARD:21175": 0.03, "AXI/STANDARD:46978": 0.1,
            "AXI/STANDARD:3813": 0.01,
        })

    def test_with_the_real_column_marked_nothing_is_scaled_but_the_promise(self) -> None:
        # La cuenta ya está a lo que ejecutó: lo que se corrige es la promesa,
        # que se medía al lote declarado y pasa a medirse al ejecutado.
        use = live_account_presentation.use_lots(
            live_account_presentation.REAL_LEADS, MEMBERS, DETAIL,
        )
        self.assertEqual(use.symbols, {})
        self.assertEqual(use.members["AXI/STANDARD:21175"], 0.1)

    def test_without_a_marked_column_nothing_is_presented(self) -> None:
        use = live_account_presentation.use_lots("", MEMBERS, DETAIL)
        self.assertEqual((use.symbols, use.members), ({}, {}))

    def test_without_an_audit_the_tester_lot_is_the_one_the_portfolio_declares(self) -> None:
        # El lote del backtest lo declara el propio portafolio; el que la
        # cuenta ejecutó, no, y no se inventa.
        tester = live_account_presentation.use_lots(
            live_account_presentation.TESTER_LEADS, MEMBERS, {},
        )
        self.assertEqual(tester.symbols, {"BTCUSD": 0.03, "ETHUSD": 0.1, "XAUUSD": 0.01})
        real = live_account_presentation.use_lots(
            live_account_presentation.REAL_LEADS, MEMBERS, {},
        )
        self.assertEqual(real.members, {})

    def test_a_symbol_two_strategies_disagree_on_is_left_as_executed(self) -> None:
        """Y con él su promesa, o las dos mitades quedarían a escalas distintas.

        Un cierre del informe nativo no dice de qué estrategia es, así que el
        único puente es el símbolo: con dos lotes no hay uno al que llevarlo.
        """
        members = [*MEMBERS, {
            "candidate_id": "AXI/STANDARD:99", "symbol": "BTCUSD", "lot": 0.05,
        }]
        use = live_account_presentation.use_lots(
            live_account_presentation.TESTER_LEADS, members, DETAIL,
        )
        self.assertNotIn("BTCUSD", use.symbols)
        self.assertNotIn("AXI/STANDARD:21175", use.members)
        self.assertNotIn("AXI/STANDARD:99", use.members)
        self.assertEqual(use.symbols, {"ETHUSD": 0.1, "XAUUSD": 0.01})
        self.assertTrue(any("BTCUSD" in warning for warning in use.warnings))

    def test_a_strategy_that_changed_lot_mid_period_has_no_single_lot(self) -> None:
        detail = {"operation_comparisons": [
            comparison("AXI/STANDARD:21175", "BTCUSD.sa", 0.03, 0.1),
            comparison("AXI/STANDARD:21175", "BTCUSD.sa", 0.06, 0.2),
        ]}
        use = live_account_presentation.use_lots(
            live_account_presentation.TESTER_LEADS, MEMBERS[:1], detail,
        )
        self.assertEqual(use.symbols, {})
        self.assertEqual(use.members, {})

    def test_the_strategies_without_a_single_lot_are_said_once(self) -> None:
        """Tres miembros sin medir son un aviso, no tres.

        Lo que la tarjeta tiene que decir es que no se escaló nada; repetir el
        `candidate_id` de cada uno no añade nada y tapa la nota de al lado.
        """
        use = live_account_presentation.use_lots(
            live_account_presentation.REAL_LEADS, MEMBERS, {},
        )
        self.assertEqual(
            use.warnings,
            ("3 estrategias sin un lote único que presentar: se dejan como se "
             "ejecutaron.",),
        )

    def test_with_autolot_the_missing_lot_is_not_an_anomaly(self) -> None:
        # El saldo decide el lote trato a trato: ninguna estrategia tiene un
        # tamaño único, así que el aviso saldría siempre y para todas.
        use = live_account_presentation.use_lots(
            live_account_presentation.REAL_LEADS, MEMBERS, {}, autolot=True,
        )
        self.assertEqual(use.warnings, ())

    def test_a_disputed_symbol_is_still_named_with_autolot(self) -> None:
        # Lo que se calla es el lote que no se pudo medir, no el conflicto:
        # ahí sí hay dos números y el usuario los puede arreglar.
        members = [*MEMBERS, {
            "candidate_id": "AXI/STANDARD:99", "symbol": "BTCUSD", "lot": 0.05,
        }]
        use = live_account_presentation.use_lots(
            live_account_presentation.TESTER_LEADS, members, DETAIL, autolot=True,
        )
        self.assertTrue(any("BTCUSD" in warning for warning in use.warnings))


class NormalizedSeriesTests(unittest.TestCase):
    LOTS = {"BTCUSD": 0.03, "ETHUSD": 0.1}

    def test_only_the_money_is_scaled_and_only_for_the_mapped_symbols(self) -> None:
        scaled = live_account_report.normalized_deal(deal("BTCUSD", 0.10, 103.0), self.LOTS)
        self.assertAlmostEqual(scaled.volume, 0.10)
        self.assertAlmostEqual(scaled.net_profit, 30.0)
        # Mismo lote a los dos lados: no hay nada que escalar, pero tampoco
        # nada que romper.
        self.assertAlmostEqual(
            live_account_report.normalized_deal(deal("ETHUSD", 0.10, 50.0), self.LOTS).net_profit,
            47.0,
        )
        original = deal("XAUUSD", 0.10, 103.0)
        self.assertIs(live_account_report.normalized_deal(original, self.LOTS), original)
        self.assertIs(live_account_report.normalized_deal(original, {}), original)

    def test_the_version_follows_the_lots_and_not_a_label(self) -> None:
        """Cambiar la casilla tiene que releer la serie entera.

        Antes el segundo término era el nombre de un mapa cableado y había que
        acordarse de subirlo. Ahora lo que identifica al criterio es su
        contenido, así que no hay nada que recordar.
        """
        plain = live_account_report.series_version({})
        self.assertEqual(plain, live_account_report.SERIES_VERSION)
        scaled = live_account_report.series_version(self.LOTS)
        self.assertNotEqual(scaled, plain)
        self.assertIn(live_account_report.SERIES_VERSION, scaled)
        self.assertEqual(scaled, live_account_report.series_version(dict(self.LOTS)))
        self.assertNotEqual(
            scaled, live_account_report.series_version({**self.LOTS, "BTCUSD": 0.01}),
        )
        self.assertEqual(
            live_account_report.ingestion_key("snapshot-1", [plain, scaled, plain]),
            ":".join(["snapshot-1", *sorted({plain, scaled})]),
        )

    def test_a_period_at_another_scale_is_not_mixed_into_the_stored_series(self) -> None:
        """Media serie a 0,01 y media a 0,03 es un mes que no existió.

        El tramo se rechaza y se dice; el sondeo siguiente pide toda la vida,
        porque la versión guardada ya no coincide con la del mapa.
        """
        key = "10010194@Axi-US50-Demo"
        with TemporaryDirectory() as folder:
            archive = LiveAccountArchive(Path(folder))
            archive.merge(
                "axi", key, login="10010194", server="Axi-US50-Demo", currency="EUR",
                movements=[(datetime(2026, 10, 2, 9, tzinfo=timezone.utc), 10.0)],
                period_start="2026-10-01", period_end="2026-10-20",
                normalization_version=live_account_report.series_version({"BTCUSD": 0.01}),
            )
            server = SimpleNamespace(live_accounts=archive)
            with mock.patch.object(live_account_ingest, "_artifact", return_value=b"<html>"):
                with self.assertRaisesRegex(
                    live_account_ingest.ScaleChanged, "leer la serie entera",
                ):
                    live_account_ingest._merge_account(
                        server, {}, "axi", "snap-1", key, {"report": {}},
                        ("2026-10-18", "2026-10-21"), {"BTCUSD": 0.03},
                    )
                # Toda la vida sí entra: no hay nada viejo con lo que mezclar.
                with mock.patch.object(
                    live_account_report, "account_from_html",
                    return_value=(
                        [(datetime(2026, 10, 19, 9, tzinfo=timezone.utc), 3.0)],
                        [("2026-10-19", 1_000.0)],
                    ),
                ):
                    live_account_ingest._merge_account(
                        server, {}, "axi", "snap-1", key, {"report": {}},
                        ("", "2026-10-21"), {"BTCUSD": 0.03},
                    )
            stored = archive.read("axi", key)
        self.assertEqual(
            stored["normalization_version"],
            live_account_report.series_version({"BTCUSD": 0.03}),
        )
        self.assertEqual(len(stored["movements"]), 1)

    def test_an_old_archive_requests_the_full_history_once(self) -> None:
        key = "10010194@Axi-US50-Demo"
        with TemporaryDirectory() as folder:
            archive = LiveAccountArchive(Path(folder))
            archive.merge(
                "axi", key, login="10010194", server="Axi-US50-Demo",
                currency="EUR", movements=[(datetime(2026, 10, 20), 10.0)],
                period_start="", period_end="2026-10-20",
            )
            server = SimpleNamespace(
                live_accounts=archive,
                live_account_scheduler_settings={"overlap_days": 3},
            )
            period = live_account_watch._period(
                server, "axi", [{"account_key": key}],
                datetime(2026, 10, 21, tzinfo=timezone.utc),
            )
        self.assertEqual(period, ("", "2026-10-21"))


class MeasuredPromiseTests(unittest.TestCase):
    ACCOUNT = {
        "account_key": "10010194@Axi-US50-Demo",
        "real_strategy_lots": {
            "AXI/STANDARD:21175": 0.1, "AXI/STANDARD:46978": 0.1,
            "AXI/STANDARD:3813": 0.01,
        },
    }

    def test_the_promise_is_measured_at_the_lot_the_series_is_shown_at(self) -> None:
        use = live_account_presentation.use_lots(
            live_account_presentation.TESTER_LEADS, MEMBERS, DETAIL,
        )
        self.assertEqual(
            live_account_ingest._measured_lots(self.ACCOUNT, {"members": MEMBERS}, "", use),
            {
                "AXI/STANDARD:21175": 0.03, "AXI/STANDARD:46978": 0.1,
                "AXI/STANDARD:3813": 0.01,
            },
        )

    def test_without_a_marked_column_the_declared_lot_keeps_mandating(self) -> None:
        self.assertEqual(
            live_account_ingest._measured_lots(self.ACCOUNT, {"members": MEMBERS}, ""),
            self.ACCOUNT["real_strategy_lots"],
        )

    def test_a_new_criterion_or_a_new_column_remeasures_without_the_clock(self) -> None:
        """La edad decide cada cuánto se comprueba; el criterio, si vale.

        El 2026-10-23 la media del #67 se arregló con 18 horas de vida: por
        edad no tocaba recalcularla hasta la medianoche, así que la pantalla
        siguió enseñando el número viejo con sondeos de por medio. Marcar una
        columna de la simulación cambia lo mismo y no puede esperar tampoco.
        """
        now = datetime(2026, 10, 23, 17, tzinfo=timezone.utc)
        fresh = {
            "updated_at": "2026-10-23T15:00:00+00:00",
            "criterion": live_account_expectation.CRITERION_VERSION,
            "portfolio_created_at": "",
        }
        self.assertFalse(live_account_ingest._needs_rebuild(fresh, now))
        self.assertTrue(live_account_ingest._needs_rebuild(
            {**fresh, "criterion": "criterio-anterior"}, now,
        ))
        self.assertTrue(live_account_ingest._needs_rebuild(fresh, now, "tester"))
        self.assertFalse(live_account_ingest._needs_rebuild(
            {**fresh, "mode": "tester"}, now, "tester",
        ))
        self.assertTrue(live_account_ingest._needs_rebuild(
            {**fresh, "mode": "tester"}, now,
        ))
        # Una media de antes de que existiera el campo tampoco es de ahora.
        self.assertTrue(live_account_ingest._needs_rebuild(
            {"updated_at": fresh["updated_at"]}, now,
        ))

    def test_a_promise_without_the_portfolio_date_is_measured_again(self) -> None:
        """Sin la clave no hay fecha que marcar, y por edad tardaría un día.

        Las nueve promesas en real del 2026-10-24 se habían escrito horas
        antes de que la ingesta empezara a guardar `portfolio_created_at`, así
        que el gráfico no tenía desde cuándo la cuenta sirve al portafolio y
        «Sondear ahora» no la reponía: por edad no tocaba hasta el día
        siguiente. Vacío sí es una respuesta y no se vuelve a medir, o la
        memoria UBS del agente se abriría en cada vuelta.
        """
        now = datetime(2026, 10, 23, 17, tzinfo=timezone.utc)
        stored = {
            "updated_at": "2026-10-23T15:00:00+00:00",
            "criterion": live_account_expectation.CRITERION_VERSION,
        }
        self.assertTrue(live_account_ingest._needs_rebuild(stored, now))
        self.assertFalse(live_account_ingest._needs_rebuild(
            {**stored, "portfolio_created_at": ""}, now,
        ))
        self.assertFalse(live_account_ingest._needs_rebuild(
            {**stored, "portfolio_created_at": "2026-08-20T02:52:14"}, now,
        ))

    def test_each_portfolio_only_presents_its_own_symbols(self) -> None:
        """Una cuenta puede servir a varios usos: el que no marca, no toca.

        Y un símbolo que dos portafolios presentan a lotes distintos se queda
        como se ejecutó, por lo mismo que dentro de un uso.
        """
        expectations = {
            7: {"presentation_lots": {"BTCUSD": 0.03}},
            8: {"presentation_lots": {"XAUUSD": 0.01}},
            9: {},
        }
        server = SimpleNamespace(live_accounts=SimpleNamespace(
            read_expectation=lambda node_id, pid: expectations.get(pid) or {},
        ))
        account = {"portfolios": [{"portfolio_id": pid} for pid in (7, 8, 9)]}
        self.assertEqual(
            live_account_presentation.account_symbols(server, "axi", account),
            {"BTCUSD": 0.03, "XAUUSD": 0.01},
        )
        expectations[9] = {"presentation_lots": {"BTCUSD": 0.01}}
        self.assertEqual(
            live_account_presentation.account_symbols(server, "axi", account),
            {"XAUUSD": 0.01},
        )


if __name__ == "__main__":
    unittest.main()
