"""SWD-494: computing overlay on wrap without waiting for Ingress poll."""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _read(static: Path, *parts: str) -> str:
    return static.joinpath(*parts).read_text(encoding="utf-8")


def test_countdown_computing_spin_group_and_host_class() -> None:
    for static in _TREES:
        js = _read(static, "js", "components", "countdown.js")
        css = _read(static, "css", "industrial.css")
        assert "export function countdownIsComputing" in js
        assert "function wrapOverlayCapS" in js
        assert "countdown__spin" in js
        assert "typeof container.closest === 'function'" in js
        assert "closest('.kpi-expand')" in js or "closest(\".kpi-expand\")" in js
        assert ".countdown__spin" in css
        assert ".kpi-expand.countdown--computing" in css
        assert "countdown-computing-spin" in css


def test_tick_applies_computing_without_waiting_for_page_update() -> None:
    for static in _TREES:
        js = _read(static, "js", "components", "countdown.js")
        tick = js.split("tick(currentState)", 1)[1]
        assert "countdownIsComputing(currentState, spec)" in tick
        assert "setCountdownComputing(container" in tick


def test_panel_countdown_computing_harness() -> None:
    harness = _ROOT / "tests" / "panel_countdown_computing.harness.mjs"
    subprocess.run(["node", str(harness)], check=True, cwd=_ROOT)


def test_calver_and_cache_bust_for_computing_overlay() -> None:
    init = (_ROOT / "heatingassistant" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "2026.09.14"' in init
    changelog = (_ROOT / "heating_assistant" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "# 2026.09.14" in changelog
    for static in _TREES:
        index = _read(static, "index.html")
        dashboard = _read(static, "industrial-dashboard.js")
        assert "industrial-dashboard.js?v=160" in index
        assert "return '160'" in dashboard
        overview = _read(static, "js", "pages", "overview.js")
        room = _read(static, "js", "pages", "room-detail.js")
        assert "countdown.js?v=154" in overview
        assert "countdown.js?v=154" in room
