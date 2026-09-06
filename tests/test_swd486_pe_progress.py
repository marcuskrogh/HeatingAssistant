"""SWD-486: live PE progress on pe_job and Identification overlay."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app import sysid_services
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)
from tests.test_swd453_pe_background_job import _ok_result, _runtime, wait_pe_job


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_nstep_estimate_publishes_progress_callback() -> None:
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
    assert snaps[0]["nfev"] == 1
    assert snaps[-1]["nfev"] >= 1
    assert snaps[-1]["f_hist"]
    assert snaps[-1]["phase"] in {"tiled_oe", "nstep_pem"}
    assert snaps[-1]["cap_s"] == 30.0
    assert all("f" in item for item in snaps)


def test_progress_callback_failure_does_not_fail_estimate() -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)

    def boom(_snap):
        raise RuntimeError("progress sink down")

    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=60.0,
        n_horizon_steps=8,
        origin_stride=4,
        max_compute_s=30.0,
        use_nstep_pem=True,
        on_progress=boom,
    )
    result = est.estimate(history)
    assert result["success"] is True


def test_pe_job_start_includes_compute_cap(tmp_path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    runtime.options["pe_max_compute_s"] = 300.0

    async def fake_handle(_runtime, _data):
        return _ok_result()

    monkeypatch.setattr(sysid_services, "handle_estimate_parameters_ml", fake_handle)
    started = sysid_services.start_estimate_parameters_ml(
        runtime, {"apply_parameters": False}
    )
    assert started["status"] == "running"
    job = sysid_services.pe_job_snapshot(runtime)
    assert job.get("cap_s") == 300.0
    done = wait_pe_job(runtime, timeout=5.0)
    assert done["status"] == "success"
    assert done.get("cap_s") == 300.0


def test_panel_js_renders_pe_progress_overlay() -> None:
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
    assert "renderPeProgress" in detail
    assert "pe-progress-overlay" in detail
    assert "waitForPeJob" in detail
    assert "RMS error" in progress
    assert "Normalised RMS" in progress
    assert "Time remaining" not in progress
    assert "pe-progress-overlay" in css
    assert "position: fixed" in css
    assert "overflow-y: auto" in css
    assert "overlayHost.appendChild" in detail
    assert "instanceof ShadowRoot" in detail
    assert "getRootNode" in detail
    assert "ftol" not in progress
    assert "L-BFGS" not in progress
