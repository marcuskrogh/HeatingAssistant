"""SWD-526: Tuning mode cards, Apply-to-switch, operator timing knobs."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TUNING_JS = ROOT / "heatingassistant" / "app" / "static" / "js" / "pages" / "tuning-controller.js"
TUNING_CSS = ROOT / "heatingassistant" / "app" / "static" / "css" / "pages" / "tuning.css"


def test_mode_cards_split_name_and_subtitle() -> None:
    source = TUNING_JS.read_text(encoding="utf-8")
    css = TUNING_CSS.read_text(encoding="utf-8")
    assert "tuning-mode-card__name" in source
    assert "tuning-mode-card__subtitle" in source
    assert "tuning-mode-card__radio" in source
    assert ">model predictive control<" in source
    assert "Fast substeps" not in source.split("NMPC_HIDDEN_PARAM_DEFS", 1)[0]
    assert 'label: \'Fast substeps\'' not in source.split("NMPC_HIDDEN_PARAM_DEFS", 1)[0]
    assert "form-label\" for=\"ctrl-nmpc_fast_substeps\"" not in source
    assert "Plan period" not in source
    assert "LINEAR_LIVE_PARAM_DEFS" in source
    assert "Setpoint pull" in source
    assert "Heater-effort penalty" in source
    shared, _linear_live, rest = source.partition("LINEAR_LIVE_PARAM_DEFS")
    assert "tracking_weight" not in shared.split("SHARED_LIVE_PARAM_DEFS", 1)[1]
    assert "energy_weight" not in shared.split("SHARED_LIVE_PARAM_DEFS", 1)[1]
    assert "terminal_weight" not in shared.split("SHARED_LIVE_PARAM_DEFS", 1)[1]
    assert "linearLiveSubsection.hidden" in rest
    assert "tuning-mode-card--in-use" in css
    assert "tuning-mode-card--selected" in css
    assert "tuning-mode-card--draft" in css
    assert "font-size: var(--type-subtitle)" in css


def test_card_click_does_not_call_apply() -> None:
    source = TUNING_JS.read_text(encoding="utf-8")
    click = source.split("btn.addEventListener('click'", 1)[1].split("});", 1)[0]
    assert "updateControllerTuning" not in click
    assert "selectedMode = card.key" in click
    assert "btnApply.addEventListener('click'" in source
    apply_body = source.split("btnApply.addEventListener('click'", 1)[1]
    assert "updateControllerTuning" in apply_body
    assert "nmpc_fast_substeps" in source
    assert "Look-ahead must be a whole number of sample intervals" in source
