"""SWD-497: normalised PE RMS (η) on progress snaps and Identification overlay."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from heatingassistant.engine.estimation.constants import PE_ETA_NOISE, PE_ETA_TOL
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_nstep_progress_publishes_eta_from_data_mse() -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)
    snaps: list[dict] = []
    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=60.0,
        n_horizon_steps=8,
        origin_stride=4,
        max_compute_s=30.0,
        use_nstep_pem=True,
        on_progress=snaps.append,
    )
    result = est.estimate(history)
    assert result["success"] is True
    assert snaps
    last = snaps[-1]
    assert last["n_obs"] > 0
    assert last["eta"] is not None and math.isfinite(last["eta"])
    assert last["rmse_c"] is not None and math.isfinite(last["rmse_c"])
    assert last["eta_tol"] == PE_ETA_TOL
    assert last["eta_noise"] == PE_ETA_NOISE
    expected = math.sqrt(float(last["data_mse"]) / float(last["n_obs"]))
    assert last["eta"] == pytest.approx(expected, rel=1e-9, abs=1e-12)
    sigma = math.sqrt(float(last["r_var"]))
    assert last["rmse_c"] == pytest.approx(last["eta"] * sigma, rel=1e-9, abs=1e-12)
    for point in last["f_hist"]:
        assert point["n_obs"] > 0
        assert math.isfinite(point["eta"])
        assert math.isfinite(point["data_mse"])
        assert point["eta"] == pytest.approx(
            math.sqrt(point["data_mse"] / point["n_obs"]),
            rel=1e-9,
            abs=1e-12,
        )


def test_panel_overlay_shows_rms_and_stays_in_view() -> None:
    detail = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "identification"
        / "sysid-detail.js"
    ).read_text(encoding="utf-8")
    progress = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "identification"
        / "pe-progress.js"
    ).read_text(encoding="utf-8")
    css = (
        ROOT / "heatingassistant" / "app" / "static" / "css" / "pages"
        / "identification.css"
    ).read_text(encoding="utf-8")
    assert "RMS error" in progress
    assert "Normalised RMS" in progress
    assert "ftol" not in progress
    assert "overlayHost.appendChild" in detail
    assert "instanceof ShadowRoot" in detail
    assert "getRootNode" in detail
    overlay_css = css.split(".pe-progress-overlay {", 1)[1].split("}", 1)[0]
    dialog_css = css.split(".pe-progress {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in overlay_css
    assert "overflow: auto" in overlay_css
    assert "overflow-y: auto" in dialog_css
    assert "max-height:" in dialog_css
    assert "Time remaining" not in progress
