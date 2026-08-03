"""Integration tests for coordinator.mpc_cycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.heating_assistant.controller.factory import (
    ControllerBuildConfig,
    build_mpc_controller,
)
from custom_components.heating_assistant.const import MPC_MODE_NONLINEAR
from custom_components.heating_assistant.coordinator import mpc_cycle
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from tests.helpers.coordinator_stubs import make_minimal_coordinator, wire_room_enablement


def _wire_disturbance_mocks(coord) -> None:
    """Attach coordinator private-method mocks used by gather_disturbances."""
    coord._async_read_price_forecast = AsyncMock(return_value=[0.12, 0.15])
    coord._async_read_weather_forecast = AsyncMock(return_value=[4.0, 3.5, 3.0])
    coord._ensure_runtime_state_loaded = AsyncMock()
    coord._read_cloud_cover_now = MagicMock(return_value=0.4)
    coord._smooth_cloud_cover = MagicMock(side_effect=lambda x: x)
    coord._async_read_cloud_forecast = AsyncMock(return_value=[0.5, 0.6])
    coord._read_ghi = MagicMock(return_value=(250.0, [200.0, 180.0]))
    coord._read_wind_speed_now = MagicMock(return_value=3.5)
    coord._async_read_wind_forecast = AsyncMock(return_value=[3.0, 2.5])
    coord._pending_ekf_state = None
    coord.is_window_override_active = MagicMock(return_value=False)
    coord.model.set_cloud_cover = MagicMock()
    coord.controller = MagicMock()
    coord.controller.set_wind_speed = MagicMock()
    coord.controller.set_cloud_cover = MagicMock()
    coord.controller.set_ground_temp = MagicMock()
    coord.controller.set_room_process_noise_covariance_scales = MagicMock()


def _build_mpc_stack():
    """Minimal physics stack for run_controller_compute (no IPOPT)."""
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
    coord.solar_gains = {"studio": 50.0}
    coord.measured_temperatures = {"studio": 19.0}
    coord._build_experiment_clamps = MagicMock(return_value={})
    coord._relax_experiment_comfort = MagicMock()
    coord.is_window_override_active = MagicMock(return_value=False)
    wire_room_enablement(coord)
    return coord, controller


@pytest.mark.integration
def test_read_measurements_averages_sensors_and_tracks_first_reading():
    coord = make_minimal_coordinator(
        room_names=["living_room"],
        temp_sensors={"living_room": ["sensor.a", "sensor.b"]},
    )
    coord.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda eid: SimpleNamespace(
                state={"sensor.a": "20.0", "sensor.b": "22.0"}[eid],
                attributes={},
            )
        )
    )
    coord._rooms_ever_measured = set()

    pending = mpc_cycle.read_measurements(coord)

    assert pending == set()
    assert coord.measured_temperatures["living_room"] == pytest.approx(21.0)
    assert "living_room" in coord._rooms_ever_measured


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_outdoor_temperature_persists_last_valid():
    coord = make_minimal_coordinator()
    coord._read_outdoor_temp = MagicMock(return_value=None)
    coord._last_valid_outdoor_temp = 4.5
    coord._outdoor_temp_startup_failures = 0

    outdoor, early = await mpc_cycle.resolve_outdoor_temperature(coord)

    assert outdoor == pytest.approx(4.5)
    assert early is None


@pytest.mark.integration
def test_build_cycle_result_includes_forecast_fields():
    coord = make_minimal_coordinator()
    coord.actions = {"hp": 0.5}
    coord.predictions = [{"living_room": 21.0}]
    coord.outdoor_forecast = [5.0]
    coord.solar_forecast = [{"living_room": 10.0}]
    coord.heating_schedule = [{"living_room": 500.0}]
    coord.solar_gains = {"living_room": 8.0}
    coord.heat_flows = {"living_room": {"external_loss": 100.0}}

    result = mpc_cycle.build_cycle_result(coord, outdoor_temp=5.0)

    assert result["actions"] == {"hp": 0.5}
    assert result["predictions"] == [{"living_room": 21.0}]
    assert result["outdoor_forecast"] == [5.0]
    assert result["solar_forecast"] == [{"living_room": 10.0}]
    assert result["heating_schedule"] == [{"living_room": 500.0}]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gather_disturbances_wires_forecasts_and_controller_inputs():
    coord = make_minimal_coordinator(room_names=["living_room"])
    _wire_disturbance_mocks(coord)
    coord.now_utc = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)

    ctx = await mpc_cycle.gather_disturbances(coord)

    assert coord.price_forecast == [0.12, 0.15]
    assert ctx.outdoor_forecast == [4.0, 3.5, 3.0]
    assert ctx.cloud_cover_now == pytest.approx(0.4)
    assert ctx.cloud_forecast == [0.5, 0.6]
    assert ctx.ghi_now == pytest.approx(250.0)
    assert ctx.ghi_forecast == [200.0, 180.0]
    assert ctx.wind_forecast == [3.0, 2.5]
    assert ctx.now == coord.now_utc
    coord.controller.set_wind_speed.assert_called_once_with(3.5)
    coord.controller.set_cloud_cover.assert_called_once_with(0.4)
    coord.controller.set_ground_temp.assert_called_once()
    coord.model.set_cloud_cover.assert_called_once_with(0.4)
    coord.controller.set_room_process_noise_covariance_scales.assert_called_once()


@pytest.mark.integration
def test_publish_cycle_solar_gains_populates_coordinator_state():
    coord = make_minimal_coordinator(room_names=["living_room"])
    coord._room_solar_gain = MagicMock(return_value=42.0)
    coord._save_runtime_state = MagicMock()
    now = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)
    ctx = mpc_cycle.DisturbanceContext(
        outdoor_forecast=[5.0],
        cloud_cover_now=0.3,
        cloud_forecast=[0.4],
        ghi_now=300.0,
        ghi_forecast=[280.0, 260.0],
        wind_forecast=[2.0],
        now=now,
    )

    mpc_cycle.publish_cycle_solar_gains(coord, ctx)

    assert coord.cloud_cover == pytest.approx(0.3)
    assert coord.ghi_now == pytest.approx(300.0)
    assert coord.ghi_forecast == [280.0, 260.0]
    assert coord.solar_source == "forecast"
    assert coord.solar_gains == {"living_room": 42.0}
    coord._room_solar_gain.assert_called_once_with(
        "living_room", now, 0.3, 300.0
    )
    coord._save_runtime_state.assert_called_once()


@pytest.mark.integration
def test_run_controller_compute_mirrors_controller_outputs():
    coord, controller = _build_mpc_stack()
    coord._system_enabled = False
    ctx = mpc_cycle.DisturbanceContext(
        outdoor_forecast=[2.0, 1.5],
        cloud_cover_now=0.2,
        cloud_forecast=[0.3],
        ghi_now=100.0,
        ghi_forecast=[90.0],
        wind_forecast=[4.0],
        now=coord.now_utc,
    )

    innovation = mpc_cycle.run_controller_compute(
        coord,
        outdoor_temp=2.0,
        control_traj=None,
        rooms_not_ready=set(),
        ctx=ctx,
    )

    assert isinstance(coord.actions, dict)
    assert coord.actions  # non-empty from Kalman-only path
    assert coord._mpc_shadow_actions == dict(controller.mpc_actions)
    assert coord.predictions == controller.predictions
    assert coord.filtered_temperatures == dict(controller.filtered_temperatures)
    assert innovation is controller.last_innovation


@pytest.mark.integration
def test_run_controller_compute_notifies_on_nonlinear_failure():
    coord = make_minimal_coordinator(room_names=["studio"], horizon=3)
    coord._mpc_mode = MPC_MODE_NONLINEAR
    source = ElectricHeater("heater", "studio", 2500.0)
    coord.heat_sources = [source]
    coord.controller = MagicMock()
    coord.controller.compute.side_effect = RuntimeError("ipopt failed")
    coord.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    coord._build_experiment_clamps = MagicMock(return_value={})
    coord._relax_experiment_comfort = MagicMock()
    coord.is_window_override_active = MagicMock(return_value=False)
    coord.solar_gains = {"studio": 50.0}
    ctx = mpc_cycle.DisturbanceContext(
        outdoor_forecast=[2.0, 1.5],
        cloud_cover_now=0.2,
        cloud_forecast=[0.3],
        ghi_now=100.0,
        ghi_forecast=[90.0],
        wind_forecast=[4.0],
        now=coord.now_utc,
    )

    innovation = mpc_cycle.run_controller_compute(
        coord,
        outdoor_temp=2.0,
        control_traj=None,
        rooms_not_ready=set(),
        ctx=ctx,
    )

    assert innovation is None
    assert coord.predictions == []
    coord.hass.services.async_call.assert_called_once()
    args, kwargs = coord.hass.services.async_call.call_args
    assert args[:2] == ("persistent_notification", "create")
    assert "switch MPC mode to linear" in args[2]["message"]
    assert kwargs["blocking"] is False


@pytest.mark.integration
def test_finalize_actions_zeros_window_override_and_reads_delivered_when_stopped():
    source = ElectricHeater("hp", "living_room", 2000.0)
    coord = make_minimal_coordinator(room_names=["living_room"])
    coord.heat_sources = [source]
    coord.actions = {"hp": 0.8}
    coord.is_window_override_active = MagicMock(return_value=True)

    mpc_cycle.finalize_actions(coord, outdoor_temp=5.0)

    assert coord.actions["hp"] == 0.0
    coord.model.compute_heat_flows.assert_called_once_with(5.0)

    coord.is_window_override_active = MagicMock(return_value=False)
    coord._system_enabled = False
    coord._read_delivered_actions = MagicMock(return_value={"hp": 0.25})
    coord.actions = {"hp": 0.8}

    mpc_cycle.finalize_actions(coord, outdoor_temp=5.0)

    coord._read_delivered_actions.assert_called_once_with(5.0)
    assert coord.actions == {"hp": 0.25}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_record_observation_history_rate_limits_and_appends():
    coord = make_minimal_coordinator(room_names=["living_room"])
    coord._update_interval_s = 900
    coord._history_buffer = []
    coord.heat_sources = [ElectricHeater("hp", "living_room", 2000.0)]
    coord.actions = {"hp": 0.5}
    coord.measured_temperatures = {"living_room": 20.5}
    coord.solar_gains = {"living_room": 10.0}
    coord.solar_source = "forecast"
    coord.predictions = [{"living_room": 21.0}]
    coord.is_window_override_active = MagicMock(return_value=False)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_append = AsyncMock()
    coord.id_history_store.async_purge_old = AsyncMock()
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    ctx = mpc_cycle.DisturbanceContext(
        outdoor_forecast=[],
        cloud_cover_now=0.1,
        cloud_forecast=[],
        ghi_now=50.0,
        ghi_forecast=[],
        wind_forecast=[],
        now=now,
    )

    await mpc_cycle.record_observation_history(coord, 5.0, ctx, [0.1])
    assert len(coord._history_buffer) == 1
    entry = coord._history_buffer[0]
    assert entry["d_outdoor"] == 5.0
    assert entry["u"] == [0.5]
    assert entry["kalman_innovation"] == [0.1]
    coord.id_history_store.async_append.assert_awaited_once()
    coord.id_history_store.async_purge_old.assert_awaited_once()

    coord.id_history_store.async_append.reset_mock()
    coord.id_history_store.async_purge_old.reset_mock()
    ctx_recent = mpc_cycle.DisturbanceContext(
        outdoor_forecast=[],
        cloud_cover_now=0.1,
        cloud_forecast=[],
        ghi_now=50.0,
        ghi_forecast=[],
        wind_forecast=[],
        now=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
    )
    await mpc_cycle.record_observation_history(coord, 5.0, ctx_recent, [0.2])
    assert len(coord._history_buffer) == 1
    coord.id_history_store.async_append.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_outdoor_temperature_early_return_on_startup():
    coord = make_minimal_coordinator()
    coord._read_outdoor_temp = MagicMock(return_value=None)
    coord._last_valid_outdoor_temp = None
    coord._outdoor_temp_startup_failures = 0
    coord._ensure_runtime_state_loaded = AsyncMock()
    coord._publish_current_solar_gains = MagicMock()
    coord.heat_sources = [ElectricHeater("hp", "living_room", 2000.0)]

    outdoor, early = await mpc_cycle.resolve_outdoor_temperature(coord)

    assert outdoor is None
    assert early is not None
    assert early["outdoor_temp"] is None
    assert early["predictions"] == []
    assert coord._outdoor_temp_startup_failures == 1
    assert coord.actions == {"hp": 0.0}
    coord._ensure_runtime_state_loaded.assert_awaited_once()
    coord._publish_current_solar_gains.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_outdoor_temperature_raises_after_startup_grace():
    coord = make_minimal_coordinator()
    coord._read_outdoor_temp = MagicMock(return_value=None)
    coord._last_valid_outdoor_temp = None
    coord._outdoor_temp_startup_failures = 2
    coord._outdoor_entity = "sensor.outdoor"
    coord._weather_entity = "weather.home"

    with pytest.raises(UpdateFailed, match="Outdoor temperature is unavailable"):
        await mpc_cycle.resolve_outdoor_temperature(coord)

    assert coord._outdoor_temp_startup_failures == 3
