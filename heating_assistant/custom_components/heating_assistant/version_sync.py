"""Version-sync helpers for the staged thin bridge."""

from __future__ import annotations

import json
from pathlib import Path

from .const import VERSION

LOADED_VERSION = VERSION


def disk_manifest_version(base_path: str | Path | None = None) -> str | None:
    manifest_path = Path(base_path or Path(__file__).parent) / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def restart_required(base_path: str | Path | None = None) -> bool:
    return disk_manifest_version(base_path) != LOADED_VERSION
