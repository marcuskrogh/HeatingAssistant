"""SWD-321: DISTURBANCES outdoor/solar history uses Measured-style points."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOM_CHARTS = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
    / "charts"
    / "room-charts.js"
)


def _dataset_block_from_marker(source: str, marker: str) -> str:
    start = source.index(marker)
    depth = 0
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"Could not parse makeDataset block from {marker!r}")


def _dataset_block(source: str, label: str) -> str:
    return _dataset_block_from_marker(source, f"makeDataset('{label}'")


def _assert_measured_style_points(block: str, *, colour: str) -> None:
    assert "showLine: false" in block, f"expected showLine: false:\n{block}"
    assert "pointRadius: 3" in block, f"expected pointRadius: 3:\n{block}"
    assert "pointHoverRadius: 5" in block, f"expected pointHoverRadius: 5:\n{block}"
    assert "borderWidth: 0" in block, f"expected borderWidth: 0:\n{block}"
    assert f"pointBackgroundColor: '{colour}'" in block
    assert f"pointBorderColor: '{colour}'" in block
    assert "fill: true" not in block, f"history points must not use fill:\n{block}"


def test_disturbance_history_uses_measured_style_points() -> None:
    """Outdoor + solar history are discrete points; forecasts stay dashed lines."""
    source = ROOM_CHARTS.read_text(encoding="utf-8")
    disturb = source.split("export function buildDisturbanceChart", 1)[1]
    # Stop before any following export to keep assertions scoped.
    next_export = disturb.find("\nexport ")
    if next_export != -1:
        disturb = disturb[:next_export]

    outdoor_hist = _dataset_block(disturb, "Outdoor Temperature")
    _assert_measured_style_points(outdoor_hist, colour="#90a4ae")

    solar_hist = _dataset_block(disturb, "Solar Gain")
    _assert_measured_style_points(solar_hist, colour="#ffd54f")
    assert "yAxisID: 'y2'" in solar_hist

    outdoor_fc = _dataset_block_from_marker(
        disturb,
        "makeDataset(forecastOnly ? 'Outdoor Temperature' : 'Outdoor Forecast'",
    )
    assert "dashed: !forecastOnly" in outdoor_fc
    assert "borderWidth: 2" in outdoor_fc
    assert "showLine: false" not in outdoor_fc
    assert "pointRadius: 3" not in outdoor_fc

    solar_fc = _dataset_block_from_marker(
        disturb,
        "makeDataset(forecastOnly ? 'Solar Gain' : 'Solar Gain Forecast'",
    )
    assert "dashed: !forecastOnly" in solar_fc
    assert "borderWidth: 2" in solar_fc
    assert "yAxisID: 'y2'" in solar_fc
    assert "showLine: false" not in solar_fc
    assert "pointRadius: 3" not in solar_fc


def test_measured_temperature_style_still_points() -> None:
    """Reference: indoor Measured remains the style template for history points."""
    source = ROOM_CHARTS.read_text(encoding="utf-8")
    temp = source.split("export function buildTemperatureChart", 1)[1]
    next_export = temp.find("\nexport ")
    if next_export != -1:
        temp = temp[:next_export]
    measured = _dataset_block(temp, "Measured")
    _assert_measured_style_points(measured, colour="#e57373")
