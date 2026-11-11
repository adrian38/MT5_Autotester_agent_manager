"""La serie persistente de cada cuenta real, en el manager.

El agente no guarda nada de esto: cada sondeo exporta el informe nativo de un
periodo y lo tira. Sin archivo no habría ni mes anterior ni media, que es justo
lo que la portada compara.

**Se guardan los movimientos, no las filas.** Es la decisión que importa: la DD
de un mes no es el máximo de las DD de sus días —una caída puede atravesar la
medianoche— así que desde filas diarias el mes no se puede reconstruir. Con los
movimientos, cualquier tramo se recalcula exacto, y el coste es pequeño: una
cuenta real cierra decenas de operaciones al día, no miles como un backtest.

Un sondeo trae un periodo y **manda sobre él**: los movimientos guardados de
esos días se tiran y se ponen los nuevos. Así una reescritura corrige lo que el
sondeo anterior vio a medias —a las 23:00 de Madrid el día UTC está sin
cerrar— y un día que pierde un cierre se queda con los que le quedan en vez de
conservar para siempre el número viejo.

**Salvo que no traiga nada.** Para mandar sobre una ventana hay que traer algo
con qué mandar: un informe sin una sola fila no prueba que la ventana esté
vacía, sólo que no se pudo leer. Ahí lo guardado se queda como está
(`ai_context/live_account_empty_report.md`).
"""
from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..common import load_json, save_json, utc_now
from .live_account_series import as_utc, day_key

MAXIMUM_MOVEMENTS = 200_000
"""Movimientos conservados por cuenta. A cincuenta cierres al día son años.

Es un tope de seguridad, no una política: si alguna vez se alcanza, lo que se
tira es lo más antiguo y la media del mes de ese año deja de tener ese año.
"""

MAXIMUM_SNAPSHOTS = 60
"""Lecturas anotadas. Es el diario de «cuándo se leyó esto», no un histórico."""

MAXIMUM_BALANCE_DAYS = 8_000
"""Días de saldo conservados por cuenta: más de veinte años de calendario."""

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    """Nombre de fichero estable y legible, sin colisiones silenciosas."""
    text = _UNSAFE.sub("_", str(value or ""))[:60] or "sin-nombre"
    digest = hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:8]
    return f"{text}-{digest}"


def _number(value: Any, previous: Any) -> float:
    """El número nuevo, o el que ya había: una lectura sin saldo no lo borra."""
    for candidate in (value, previous):
        try:
            if candidate is not None:
                return float(candidate)
        except (TypeError, ValueError):
            continue
    return 0.0


def account_key(login: Any, server: Any) -> str:
    """La identidad de una cuenta: su login en su servidor.

    La tarjeta es de la **cuenta**, no del portafolio. Hoy cada portafolio
    activo tiene su propia cuenta, pero dos usos sobre el mismo login tienen un
    único saldo: contarlo dos veces duplicaría el total de arriba.
    """
    return f"{str(login or '').strip()}@{str(server or '').strip()}"


def movement_rows(ordered: Iterable[tuple[Any, float]]) -> list[list[Any]]:
    """Los movimientos como los guarda el JSON: `[instante, importe]`."""
    return [[as_utc(moment).isoformat(), float(amount)] for moment, amount in ordered]


