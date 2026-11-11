"""`static/` es una carpeta por pantalla, y su URL sigue siendo plana.

Eran 75 ficheros sueltos de 18 pantallas. Lo que hace raro este reparto es que
el espacio de nombres de la URL **no** se movio con ellos: cada pagina
referencia a sus hermanos en absoluto (`src="/app.js"`), `sw.js` solo manda
desde la raiz y el manifest declara `scope: "/"`. Asi que hay dos capas que no
pueden separarse sin que alguien se entere:

- La URL la resuelve `mt5_manager/static_files.py`. Dos estaticos con el mismo
  nombre en dos carpetas comparten URL, y eso revienta al importar.
- El disco lo vigila esto: ni un fichero suelto en la raiz de `static/`, ni una
  carpeta sin declarar, y nadie construyendo la ruta de un estatico a mano.

La tercera es la que de verdad aguanta el reparto. Mientras nadie escriba
`STATIC / "app.js"`, mover un estatico de carpeta es un `git mv` y nada mas; la
primera vez costo 258 sitios en 39 ficheros de prueba, y diez de ellos eran
`glob("*.html")` sobre un solo nivel, que al moverse habrian devuelto una lista
vacia dejando la guarda **verde sin comprobar nada**.
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.static_files import build_index  # noqa: E402
from tools.package_layout import static_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "mt5_manager" / "static"

#: Una carpeta por pantalla. Los nombres son los dominios del paquete, con dos
#: que no tienen subpaquete propio: `generation` es el `index.html` historico y
#: `correlation` se sirve desde el dominio del manager.
SCREENS = (
    "correlation",
    "experiment",
    "generation",
    "live_account",
    "live_audit",
    "manual_check",
    "monthly_summary",
    "portfolio",
    "prop",
    "shared",
)

#: Quien si puede nombrar la carpeta: el resolutor, su reexportacion y la
#: guarda de empaquetado, que comprueba los patrones de `pyproject.toml`.
MAY_NAME_THE_FOLDER = {
    "mt5_manager/static_files.py",
    "tools/package_layout.py",
    "tests/repository/test_packaging.py",
    "tests/repository/test_static_layout.py",
    # Este **escribe** los tres iconos, no los lee: tiene que declarar el
    # destino. Si lo pusiera en la raiz, falla `test_no_static_file_is_left_loose`.
    "tools/make_push_icons.py",
}

#: `/ "static"`, `/ "static/algo"` o `"static" /`: las formas de construir la
#: ruta a mano que el reparto dejo sin usuarios.
BY_HAND = re.compile(r"/ ['\"]static['\"]|['\"]static/|['\"]static['\"] /")


class ScreenFoldersTest(unittest.TestCase):
    def test_no_static_file_is_left_loose_in_the_root(self) -> None:
        loose = sorted(path.name for path in STATIC.iterdir() if path.is_file())
        self.assertFalse(
            loose,
            "Estatico sin pantalla. Elige la carpeta de la pantalla que lo usa, "
            f"o `shared/` si lo montan varias: {loose}\n"
            f"  pantallas: {', '.join(SCREENS)}",
        )

    def test_every_folder_is_a_declared_screen(self) -> None:
        undeclared = sorted(
            path.name for path in STATIC.iterdir()
            if path.is_dir() and path.name not in SCREENS
        )
        self.assertFalse(
            undeclared,
            "Carpeta de `static/` sin declarar. Si es una pantalla nueva, entra "
            f"en SCREENS y en la tabla de AGENTS.md: {undeclared}",
        )

    def test_the_declared_screens_exist_and_are_not_empty(self) -> None:
        empty = sorted(
            name for name in SCREENS
            if not any((STATIC / name).glob("*")) if (STATIC / name).is_dir()
        )
        missing = sorted(name for name in SCREENS if not (STATIC / name).is_dir())
        self.assertEqual([], missing, f"SCREENS nombra carpetas que no existen: {missing}")
        self.assertEqual([], empty, f"carpetas de pantalla vacias: {empty}")


class FlatUrlNamespaceTest(unittest.TestCase):
    def test_the_index_refuses_two_statics_with_the_same_name(self) -> None:
        """Comparten URL: uno taparia al otro y nadie lo sabria.

        Sobre el arbol de verdad esto no se puede comprobar: `static_files`
        revienta al importarse y la prueba no llega a correr. Asi que se
        comprueba el rechazo, que es la regla, sobre un arbol de mentira.
        """
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for screen in ("shared", "portfolio"):
                (base / screen).mkdir()
                (base / screen / "app.js").write_text("//", encoding="utf-8")
            with self.assertRaises(AssertionError) as caught:
                build_index(base)
        self.assertIn("comparten URL", str(caught.exception))
        self.assertIn("app.js", str(caught.exception))

    def test_the_index_sees_every_file_on_disk(self) -> None:
        on_disk = {path.name for path in STATIC.rglob("*") if path.is_file()}
        self.assertEqual(on_disk, set(static_names()))


class NobodyBuildsThePathTest(unittest.TestCase):
    """El trinquete: sin esto, el siguiente reparto vuelve a costar 258 sitios."""

    def test_nobody_names_the_static_folder_to_reach_a_file(self) -> None:
        offenders = {}
        for folder in ("tests", "tools", "mt5_manager"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                relative = str(path.relative_to(ROOT)).replace("\\", "/")
                if relative in MAY_NAME_THE_FOLDER:
                    continue
                lines = [
                    number for number, line in
                    enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                    if BY_HAND.search(line)
                ]
                if lines:
                    offenders[relative] = lines
        self.assertFalse(
            offenders,
            "Ruta a un estatico construida a mano. Pidelo por su nombre de URL "
            "con `static_source`, `static_path` o `static_matching`, que es "
            "ademas lo que el navegador recibe:\n"
            + "\n".join(f"  {name}: {lines}" for name, lines in sorted(offenders.items())),
        )


if __name__ == "__main__":
    unittest.main()
