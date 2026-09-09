"""SWD-529: Linear and Nonlinear Tuning share Sample interval and Look-ahead."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TUNING_JS = ROOT / "heatingassistant" / "app" / "static" / "js" / "pages" / "tuning-controller.js"


def test_timing_labels_match_across_planners() -> None:
    source = TUNING_JS.read_text(encoding="utf-8")
    assert "Prediction horizon" not in source
    assert "SAMPLE_INTERVAL_HINT" in source
    assert "LOOK_AHEAD_HINT" in source
    assert 'label: \'Sample interval\'' in source
    assert source.count("Look-ahead") >= 2
    assert 'for="ctrl-linear_look_ahead_h">Look-ahead<' in source
    assert "form-label\" for=\"ctrl-horizon\"" not in source
    assert "ctrl-linear_look_ahead_h" in source
    assert "nmpc_horizon_h" in source
    assert "Plan period" in source
    linear_section, _, nmpc_section = source.partition("nmpcRestartSubsection")
    assert "'Timing'" in linear_section
    assert "'Timing'" in nmpc_section
    assert "insertBefore(derivedGroup" in source
    assert "syncHorizonFromLookAhead" in source
    assert "timingError()" in source


def test_apply_uses_shared_timing_error() -> None:
    source = TUNING_JS.read_text(encoding="utf-8")
    apply_body = source.split("btnApply.addEventListener('click'", 1)[1]
    assert "timingError()" in apply_body
    assert "prediction horizon" not in apply_body.lower()
