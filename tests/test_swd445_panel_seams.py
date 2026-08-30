"""SWD-445: lock Ingress panel page-detail seams before splits."""

from __future__ import annotations

from pathlib import Path
import subprocess

STATIC = Path(__file__).resolve().parents[1] / "heatingassistant" / "app" / "static"
REPO = Path(__file__).resolve().parents[1]


def test_industrial_dashboard_is_classic_script_iife() -> None:
    source = (STATIC / "industrial-dashboard.js").read_text(encoding="utf-8")
    assert "(() => {" in source
    assert source.rstrip().endswith("})();")
    assert "customElements.get('ha-industrial-panel')" in source
    assert "customElements.define('ha-industrial-panel'" in source
    assert "import.meta.url is NOT used here" in source


def test_industrial_dashboard_dynamically_imports_page_modules() -> None:
    source = (STATIC / "industrial-dashboard.js").read_text(encoding="utf-8")
    for path in (
        "js/pages/room-detail.js",
        "js/pages/parameter-estimation.js",
        "js/pages/schedules.js",
        "js/pages/overview.js",
        "js/pages/tuning-controller.js",
        "js/pages/system-status.js",
        "js/pages/configuration.js",
    ):
        assert path in source, path
    assert "${BASE_PATH}/js/pages/room-detail.js?v=${PANEL_VERSION}" in source


def test_page_detail_entry_exports_exist() -> None:
    room = (STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    sysid = (STATIC / "js" / "identification" / "sysid-detail.js").read_text(
        encoding="utf-8"
    )
    schedules = (STATIC / "js" / "schedules" / "schedules-detail.js").read_text(
        encoding="utf-8"
    )
    pe_page = (STATIC / "js" / "pages" / "parameter-estimation.js").read_text(
        encoding="utf-8"
    )
    sched_page = (STATIC / "js" / "pages" / "schedules.js").read_text(encoding="utf-8")
    assert "export function renderRoomDetail(" in room
    assert "export function renderIdentificationDetail(" in sysid
    assert "export function renderScheduleDetail(" in schedules
    assert "from '../identification/sysid-detail.js" in pe_page
    assert "from '../schedules/schedules-detail.js" in sched_page


def test_index_html_cache_busts_dashboard_entry() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "industrial-dashboard.js?v=" in index


def test_room_detail_keeps_live_chart_update_helpers() -> None:
    source = (STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    assert "function updateChartsFromState" in source
    assert "function extendLiveChartHistory" in source
    assert "function mpcForecastStamp" in source
    update_fn = source.split("function updateChartsFromState", 1)[1]
    assert update_fn.index("extendLiveChartHistory") < update_fn.index(
        "mpcForecastStamp(state)"
    )


def test_room_detail_loads_history_from_collaborator() -> None:
    source = (STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    hist = (STATIC / "js" / "pages" / "room-detail-history.js").read_text(
        encoding="utf-8"
    )
    assert "from './room-detail-history.js" in source
    assert "export async function loadChartsData" in hist
    assert hist.index("function clampFirstToWindow") < hist.index(
        "export async function loadChartsData"
    )
    assert hist.index("function closeStepSegments") < hist.index(
        "export async function loadChartsData"
    )


def test_sysid_detail_loads_markup_from_collaborator() -> None:
    source = (STATIC / "js" / "identification" / "sysid-detail.js").read_text(
        encoding="utf-8"
    )
    markup = (
        STATIC / "js" / "identification" / "sysid-detail-markup.js"
    ).read_text(encoding="utf-8")
    assert "from './sysid-detail-markup.js" in source
    assert "export function paramsCardHtml" in markup
    assert "export function buildValidationSection" in markup
    assert "export function renderIdentificationDetail(" in source


def test_schedules_detail_loads_markup_from_collaborator() -> None:
    source = (STATIC / "js" / "schedules" / "schedules-detail.js").read_text(
        encoding="utf-8"
    )
    markup = (STATIC / "js" / "schedules" / "schedules-detail-markup.js").read_text(
        encoding="utf-8"
    )
    assert "from './schedules-detail-markup.js" in source
    assert "export function periodBodyHtml" in markup
    assert "export function periodHeaderHtml" in markup
    assert "export function renderScheduleDetail(" in source


def test_sysid_detail_markup_keeps_query_ids() -> None:
    markup = (
        STATIC / "js" / "identification" / "sysid-detail-markup.js"
    ).read_text(encoding="utf-8")
    for needle in (
        "param-thermal-mass",
        "param-ua-open",
        "param-t-wall-initial",
        "btn-apply-params",
        "fit-comparison-kpis",
        "param-history-list",
        "heater-scales-list",
        "window-mode-custom",
        "param-window-start",
        'data-chart="temp"',
    ):
        assert needle in markup, needle


def test_schedules_detail_markup_keeps_editor_hooks() -> None:
    markup = (STATIC / "js" / "schedules" / "schedules-detail-markup.js").read_text(
        encoding="utf-8"
    )
    for needle in (
        'data-action="toggle-enabled"',
        "data-segmented-field",
        "data-when-field",
        "data-remove-override",
        "schedule-form__period-name",
    ):
        assert needle in markup, needle


def _run_panel_harness(name: str) -> None:
    result = subprocess.run(
        ["node", str(REPO / "tests" / name)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_period_editor_markup_harness() -> None:
    _run_panel_harness("panel_period_editor_markup.harness.mjs")


def test_sysid_detail_imports_harness() -> None:
    _run_panel_harness("panel_sysid_detail_imports.harness.mjs")
