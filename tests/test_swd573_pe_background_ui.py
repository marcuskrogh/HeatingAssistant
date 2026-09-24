"""SWD-573: dismiss PE overlay without cancel; banners, nav, exclusive start."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "heatingassistant" / "app" / "static"
SESSION = STATIC / "js" / "identification" / "pe-session.js"
PROGRESS = STATIC / "js" / "identification" / "pe-progress.js"
DETAIL = STATIC / "js" / "identification" / "sysid-detail.js"
DATASETS = STATIC / "js" / "identification" / "sysid-datasets.js"
DASHBOARD = STATIC / "industrial-dashboard.js"
OVERVIEW = STATIC / "js" / "pages" / "overview.js"
TUNING = STATIC / "js" / "pages" / "tuning-controller.js"
INDEX = STATIC / "js" / "identification" / "sysid-index.js"
IDENT_CSS = STATIC / "css" / "pages" / "identification.css"
NAV_CSS = STATIC / "css" / "industrial.css"


def test_close_hides_overlay_without_cancel() -> None:
    session = SESSION.read_text(encoding="utf-8")
    close_idx = session.index("data-pe-close")
    stop_idx = session.index("data-pe-stop")
    close_chunk = session[close_idx : close_idx + 160]
    assert "hide();" in close_chunk
    assert "stopJob" not in close_chunk
    assert "cancelParameterEstimation" not in close_chunk
    stop_chunk = session[stop_idx : stop_idx + 220]
    assert "stopJob" in stop_chunk
    assert "cancelParameterEstimation" in session
    assert "function hide()" in session or "function hide(" in session


def test_stop_control_is_on_overlay_and_banners() -> None:
    progress = PROGRESS.read_text(encoding="utf-8")
    session = SESSION.read_text(encoding="utf-8")
    assert "data-pe-stop" in progress
    assert "Stop estimation" in progress
    assert "data-pe-open" in session
    assert "mountPeRunningBanner" in session
    assert "Stop" in session


def test_nav_and_page_surfaces_reopen_overlay() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    nav_css = NAV_CSS.read_text(encoding="utf-8")
    ident_css = IDENT_CSS.read_text(encoding="utf-8")
    assert "pe-nav-chip" in dashboard
    assert "pe-top-chip" not in dashboard
    assert "Estimating" in dashboard
    assert "attachPeSession" in dashboard
    assert "js/identification/pe-session.js" in dashboard
    assert "panel-nav__pe-chip" in nav_css
    mobile_nav = nav_css.split("@media (max-width: 1024px)", 1)[1].split("@media (max-width: 480px)", 1)[0]
    assert "minmax(0, 1fr)" in mobile_nav
    assert "text-overflow: ellipsis" in mobile_nav
    assert "flex-wrap: wrap" in mobile_nav
    assert "overflow-x: hidden" in nav_css
    assert "pe-running-banner" in ident_css
    for path in (OVERVIEW, TUNING, INDEX, DETAIL):
        text = path.read_text(encoding="utf-8")
        assert "mountPeRunningBanner" in text, path.name


def test_start_guard_blocks_second_estimation() -> None:
    detail = DETAIL.read_text(encoding="utf-8")
    datasets = DATASETS.read_text(encoding="utf-8")
    assert "already running. Stop it before starting another" in detail
    assert "already running. Stop it before starting another" in datasets
    assert "peSession.isRunning()" in detail
    assert "session.isRunning()" in datasets
    assert "Estimation running" in datasets


def test_overlay_survives_leaving_identification() -> None:
    detail = DETAIL.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    assert "hidePeOverlay();" not in detail
    assert "peOverlay.remove()" not in detail
    assert "unmountPeBanner();" in detail
    assert "this._peSession.destroy()" in dashboard
    destroy_idx = detail.index("destroy()")
    chunk = detail[destroy_idx : destroy_idx + 400]
    assert "unmountPeBanner" in chunk
    assert "peSession.destroy" not in chunk
    assert "overlay.remove" not in chunk


def test_pe_session_exports_and_close_path() -> None:
    source = SESSION.read_text(encoding="utf-8")
    assert "export function attachPeSession" in source
    assert "export function mountPeRunningBanner" in source
    assert "function hide()" in source
    assert "async function stopJob()" in source
    assert "async function waitUntilSettled()" in source


def test_overlay_has_titled_fit_and_convergence_plots() -> None:
    progress = PROGRESS.read_text(encoding="utf-8")
    ident_css = IDENT_CSS.read_text(encoding="utf-8")
    assert "Fit error" in progress
    assert "Optimiser convergence" in progress
    assert 'data-pe-plot="eta"' in progress
    assert 'data-pe-plot="ftol"' in progress
    assert "pe-progress__plot-title" in progress
    assert "pe-progress__plots" in ident_css
    assert "grid-template-columns: 1fr 1fr" in ident_css


def test_lbfgs_rel_reduction_matches_scipy_ftol() -> None:
    from heatingassistant.engine.estimation.nlp_eval import lbfgs_rel_reduction

    assert lbfgs_rel_reduction(2.0, 1.0) == pytest.approx(0.5)
    assert lbfgs_rel_reduction(0.1, 0.05) == pytest.approx(0.05)
    assert lbfgs_rel_reduction(1.0, 1.0) == pytest.approx(0.0)
