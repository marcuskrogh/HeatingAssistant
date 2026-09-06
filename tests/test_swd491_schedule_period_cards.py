"""SWD-491: persisted schedule periods must render as editable cards."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _run(name: str) -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / name)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_schedules_module_defines_ensure_when_state() -> None:
    _run("panel_schedules_module.harness.mjs")


def test_schedule_detail_renders_persisted_period_cards() -> None:
    _run("panel_schedules_detail_cards.harness.mjs")
