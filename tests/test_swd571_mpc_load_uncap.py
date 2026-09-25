"""SWD-571: MPC Load percent is not capped at 100%."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TREES = (
    ROOT / "heatingassistant" / "app" / "static" / "js" / "kpi-engine.js",
    ROOT / "heating_assistant" / "heatingassistant" / "app" / "static" / "js" / "kpi-engine.js",
)


def _mpc_load_percent_fn(src: str) -> str:
    start = src.index("export function mpcLoadPercent")
    end = src.index("export const nmpcLoadPercent")
    return src[start:end]


@pytest.mark.parametrize("path", TREES, ids=("src", "app"))
def test_mpc_load_percent_is_not_capped(path: Path) -> None:
    fn = _mpc_load_percent_fn(path.read_text(encoding="utf-8"))
    assert "return (duration / budget) * 100;" in fn
    assert "Math.min(100" not in fn
    assert "clamped at 100%" not in fn


def test_overview_formats_uncapped_load() -> None:
    overview = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "pages" / "overview.js"
    ).read_text(encoding="utf-8")
    catalog = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "kpi-detail-catalog.js"
    ).read_text(encoding="utf-8")
    gauge = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "components" / "gauge.js"
    ).read_text(encoding="utf-8")
    assert "format: (v) => `${formatNumber(v, 0)}%`" in overview
    assert "max: 100" in overview
    assert "formatPercent(load)" in catalog
    assert "Math.min(1, (value - min) / (max - min))" in gauge
    assert "format ? format(value)" in gauge
