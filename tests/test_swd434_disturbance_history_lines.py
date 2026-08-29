"""SWD-434: DISTURBANCES outdoor/solar history uses solid lines, not points."""

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
APP_TREES = (
    Path(__file__).resolve().parents[1] / "heatingassistant" / "app" / "static",
    Path(__file__).resolve().parents[1]
    / "heating_assistant"
    / "heatingassistant"
    / "app"
    / "static",
)
APP_STATIC = APP_TREES[0]


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


def _disturbance_source() -> str:
    source = ROOM_CHARTS.read_text(encoding="utf-8")
    disturb = source.split("export function buildDisturbanceChart", 1)[1]
    next_export = disturb.find("\nexport ")
    if next_export != -1:
        disturb = disturb[:next_export]
    return disturb


def _assert_solid_history_line(block: str, *, colour: str) -> None:
    assert f"'{colour}'" in block, f"expected colour {colour}:\n{block}"
    assert "borderWidth: 2" in block, f"expected borderWidth: 2:\n{block}"
    assert "showLine: false" not in block, f"history must be a line, not points:\n{block}"
    assert "pointRadius: 3" not in block, f"history must not use Measured-style points:\n{block}"
    assert "pointBackgroundColor" not in block, f"history must not use point fill:\n{block}"


def test_disturbance_history_uses_solid_lines() -> None:
    """Outdoor + solar history are solid lines; forecasts stay dashed lines."""
    disturb = _disturbance_source()

    outdoor_hist = _dataset_block(disturb, "Outdoor Temperature")
    _assert_solid_history_line(outdoor_hist, colour="#90a4ae")

    solar_hist = _dataset_block(disturb, "Solar Gain")
    _assert_solid_history_line(solar_hist, colour="#ffd54f")
    assert "yAxisID: 'y2'" in solar_hist
    assert "fill: true" in solar_hist
    assert "backgroundColor: 'rgba(255,213,79,0.08)'" in solar_hist

    outdoor_fc = _dataset_block_from_marker(
        disturb,
        "makeDataset(forecastOnly ? 'Outdoor Temperature' : 'Outdoor Forecast'",
    )
    assert "dashed: !forecastOnly" in outdoor_fc
    assert "borderWidth: 2" in outdoor_fc
    assert "showLine: false" not in outdoor_fc

    solar_fc = _dataset_block_from_marker(
        disturb,
        "makeDataset(forecastOnly ? 'Solar Gain' : 'Solar Gain Forecast'",
    )
    assert "dashed: !forecastOnly" in solar_fc
    assert "borderWidth: 2" in solar_fc
    assert "yAxisID: 'y2'" in solar_fc
    assert "showLine: false" not in solar_fc


def test_measured_temperature_style_still_points() -> None:
    """Indoor Measured stays discrete points after restoring disturbance lines."""
    source = ROOM_CHARTS.read_text(encoding="utf-8")
    temp = source.split("export function buildTemperatureChart", 1)[1]
    next_export = temp.find("\nexport ")
    if next_export != -1:
        temp = temp[:next_export]
    measured = _dataset_block(temp, "Measured")
    assert "showLine: false" in measured
    assert "pointRadius: 3" in measured
    assert "pointHoverRadius: 5" in measured
    assert "borderWidth: 0" in measured
    assert "pointBackgroundColor: '#e57373'" in measured
    assert "pointBorderColor: '#e57373'" in measured


def test_room_charts_import_cache_bust() -> None:
    """Importers of room-charts.js use a bumped ?v= so Ingress does not keep points."""
    for static in APP_TREES:
        room_detail = (static / "js" / "pages" / "room-detail.js").read_text(
            encoding="utf-8"
        )
        mpc_preview = (static / "js" / "charts" / "mpc-preview-charts.js").read_text(
            encoding="utf-8"
        )
        tuning = (static / "js" / "pages" / "tuning-controller.js").read_text(
            encoding="utf-8"
        )
        index = (static / "index.html").read_text(encoding="utf-8")
        dashboard = (static / "industrial-dashboard.js").read_text(encoding="utf-8")
        disturb = (static / "js" / "charts" / "room-charts.js").read_text(
            encoding="utf-8"
        )
        disturb = disturb.split("export function buildDisturbanceChart", 1)[1]
        next_export = disturb.find("\nexport ")
        if next_export != -1:
            disturb = disturb[:next_export]
        outdoor_hist = _dataset_block(disturb, "Outdoor Temperature")
        _assert_solid_history_line(outdoor_hist, colour="#90a4ae")
        assert "room-charts.js?v=140" in room_detail
        assert "room-charts.js?v=140" in mpc_preview
        assert "mpc-preview-charts.js?v=140" in tuning
        assert "industrial-dashboard.js?v=143" in index
        assert "return '143'" in dashboard
        assert "room-charts.js?v=124" not in room_detail
        assert "room-charts.js?v=124" not in mpc_preview
