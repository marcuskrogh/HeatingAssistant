"""Integration tests for coordinator.mpc_cycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import mpc_cycle
from tests.helpers.coordinator_stubs import make_minimal_coordinator, wire_room_enablement


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
