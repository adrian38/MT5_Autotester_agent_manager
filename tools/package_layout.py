"""Donde vive cada modulo de `mt5_manager`, que ya no es una carpeta plana.

El paquete eran 167 modulos en la raiz, asi que media docena de guardas
localizaban uno con `ROOT / "mt5_manager" / f"{name}.py"`. Al repartirlo en
subpaquetes por dominio esa expresion dejo de resolver, y lo hizo de la peor
manera: con `FileNotFoundError` en unas y, donde la ruta alimentaba un `glob`,
en silencio y en verde.

Aqui esta la busqueda, una sola vez. Nadie mas tiene que saber en que dominio
cayo un modulo, asi que el siguiente movimiento no rompe a nadie.

    from tools.package_layout import module_path, module_source
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mt5_manager"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# El resolutor de estaticos es de produccion —lo usa el handler para servir—,
# asi que aqui se reexporta en vez de duplicarse: dos busquedas del mismo
# fichero acabarian discrepando.
from mt5_manager.static_files import (  # noqa: E402,F401
    STATIC_DIR, static_matching, static_names, static_path,
)


def static_source(name: str) -> str:
    """El texto del estatico que la URL `/<name>` sirve.

    Las pruebas lo leian con `STATIC / "app.js"`, y esa ruta plana dejo de
    resolver al repartir `static/` por pantallas. Pedirlo por su nombre de URL
    es ademas lo que se quiere comprobar: lo que el navegador recibe.
    """
    return static_path(name).read_text(encoding="utf-8")


def module_path(name: str, package: Path | None = None) -> Path:
    """El fichero del modulo `name`, en la raiz del paquete o en su dominio.

    Falla con el nombre pedido en vez de devolver una ruta que no existe: una
    guarda que lee un fichero vacio porque la ruta cambio es peor que una que se
    rompe.
    """
    base = package or PACKAGE
    stem = name[:-3] if name.endswith(".py") else name
    found = sorted(path for path in base.rglob(f"{stem}.py") if path.is_file())
    if not found:
        raise FileNotFoundError(f"{stem}.py no esta en {base}")
    if len(found) > 1:
        raise AssertionError(f"{stem}.py esta duplicado: {[str(p) for p in found]}")
    return found[0]


def has_module(name: str, package: Path | None = None) -> bool:
    """Si el modulo existe, para las guardas que comprueban su lista."""
    try:
        module_path(name, package)
    except FileNotFoundError:
        return False
    return True


def module_source(name: str, package: Path | None = None) -> str:
    """El texto del modulo, para las guardas que comparan por criterio."""
    return module_path(name, package).read_text(encoding="utf-8")


def domain_sources(prefix: str, package: Path | None = None) -> str:
    """Todos los modulos cuyo nombre empieza por `prefix`, leidos juntos.

    Es lo que necesita la paridad con el fork del nodo: compara **criterios**,
    no ficheros, asi que da igual en cuantos modulos este repartido el nodo y en
    que carpeta vivan.
    """
    base = package or PACKAGE
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(base.rglob(f"{prefix}*.py"))
        if path.is_file() and path.name != "__init__.py"
    )
