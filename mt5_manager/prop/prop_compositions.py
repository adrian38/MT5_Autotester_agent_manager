"""Copias de las composiciones de fase 1 guardadas en el manager."""

import hashlib
import json
from pathlib import Path
from typing import Any

from ..common import load_json, save_json, utc_now


def composition_snapshot(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result or not result.get("allocations"):
        return None
    fields = ("name", "revision", "result_schema_version", "profile", "settings", "axis",
              "phase_one", "evaluation", "allocations", "excluded_keys")
    snapshot = {field: result[field] for field in fields if field in result}
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"id": hashlib.sha256(encoded).hexdigest(), **snapshot}


class PropCompositionArchive:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        return load_json(self.path) if self.path.is_file() else {}

    def get(self, composition_id: str) -> dict[str, Any] | None:
        return self._load().get(composition_id)

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item["id"], "saved_at": item["saved_at"],
                "revision": item.get("revision", 1), "members": len(item["allocations"]),
                "capital": item.get("settings", {}).get("capital"),
                "passed": item.get("evaluation", {}).get("passed"),
                "median_days": item.get("evaluation", {}).get("median_days"),
            }
            for item in reversed(list(self._load().values()))
        ]

    def save(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        saved = self._load()
        composition_id = snapshot["id"]
        if composition_id not in saved:
            saved[composition_id] = {**snapshot, "saved_at": utc_now()}
            save_json(self.path, saved)
        return saved[composition_id]
