"""Base comun de las guardas de tamano: que ficheros se miden y como se
mantiene su trinquete.

Lo usan ``tools/function_length.py`` (techo por funcion) y
``tools/file_length.py`` (techo por fichero). Aqui esta lo unico que las dos
comparten, para que no puedan discrepar sobre el alcance ni sobre el formato
del baseline.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_PARTS = {".git", "__pycache__", "runtime", ".venv", "node_modules", "build", "dist"}
"""Lo que no es codigo nuestro. ``runtime/`` trae 232 ficheros de node-gyp.

No se anaden rutas aqui para esquivar una guarda: si el fichero es nuestro y no
cabe, se parte.
"""


def iter_python_files(root: Path | None = None):
    base = root or ROOT
    for path in sorted(base.rglob("*.py")):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        yield path


def load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["grandfathered"]


def write_baseline(path: Path, entries: dict[str, int], limit: int, subject: str) -> None:
    """Escribe el baseline ya filtrado. Solo deberia usarse para bajarlo."""
    path.write_text(
        json.dumps(
            {
                "_comment": (
                    f"{subject} que ya superaba(n) {limit} lineas cuando se instalo la "
                    "guarda. Esta lista solo puede encoger: al partir, borra su "
                    "entrada. Regenerar no es una forma valida de silenciar el test."
                ),
                "max_lines": limit,
                "grandfathered": dict(sorted(entries.items())),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def ratchet_offenders(
    sizes: dict[str, int], baseline: dict[str, int], limit: int
) -> dict[str, dict]:
    """Las tres formas de romper un trinquete, con una sola definicion.

    ``new``: supera el techo y no estaba perdonado. ``grown``: perdonado, pero
    mas grande que lo registrado. ``stale``: entrada del baseline que ya no hace
    falta, porque se partio, se renombro o desaparecio.
    """
    return {
        "new": {k: v for k, v in sizes.items() if v > limit and k not in baseline},
        "grown": {
            k: (baseline[k], sizes[k])
            for k in baseline
            if k in sizes and sizes[k] > baseline[k]
        },
        "stale": {k: baseline[k] for k in baseline if sizes.get(k, 0) <= limit},
    }