def _stored_series(stored: dict[str, Any], key: str) -> list[tuple[str, float]]:
    """Una serie monetaria guardada, ordenada y sin filas corruptas."""
    found: list[tuple[str, float]] = []
    for row in stored.get(key) or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        try:
            found.append((str(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue
    found.sort(key=lambda item: item[0])
    return found


def stored_movements(stored: dict[str, Any]) -> list[tuple[str, float]]:
    """El efecto neto en saldo guardado, ya ordenado."""
    return _stored_series(stored, "movements")


def stored_profits(stored: dict[str, Any]) -> list[tuple[str, float]]:
    """La columna Profit de MT5 guardada, ya ordenada."""
    return _stored_series(stored, "profits")


def stored_balances(stored: dict[str, Any]) -> list[tuple[str, float]]:
    """El saldo diario guardado: `[("2026-10-23", 6109.55), …]`, ordenado.

    Una cuenta archivada antes de que esto existiera no lo trae, y eso no es
    un error: la lista vacía significa «no se sabe», y quien la consulte tiene
    que decirlo en vez de inventarse un tamaño de cuenta.
    """
    found: list[tuple[str, float]] = []
    for row in stored.get("balances") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        try:
            found.append((str(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue
    found.sort(key=lambda item: item[0])
    return found


def ordered_movements(stored: dict[str, Any]) -> list[tuple[datetime, float]]:
    """Lo mismo, con el instante ya convertido: lo que mide `live_account_series`.

    Vive aquí y no en cada consumidor porque son dos —la portada y el gráfico
    mes a mes— y los dos tienen que leer la misma serie de la misma forma.
    """
    return [
        (datetime.fromisoformat(stamp), amount)
        for stamp, amount in stored_movements(stored)
    ]


def ordered_profits(stored: dict[str, Any]) -> list[tuple[datetime, float]]:
    """La columna Profit de MT5, con el instante convertido."""
    return [
        (datetime.fromisoformat(stamp), amount)
        for stamp, amount in stored_profits(stored)
    ]


class LiveAccountArchive:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.lock = threading.RLock()

    def _node_directory(self, node_id: str) -> Path:
        return self.directory / _slug(node_id)

    def _path(self, node_id: str, key: str) -> Path:
        return self._node_directory(node_id) / f"{_slug(key)}.json"

    def read(self, node_id: str, key: str) -> dict[str, Any]:
        """Lo guardado de esa cuenta, o un registro vacío pero completo."""
        path = self._path(node_id, key)
        empty = {
            "account_key": key, "login": "", "server": "", "currency": "",
            "movements": [], "profits": [], "first_day": "", "last_day": "",
            "snapshots": [], "balance": 0.0, "equity": 0.0,
            "balances": [], "normalization_version": "",
        }
        if not path.is_file():
            return empty
        try:
            stored = load_json(path)
        except ValueError:
            return empty
        return {**empty, **stored}

    def accounts(self, node_id: str) -> list[str]:
        """Las cuentas con serie guardada de ese nodo."""
        directory = self._node_directory(node_id)
        if not directory.is_dir():
            return []
        keys: list[str] = []
        for path in sorted(directory.glob("*.json")):
            try:
                stored = load_json(path)
            except ValueError:
                continue
            key = str(stored.get("account_key") or "")
            if key:
                keys.append(key)
        return keys

    @staticmethod
    def _kept(
        movements: Sequence[tuple[str, float]], start: str, end: str,
    ) -> list[tuple[str, float]]:
        """Lo de fuera del periodo sondeado. Sin periodo no se conserva nada.

        Un sondeo de toda la vida de la cuenta sustituye la serie entera: es la
        lectura más completa que existe y discutirla con lo guardado sólo
        podría duplicar movimientos.
        """
        if not start:
            return []
        return [
            row for row in movements
            if not start <= row[0][:10] <= (end or "9999-12-31")
        ]

    @staticmethod
    def _inside(
        movements: Sequence[Any], start: str, end: str,
    ) -> list[Any]:
        """Lo que el informe trae **dentro** del periodo que se le pidió.

        `_kept` poda exactamente el periodo pedido, así que lo que entra tiene
        que ser exactamente ese periodo o las dos mitades dejan de ser
        complementarias. Y el informe nativo no se limita a lo pedido: MT5 lo
        arma por posición, de modo que una abierta antes de la ventana imprime
        su cierre con la fecha de fuera.

        Pasó el 2026-10-21: el sondeo pidió del 17 al 21 de octubre y el
        informe trajo un cierre del día 15. Ese día no se podaba —está fuera de
        la ventana— pero el cierre sí se añadía, encima del que ya estaba desde
        la lectura de toda la vida. Medido el 2026-10-23 en las cuatro cuentas
        de AXI: el mismo instante duplicado en tres de ellas, 66,30 € de más en
        octubre de 10010194 y 101,51 € en 60082017.

        Recortarlo es la mitad segura: el día de fuera ya está completo en la
        serie, y el informe sólo traía de él la operación que la ventana rozó.
        Quedarse con esa y tirar las demás perdería el resto del día.

        Sin periodo no se recorta: un sondeo de toda la vida manda sobre todo.
        """
        if not start:
            return list(movements)
        return [
            row for row in movements
            if start <= row[0][:10] <= (end or "9999-12-31")
        ]

    @classmethod
    def _merged_balances(
        cls, stored: dict[str, Any], fresh: Sequence[tuple[str, float]] | None,
        period_start: str, period_end: str,
    ) -> list[list[Any]]:
        """El saldo diario después del sondeo, con la regla de siempre.

        El periodo sondeado manda sobre sus días y lo de fuera se conserva,
        igual que con los movimientos. Un sondeo que no trae saldos —una
        lectura vieja, un informe sin la columna, o uno que volvió sin tabla
        de transacciones— deja lo guardado intacto: una serie de saldo no se
        borra por no haberla vuelto a ver.
        """
        if not fresh:
            return [[day, value] for day, value in stored_balances(stored)]
        kept = cls._kept(stored_balances(stored), period_start, period_end)
        arrived = cls._inside(
            [[str(day), float(value)] for day, value in fresh],
            period_start, period_end,
        )
        merged = {str(day): float(value) for day, value in kept}
        merged.update({str(row[0]): float(row[1]) for row in arrived})
        return [[day, merged[day]] for day in sorted(merged)][-MAXIMUM_BALANCE_DAYS:]

    @classmethod
    def _merged_series(
        cls, stored: Sequence[tuple[str, float]], fresh: Sequence[tuple[Any, float]],
        period_start: str, period_end: str,
    ) -> list[list[Any]]:
        """Una serie monetaria después de sustituir el periodo sondeado.

        **Un informe que no trae nada no sustituye nada.** Podar exige algo
        con qué sustituir: una lectura cuyo historial volvió vacío no dice que
        la ventana esté vacía, sólo que no se leyó.

        Medido el 2026-11-09 sobre 52560280: el informe nativo llegó con la
        tabla de transacciones en blanco —`Total de operaciones ejecutadas: 0`
        con el saldo movido— y el merge se llevó los 84 movimientos y los
        cuatro días de saldo del 3 al 6 de noviembre, que ya estaban guardados
        y eran ciertos. Lo mismo el 2026-11-01 con 77049426 de RoboForex, esa
        vez con la ventana de toda la vida y la serie entera.

        Un informe que trae **menos** filas sí manda sobre su ventana: ahí hay
        con qué comparar, y corregir es justo para lo que esto existe.
        """
        arrived = cls._inside(movement_rows(fresh), period_start, period_end)
        if not arrived:
            return [[moment, amount] for moment, amount in stored][-MAXIMUM_MOVEMENTS:]
        kept = cls._kept(stored, period_start, period_end)
        return sorted(
            [[moment, amount] for moment, amount in kept] + arrived,
            key=lambda row: row[0],
        )[-MAXIMUM_MOVEMENTS:]

    def merge(
        self, node_id: str, key: str, *, login: Any, server: Any, currency: Any,
        movements: Sequence[tuple[Any, float]], period_start: str, period_end: str,
        profits: Sequence[tuple[Any, float]] | None = None,
        taken_at: str = "", balance: Any = None, equity: Any = None,
        balances: Sequence[tuple[str, float]] | None = None,
        normalization_version: str | None = None,
    ) -> dict[str, Any]:
        """Mete el sondeo en la serie y devuelve el registro ya guardado."""
        with self.lock:
            stored = self.read(node_id, key)
            fresh = self._inside(movement_rows(movements), period_start, period_end)
            combined = self._merged_series(
                stored_movements(stored), movements, period_start, period_end,
            )
            gross = self._merged_series(
                stored_profits(stored), profits or movements, period_start, period_end,
            )
            days = self._merged_balances(stored, balances, period_start, period_end)
            snapshots = [
                *(row for row in stored.get("snapshots") or [] if isinstance(row, dict)),
                {
                    "taken_at": taken_at or utc_now(),
                    "period_start": period_start, "period_end": period_end,
                    "movements": len(fresh),
                },
            ][-MAXIMUM_SNAPSHOTS:]
            record = {
                **stored,
                "account_key": key,
                "login": str(login or "").strip(),
                "server": str(server or "").strip(),
                "currency": str(currency or "").strip().upper() or stored.get("currency") or "",
                "movements": combined,
                "profits": gross,
                "first_day": combined[0][0][:10] if combined else "",
                "last_day": combined[-1][0][:10] if combined else "",
                "updated_at": taken_at or utc_now(),
                "snapshots": snapshots,
                # El saldo y la equidad del momento de la lectura. No son serie
                # —el informe nativo no los da por día— pero son lo que la
                # tarjeta necesita para decir de qué tamaño es la cuenta.
                "balance": _number(balance, stored.get("balance")),
                "equity": _number(equity, stored.get("equity")),
                # El saldo **sí** es serie, aunque los dos de arriba no lo
                # sean: la tabla de transacciones del informe nativo publica
                # el acumulado tras cada operación, y de ahí sale con qué
                # tamaño operaba la cuenta cada día. Lo necesita la promesa de
                # una cuenta con autolote, donde el lote lo decide el saldo.
                "balances": days,
                "normalization_version": (
                    stored.get("normalization_version")
                    if normalization_version is None else normalization_version
                ),
            }
            path = self._path(node_id, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            save_json(path, record)
            return record

    def last_day(self, node_id: str, key: str) -> str:
        """Hasta dónde llega la serie, que es desde dónde se vuelve a leer."""
        return str(self.read(node_id, key).get("last_day") or "")

    def _total_selection_path(self) -> Path:
        return self.directory / "total_selection.json"

    def total_selection(self) -> dict[str, Any]:
        """Selección compartida por todos los navegadores que abren el manager."""
        path = self._total_selection_path()
        if not path.is_file():
            return {"initialized": False, "excluded_accounts": []}
        try:
            stored = load_json(path)
        except ValueError:
            return {"initialized": False, "excluded_accounts": []}
        excluded = sorted({
            str(value) for value in stored.get("excluded_accounts") or []
            if isinstance(value, str) and value.strip()
        })
        return {"initialized": True, "excluded_accounts": excluded}

    def update_total_selection(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Migra la selección local una vez o cambia una sola cuenta."""
        with self.lock:
            current = self.total_selection()
            if "excluded_accounts" in changes:
                values = changes["excluded_accounts"]
                if not isinstance(values, list) or len(values) > 10_000:
                    raise ValueError("excluded_accounts debe ser una lista")
                if current["initialized"]:
                    return current
                excluded = {str(value).strip() for value in values if str(value).strip()}
            else:
                key = str(changes.get("account") or "").strip()
                included = changes.get("included")
                if not key or "::" not in key or len(key) > 400:
                    raise ValueError("La cuenta seleccionada no es válida")
                if not isinstance(included, bool):
                    raise ValueError("included debe ser true o false")
                excluded = set(current["excluded_accounts"])
                if included:
                    excluded.discard(key)
                else:
                    excluded.add(key)
            state = {"initialized": True, "excluded_accounts": sorted(excluded)}
            save_json(self._total_selection_path(), state)
            return state

    def _expectation_path(self, node_id: str, portfolio_id: Any) -> Path:
        return self._node_directory(node_id) / f"expectation-{_slug(str(portfolio_id))}.json"

    def read_expectation(self, node_id: str, portfolio_id: Any) -> dict[str, Any]:
        """La media estacional guardada de ese portafolio, o vacío.

        Vive junto a la serie porque se usa en el mismo sitio y, sobre todo,
        porque **no se puede calcular en la portada**: hay que parsear los
        informes del backtest de cada estrategia, y la pantalla se refresca
        cada minuto. La calcula el sondeo nocturno y aquí sólo se lee.
        """
        path = self._expectation_path(node_id, portfolio_id)
        if not path.is_file():
            return {}
        try:
            return load_json(path)
        except ValueError:
            return {}

    def write_expectation(
        self, node_id: str, portfolio_id: Any, value: dict[str, Any],
    ) -> None:
        with self.lock:
            path = self._expectation_path(node_id, portfolio_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            save_json(path, {**value, "portfolio_id": portfolio_id, "updated_at": utc_now()})

    def discard_expectation(self, node_id: str, portfolio_id: Any) -> bool:
        """Borra la promesa guardada de ese portafolio. Devuelve si existía.

        La firma de `live_account_expectation` cubre los miembros, así que una
        composición distinta da otro número **cuando se vuelve a medir**. Lo
        que no cubre es *cuándo*: `_needs_rebuild` deja pasar hasta veinticuatro
        horas antes de abrir la memoria UBS del agente, que es lo que esta
        caché existe para evitar. Cambiar los sets de un portafolio manual sin
        borrar esto deja la tarjeta de la cuenta real prometiendo el beneficio
        y la DD de la composición anterior durante todo ese día.

        Se borra el fichero en vez de marcarlo caducado: lo guardado describe
        una composición que ya no existe, y `read_expectation` vacío es
        exactamente lo que `_needs_rebuild` entiende como «mídelo otra vez».
        """
        with self.lock:
            path = self._expectation_path(node_id, portfolio_id)
            if not path.is_file():
                return False
            path.unlink()
            return True

    def day_keys(self, node_id: str, key: str) -> list[str]:
        """Los días con algún cierre, de viejo a nuevo. Para medir, no pintar."""
        seen: dict[str, None] = {}
        for moment, _amount in stored_movements(self.read(node_id, key)):
            seen[moment[:10]] = None
        return list(seen)


def day_of(moment: Any) -> str:
    """El día UTC de un instante guardado o recién leído."""
    return moment[:10] if isinstance(moment, str) else day_key(moment)
