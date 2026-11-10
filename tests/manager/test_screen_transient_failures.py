"""Un corte del sondeo no se enseña como error en las pantallas que refrescan.

Los agentes se desconectan a ratos y vuelven solos. La portada sondea cada 60 s,
Generación cada 5 s: con el aviso rojo en cada fallo, la pantalla se pasaba el
día diciendo «Failed to fetch» por algo que no estaba mal, y ese ruido tapa los
avisos que sí hay que leer.

No hay runtime de JavaScript en el equipo, así que se comprueba el texto de los
`.js`: lo que se exige es que la rama del corte salga antes del aviso.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).parents[2] / "mt5_manager" / "static"
# Las tres pantallas que repintan solas. `live_audit.js` ya se callaba.
POLLING_SCREENS = ("app.js", "accounts.js", "dashboard.js")
OFFLINE_LABEL = "'Sin conexión · reintentando…'"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def refresh_catch(text: str) -> str:
    """El `catch` del refresco periódico, desde el corte hasta el `finally`."""
    start = text.index(OFFLINE_LABEL)
    opening = text.rindex("} catch (error) {", 0, start)
    return text[opening:text.index("} finally {", start)]


class TransientFailureTests(unittest.TestCase):
    def test_every_polling_screen_knows_a_dropped_connection(self) -> None:
        for name in POLLING_SCREENS:
            with self.subTest(name):
                text = source(name)
                self.assertIn("const TRANSIENT_STATUSES = new Set([502, 503, 504]);", text)
                # `fetch` rechaza con `TypeError` cuando no hubo respuesta
                # ninguna: es el «Failed to fetch» que ve el usuario.
                self.assertIn("error instanceof TypeError", text)
                self.assertIn("TRANSIENT_STATUSES.has(error?.status)", text)

    def test_a_dropped_connection_never_reaches_the_red_toast(self) -> None:
        for name in POLLING_SCREENS:
            with self.subTest(name):
                block = refresh_catch(source(name))
                self.assertLess(
                    block.index("isTransientFailure(error)"), block.index("toast("),
                    f"{name}: el aviso rojo sale antes de descartar el corte",
                )
                transient = block[:block.index("toast(")]
                self.assertIn(OFFLINE_LABEL, transient)
                self.assertIn("return;", transient)

    def test_a_server_that_answers_with_a_reason_still_warns(self) -> None:
        for name in POLLING_SCREENS:
            with self.subTest(name):
                block = refresh_catch(source(name))
                self.assertIn("toast(error.message, true)", block)
                self.assertIn("'Error de conexión'", block)

    def test_the_status_travels_with_the_failure(self) -> None:
        # Sin el codigo en el error, el `catch` no puede distinguir un 502 del
        # agente de un 400 que si hay que leer.
        for name in POLLING_SCREENS:
            with self.subTest(name):
                text = source(name)
                self.assertRegex(
                    text, re.compile(r"function failure\(message, status\) \{\s+"
                                     r"const error = new Error\(message \|\| `HTTP \$\{status\}`\);\s+"
                                     r"error\.status = status;"),
                )
                self.assertIn("throw failure(", text)


if __name__ == "__main__":
    unittest.main()
