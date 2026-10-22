"""Toda pieza que una pantalla del manager pide tiene que estar en la lista servida.

`STATIC_FILES` es una lista de permitidos: un `.js` nuevo referenciado desde el
HTML pero sin su línea ahí devuelve 404 y **la pantalla no se rompe a la vista**
—el resto sigue funcionando—, sólo deja de existir lo que ese script montaba.
Pasó con `portfolio_manual_check.js` el 2026-10-21: el botón salía y no abría
nada. El suite entero estaba verde porque nadie servía la página de verdad.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from mt5_manager.correlation_routes import STATIC_FILES as CORRELATION_FILES
from mt5_manager.experiment_routes import STATIC_FILES as EXPERIMENT_FILES
from mt5_manager.manager_handler import STATIC_DIR, STATIC_FILES


SERVED = STATIC_FILES | CORRELATION_FILES | EXPERIMENT_FILES
"""Las tres puertas: la lista general y las dos pantallas con ruta propia."""


REFERENCE = re.compile(r"""(?:src|href)=["']/([A-Za-z0-9_.-]+\.(?:js|css|webmanifest))["']""")


class StaticAssetsServedTest(unittest.TestCase):
    def test_every_asset_referenced_by_a_page_is_served(self) -> None:
        missing: dict[str, set[str]] = {}
        # Todas: las que no estan en la lista las sirve su propia ruta
        # (`/`, `/generation.html`, correlacion, experimenta), y lo que piden
        # sale igualmente de la lista.
        for page in sorted(STATIC_DIR.glob("*.html")):
            referenced = set(REFERENCE.findall(page.read_text(encoding="utf-8")))
            absent = {
                name for name in referenced
                if name not in SERVED and (STATIC_DIR / name).is_file()
            }
            if absent:
                missing[page.name] = absent
        self.assertFalse(
            missing,
            "Estas pantallas piden ficheros que el manager no sirve; añadir su "
            f"línea a STATIC_FILES en manager_handler.py: {missing}",
        )

    def test_the_list_does_not_promise_files_that_no_longer_exist(self) -> None:
        gone = sorted(name for name in STATIC_FILES if not (STATIC_DIR / name).is_file())
        self.assertFalse(gone, f"STATIC_FILES nombra ficheros que ya no existen: {gone}")


if __name__ == "__main__":
    unittest.main()
