"""SWD-430: computing overlay on countdown rings, not live KPI gauges."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _read(static: Path, *parts: str) -> str:
    return static.joinpath(*parts).read_text(encoding="utf-8")


def test_countdown_computing_css_and_export() -> None:
    for static in _TREES:
        countdown = _read(static, "js", "components", "countdown.js")
        css = _read(static, "css", "industrial.css")
        assert "export function setCountdownComputing" in countdown
        assert "countdown--computing" in css
        assert "countdown-computing-spin" in css
        assert "gauge--computing" not in css
        assert "kpi-shimmer" not in css


def test_overview_and_room_use_countdown_computing_decision() -> None:
    for static in _TREES:
        overview = _read(static, "js", "pages", "overview.js")
        room = _read(static, "js", "pages", "room-detail.js")
        for source in (overview, room):
            assert "setCountdownComputing" not in source
            assert "setGaugeComputing" not in source
            assert "updateCountdown" in source


def test_panel_entry_cache_bust_matches_dashboard_fallback() -> None:
    for static in _TREES:
        index = _read(static, "index.html")
        dashboard = _read(static, "industrial-dashboard.js")
        assert "industrial-dashboard.js?v=163" in index
        assert "return '163'" in dashboard
