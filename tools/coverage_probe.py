"""Cuenta cuántas veces el suite entero alcanza una función.

Un suite verde no prueba equivalencia. `optimize_strict_monthly_portfolio`,
`_recalculate_saved` y `generate_completion_proposal` tenían **cero** llamadas
mientras las 620 pruebas pasaban: refactorizarlas a ciegas no lo habría notado
nadie. Antes de tocar algo compartido, medir.

    python -m tools.coverage_probe mt5_manager.portfolio_saved:_recalculate_saved

Envuelve el nombre en su módulo y en todos los que ya lo tenían copiado, porque
``from X import f`` copia la referencia y sustituirla en X no intercepta nada
—es el mismo motivo por el que un `patch()` deja de interceptar al mover el
consumidor—. El informe dice en cuántos módulos quedó envuelto: si dice
``ninguno``, la medida no vale.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGES = ("mt5_manager", "portfolio_manager")


def import_everything() -> None:
    """Carga el proyecto entero, para envolver después de que se copien."""
    for name in PACKAGES:
        package = importlib.import_module(name)
        for module in pkgutil.walk_packages(package.__path__, f"{name}."):
            try:
                importlib.import_module(module.name)
            except Exception:                     # un módulo opcional no invalida la medida
                pass


def _counting(counts: dict[str, int], label: str, function):
    def wrapper(*args, **kwargs):
        counts[label] += 1
        return function(*args, **kwargs)
    wrapper.__name__ = getattr(function, "__name__", label)
    return wrapper


def _project_modules():
    for module in list(sys.modules.values()):
        if module and getattr(module, "__name__", "").startswith(PACKAGES):
            yield module


def wrap(label: str, counts: dict[str, int]) -> list[str]:
    """Sustituye la referencia allí donde apunte al original. Devuelve dónde."""
    module_name, _, attribute = label.partition(":")
    original = getattr(importlib.import_module(module_name), attribute)
    counted = _counting(counts, label, original)
    placed = []
    for module in _project_modules():
        if getattr(module, attribute, None) is original:
            setattr(module, attribute, counted)
            placed.append(module.__name__)
    return placed


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    sys.path.insert(0, str(REPO))
    os.chdir(REPO)                                # el suite abre rutas relativas
    import_everything()
    counts = {label: 0 for label in argv}
    placements = {label: wrap(label, counts) for label in argv}
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().discover("tests")
    )
    print()
    for label in argv:
        where = ", ".join(placements[label]) or "ninguno"
        print(f"{counts[label]:5d} invocaciones  {label}   (envuelto en: {where})")
    print("pruebas fallidas:", len(result.failures) + len(result.errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
