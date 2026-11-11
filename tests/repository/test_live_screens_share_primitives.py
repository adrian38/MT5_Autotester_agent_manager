"""Las cuatro vistas de rendimiento real calculan con las mismas funciones.

Resumen mensual, Portafolios activos, Cuentas en real y Análisis de auditoría
enseñan los mismos hechos en pantallas distintas. Cuando cada una se armaba sus
grupos, sus filtros y su identidad estrategia/símbolo, un cambio en una dejaba
a las otras contando otra cosa sin decirlo: una real atribuida a mano, una
posición aún abierta o un cierre descartado por lote podían entrar en una y no
en otra, y las dos pantallas daban un veredicto distinto de la misma auditoría.

Esta guarda no mide números: vigila que el cálculo siga viniendo de la
primitiva. Es una comprobación de texto porque en este equipo no hay runtime de
JavaScript, así que lo que se puede exigir es que la expresión prohibida no
exista fuera de su único dueño.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.package_layout import module_source  # noqa: E402

STATIC = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"

# El dueño de todo lo de abajo. Lo que aquí se prohíbe a las pantallas, aquí
# está escrito una vez.
PRIMITIVE = "live_audit_reproduction.js"

# Las pantallas que dibujan reproducción. El resumen mensual no aparece porque
# no agrupa: consume lo que le da `dashboard_metrics`.
CONSUMERS = (
    "live_audit_analysis.js",
    "dashboard_metrics.js",
    "live_audit_history_verdicts.js",
)

# Expresión prohibida -> qué estaría reimplementando quien la escriba.
FORBIDDEN = {
    "row.strategy || tester.strategy": "la identidad estrategia/símbolo",
    "groups.set(key,": "la agrupación por estrategia",
    "operation_comparisons || []": "el filtro de operaciones comparadas",
    "_real_operations || []": "el filtro de reales sin cuenta",
    ".filter(row => row.real?.close_time)": "el filtro de reales cerradas",
    "SHORTFALL_PCT =": "el umbral del veredicto",
}


class ScreenPrimitiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scripts = {
            name: (STATIC / name).read_text(encoding="utf-8")
            for name in (PRIMITIVE, *CONSUMERS)
        }

    def test_no_screen_rebuilds_the_calculation_it_should_be_asking_for(self) -> None:
        for name in CONSUMERS:
            script = self.scripts[name]
            for expression, what in FORBIDDEN.items():
                with self.subTest(screen=name, rebuilds=what):
                    self.assertNotIn(
                        expression, script,
                        f"{name} vuelve a implementar {what}: eso lo decide "
                        f"{PRIMITIVE} y la pantalla sólo tiene que llamarlo.",
                    )

    def test_the_primitive_is_the_one_that_does_implement_it(self) -> None:
        """Una guarda que prohíbe sin exigir se pasa borrando la función."""
        primitive = self.scripts[PRIMITIVE]
        for expression in FORBIDDEN:
            with self.subTest(expression=expression):
                self.assertIn(expression, primitive)

    def test_every_screen_asks_the_primitive_for_its_groups(self) -> None:
        for name in CONSUMERS:
            with self.subTest(screen=name):
                self.assertIn("strategyGroups(", self.scripts[name])

    def test_the_primitive_exports_what_the_screens_need(self) -> None:
        primitive = self.scripts[PRIMITIVE]
        for name in (
            "comparisonRows", "discardedRows", "strategyGroups",
            "unaccountedRows", "unmatchedRows", "verdict",
        ):
            with self.subTest(name=name):
                self.assertIn(f"function {name}(", primitive)
                self.assertRegex(primitive, rf"\n\s*{name},|,\s*{name},|{name},\n")


class AccountPerformanceTests(unittest.TestCase):
    """Cuentas en real y el resumen mensual miden con la misma primitiva.

    Ninguna de las dos calcula reproducción, pero las dos tienen que separar
    igual el Profit visible de MT5 del resultado neto comparable: lo visible
    sale de la columna Profit y el drawdown, el color, el objetivo y el
    veredicto salen de la serie neta. Si una de las dos volviera a agregar por
    su cuenta, la misma cuenta saldría con dos cifras el mismo día.
    """

    OWNERS = ("live_account_dashboard.py", "monthly_summary_build.py")

    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {
            name: module_source(name) for name in cls.OWNERS
        }

    def test_neither_screen_aggregates_the_series_on_its_own(self) -> None:
        for name, source in self.sources.items():
            for call in ("monthly_rows(", "daily_rows("):
                with self.subTest(screen=name, call=call):
                    self.assertNotIn(call, source)

    def test_both_screens_read_the_series_through_the_primitive(self) -> None:
        # El prefijo relativo lo decide donde vive cada pantalla: `.` para la
        # vecina del dominio `live_account`, `..live_account.` para el resumen
        # mensual. El criterio es de que modulo importa, no a que profundidad
        # esta el fichero, asi que el prefijo no se compara.
        for name, source in self.sources.items():
            with self.subTest(screen=name):
                self.assertRegex(
                    source, r"from \.+(?:live_account\.)?live_account_performance import",
                )
                self.assertIn("account_series(stored)", source)
                self.assertIn("monthly_performance(", source)

    def test_the_same_function_object_reaches_both_screens(self) -> None:
        """El texto no basta: dos imports del mismo nombre pueden ser dos cosas."""
        from mt5_manager.live_account import live_account_dashboard
        from mt5_manager.monthly_summary import monthly_summary_build

        self.assertIs(
            live_account_dashboard.monthly_performance,
            monthly_summary_build.monthly_performance,
        )
        self.assertIs(
            live_account_dashboard.account_series,
            monthly_summary_build.account_series,
        )


if __name__ == "__main__":
    unittest.main()
