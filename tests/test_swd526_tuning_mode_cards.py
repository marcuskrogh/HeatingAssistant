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
    assert ">model predictive control<" in source
    assert "Fast substeps" not in source.split("NMPC_HIDDEN_PARAM_DEFS", 1)[0]
    assert 'label: \'Fast substeps\'' not in source.split("NMPC_HIDDEN_PARAM_DEFS", 1)[0]
    assert "form-label\" for=\"ctrl-nmpc_fast_substeps\"" not in source
    assert "Plan period" in source
    assert "tuning-mode-card--in-use" in css
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
    assert "Must divide the plan period" in source
