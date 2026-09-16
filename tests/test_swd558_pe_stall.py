"""SWD-558: PE origin stride, η plateau stop, best-RMS overlay, wait vs cap."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from heatingassistant.engine.estimation.constants import PE_ETA_STALE_EVALS
from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator
from heatingassistant.engine.nmpc_timing import pe_origin_stride
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "heatingassistant" / "engine" / "parameter_lifecycle.py"
DETAIL = (
    ROOT
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
    / "identification"
    / "sysid-detail.js"
)
PROGRESS = (
    ROOT
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
    / "identification"
    / "pe-progress.js"
)


def test_pe_origin_stride_is_two_hour_grid() -> None:
    assert pe_origin_stride(900.0) == 8
    assert pe_origin_stride(60.0) == 120
    assert pe_origin_stride(0.0) == 1
    assert pe_origin_stride(-15.0) == 1


def test_production_lifecycle_uses_pe_origin_stride() -> None:
    src = LIFECYCLE.read_text(encoding="utf-8")
    assert "pe_origin_stride(timing.dt_s)" in src
    assert "origin_stride=timing.fast_substeps" not in src


def test_constant_eta_stops_before_burning_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=24, dt=900.0)

    def fake_nstep(self, theta, *args, **kwargs):
        self._pe_n_obs = 10
        # Nonzero jac so L-BFGS-B cannot exit on gtol before the η plateau.
        return 4.0, np.ones(len(theta))

    monkeypatch.setattr(KalmanMLEstimator, "_nstep_pem_and_grad", fake_nstep)
    snaps: list[dict] = []
    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=900.0,
        n_horizon_steps=8,
        origin_stride=8,
        max_compute_s=0.0,
        use_nstep_pem=True,
        on_progress=snaps.append,
    )
    result = est.estimate(history)
    assert result["success"] is True
    assert result.get("timed_out") is False
    assert result.get("exit_label") == "Fit stopped improving"
    assert est._pe_nfev <= PE_ETA_STALE_EVALS + 2
    assert snaps[-1]["eta_best"] == pytest.approx(snaps[0]["eta"])


def test_best_eta_survives_a_worse_last_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=24, dt=900.0)
    calls = {"n": 0}

    def fake_nstep(self, theta, *args, **kwargs):
        self._pe_n_obs = 16
        calls["n"] += 1
        mse = 1.0 if calls["n"] == 1 else 100.0
        return mse, np.ones(len(theta))

    monkeypatch.setattr(KalmanMLEstimator, "_nstep_pem_and_grad", fake_nstep)
    snaps: list[dict] = []
    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=900.0,
        n_horizon_steps=8,
        origin_stride=8,
        max_compute_s=0.0,
        use_nstep_pem=True,
        on_progress=snaps.append,
    )
    result = est.estimate(history)
    assert result["success"] is True
    assert snaps[-1]["eta_best"] == pytest.approx((1.0 / 16.0) ** 0.5)
    assert snaps[-1]["eta"] == pytest.approx((100.0 / 16.0) ** 0.5)
    assert snaps[-1]["eta"] > snaps[-1]["eta_best"]


def test_wait_for_pe_job_follows_cap_and_cancels() -> None:
    source = DETAIL.read_text(encoding="utf-8")
    start = source.index("async function waitForPeJob")
    chunk = source[start : start + 1800]
    assert "job.cap_s" in chunk
    assert "Date.now() + 30 * 60 * 1000" not in source
    assert "await cancelParameterEstimation(hass)" in chunk
    progress = PROGRESS.read_text(encoding="utf-8")
    assert "eta_best" in progress
    assert "rmse_c_best" in progress
