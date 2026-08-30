"""SWD-453: background PE job so Ingress does not drop long fits."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from heatingassistant.app import sysid_services
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus

from tests.test_app_http import app_server, get_json, send_json


pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "comfort_offset": 1.5,
                    "thermal_mass": 1_000_000.0,
                    "r_external": 0.01,
                    "temp_tags": ["living_temp"],
                    "enabled": True,
                }
            ],
            "heat_sources": [
                {
                    "name": "Living Heater",
                    "room": "Living Room",
                    "type": "electric",
                    "max_power": 1200.0,
                    "heater_entity": "switch.living_heater",
                }
            ],
        },
    )


def _ok_result() -> dict:
    return {
        "success": True,
        "estimated_params": {
            "Living Room": {"thermal_mass": 1_250_000.0, "r_external": 0.012}
        },
        "estimated_internal_gains": {"Living Room": 42.0},
        "estimated_solar_scales": {"Living Room": 0.8},
        "estimated_envelope_splits": {
            "Living Room": {"c_air_fraction": 0.08, "r_aw_fraction": 0.12}
        },
        "estimated_t_wall_initial": {"Living Room": 20.5},
        "estimated_heater_scales": {"Living Heater": 1.1},
        "estimated_inter_room_r": {},
        "message": "Joint optimisation converged.",
    }


def wait_pe_job(runtime: HeatingRuntime, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = sysid_services.pe_job_snapshot(runtime)
        if last.get("status") in {"success", "error"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"PE job did not finish: {last}")


def test_start_returns_running_before_fit_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    release = threading.Event()
    calls = {"n": 0}

    async def fake_estimate(*args, **kwargs):
        calls["n"] += 1
        release.wait(timeout=2)
        return _ok_result()

    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)

    t0 = time.perf_counter()
    started = sysid_services.start_estimate_parameters_ml(
        runtime, {"apply_parameters": False}
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5
    assert started["status"] == "running"
    assert sysid_services.pe_job_snapshot(runtime)["status"] == "running"

    second = sysid_services.start_estimate_parameters_ml(
        runtime, {"apply_parameters": False}
    )
    assert second["status"] == "running"
    release.set()
    job = wait_pe_job(runtime)
    assert job["status"] == "success"
    assert job["success"] is True
    assert calls["n"] == 1
    assert runtime.sysid_results["Living Room"]["thermal_mass"] == pytest.approx(
        1_250_000.0
    )


def test_worker_exception_sets_error_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)

    async def fake_estimate(*args, **kwargs):
        raise RuntimeError("optimizer exploded")

    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)
    started = sysid_services.start_estimate_parameters_ml(runtime, {})
    assert started["status"] == "running"
    job = wait_pe_job(runtime)
    assert job["status"] == "error"
    assert "optimizer exploded" in str(job.get("message"))


def test_unsuccessful_fit_sets_error_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)

    async def fake_estimate(*args, **kwargs):
        return {"success": False, "message": "Insufficient data: 2 steps available"}

    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)
    sysid_services.start_estimate_parameters_ml(runtime, {})
    job = wait_pe_job(runtime)
    assert job["status"] == "error"
    assert "Insufficient data" in str(job.get("message"))


def test_http_estimate_returns_before_fit_and_poll_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    async def fake_estimate(*args, **kwargs):
        release.wait(timeout=2)
        return _ok_result()

    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)
    runtime = _runtime(tmp_path)

    with app_server(runtime) as base_url:
        idle = get_json(base_url, "/api/pe_job")
        assert idle["job"]["status"] == "idle"

        t0 = time.perf_counter()
        result = send_json(
            base_url,
            "/api/services",
            "POST",
            {
                "domain": "heating_assistant",
                "service": "estimate_parameters_ml",
                "data": {"apply_parameters": False},
            },
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0
        assert result["status"] == "running"
        running = get_json(base_url, "/api/pe_job")
        assert running["job"]["status"] == "running"
        state = get_json(base_url, "/api/state")
        assert state["pe_job"]["status"] == "running"

        release.set()
        deadline = time.monotonic() + 2.0
        job = running
        while time.monotonic() < deadline:
            job = get_json(base_url, "/api/pe_job")
            if job["job"]["status"] == "success":
                break
            time.sleep(0.02)
        assert job["job"]["status"] == "success"
        assert job["job"]["success"] is True


def test_http_post_unexpected_error_returns_500(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    async def boom(_domain: str, _service: str, _data: dict) -> dict:
        raise RuntimeError("kaboom")

    runtime.apply_service = boom  # type: ignore[method-assign]

    with app_server(runtime) as base_url:
        request = Request(
            f"{base_url}/api/services",
            data=json.dumps(
                {
                    "domain": "heating_assistant",
                    "service": "estimate_parameters_ml",
                    "data": {},
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=5)
        assert caught.value.code == 500


def test_panel_js_polls_pe_job_status() -> None:
    root = Path(__file__).resolve().parents[1]
    detail = (
        root / "heatingassistant" / "app" / "static" / "js" / "identification"
        / "sysid-detail.js"
    ).read_text(encoding="utf-8")
    connection = (
        root / "heatingassistant" / "app" / "static" / "js" / "ha-connection.js"
    ).read_text(encoding="utf-8")
    shim = (
        root / "heatingassistant" / "app" / "static" / "js" / "app-hass-shim.js"
    ).read_text(encoding="utf-8")
    assert "waitForPeJob" in detail
    assert "getPeJob" in detail
    assert "getPeJob" in connection
    assert "heating_assistant/get_pe_job" in connection
    assert "api/pe_job" in shim
    assert "Load failed" not in detail
