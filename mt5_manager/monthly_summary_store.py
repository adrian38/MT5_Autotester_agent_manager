"""Los resúmenes ya calculados, uno por mes, y cuáles se han mirado.

Vive en el manager y no en el navegador por lo mismo que la campana: el
resumen del 1 de noviembre tiene que seguir ahí el día 12, desde cualquier
navegador y aunque nadie tuviera la página abierta ese día.

Se guarda **el resultado, no la receta**. Un resumen es la foto de un mes con
los datos que había al cerrarlo, y volver a calcularlo en marzo daría otra
cosa: para entonces el archivo tiene auditorías que entonces no estaban y la
promesa del backtest puede haberse vuelto a medir a otros lotes. Lo que se
guarda es lo que se decidió con lo que se sabía, que es lo único que después
se puede releer sin mentir. Recalcularlo a mano sigue siendo posible —«Volver
a calcular» en el panel—, y entonces sustituye a la foto y lo dice con su
`generated_at` nuevo.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now

MAXIMUM_MONTHS = 24
"""Meses conservados. Dos años caben de sobra en un JSON y son los que dan
contexto a una racha; lo anterior ya no decide sobre la cartera de hoy."""


class MonthlySummaryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.reports: dict[str, dict[str, Any]] = {}
        self.read: dict[str, str] = {}
        if self.path.is_file():
            try:
                stored = load_json(self.path)
            except ValueError:
                stored = {}
            self.reports = {
                str(label): dict(value)
                for label, value in (stored.get("reports") or {}).items()
                if isinstance(value, dict)
            }
            self.read = {
                str(label): str(value)
                for label, value in (stored.get("read") or {}).items()
            }

    def _save(self) -> None:
        save_json(self.path, {"reports": self.reports, "read": self.read})

    def _prune(self) -> None:
        for label in sorted(self.reports)[:-MAXIMUM_MONTHS]:
            self.reports.pop(label, None)
            self.read.pop(label, None)

    def remember(self, report: dict[str, Any]) -> dict[str, Any]:
        """Guarda el resumen de un mes y lo devuelve ya marcado como sin leer.

        Recalcular un mes **borra su marca de leído**: lo que el usuario miró
        ya no es lo que hay, y dejar la marca escondería el resumen nuevo
        detrás de un botón sin chapa.
        """
        label = str(report.get("month") or "")
        if not label:
            raise ValueError("Un resumen tiene que decir de qué mes es")
        with self.lock:
            self.reports[label] = dict(report)
            self.read.pop(label, None)
            self._prune()
            self._save()
            return dict(self.reports[label])

    def months(self) -> list[str]:
        """Los meses guardados, del más reciente al más antiguo."""
        with self.lock:
            return sorted(self.reports, reverse=True)

    def report(self, label: str = "") -> dict[str, Any]:
        """El resumen de ese mes, o el último guardado si no se nombra."""
        with self.lock:
            wanted = str(label or "") or (sorted(self.reports)[-1] if self.reports else "")
            return dict(self.reports.get(wanted) or {})

    def mark_read(self, label: str = "") -> dict[str, Any]:
        with self.lock:
            wanted = str(label or "") or (sorted(self.reports)[-1] if self.reports else "")
            if wanted in self.reports:
                self.read[wanted] = utc_now()
                self._save()
        return self.state(label)

    def unread(self) -> list[str]:
        """Los meses guardados que nadie ha abierto todavía."""
        with self.lock:
            return sorted(
                (label for label in self.reports if label not in self.read),
                reverse=True,
            )

    def state(self, label: str = "") -> dict[str, Any]:
        """Lo que el panel necesita: un mes, cuáles hay y qué falta por mirar."""
        report = self.report(label)
        with self.lock:
            month = str(report.get("month") or "")
            return {
                "report": report,
                "months": sorted(self.reports, reverse=True),
                "unread": sorted(
                    (key for key in self.reports if key not in self.read), reverse=True,
                ),
                "read_at": self.read.get(month, ""),
            }
