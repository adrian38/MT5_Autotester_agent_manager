import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from portfolio_manager.mt5_account_history import read_native_account_history


def _closed_row(
    open_time: str, position: str, symbol: str, side: str, volume: str,
    open_price: str, sl: str, tp: str, close_time: str, close_price: str,
    commission: str, swap: str, profit: str,
) -> str:
    # El separador oculto con `colspan` es el que escribe el terminal: sin
    # descartarlo, las columnas se leen corridas desde el volumen.
    return (
        '<tr bgcolor="#FFFFFF" align="right">'
        f"<td>{open_time}</td><td>{position}</td><td>{symbol}</td><td>{side}</td>"
        '<td class="hidden" colspan="8"></td>'
        f'<td class="">{volume}</td><td class="">{open_price}</td>'
        f'<td class="">{sl}</td><td class="">{tp}</td>'
        f'<td class="">{close_time}</td><td class="">{close_price}</td>'
        f'<td class="">{commission}</td><td class="">{swap}</td>'
        f'<td colspan="2">{profit}</td></tr>'
    )


CLOSED_HEADER = (
    '<tr align="center" bgcolor="#E5F0FC">'
    "<td><b>Fecha/Hora</b></td><td><b>Posición</b></td><td><b>Símbolo</b></td>"
    "<td><b>Tipo</b></td><td><b>Volumen </b></td><td><b>Precio</b></td>"
    "<td><b>S / L</b></td><td><b>T / P</b></td><td><b>Fecha/Hora</b></td>"
    "<td><b>Precio</b></td><td><b>Comisión</b></td><td><b>Swap</b></td>"
    '<td colspan="2"><b>Beneficio</b></td></tr>'
)

OPEN_HEADER = (
    '<tr align="center" bgcolor="#E5F0FC">'
    "<td><b>Fecha/Hora</b></td><td><b>Posición</b></td><td><b>Símbolo</b></td>"
    "<td><b>Tipo</b></td><td><b>Volumen </b></td><td><b>Precio</b></td>"
    "<td><b>S / L</b></td><td><b>T / P</b></td><td><b>Precio de mercado</b></td>"
    "<td><b>Swap</b></td><td><b>Beneficio</b></td><td><b>Comentario</b></td></tr>"
)

DEALS = (
    '<tr align="center"><th colspan="14"><b>Transacciones</b></th></tr>'
    '<tr align="center" bgcolor="#E5F0FC">'
    "<td><b>Fecha/Hora</b></td><td><b>Transacción</b></td><td><b>Símbolo</b></td>"
    "<td><b>Tipo</b></td><td><b>Dirección</b></td><td><b>Volumen</b></td>"
    "<td><b>Precio</b></td><td><b>Orden</b></td><td><b>Comisión</b></td>"
    "<td><b>Tasa</b></td><td><b>Swap</b></td><td><b>Beneficio</b></td>"
    "<td><b>Balance</b></td><td><b>Comentario</b></td></tr>"
    '<tr align="right"><td>2026.10.19 15:34:30</td><td>583811744</td><td>EURUSD</td>'
    "<td>buy</td><td>in</td><td>0.08</td><td>1.1</td><td>772603812</td><td>-0.14</td>"
    "<td>0.00</td><td>0.00</td><td>0.00</td><td>2 319.81</td><td>NAS_VOL_M15_C</td></tr>"
)


def _report(closed: list[str], opened: list[str] = (), deals: str = DEALS) -> str:
    return (
        "<html><head><title>77049426: Informe del historial de trading</title></head>"
        '<body><table>'
        '<tr align="center"><th colspan="14"><b>Posiciones</b></th></tr>'
        + CLOSED_HEADER + "".join(closed)
        + deals
        + '<tr align="center"><th colspan="14"><b>Posiciones abiertas</b></th></tr>'
        + OPEN_HEADER + "".join(opened)
        + "</table></body></html>"
    )


