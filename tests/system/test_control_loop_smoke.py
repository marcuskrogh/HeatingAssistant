"""System smoke test: physics model → controller factory → forecast payload."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.controller.factory import (
    ControllerBuildConfig,
    build_mpc_controller,
)
from custom_components.heating_assistant.coordinator import mpc_cycle
from custom_components.heating_assistant.coordinator.forecast_payload import (
    build_forecast_payload,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from tests.helpers.coordinator_stubs import make_minimal_coordinator, wire_room_enablement


def _build_stack():
    room = Room(
        name="studio",
        thermal_mass=4_000_000.0,
        r_external=0.05,
        temperature=19.0,
        setpoint=21.0,
    )
    model = HouseModel([room])
    source = ElectricHeater("heater", "studio", 2500.0)
    config = ControllerBuildConfig(
        model=model,
        heat_sources=[source],
        horizon=3,
        dt=900.0,
        latitude=55.7,
        longitude=12.6,
        tracking_weight=1.0,
        energy_weight=0.05,
        smoothing_weight=0.02,
        soft_constraint_weight=10.0,
        soft_constraint_linear_weight=0.0,
        terminal_weight=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
        sigma_b=0.002,
        energy_price_weight=0.0,
    )
    controller = build_mpc_controller(config)
    coord = make_minimal_coordinator(room_names=["studio"], horizon=3)
    coord.model = model
    coord.heat_sources = [source]
    coord.controller = controller
    coord.outdoor_temp = 2.0
    coord.filtered_temperatures = {"studio": 19.2}
    coord.measured_temperatures = {"studio": 19.0}
    wire_room_enablement(coord)
    coord.build_forecast_payload = (
        __import__(
            "custom_components.heating_assistant.coordinator.core",
            fromlist=["HeatingAssistantCoordinator"],
        ).HeatingAssistantCoordinator.build_forecast_payload.__get__(coord)
    )
    return coord, controller


@pytest.mark.system
def test_mpc_compute_populates_forecast_payload():
    """A minimal house stack must run MPC and produce a WebSocket-ready payload."""
    coord, controller = _build_stack()

    actions = controller.compute(
        outdoor_temp=2.0,
        run_optimization=True,
        cloud_cover_now=0.2,
    )
    assert isinstance(actions, dict)

    coord.predictions = list(controller.predictions)
    coord.heating_schedule = list(controller.heating_schedule)
    coord.outdoor_forecast = list(controller.outdoor_forecast)
    coord.solar_forecast = list(controller.solar_forecast)

    payload = coord.build_forecast_payload(room_names=["studio"])

    assert "studio" in payload["rooms"]
    assert len(payload["rooms"]["studio"]["forecast"]) >= 2
    assert payload["step_seconds"] == pytest.approx(900.0)


@pytest.mark.system
@pytest.mark.asyncio
async def test_mpc_cycle_orchestration_produces_forecast_payload():
    """Full mpc_cycle path: measure → disturbances → compute → finalize → payload."""
    coord, controller = _build_stack()
    coord._system_enabled = False
    coord._temp_sensors = {"studio": ["sensor.studio_temp"]}
    coord.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda eid: SimpleNamespace(state="19.0", attributes={})
        )
    )
    coord._rooms_ever_measured = set()
    coord._read_outdoor_temp = MagicMock(return_value=2.0)
    coord._async_read_price_forecast = AsyncMock(return_value=[0.1])
    coord._async_read_weather_forecast = AsyncMock(return_value=[2.0, 1.5, 1.0])
    coord._ensure_runtime_state_loaded = AsyncMock()
    coord._read_cloud_cover_now = MagicMock(return_value=0.2)
    coord._smooth_cloud_cover = MagicMock(side_effect=lambda x: x)
    coord._async_read_cloud_forecast = AsyncMock(return_value=[0.3])
    coord._read_ghi = MagicMock(return_value=(150.0, [140.0]))
    coord._read_wind_speed_now = MagicMock(return_value=2.0)
    coord._async_read_wind_forecast = AsyncMock(return_value=[2.5])
    coord._room_solar_gain = MagicMock(return_value=30.0)
    coord._save_runtime_state = MagicMock()
    coord._read_delivered_actions = MagicMock(return_value={"heater": 0.0})
    coord._build_experiment_clamps = MagicMock(return_value={})
    coord._relax_experiment_comfort = MagicMock()
    coord._pending_ekf_state = None
    coord.is_window_override_active = MagicMock(return_value=False)
    coord.now_utc = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)

    rooms_not_ready = mpc_cycle.read_measurements(coord)
    outdoor, early = await mpc_cycle.resolve_outdoor_temperature(coord)
    assert early is None
    assert outdoor == pytest.approx(2.0)

    ctx = await mpc_cycle.gather_disturbances(coord)
    mpc_cycle.publish_cycle_solar_gains(coord, ctx)
    mpc_cycle.run_controller_compute(
        coord, outdoor, control_traj=None, rooms_not_ready=rooms_not_ready, ctx=ctx
    )
    mpc_cycle.finalize_actions(coord, outdoor)
    result = mpc_cycle.build_cycle_result(coord, outdoor)

    assert result["outdoor_temp"] == pytest.approx(2.0)
    assert "heater" in result["actions"]

    coord.predictions = list(controller.predictions)
    coord.heating_schedule = list(controller.heating_schedule)
    coord.outdoor_forecast = list(controller.outdoor_forecast)
    coord.solar_forecast = list(controller.solar_forecast)

    payload = coord.build_forecast_payload(room_names=["studio"])
    assert "studio" in payload["rooms"]
    assert len(payload["rooms"]["studio"]["forecast"]) >= 1
    assert payload["step_seconds"] == pytest.approx(900.0)
