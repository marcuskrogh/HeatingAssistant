"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import mpc_cycle
from heatingassistant.engine.heat_sources import ElectricHeater
from tests.helpers.coordinator_stubs import make_hass_stub
from tests.integration.test_mpc_cycle import _build_mpc_stack, _wire_disturbance_mocks


def _wire_orchestration_mocks(coord) -> None:
    """Mock coordinator hooks exercised only by core._async_update_data."""
    coord._apply_pending_runtime_reconfiguration = MagicMock()
    coord._apply_schedule = MagicMock()
    coord._compute_control_trajectory = MagicMock(return_value=None)
    coord._update_window_state_machine = MagicMock()
    coord._async_refresh_time_in_range_kpis_if_due = AsyncMock()


def _wire_update_cycle_support(coord) -> None:
    """Shared wiring so mpc_cycle helpers can run under _async_update_data."""
    _wire_disturbance_mocks(coord)
    _wire_orchestration_mocks(coord)

    room = coord.model.room_names[0]
    sensor_id = coord._temp_sensors[room][0]
    coord.hass = make_hass_stub(temp_states={sensor_id: "19.5"})
    coord._rooms_ever_measured = set(coord.model.room_names)

    coord._read_outdoor_temp = MagicMock(return_value=5.0)
    coord._history_buffer = deque(maxlen=100)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_append = AsyncMock()
    coord.id_history_store.async_purge_old = AsyncMock()
    coord._room_solar_gain = MagicMock(return_value=50.0)
    coord._save_runtime_state = MagicMock()
    coord.solar_source = "analytical"
    coord.estimated_internal_gains = {}
    coord.experiment_manager = MagicMock()
    coord.experiment_manager.advance.return_value = {"completed": [], "started": []}
    coord._read_delivered_actions = MagicMock(return_value={})
    coord._apply_actions = AsyncMock()
    coord._publish_current_solar_gains = MagicMock()


def _make_update_cycle_coordinator(*, system_enabled: bool = True):
    coord, _controller = _build_mpc_stack()
    _wire_update_cycle_support(coord)
    coord._system_enabled = system_enabled
    return coord


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_update_data_happy_path_returns_cycle_result():
    coord = _make_update_cycle_coordinator(system_enabled=True)

    result = await coord._async_update_data()

    coord._apply_pending_runtime_reconfiguration.assert_called_once()
    coord._apply_schedule.assert_called_once()
    coord._compute_control_trajectory.assert_called_once()
    coord._update_window_state_machine.assert_called_once()
    coord._async_refresh_time_in_range_kpis_if_due.assert_awaited_once()
    coord._apply_actions.assert_awaited_once_with(5.0)

    assert isinstance(result, dict)
    assert result["outdoor_temp"] == pytest.approx(5.0)
    assert "actions" in result
    assert "predictions" in result
    assert "temperatures" in result
    assert "outdoor_forecast" in result
    assert "solar_forecast" in result
    assert "heating_schedule" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_update_data_early_outdoor_skips_gather_disturbances(monkeypatch):
    coord = _make_update_cycle_coordinator()
    coord._read_outdoor_temp = MagicMock(return_value=None)
    coord._last_valid_outdoor_temp = None
    coord._outdoor_temp_startup_failures = 0
    coord.heat_sources = [ElectricHeater("hp", "studio", 2000.0)]
    coord._ensure_runtime_state_loaded = AsyncMock()

    gather_mock = AsyncMock()
    monkeypatch.setattr(mpc_cycle, "gather_disturbances", gather_mock)

    result = await coord._async_update_data()

    gather_mock.assert_not_awaited()
    coord._apply_actions.assert_not_awaited()
    assert result["outdoor_temp"] is None
    assert result["predictions"] == []
    assert result["heat_flows"] == {}
    coord._ensure_runtime_state_loaded.assert_awaited_once()
    coord._publish_current_solar_gains.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_update_data_system_disabled_skips_apply_actions():
    coord = _make_update_cycle_coordinator(system_enabled=False)

    result = await coord._async_update_data()

    coord._apply_actions.assert_not_awaited()
    assert isinstance(result, dict)
    assert result["outdoor_temp"] == pytest.approx(5.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_update_data_schedule_failure_passes_none_trajectory():
    coord = _make_update_cycle_coordinator()
    coord._compute_control_trajectory = MagicMock(
        side_effect=RuntimeError("schedule projection failed")
    )

    result = await coord._async_update_data()

    assert coord._control_trajectory is None
    coord._apply_actions.assert_awaited_once()
    assert isinstance(result, dict)
    assert result["outdoor_temp"] == pytest.approx(5.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_update_data_apply_actions_failure_still_returns():
    coord = _make_update_cycle_coordinator()
    coord._apply_actions = AsyncMock(side_effect=RuntimeError("service failed"))

    result = await coord._async_update_data()

    coord._apply_actions.assert_awaited_once_with(5.0)
    assert isinstance(result, dict)
    assert result["outdoor_temp"] == pytest.approx(5.0)
    assert "actions" in result
    assert "predictions" in result
