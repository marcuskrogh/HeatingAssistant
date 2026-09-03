"""SWD-475: sandbox KPI expand motion candidate (not production)."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SANDBOX = _ROOT / "sandbox" / "kpi-expand"


def test_candidate_host_flips_and_follows() -> None:
    js = (_SANDBOX / "kpi-expand.js").read_text(encoding="utf-8")
    assert "scrollIntoView" in js
    assert "getBoundingClientRect" in js
    assert "translate(" in js
    assert "kpi-expand__detail-inner" in js
    assert "kpi-expand__inset-lead" in js
    assert "prefers-reduced-motion" in js
    assert "{ motion: false }" in js


def test_overlay_grows_one_card() -> None:
    css = (_SANDBOX / "expand.css").read_text(encoding="utf-8")
    assert "overflow: hidden" in css
    assert "kpi-expand__detail-inner" in css
    assert "kpi-expand__inset-lead" in css
    assert "background: var(--bg-primary)" in css


def test_app_loads_candidate_not_production_host() -> None:
    app = (_SANDBOX / "app.js").read_text(encoding="utf-8")
    assert "from './kpi-expand.js'" in app
    assert "expand.css" in app
    assert "spare-" in app
