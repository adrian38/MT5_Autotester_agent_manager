"""Notas de `ai_context/` que el índice no menciona.

`AGENTS.md` llama a `ai_context/` «la primera consulta», y su `README.md` dice
ser el índice. Llegó a tener 24 de 42 notas sin indexar, varias de ellas
críticas —`guided_batches.md`, `cross_scope_parity.md`—. Un índice a medias es
peor que ninguno: quien lo lee cree que ya ha visto lo que hay.

    python -m tools.ai_context_index

Devuelve 1 si falta alguna, así que sirve igual desde un script. No inventa
descripciones: la línea la escribe quien conoce la nota.
"""
from __future__ import annotations

import sys
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "ai_context"
INDEX = CONTEXT / "README.md"


def notes() -> list[Path]:
    return sorted(p for p in CONTEXT.glob("*.md") if p.name != INDEX.name)


def missing() -> list[Path]:
    """Las notas cuyo nombre de fichero no aparece en el índice."""
    index = INDEX.read_text(encoding="utf-8")
    return [path for path in notes() if path.name not in index]


def main() -> int:
    absent = missing()
    for path in absent:
        print(f"sin indexar: {path.name}")
    print(f"{len(notes())} notas, {len(absent)} sin indexar")
    return 1 if absent else 0


if __name__ == "__main__":
    raise SystemExit(main())
