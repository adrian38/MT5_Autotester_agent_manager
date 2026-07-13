from __future__ import annotations

import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent

RELOCATABLE_WORKSPACE_DIRS = ("outputs", "sets", "reports", "configs", "assets")


def resolve_workspace_path(value: str | Path, *, base_dir: str | Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.exists():
        return path

    root = Path(base_dir).expanduser() if base_dir is not None else BASE_DIR
    if not path.is_absolute():
        candidate = root / path
        return candidate if candidate.exists() else path

    parts = path.parts
    lower_parts = [part.lower() for part in parts]
    for root_name in RELOCATABLE_WORKSPACE_DIRS:
        try:
            root_index = lower_parts.index(root_name)
        except ValueError:
            continue
        candidate = root.joinpath(*parts[root_index:])
        if candidate.exists():
            return candidate
    return path


def workspace_path_exists(value: str | Path, *, base_dir: str | Path | None = None) -> bool:
    return resolve_workspace_path(value, base_dir=base_dir).exists()
