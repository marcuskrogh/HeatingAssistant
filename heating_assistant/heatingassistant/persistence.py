"""JSON persistence for the Heating Assistant application."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import tempfile
from typing import Any

CONFIG_FILENAME = "config.json"
STATE_FILENAME = "state.json"


def _path(data_dir: str | Path, filename: str) -> Path:
    if "/" in filename or filename in {"", ".", ".."}:
        raise ValueError("filename must be a simple JSON file name")
    return Path(data_dir) / filename


def load_json(
    data_dir: str | Path,
    filename: str,
    *,
    default: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load a JSON object from ``data_dir`` or return ``default`` when absent."""

    path = _path(data_dir, filename)
    if not path.exists():
        return dict(default or {})
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def save_json(data_dir: str | Path, filename: str, data: Mapping[str, Any]) -> Path:
    """Atomically save a JSON object under ``data_dir``."""

    path = _path(data_dir, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(data), handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


def load_config(data_dir: str | Path) -> dict[str, Any]:
    """Load application configuration."""

    return load_json(data_dir, CONFIG_FILENAME)


def save_config(data_dir: str | Path, config: Mapping[str, Any]) -> Path:
    """Save application configuration."""

    return save_json(data_dir, CONFIG_FILENAME, config)


def load_state(data_dir: str | Path) -> dict[str, Any]:
    """Load application runtime state."""

    return load_json(data_dir, STATE_FILENAME)


def save_state(data_dir: str | Path, state: Mapping[str, Any]) -> Path:
    """Save application runtime state."""

    return save_json(data_dir, STATE_FILENAME, state)
