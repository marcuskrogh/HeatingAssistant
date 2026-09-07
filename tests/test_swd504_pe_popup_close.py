"""SWD-504: PE overlay stays open with exit copy and close/cancel."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from heatingassistant.app import sysid_services
from heatingassistant.engine.estimation.nlp_eval import lbfgs_exit_label
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)
from tests.test_swd453_pe_background_job import _runtime, wait_pe_job


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_lbfgs_exit_label_maps_scipy_messages() -> None:
    assert lbfgs_exit_label(SimpleNamespace(success=True, status=0, message="CONVERGENCE: REL_REDUCTION_OF_F")) == (
        "Converged (cost reduction)"
    )
    assert lbfgs_exit_label(
        SimpleNamespace(success=True, status=1, message="CONVERGENCE: NORM OF PROJECTED GRADIENT <= PGTOL")
    ) == "Converged (gradient small enough)"
    assert (
        lbfgs_exit_label(
            SimpleNamespace(
                success=False,
                status=1,
                message="STOP: TOTAL NO. of ITERATIONS REACHED LIMIT",
            )
        )
        == "Maximum iterations reached"
    )
    assert (
        lbfgs_exit_label(
            SimpleNamespace(
                success=False,
                status=2,
                message="STOP: TOTAL NO. of F AND G EVALUATIONS EXCEEDS LIMIT",
            )
        )
        == "Maximum evaluations reached"
    )
    assert lbfgs_exit_label(SimpleNamespace(success=False, status=0, message="")) == "Did not converge"


def test_overlay_stays_open_and_has_close_control() -> None:
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
    assert "data-pe-close" in progress
    assert "pe-progress__close" in css
    assert "cancelParameterEstimation" in detail
    assert "hidePeOverlay();" in detail
    assert "finally {\n      hidePeOverlay();" not in detail
    assert "exit_label" in progress
    assert "Maximum iterations reached" not in progress
    assert "lbfgs_exit_label" not in progress


def test_cancel_check_stops_estimate() -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)
    n = {"c": 0}

    def cancel() -> bool:
        n["c"] += 1
        return n["c"] > 2

    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=60.0,
        n_horizon_steps=8,
        origin_stride=4,
        max_compute_s=30.0,
        use_nstep_pem=True,
    )
    est._pe_cancel_check = cancel
    result = est.estimate(history)
    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["exit_label"] == "Stopped by the user"
    assert "not applied" in result["message"].lower()


def test_cancel_service_marks_running_job_cancelled(tmp_path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    gate = {"go": False}

    async def fake_handle(_runtime, _data):
        gate["go"] = True
        while not getattr(_runtime, "_pe_cancel").is_set():
            await asyncio.sleep(0.01)
        return {
            "success": False,
            "cancelled": True,
            "exit_label": "Stopped by the user",
            "message": "Stopped by the user. Parameters were not applied.",
        }

    monkeypatch.setattr(sysid_services, "handle_estimate_parameters_ml", fake_handle)
    sysid_services.start_estimate_parameters_ml(runtime, {})
    deadline = time.monotonic() + 2.0
    while not gate["go"]:
        if time.monotonic() > deadline:
            raise AssertionError("PE worker did not start")
        time.sleep(0.01)
    out = sysid_services.cancel_estimate_parameters_ml(runtime)
    assert out["status"] == "cancelling"
    job = wait_pe_job(runtime, timeout=2.0)
    assert job["status"] == "cancelled"
    assert job["success"] is False
    assert job["exit_label"] == "Stopped by the user"


def test_successful_job_keeps_exit_label(tmp_path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)

    async def fake_handle(_runtime, _data):
        return {
            "success": True,
            "exit_label": "Maximum iterations reached",
            "message": "Maximum iterations reached.",
            "estimated_params": {},
        }

    monkeypatch.setattr(sysid_services, "handle_estimate_parameters_ml", fake_handle)
    sysid_services.start_estimate_parameters_ml(runtime, {})
    job = wait_pe_job(runtime, timeout=2.0)
    assert job["status"] == "success"
    assert job["exit_label"] == "Maximum iterations reached"