class NativeAccountHistoryTests(unittest.TestCase):
    """El informe nativo del terminal como fuente de las operaciones reales."""

    def setUp(self) -> None:
        self.period_start = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.period_end = datetime(2026, 10, 24, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def _read(self, html_text: str):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "real_account_mt5_report.html"
            path.write_text(html_text, encoding="utf-16")
            return read_native_account_history(path, self.period_start, self.period_end)

    def test_a_closed_position_keeps_apertura_cierre_and_its_stops(self) -> None:
        history = self._read(_report([_closed_row(
            "2026.10.01 19:50:10", "764327055", "GOOGL", "sell", "0.01",
            "337.14", "326.82", "322.65", "2026.10.22 22:08:39", "322.65",
            "-0.12", "-0.59", "12.92",
        )]))
        self.assertEqual(len(history.closed), 1)
        trade = history.closed[0]
        self.assertEqual(trade["position_id"], 764327055)
        self.assertEqual(trade["symbol"], "GOOGL")
        self.assertEqual(trade["side"], "sell")
        self.assertEqual(trade["volume"], .01)
        # Las dos columnas se llaman igual en la cabecera: si se leyera por
        # nombre, la apertura sería el cierre y el precio de apertura se
        # perdería.
        self.assertEqual(trade["open_time"], datetime(2026, 10, 1, 19, 50, 10, tzinfo=timezone.utc))
        self.assertEqual(trade["close_time"], datetime(2026, 10, 22, 22, 8, 39, tzinfo=timezone.utc))
        self.assertEqual(trade["open_price"], 337.14)
        self.assertEqual(trade["close_price"], 322.65)
        self.assertEqual(trade["sl"], 326.82)
        self.assertEqual(trade["tp"], 322.65)
        self.assertAlmostEqual(trade["profit"], 12.92 - .12 - .59)

    def test_only_the_closes_inside_the_period_count_as_operations(self) -> None:
        inside = _closed_row(
            "2026.10.19 10:00:00", "1", "EURUSD", "buy", "0.10", "1.10", "0", "0",
            "2026.10.20 11:00:00", "1.11", "0.00", "0.00", "5.00",
        )
        after = _closed_row(
            "2026.10.19 10:00:00", "2", "EURUSD", "buy", "0.10", "1.10", "0", "0",
            "2026.10.27 11:00:00", "1.11", "0.00", "0.00", "5.00",
        )
        history = self._read(_report([inside, after]))
        self.assertEqual([trade["position_id"] for trade in history.closed], [1])
        # La que cerró después seguía abierta al acabar el periodo, y el
        # informe la lista entre las cerradas, no entre las abiertas.
        self.assertEqual([row["position_id"] for row in history.open_at_end], [2])

    def test_a_position_opened_after_the_period_is_not_open_at_its_end(self) -> None:
        opened_later = (
            '<tr align="right"><td>2026.10.28 09:00:00</td><td>7</td><td>AMZN</td>'
            "<td>sell</td><td>0.02</td><td>245.62</td><td>270.62</td><td>239.62</td>"
            "<td>248.06</td><td>-1.09</td><td>-4.36</td><td></td></tr>"
        )
        inside = (
            '<tr align="right"><td>2026.09.28 17:03:18</td><td>8</td><td>AMZN</td>'
            "<td>sell</td><td>0.02</td><td>245.62</td><td>270.62</td><td>239.62</td>"
            "<td>248.06</td><td>-1.09</td><td>-4.36</td><td></td></tr>"
        )
        history = self._read(_report([], [opened_later, inside]))
        self.assertEqual([row["position_id"] for row in history.open_at_end], [8])

    def test_the_entry_comment_of_the_deal_names_the_strategy(self) -> None:
        history = self._read(_report([_closed_row(
            "2026.10.19 15:34:30", "99", "EURUSD", "buy", "0.08", "1.1", "0", "0",
            "2026.10.20 11:00:00", "1.11", "0.00", "0.00", "5.00",
        )]))
        trade = history.closed[0]
        self.assertEqual(trade["comment"], "NAS_VOL_M15_C")
        self.assertEqual(trade["strategy"], "NAS_VOL_M15_C")

    def test_without_a_matching_deal_the_position_id_names_the_strategy(self) -> None:
        history = self._read(_report([_closed_row(
            "2026.10.19 09:00:00", "99", "EURUSD", "buy", "0.08", "1.1", "0", "0",
            "2026.10.20 11:00:00", "1.11", "0.00", "0.00", "5.00",
        )]))
        self.assertEqual(history.closed[0]["comment"], "")
        self.assertEqual(history.closed[0]["strategy"], "99")

    def test_amounts_carry_the_thousands_separator_the_terminal_writes(self) -> None:
        history = self._read(_report([_closed_row(
            "2026.10.19 09:00:00", "5", "XAUUSD", "buy", "0.50", "3 912.45", "0", "0",
            "2026.10.20 11:00:00", "3 999.05", "-1.20", "0.00", "1 043.00",
        )]))
        trade = history.closed[0]
        self.assertEqual(trade["open_price"], 3912.45)
        self.assertEqual(trade["close_price"], 3999.05)
        self.assertAlmostEqual(trade["profit"], 1041.80)

    def test_a_report_without_operations_is_not_an_error(self) -> None:
        history = self._read(_report([]))
        self.assertEqual(history.closed, [])
        self.assertEqual(history.open_at_end, [])

    def test_the_period_filter_uses_the_same_window_as_the_api_path(self) -> None:
        edge = _closed_row(
            "2026.10.19 09:00:00", "6", "EURUSD", "buy", "0.10", "1.10", "0", "0",
            self.period_end.strftime("%Y.%m.%d %H:%M:%S"), "1.11", "0", "0", "1.00",
        )
        self.assertEqual(len(self._read(_report([edge])).closed), 1)
        beyond = _closed_row(
            "2026.10.19 09:00:00", "6", "EURUSD", "buy", "0.10", "1.10", "0", "0",
            (self.period_end + timedelta(seconds=1)).strftime("%Y.%m.%d %H:%M:%S"),
            "1.11", "0", "0", "1.00",
        )
        self.assertEqual(self._read(_report([beyond])).closed, [])


if __name__ == "__main__":
    unittest.main()
