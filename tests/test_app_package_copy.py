"""The Home Assistant image is built from heating_assistant/, not the repo root.

Root ``heatingassistant/`` and ``custom_components/heating_assistant/`` are the
edited copies. ``scripts/sync-ha-app-package.sh`` copies them into
``heating_assistant/``. This test fails when those copies drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]

PAIRS = (
    (ROOT / "heatingassistant", ROOT / "heating_assistant" / "heatingassistant"),
    (
        ROOT / "custom_components" / "heating_assistant",
        ROOT / "heating_assistant" / "custom_components" / "heating_assistant",
    ),
)


def _files(base: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        found[path.relative_to(base).as_posix()] = path
    return found


def test_app_package_matches_root() -> None:
    problems: list[str] = []
    for source, copy in PAIRS:
        source_files = _files(source)
        copy_files = _files(copy)
        label = f"{source.relative_to(ROOT)} -> {copy.relative_to(ROOT)}"
        for name in sorted(source_files.keys() - copy_files.keys()):
            problems.append(f"{label}: missing from app copy: {name}")
        for name in sorted(copy_files.keys() - source_files.keys()):
            problems.append(f"{label}: only in app copy: {name}")
        for name in sorted(source_files.keys() & copy_files.keys()):
            if source_files[name].read_bytes() != copy_files[name].read_bytes():
                problems.append(f"{label}: differs: {name}")
    assert not problems, "app package drift:\n" + "\n".join(problems)
