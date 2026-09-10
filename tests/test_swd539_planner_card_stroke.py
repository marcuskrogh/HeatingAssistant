"""SWD-539: planner selection cards use one even stroke."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TUNING_CSS = ROOT / "heatingassistant" / "app" / "static" / "css" / "pages" / "tuning.css"


def _mode_card_block(css: str) -> str:
    start = css.index(".tuning-mode-card {")
    end = css.index(".tuning-section {")
    return css[start:end]


def test_mode_cards_use_single_hairline_stroke() -> None:
    css = TUNING_CSS.read_text(encoding="utf-8")
    block = _mode_card_block(css)
    assert "border: 1px solid var(--border);" in block
    assert "border: 2px solid" not in block
    assert "box-shadow: 0 0 0 2px" not in block
    assert "width: 4px" not in block
    assert ".tuning-mode-card::before" not in block
    assert "padding: 20px;" in block
    assert ".tuning-mode-card__badge:empty" in block
    assert "outline-offset: 3px;" in block
    assert ".tuning-mode-card__radio::after" in block
