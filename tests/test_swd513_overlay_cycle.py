"""SWD-513: wrap overlay must not fight idle computing flags."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _read(static: Path, *parts: str) -> str:
    return static.joinpath(*parts).read_text(encoding="utf-8")


def test_wrap_overlay_uses_poll_gap_cap_not_nmpc_duration() -> None:
    for static in _TREES:
        js = _read(static, "js", "components", "countdown.js")
        assert "function wrapOverlayCapS" in js
        assert "last_nmpc_duration_s" not in js
        assert "Math.max(90" not in js
        assert "? 8 : 2.5" in js


def test_pages_do_not_paint_overlay_from_raw_flags() -> None:
    for static in _TREES:
        overview = _read(static, "js", "pages", "overview.js")
        room = _read(static, "js", "pages", "room-detail.js")
        assert "applyComputing" not in overview
        assert "paintCountdownLoading" not in room
        assert "setCountdownComputing" not in overview
        assert "setCountdownComputing" not in room
        assert "updateCountdown" in overview
        assert "updateCountdown" in room
        tick = _read(static, "js", "components", "countdown.js")
        assert "countdownIsComputing(currentState, spec)" in tick
