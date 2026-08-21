"""SWD-430: computing overlay on countdown rings, not live KPI gauges."""

from __future__ import annotations

from pathlib import Path

_STATIC = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "app"
    / "static"
)


def _read(*parts: str) -> str:
    return (_STATIC.joinpath(*parts)).read_text(encoding="utf-8")


def test_countdown_computing_css_and_export() -> None:
    countdown = _read("js", "components", "countdown.js")
    css = _read("css", "industrial.css")
    assert "export function setCountdownComputing" in countdown
    assert "countdown--computing" in css
    assert "countdown-computing-spin" in css
    assert "gauge--computing" not in css
    assert "kpi-shimmer" not in css


def test_overview_and_room_wire_flags_to_matching_rings() -> None:
    overview = _read("js", "pages", "overview.js")
    room = _read("js", "pages", "room-detail.js")
    for source in (overview, room):
        assert "setCountdownComputing" in source
        assert "nmpc_computing" in source
        assert "control_computing" in source
        assert "setGaugeComputing" not in source
        nmpc_idx = source.index("nmpc_computing")
        control_idx = source.index("control_computing")
        nmpc_call = source.rfind("setCountdownComputing", 0, nmpc_idx)
        control_call = source.rfind("setCountdownComputing", 0, control_idx)
        assert nmpc_call != -1
        assert control_call != -1
        nmpc_target = source[nmpc_call:nmpc_idx]
        control_target = source[control_call:control_idx]
        assert "nmpcCountdown" in nmpc_target
        assert "countdown.element" in control_target


def test_panel_entry_cache_bust_matches_dashboard_fallback() -> None:
    index = _read("index.html")
    dashboard = _read("industrial-dashboard.js")
    assert "industrial-dashboard.js?v=139" in index
    assert "return '139'" in dashboard
