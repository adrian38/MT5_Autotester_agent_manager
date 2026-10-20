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
cerrar— y un día que se queda sin cierres baja a cero en vez de conservar para
siempre el número viejo.
"""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from .common import load_json, save_json, utc_now
from .live_account_series import as_utc, day_key

MAXIMUM_MOVEMENTS = 200_000
"""Movimientos conservados por cuenta. A cincuenta cierres al día son años.

Es un tope de seguridad, no una política: si alguna vez se alcanza, lo que se
tira es lo más antiguo y la media del mes de ese año deja de tener ese año.
"""

MAXIMUM_SNAPSHOTS = 60
"""Lecturas anotadas. Es el diario de «cuándo se leyó esto», no un histórico."""

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    """Nombre de fichero estable y legible, sin colisiones silenciosas."""
    text = _UNSAFE.sub("_", str(value or ""))[:60] or "sin-nombre"
    digest = hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:8]
    return f"{text}-{digest}"


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


def stored_movements(stored: dict[str, Any]) -> list[tuple[str, float]]:
    """Los movimientos guardados, ya ordenados y sin filas corruptas."""
    found: list[tuple[str, float]] = []
    for row in stored.get("movements") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        try:
            found.append((str(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue
    found.sort(key=lambda item: item[0])
    return found


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
            "movements": [], "first_day": "", "last_day": "", "updated_at": "",
            "snapshots": [],
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

    def merge(
        self, node_id: str, key: str, *, login: Any, server: Any, currency: Any,
        movements: Sequence[tuple[Any, float]], period_start: str, period_end: str,
        taken_at: str = "",
    ) -> dict[str, Any]:
        """Mete el sondeo en la serie y devuelve el registro ya guardado."""
        with self.lock:
            stored = self.read(node_id, key)
            fresh = movement_rows(movements)
            kept = self._kept(stored_movements(stored), period_start, period_end)
            combined = sorted(
                [[moment, amount] for moment, amount in kept] + fresh,
                key=lambda row: row[0],
            )[-MAXIMUM_MOVEMENTS:]
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
                "first_day": combined[0][0][:10] if combined else "",
                "last_day": combined[-1][0][:10] if combined else "",
                "updated_at": taken_at or utc_now(),
                "snapshots": snapshots,
            }
            path = self._path(node_id, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            save_json(path, record)
            return record

    def last_day(self, node_id: str, key: str) -> str:
        """Hasta dónde llega la serie, que es desde dónde se vuelve a leer."""
        return str(self.read(node_id, key).get("last_day") or "")

    def day_keys(self, node_id: str, key: str) -> list[str]:
        """Los días con algún cierre, de viejo a nuevo. Para medir, no pintar."""
        seen: dict[str, None] = {}
        for moment, _amount in stored_movements(self.read(node_id, key)):
            seen[moment[:10]] = None
        return list(seen)


def day_of(moment: Any) -> str:
    """El día UTC de un instante guardado o recién leído."""
    return moment[:10] if isinstance(moment, str) else day_key(moment)
