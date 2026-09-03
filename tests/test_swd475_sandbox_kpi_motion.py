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
    assert "kpi-expand__section-title" in js
    assert "kpi-expand__description-title" in js
    assert "prefers-reduced-motion" in js
    assert "{ motion: false }" in js


def test_overlay_grows_one_card() -> None:
    css = (_SANDBOX / "expand.css").read_text(encoding="utf-8")
    assert "overflow: hidden" in css
    assert "kpi-expand__detail-inner" in css
    assert "kpi-expand__inset-lead" in css
    assert "kpi-expand__description-title" in css
    assert "kpi-expand__section-title" in css
    assert "row:not(:last-child)" in css
    assert "color-mix" in css


def test_app_loads_candidate_not_production_host() -> None:
    app = (_SANDBOX / "app.js").read_text(encoding="utf-8")
    assert "from './kpi-expand.js'" in app
    assert "from './load-catalog.js'" in app
    assert "NMPC LOAD" in app
    assert "REGULATOR LOAD" in app
    assert "from '/ha-industrial-panel/js/kpi-detail-catalog.js'" in app
    assert "{ mpcLoadDetail" not in app
    assert " mpcLoadDetail" not in app


def test_split_load_catalog_is_sandbox_only() -> None:
    catalog = (_SANDBOX / "load-catalog.js").read_text(encoding="utf-8")
    assert "NMPC_LOAD_FRACTION = 0.1" in catalog
    assert "REGULATOR_BUDGET_S = 2" in catalog
    assert "export function nmpcLoadDetail" in catalog
    assert "export function regulatorLoadDetail" in catalog
    assert "title: 'Regulator'" in catalog
    assert "title: 'NMPC'" in catalog
