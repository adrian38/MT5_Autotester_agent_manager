"""Los ficheros en disco de un portafolio manual, y cómo retirarlos al borrarlo.

Un portafolio manual no vive sólo en SQLite. El runtime del agente guarda los
`.set` que subió el usuario en
`<runtime>/<nodo>/manual_portfolios/<uuid>/sets/` y deja la ruta completa en
`set_path`. Borrar el portafolio quitaba únicamente las filas, así que esa
carpeta quedaba huérfana para siempre: nadie la nombra ya y nada la recoge.

La regla de aquí es deliberadamente estrecha, porque borrar ficheros no se
deshace:

- sólo la carpeta `<uuid>` cuyo `sets/` contiene el `.set` guardado;
- sólo si el abuelo de ese `.set` se llama `manual_portfolios`;
- sólo si cae dentro de la raíz de runtime que pasa el llamante;
- sólo si ninguna fila de otro portafolio sigue apuntando a esa carpeta.

Un `set_path` de un portafolio UBS —`<proyecto>/data/<broker>/…`— no cumple
ninguna de las tres primeras, así que nunca se toca. Y que la carpeta ya no
esté no es un error: el borrado del portafolio no depende de ella.

ATENCIÓN: la copia que de verdad se ejecuta al borrar desde la interfaz es la
del agente, `manager_node_runtime/manual_portfolio.py`, que reimplementa estas
tres funciones con los mismos nombres. Cambiar sólo este fichero no tiene
efecto sobre ningún nodo: ver `AGENTS.md`, sección «El nodo NO ejecuta este
repositorio», y `ai_context/manual_portfolio_files_survive_delete.md`.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import dev_branch
from .portfolio_schema import _table_exists


MANUAL_PORTFOLIOS_DIRNAME = "manual_portfolios"
MANUAL_SETS_DIRNAME = "sets"
MEMBER_TABLES = ("portfolio_allocations", "portfolio_members")


def _key(folder: Path) -> str:
    return folder.as_posix().casefold()


def _within(folder: Path, root: Path) -> bool:
    """Comparación por texto: resolver una unidad de red cuesta segundos."""
    base = root.as_posix().rstrip("/").casefold()
    target = _key(folder)
    return bool(base) and (target == base or target.startswith(base + "/"))


def manual_portfolio_folder(root: Path | str, set_path: Any) -> Path | None:
    """`<root>/…/manual_portfolios/<uuid>` de un set manual, o `None`.

    `root` es el árbol donde el nodo creó `manual_portfolios`: su `runtime_dir`
    en el agente, `<proyecto>/runtime` visto desde el manager.
    """
    text = str(set_path or "").replace("\\", "/").strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.is_absolute() or candidate.parent.name.casefold() != MANUAL_SETS_DIRNAME:
        return None
    folder = candidate.parent.parent
    if folder.parent.name.casefold() != MANUAL_PORTFOLIOS_DIRNAME:
        return None
    return folder if _within(folder, Path(root).expanduser().absolute()) else None


def _set_paths(conn: sqlite3.Connection, portfolio_id: int, mine: bool) -> list[Any]:
    comparison = "=" if mine else "<>"
    values: list[Any] = []
    for table in MEMBER_TABLES:
        if _table_exists(conn, table):
            values.extend(
                row[0] for row in conn.execute(
                    f"select set_path from {table} where portfolio_id{comparison}?",
                    (portfolio_id,),
                )
            )
    return values


def manual_portfolio_folders(root: Path | str, set_paths: Iterable[Any]) -> list[Path]:
    """Las carpetas distintas que nombran estos `set_path`, sin repetir."""
    found: dict[str, Path] = {}
    for value in set_paths:
        folder = manual_portfolio_folder(root, value)
        if folder is not None:
            found.setdefault(_key(folder), folder)
    return list(found.values())


def manual_portfolio_folders_only_for(
    conn: sqlite3.Connection, root: Path | str, portfolio_id: int,
) -> list[Path]:
    """Carpetas que sólo usa este portafolio, listas para retirar.

    Se llama **antes** de borrar ninguna fila, por dos motivos: así la consulta
    de «las de los demás» no se confunde con las que están desapareciendo, y
    así `dev_branch.assert_writable` —obligatorio en todo punto de escritura
    hacia el proyecto de un agente— rechaza el borrado entero en vez de dejarlo
    a medias.
    """
    mine = manual_portfolio_folders(root, _set_paths(conn, portfolio_id, mine=True))
    shared = {_key(folder) for folder in manual_portfolio_folders(
        root, _set_paths(conn, portfolio_id, mine=False)
    )}
    folders = [folder for folder in mine if _key(folder) not in shared]
    for folder in folders:
        dev_branch.assert_writable(folder, "ficheros de un portafolio manual")
    return folders


def discard_manual_portfolio_folders(folders: Iterable[Path]) -> list[str]:
    """Retira cada carpeta y devuelve las que ya no están.

    Que falte, o que el sistema de ficheros se niegue, no interrumpe el borrado
    del portafolio: las filas ya no existen y repetir la acción no las devuelve.
    """
    removed: list[str] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            removed.append(str(folder))
    return removed
