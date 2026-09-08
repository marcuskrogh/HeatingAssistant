"""SWD-498: App UI type/plot tokens stay consistent across pages and plots."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "heatingassistant" / "app" / "static"
CSS_ROOT = STATIC / "css"
STYLEGUIDE = ROOT / "docs" / "agents" / "APP-UI-STYLEGUIDE.md"


def test_styleguide_documents_the_closed_scale() -> None:
    text = STYLEGUIDE.read_text(encoding="utf-8")
    for token in (
        "--type-kicker",
        "--type-ui",
        "--type-title",
        "--type-metric",
        "--chart-height-primary",
        "--chart-height-secondary",
        ".panel-nav__link",
        "chart-theme.js",
        "sizePlotCanvas",
        "Room-view plots are the guide",
    ):
        assert token in text


def test_host_defines_type_and_plot_tokens() -> None:
    css = (CSS_ROOT / "industrial.css").read_text(encoding="utf-8")
    host = css.split(":host {", 1)[1].split("}", 1)[0]
    for token, value in {
        "--type-kicker": "11px",
        "--type-caption": "12px",
        "--type-ui": "13px",
        "--type-subtitle": "16px",
        "--type-title": "20px",
        "--type-metric": "24px",
        "--type-hero": "32px",
        "--type-display": "46px",
        "--chart-height-primary": "240px",
        "--chart-height-secondary": "200px",
    }.items():
        assert f"{token}: {value}" in host


def test_production_css_has_no_tiny_or_literal_px_fonts() -> None:
    forbidden = re.compile(
        r"font-size:\s*(?:8px|9px|10px|0\.65rem|0\.7rem)\b",
    )
    literal_px = re.compile(r"font-size:\s*\d+px")
    offenders: list[str] = []
    for path in sorted(CSS_ROOT.rglob("*.css")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        if forbidden.search(text):
            offenders.append(f"{rel}: tiny font-size")
        if literal_px.search(text):
            offenders.append(f"{rel}: literal px font-size")
    assert not offenders, offenders


def test_nav_links_use_ui_size_including_hamburger() -> None:
    css = (CSS_ROOT / "industrial.css").read_text(encoding="utf-8")
    assert ".panel-nav__link {\n  font-size: var(--type-ui);" in css
    hamburger = css.split("@media (max-width: 1024px)", 1)[1]
    assert "min-height: 44px;" in hamburger
    assert (
        ".panel-nav__link {\n    min-height: 44px;\n    padding: 12px 14px;\n"
        "    display: flex;\n    align-items: center;\n"
        "    border-radius: var(--radius-sm);\n    font-size: var(--type-ui);"
    ) in hamburger


def test_pe_progress_plot_matches_room_guide() -> None:
    progress = (
        STATIC / "js" / "identification" / "pe-progress.js"
    ).read_text(encoding="utf-8")
    ident = (CSS_ROOT / "pages" / "identification.css").read_text(encoding="utf-8")
    theme = (STATIC / "js" / "components" / "chart-theme.js").read_text(encoding="utf-8")
    assert "from '../components/chart-theme.js?v=157'" in progress
    assert "sizePlotCanvas(canvas)" in progress
    assert "CHART_LINE_WIDTH" in progress
    assert "CHART_DASH_PATTERN" in progress
    assert "CHART_TICK_SIZE" in progress
    assert "height: var(--chart-height-primary)" in ident
    assert ".pe-progress__plot-frame" in ident
    assert "@media (max-width: 768px)" in ident
    assert "align-items: flex-start" in ident
    assert "CHART_TICK_SIZE = 10" in theme
    assert "CHART_LINE_WIDTH = 2" in theme
    assert "Always measure the wrapper" in theme
    assert "export function sizePlotCanvas" in theme
    assert "CHART_HEIGHT_PRIMARY = 240" in theme
    assert "CHART_HEIGHT_SECONDARY = 200" in theme
    assert "font-size: 80px" not in ident
    assert "font-size: var(--type-hero)" in ident


def test_room_chart_js_defaults_are_unchanged_from_the_guide() -> None:
    chart = (STATIC / "js" / "components" / "time-series-chart.js").read_text(
        encoding="utf-8"
    )
    room = (STATIC / "js" / "charts" / "room-charts.js").read_text(encoding="utf-8")
    assert "from './chart-theme.js?v=157'" in chart
    assert "elements: {\n    line: { borderWidth:" not in chart
    assert "CHART_LINE_WIDTH" not in chart
    assert 'font: { size: 10, family: "system-ui, sans-serif" }' in chart
    assert "9px system-ui" in chart
    assert "borderWidth: options.borderWidth || 1.5" in chart
    assert "borderWidth: 2" in room
    assert "titleFont: { size: 11 }" in chart


def test_identification_plots_use_primary_and_secondary_heights() -> None:
    detail = (
        STATIC / "js" / "identification" / "sysid-detail.js"
    ).read_text(encoding="utf-8")
    assert "height: CHART_HEIGHT_PRIMARY" in detail
    assert "height: CHART_HEIGHT_SECONDARY" in detail
    assert "height: 260" not in detail
    assert "height: 180" not in detail
    room = (STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    tuning = (STATIC / "js" / "pages" / "tuning-controller.js").read_text(encoding="utf-8")
    assert "CHART_HEIGHT_PRIMARY" in room and "CHART_HEIGHT_SECONDARY" in room
    assert "CHART_HEIGHT_PRIMARY" in tuning and "CHART_HEIGHT_SECONDARY" in tuning


def test_calver_and_cache_bust_for_ui_tokens() -> None:
    init = (ROOT / "heatingassistant" / "__init__.py").read_text(encoding="utf-8")
    changelog = (ROOT / "heating_assistant" / "CHANGELOG.md").read_text(encoding="utf-8")
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    dashboard = (STATIC / "industrial-dashboard.js").read_text(encoding="utf-8")
    assert '__version__ = "2026.09.14"' in init
    assert "# 2026.09.10" in changelog
    assert "industrial-dashboard.js?v=160" in index
    assert "return '160'" in dashboard
