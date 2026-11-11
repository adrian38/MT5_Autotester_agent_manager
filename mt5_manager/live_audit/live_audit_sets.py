from __future__ import annotations

from pathlib import Path


def resolve_portfolio_set(project: Path, raw: str) -> Path:
    """Resolve a saved set path without guessing between different contents."""
    path = Path(raw)
    if path.is_file():
        return path
    normalized = raw.replace("\\", "/")
    for prefix in ("/data/ic/", "/data/axi/", "/data/roboforex/"):
        if normalized.casefold().startswith(prefix):
            candidate = project / normalized[len(prefix):]
            if candidate.is_file():
                return candidate
    matches = sorted(project.rglob(path.name), key=lambda item: str(item).casefold()) if path.name else []
    if len(matches) == 1:
        return matches[0]
    if matches:
        first_contents = matches[0].read_bytes()
        if all(candidate.read_bytes() == first_contents for candidate in matches[1:]):
            return matches[0]
        raise FileNotFoundError(
            f"Se encontraron varios sets distintos para {path.name or raw}: {len(matches)} coincidencias"
        )
    raise FileNotFoundError(f"No se encontró el set del portafolio: {path.name or raw}")
