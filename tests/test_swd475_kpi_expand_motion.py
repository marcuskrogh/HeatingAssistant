"""SWD-475: KPI expand motion, Description topic, and split load cards."""

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)
_SANDBOX = _ROOT / "sandbox" / "kpi-expand"


def _read(static: Path, *parts: str) -> str:
    return static.joinpath(*parts).read_text(encoding="utf-8")


def test_production_host_flips_follows_and_writes_description_topic() -> None:
    for static in _TREES:
        host = _read(static, "js", "components", "kpi-expand.js")
        assert "scrollIntoView" in host
        assert "getBoundingClientRect" in host
        assert "translate(" in host
        assert "kpi-expand__detail-inner" in host
        assert "kpi-expand__description-title" in host
        assert ">Description<" in host
        assert "payload.sections" in host
        assert "{ motion: false }" in host
        assert "prefers-reduced-motion" in host
        assert "kpi-expand card" in host
        assert "kpi-expand__lead" not in host


def test_production_css_inset_and_row_separators() -> None:
    for static in _TREES:
        css = _read(static, "css", "industrial.css")
        assert "kpi-expand__detail-inner" in css
        assert "kpi-expand__description-title" in css
        assert "kpi-expand__section-title" in css
        assert "row:not(:last-child)" in css
        assert ".kpi-expand.card" in css
        assert "kpi-expand__lead" not in css


def test_overview_nmpc_and_room_regulator_load() -> None:
    for static in _TREES:
        overview = _read(static, "js", "pages", "overview.js")
        room = _read(static, "js", "pages", "room-detail.js")
        engine = _read(static, "js", "kpi-engine.js")
        catalog = _read(static, "js", "kpi-detail-catalog.js")
        assert "MPC LOAD" in overview
        assert "mpcLoadPercent" in overview
        assert "mpcLoadDetail" in overview
        assert "label: 'NMPC LOAD'" not in overview
        assert "nmpcCountdown" not in overview
        assert "REGULATOR LOAD" not in room
        assert "regulatorLoadPercent" not in room
        assert "regulatorLoadDetail" not in room
        assert "NMPC_LOAD_FRACTION = 0.1" in engine
        assert "export function mpcLoadPercent" in engine
        assert "nmpcLoadPercent" in engine
        assert "export function regulatorLoadPercent" not in engine
        assert "title: 'NMPC'" not in catalog
        assert "title: 'Regulator'" not in catalog
        assert "export function mpcLoadDetail" in catalog
        assert "export function nmpcLoadDetail" in catalog
        assert "export function regulatorLoadDetail" not in catalog


def test_panel_entry_cache_bust() -> None:
    for static in _TREES:
        index = _read(static, "index.html")
        dashboard = _read(static, "industrial-dashboard.js")
        assert "industrial-dashboard.js?v=168" in index
        assert "return '168'" in dashboard


def test_load_catalog_harness() -> None:
    result = subprocess.run(
        ["node", str(_ROOT / "tests" / "panel_kpi_load_catalog.harness.mjs")],
        check=False,
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sandbox_candidate_remains_isolated() -> None:
    app = (_SANDBOX / "app.js").read_text(encoding="utf-8")
    assert "from './kpi-expand.js'" in app
    assert "from './load-catalog.js'" in app
