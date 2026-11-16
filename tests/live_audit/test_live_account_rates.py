"""El cambio de divisa que refresca el sondeo.

Nada de esto sale a la red: el feed del BCE entra como bytes y la descarga se
sustituye. Lo que se comprueba es la aritmética del cruce, que es donde un
signo invertido pasa inadvertido —0,92 y 1,087 son los dos «plausibles»— y las
tres formas de no escribir: ya se miró hoy, no cambió nada, y el feed falló.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import live_account_rates as rates  # noqa: E402

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-11-13">
      <Cube currency="USD" rate="1.0870"/>
      <Cube currency="GBP" rate="0.8350"/>
      <Cube currency="JPY" rate="168.21"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


class _Notifications:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def add(self, level: str, title: str, message: str = "", **extra: object) -> dict:
        event = {"level": level, "title": title, "message": message, **extra}
        self.events.append(event)
        return event


class _Archive:
    def __init__(self, currencies: dict[str, str]) -> None:
        self.currencies = currencies

    def read(self, node_id: str, account_key: str) -> dict[str, str]:
        return {"currency": self.currencies.get(f"{node_id}:{account_key}", "")}


class _Server:
    """Lo justo que `refresh_after_snapshot` toca del servidor real."""

    def __init__(self, settings: dict[str, object], currencies: dict[str, str]) -> None:
        self.nodes = [{"id": "ic"}]
        self.live_account_scheduler_settings = dict(settings)
        self.live_accounts = _Archive(currencies)
        self.live_audit_notifications = _Notifications()
        self.saved: list[dict[str, object]] = []

    def update_live_account_scheduler(self, changes: dict[str, object]) -> dict:
        self.saved.append(dict(changes))
        self.live_account_scheduler_settings = {
            **self.live_account_scheduler_settings, **changes,
        }
        return self.live_account_scheduler_settings


def _server(**overrides: object) -> _Server:
    settings = {
        "display_currency": "EUR",
        "exchange_rates": {"USD": 0.5},
        "rates_updated_at": "2026-11-10",
        **overrides,
    }
    return _Server(settings, {"ic:1001@IC": "USD"})


def _accounts(_server_arg: object, _node_id: str) -> list[dict[str, str]]:
    return [{"account_key": "1001@IC"}]


NOW = datetime(2026, 11, 16, 23, 5, tzinfo=timezone.utc)


class ParseTests(unittest.TestCase):
    def test_the_feed_is_read_without_declaring_its_namespace(self) -> None:
        """El BCE ha cambiado el espacio de nombres antes.

        Declararlo y que cambie no da error: devuelve cero divisas, se guarda
        un diccionario vacío y nadie se entera. Por eso se recorre por el
        final del nombre de la etiqueta, y por eso un feed sin divisas es un
        `ValueError` explícito.
        """
        published, per_eur = rates.parse_eurofxref(FEED)
        self.assertEqual("2026-11-13", published)
        self.assertEqual(1.0, per_eur["EUR"])
        self.assertAlmostEqual(1.0870, per_eur["USD"])
        self.assertAlmostEqual(168.21, per_eur["JPY"])

    def test_a_feed_without_rates_is_an_error_and_not_an_empty_dict(self) -> None:
        empty = b'<Envelope xmlns="x"><Cube><Cube time="2026-11-13"/></Cube></Envelope>'
        with self.assertRaises(ValueError):
            rates.parse_eurofxref(empty)


class CrossRateTests(unittest.TestCase):
    def test_the_stored_rate_is_what_convert_multiplies_by(self) -> None:
        """Con el euro delante, 1,087 dólares por euro se guarda como 0,92.

        Es el sentido que ya tenía el número escrito a mano, y el que
        `convert` multiplica. Invertirlo daría 1,087: un total un 18 % alto
        que sigue pareciendo un número razonable.
        """
        _published, per_eur = rates.parse_eurofxref(FEED)
        converted = rates.rates_towards("EUR", {"USD", "GBP"}, per_eur)
        self.assertAlmostEqual(0.9200, converted["USD"], places=4)
        self.assertAlmostEqual(1.1976, converted["GBP"], places=4)

    def test_a_display_currency_other_than_the_euro_crosses_through_it(self) -> None:
        """La referencia es en euros; el cruce sale de dos de sus filas."""
        _published, per_eur = rates.parse_eurofxref(FEED)
        converted = rates.rates_towards("USD", {"EUR", "GBP"}, per_eur)
        self.assertAlmostEqual(1.0870, converted["EUR"], places=4)
        self.assertAlmostEqual(1.3018, converted["GBP"], places=4)
        self.assertNotIn("USD", converted)

    def test_a_currency_the_feed_does_not_publish_is_left_out(self) -> None:
        _published, per_eur = rates.parse_eurofxref(FEED)
        self.assertEqual({}, rates.rates_towards("EUR", {"XAU"}, per_eur))


class RefreshTests(unittest.TestCase):
    def test_a_delivered_snapshot_writes_the_fresh_rate_with_the_feed_date(self) -> None:
        server = _server()
        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", lambda: rates.parse_eurofxref(FEED)):
            result = rates.refresh_after_snapshot(server, NOW)
        self.assertEqual(["USD"], result["updated"])
        self.assertEqual("2026-11-13", result["published"])
        self.assertEqual(1, len(server.saved))
        self.assertAlmostEqual(0.92, server.saved[0]["exchange_rates"]["USD"], places=4)
        # La fecha es la del feed, no la del día en que se descargó: el BCE
        # publica en días hábiles y el 16 es lunes, pero el dato es del 13.
        self.assertEqual("2026-11-13", server.saved[0]["rates_updated_at"])
        self.assertEqual([], server.live_audit_notifications.events)

    def test_a_currency_only_in_the_accounts_enters_without_being_typed(self) -> None:
        """Una cuenta nueva en libras no espera a que alguien abra el diálogo."""
        server = _Server(
            {"display_currency": "EUR", "exchange_rates": {}, "rates_updated_at": ""},
            {"ic:1001@IC": "GBP"},
        )
        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", lambda: rates.parse_eurofxref(FEED)):
            rates.refresh_after_snapshot(server, NOW)
        self.assertIn("GBP", server.saved[0]["exchange_rates"])

    def test_a_rate_the_feed_does_not_publish_keeps_what_was_typed(self) -> None:
        """Fundir y no sustituir: el oro escrito a mano no desaparece."""
        server = _server(exchange_rates={"USD": 0.5, "XAU": 2400.0})
        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", lambda: rates.parse_eurofxref(FEED)):
            rates.refresh_after_snapshot(server, NOW)
        saved = server.saved[0]["exchange_rates"]
        self.assertEqual(2400.0, saved["XAU"])
        self.assertAlmostEqual(0.92, saved["USD"], places=4)

    def test_it_looks_once_a_day_even_though_the_tick_runs_every_few_seconds(self) -> None:
        server = _server()
        calls: list[int] = []

        def _fetch() -> tuple[str, dict[str, float]]:
            calls.append(1)
            return rates.parse_eurofxref(FEED)

        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", _fetch):
            rates.refresh_after_snapshot(server, NOW)
            again = rates.refresh_after_snapshot(server, NOW)
        self.assertEqual(1, len(calls))
        self.assertIn("skipped", again)

    def test_the_same_published_day_is_not_written_again(self) -> None:
        """El sábado el feed devuelve el del viernes: no hay nada que guardar.

        Y lo decide la fecha, no comparar los números: el cambio calculado es
        0,919963… y el guardado el 0,92 que alguien tecleó, así que comparar
        `float` reescribiría el fichero cada día sin que nada hubiera cambiado.
        """
        server = _server(exchange_rates={"USD": 0.92}, rates_updated_at="2026-11-13")
        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", lambda: rates.parse_eurofxref(FEED)):
            result = rates.refresh_after_snapshot(server, NOW)
        self.assertEqual({"unchanged": "2026-11-13"}, result)
        self.assertEqual([], server.saved)

    def test_a_new_currency_is_filled_in_even_on_a_day_already_read(self) -> None:
        """Misma fecha del BCE, pero falta una divisa: eso sí hay que escribirlo."""
        server = _Server(
            {
                "display_currency": "EUR", "exchange_rates": {"USD": 0.92},
                "rates_updated_at": "2026-11-13",
            },
            {"ic:1001@IC": "GBP"},
        )
        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", lambda: rates.parse_eurofxref(FEED)):
            result = rates.refresh_after_snapshot(server, NOW)
        self.assertEqual(["GBP", "USD"], result["updated"])
        self.assertIn("GBP", server.saved[0]["exchange_rates"])

    def test_a_dead_feed_keeps_the_previous_rate_and_rings_the_bell(self) -> None:
        """Lo que el usuario eligió: conservar el anterior y avisar.

        Borrarlo habría dejado seis cuentas de catorce fuera del patrimonio
        por un corte de red.
        """
        server = _server()

        def _boom() -> tuple[str, dict[str, float]]:
            raise OSError("Network is unreachable")

        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", _boom):
            result = rates.refresh_after_snapshot(server, NOW)
        self.assertIn("error", result)
        self.assertEqual([], server.saved)
        self.assertEqual({"USD": 0.5}, server.live_account_scheduler_settings["exchange_rates"])
        event = server.live_audit_notifications.events[0]
        self.assertEqual("warning", event["level"])
        self.assertEqual(rates.NOTICE_KIND, event["kind"])
        self.assertIn("2026-11-10", str(event["message"]))
        self.assertIn("USD", str(event["message"]))

    def test_a_dead_feed_does_not_ring_the_bell_on_every_tick(self) -> None:
        """El `tick` son segundos: avisar en cada vuelta es enterrar la campana."""
        server = _server()

        def _boom() -> tuple[str, dict[str, float]]:
            raise OSError("Network is unreachable")

        with patch.object(rates.live_account_inventory, "node_accounts", _accounts), \
                patch.object(rates, "fetch_eurofxref", _boom):
            rates.refresh_after_snapshot(server, NOW)
            rates.refresh_after_snapshot(server, NOW)
        self.assertEqual(1, len(server.live_audit_notifications.events))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
