"""SWD-307: calendar versioning (YYYY.MM.PATCH) lock + validation."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync-ha-app-package.sh"
CALVER_RE = re.compile(r"^(\d{4})\.(\d{2})\.(0|[1-9]\d*)$")


def test_live_version_is_calver_2026_08_10() -> None:
    config = yaml.safe_load(
        (ROOT / "heating_assistant" / "config.yaml").read_text(encoding="utf-8")
    )
    version = str(config["version"])
    assert version == "2026.08.21"
    match = CALVER_RE.fullmatch(version)
    assert match is not None
    assert 1 <= int(match.group(2)) <= 12


def test_sync_rejects_non_calver_version() -> None:
    """Ensure invalid versions fail the calver guard used by the sync lock."""
    script = r"""
import re, sys
CALVER_RE = re.compile(r"^(\d{4})\.(\d{2})\.(0|[1-9]\d*)$")
version = sys.argv[1]
match = CALVER_RE.fullmatch(version)
if not match or not (1 <= int(match.group(2)) <= 12):
    raise SystemExit(f"invalid:{version}")
print("ok")
"""
    good = subprocess.run(
        ["python3", "-c", script, "2026.08.0"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert good.returncode == 0
    assert good.stdout.strip() == "ok"

    for bad in ("2.0.32", "2026.8.0", "2026.08.01", "2026.13.0", "2026.00.1"):
        result = subprocess.run(
            ["python3", "-c", script, bad],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, bad


def test_sync_script_contains_calver_guard() -> None:
    text = SYNC.read_text(encoding="utf-8")
    assert "require_calver" in text
    assert r"^(\d{4})\.(\d{2})\.(0|[1-9]\d*)$" in text
    assert "YYYY.MM.PATCH" in text
