"""`pyproject.toml` no lo ejecuta nadie hasta que alguien instala el paquete.

Y entonces es tarde. Las dos cosas que declara sobre el codigo se rompieron en
silencio al repartir `mt5_manager/` por dominios, el 2026-11-11:

- **Los puntos de entrada.** `mt5-manager = "mt5_manager.manager:main"` apuntaba
  al modulo `manager.py`; convertido `manager` en paquete con `__init__` vacio,
  ese `main` dejo de existir. `python -m mt5_manager.manager` seguia valiendo
  —lo sirve su `__main__.py`— y el contenedor arranca por `docker_entrypoint`,
  asi que ni el suite ni produccion lo notaron: solo lo habria notado quien
  hiciera `pip install .` y escribiera `mt5-manager`.
- **Los datos del paquete.** `static/*.html` mira un solo nivel. Repartidos los
  estaticos por pantallas, el paquete instalado se habria quedado sin interfaz,
  tambien en silencio, porque la imagen los copia aparte con `COPY`.

Las dos son la misma clase de fallo que `test_agents_md_stacks` vigila en la
documentacion: algo que describe al codigo y deja de describirlo.
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - solo en Python 3.10
    tomllib = None

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "mt5_manager"


def manifest() -> dict:
    if tomllib is None:
        raise unittest.SkipTest("`tomllib` necesita Python 3.11; el equipo usa 3.14")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class EntryPointTests(unittest.TestCase):
    def test_every_console_script_resolves_to_something_callable(self) -> None:
        scripts = manifest()["project"]["scripts"]
        self.assertTrue(scripts, "pyproject ya no declara puntos de entrada")
        broken = {}
        for name, target in scripts.items():
            module_name, _, attribute = target.partition(":")
            try:
                found = getattr(importlib.import_module(module_name), attribute, None)
            except Exception as exc:                     # noqa: BLE001
                broken[name] = f"{target}: {type(exc).__name__}"
                continue
            if not callable(found):
                broken[name] = f"{target}: no es invocable"
        self.assertFalse(
            broken,
            "Punto de entrada roto. Un paquete con `__init__` vacio no tiene el "
            f"`main` de su modulo interior: {broken}",
        )


class PackageDataTests(unittest.TestCase):
    def test_every_static_file_is_declared_as_package_data(self) -> None:
        # Con los patrones de verdad, no con `fnmatch`: ahi `*` cruza las
        # barras y `**/` exige una, asi que `static/**/*.js` no casaria con
        # `static/app.js` y la guarda acusaria a quien no es. `setuptools`
        # resuelve con `glob(recursive=True)`, donde `**` son cero o mas
        # carpetas, que es lo que hace `Path.glob`.
        patterns = manifest()["tool"]["setuptools"]["package-data"]["mt5_manager"]
        covered = {
            path for pattern in patterns for path in PACKAGE.glob(pattern)
            if path.is_file()
        }
        missing = sorted(
            str(path.relative_to(PACKAGE)).replace("\\", "/")
            for path in sorted((PACKAGE / "static").rglob("*"))
            if path.is_file() and path not in covered
        )
        self.assertFalse(
            missing,
            "Estatico que el paquete instalado no llevaria. Si es de un tipo "
            "nuevo, declaralo; si esta en una carpeta, el patron necesita `**`: "
            f"{missing}",
        )

    def test_the_patterns_reach_into_subfolders(self) -> None:
        """Sin `**` el reparto por pantallas vuelve a perderse en silencio."""
        patterns = manifest()["tool"]["setuptools"]["package-data"]["mt5_manager"]
        flat = sorted(p for p in patterns if p.startswith("static/") and "**" not in p)
        self.assertFalse(
            flat, f"patrones de `static/` que solo miran un nivel: {flat}",
        )


if __name__ == "__main__":
    unittest.main()
