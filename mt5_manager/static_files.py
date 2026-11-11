"""Donde vive cada estatico, que ya no tiene que ser una carpeta plana.

**Las URLs son planas a proposito y no cambian.** Cada pagina referencia a sus
hermanos en absoluto (`src="/app.js"`, `href="/styles.css"`), `sw.js` solo manda
desde la raiz —un service worker no ve lo que esta por encima de su carpeta— y
el manifest declara `scope: "/"`. Asi que repartir los estaticos por pantalla no
puede tocar ni una URL: lo unico que cambia es donde estan en disco, y esto es
lo que traduce el nombre plano de la URL a su ruta.

La consecuencia tiene que estar dicha: **dos estaticos no pueden llamarse igual
en dos carpetas**, porque comparten el espacio de nombres de la URL. El indice
se construye al importar y revienta nombrando las dos rutas, que es la unica
forma de que no se descubra en produccion sirviendo el fichero equivocado.

Fuera de la raiz del paquete no cabe: `static/` es del paquete y lo sirven
cuatro dominios (`manager`, `experiment`, `prop` y las rutas de correlacion).
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _build_index() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(STATIC_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.name in found:
            raise AssertionError(
                "Dos estaticos con el mismo nombre comparten URL: "
                f"{found[path.name]} y {path}"
            )
        found[path.name] = path
    return found


_INDEX = _build_index()


def static_path(name: str) -> Path:
    """La ruta del estatico que la URL `/<name>` sirve.

    Un nombre que no esta devuelve la ruta que tendria en la raiz, que no
    existe: quien lo sirve ya comprueba `is_file()` y contesta 404. Asi un
    nombre de mas en `STATIC_FILES` sigue siendo un 404 y no un 500, y es la
    guarda —no el usuario— la que lo denuncia.
    """
    return _INDEX.get(name, STATIC_DIR / name)


def static_names() -> frozenset[str]:
    """Los nombres que hay en disco, para las guardas que los comparan."""
    return frozenset(_INDEX)


def static_matching(pattern: str) -> list[Path]:
    """Los estaticos cuyo **nombre** encaja con `pattern`, en cualquier carpeta.

    Es el sustituto de `STATIC_DIR.glob("*.html")`, que recorre un solo nivel:
    repartidas las pantallas por carpetas habria devuelto una lista vacia, y un
    bucle vacio deja la prueba en verde comprobando nada. Devuelve ordenado por
    nombre, que es lo que piden los mensajes de las guardas.
    """
    return [path for name, path in sorted(_INDEX.items()) if fnmatch(name, pattern)]
