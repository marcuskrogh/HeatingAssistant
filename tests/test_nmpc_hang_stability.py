"""Tests for NMPC hang mitigations (SWD-248)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.const import (
    MPC_MODE_NONLINEAR,
    SCIPY_NMPC_MAX_HORIZON,
    SCIPY_NMPC_MAXITER,
)
from custom_components.heating_assistant.controller.facade import _GuardedNLPBackend
from custom_components.heating_assistant.controller.factory import (
    ControllerBuildConfig,
    build_mpc_controller,
)
from custom_components.heating_assistant.coordinator import mpc_cycle
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from tests.helpers.coordinator_stubs import make_minimal_coordinator


def _tiny_model():
    room = Room(
        name="studio",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=20.0,
        setpoint=21.0,
    )
    return HouseModel([room])


def test_guarded_nlp_backend_raises_on_soft_failure():
    class FakeBackend:
        def solve(self, _problem):
            return SimpleNamespace(success=False, message="linesearch failed", x=[0.0])

    with pytest.raises(RuntimeError, match="linesearch failed"):
        _GuardedNLPBackend(FakeBackend()).solve(object())


def test_guarded_nlp_backend_passes_success_through():
    result = SimpleNamespace(success=True, message="", x=[1.0])

    class FakeBackend:
        def solve(self, _problem):
            return result

    assert _GuardedNLPBackend(FakeBackend()).solve(object()) is result


@pytest.mark.integration
def test_build_mpc_controller_caps_scipy_horizon():
    model = _tiny_model()
    sources = [ElectricHeater("hp", "studio", 2000.0)]
    config = ControllerBuildConfig(
        model=model,
        heat_sources=sources,
        horizon=100,
        dt=900.0,
        latitude=55.0,
        longitude=12.0,
        tracking_weight=1.0,
        energy_weight=0.1,
        smoothing_weight=0.05,
        soft_constraint_weight=10.0,
        soft_constraint_linear_weight=0.0,
        terminal_weight=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
        sigma_b=0.002,
        energy_price_weight=0.0,
        mpc_mode=MPC_MODE_NONLINEAR,
        nonlinear_available=True,
        nonlinear_backend="scipy",
        ipopt_available=False,
    )

    controller = build_mpc_controller(config)

    assert controller.horizon == SCIPY_NMPC_MAX_HORIZON
    assert controller.solver_active == "scipy"
    # SciPy options must stay bounded (not the old 500-iter path).
    assert SCIPY_NMPC_MAXITER <= 100


def test_handle_controller_compute_timeout_keeps_actions_and_notifies():
    coord = make_minimal_coordinator(room_names=["studio"], horizon=4)
    coord._mpc_mode = MPC_MODE_NONLINEAR
    coord._nonlinear_backend = "scipy"
    coord.heat_sources = [ElectricHeater("heater", "studio", 2500.0)]
    coord.actions = {"heater": 0.4}
    coord.predictions = [[20.0, 21.0]]
    coord.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    ctx = mpc_cycle.DisturbanceContext(
        outdoor_forecast=[2.0, 1.5, 1.0, 0.5],
        cloud_cover_now=0.2,
        cloud_forecast=[0.3],
        ghi_now=100.0,
        ghi_forecast=[90.0],
        wind_forecast=[4.0],
        now=coord.now_utc,
    )

    mpc_cycle.handle_controller_compute_timeout(
        coord, outdoor_temp=2.0, ctx=ctx, timeout_s=45.0
    )

    assert coord.actions == {"heater": 0.4}
    assert coord.predictions == []
    coord.hass.services.async_call.assert_called_once()
    args, kwargs = coord.hass.services.async_call.call_args
    assert args[:2] == ("persistent_notification", "create")
    assert "45" in args[2]["message"]
    assert kwargs["blocking"] is False


def test_async_update_data_runs_mpc_compute_off_event_loop():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "custom_components"
        / "heating_assistant"
        / "coordinator"
        / "core.py"
    ).read_text(encoding="utf-8")
    assert "async_add_executor_job" in source
    assert "run_controller_compute" in source
    assert "asyncio.wait_for" in source
    assert "MPC_COMPUTE_TIMEOUT_NONLINEAR_S" in source
    assert "handle_controller_compute_timeout" in source
