"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import os
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.kpi import RoomSnapshot, TIME_IN_RANGE_WINDOW_S
from custom_components.heating_assistant.kpi_history import (
    TIME_IN_RANGE_REFRESH_S,
    _prefer_filtered_temperature,
    async_compute_room_time_in_range_pct,
    async_fetch_entity_samples,
    async_refresh_time_in_range_kpis,
    room_comfort_entity_ids,
)
from tests.helpers.coordinator_stubs import make_hass_stub, make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def test_room_comfort_entity_ids_uses_slugified_room_name():
    ids = room_comfort_entity_ids("Living Room")
    assert ids["temperature_filtered"] == (
        "sensor.heating_assistant_living_room_temperature_filtered"
    )
    assert ids["temperature_measured"] == (
        "sensor.heating_assistant_living_room_temperature_measured"
    )
    assert ids["constraint_lower"] == (
        "sensor.heating_assistant_living_room_constraint_lower"
    )
    assert ids["constraint_upper"] == (
        "sensor.heating_assistant_living_room_constraint_upper"
    )


def test_prefer_filtered_temperature_returns_filtered_when_present():
    ids = room_comfort_entity_ids("living_room")
    filtered = [(1_700_000_000.0, 21.0)]
    measured = [(1_700_000_000.0, 20.5)]
    samples = {
        ids["temperature_filtered"]: filtered,
        ids["temperature_measured"]: measured,
    }
    assert _prefer_filtered_temperature(samples, ids) == filtered


def test_prefer_filtered_temperature_falls_back_to_measured():
    ids = room_comfort_entity_ids("living_room")
    measured = [(1_700_000_000.0, 20.5)]
    samples = {ids["temperature_measured"]: measured}
    assert _prefer_filtered_temperature(samples, ids) == measured


@pytest.mark.asyncio
async def test_async_fetch_entity_samples_uses_recorder(monkeypatch):
    ids = room_comfort_entity_ids("living_room")
    entity_ids = list(ids.values())
    end_ts = 1_700_010_000.0
    start_ts = end_ts - 3600.0

    state = SimpleNamespace(
        state="21.0",
        last_updated=datetime.fromtimestamp(end_ts - 1800.0, tz=timezone.utc),
    )
    history = {entity_ids[0]: [state]}

    recorder_instance = MagicMock()
    recorder_instance.async_add_executor_job = AsyncMock(return_value=history)

    recorder_mod = types.ModuleType("homeassistant.components.recorder")
    recorder_mod.get_instance = MagicMock(return_value=recorder_instance)
    history_mod = types.ModuleType("homeassistant.components.recorder.history")
    history_mod.get_significant_states = MagicMock(return_value=history)

    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", recorder_mod)
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder.history", history_mod,
    )

    hass = make_hass_stub()
    samples = await async_fetch_entity_samples(hass, entity_ids, start_ts, end_ts)

    recorder_mod.get_instance.assert_called_once_with(hass)
    assert entity_ids[0] in samples
    assert samples[entity_ids[0]][0][1] == pytest.approx(21.0)


@pytest.mark.asyncio
async def test_async_compute_room_time_in_range_pct_with_synthetic_samples(monkeypatch):
    ids = room_comfort_entity_ids("living_room")
    window_end = 1_700_086_400.0
    window_start = window_end - TIME_IN_RANGE_WINDOW_S

    async def _fake_fetch(_hass, _entity_ids, start_ts, end_ts):
        assert start_ts == pytest.approx(window_start)
        assert end_ts == pytest.approx(window_end)
        return {
            ids["temperature_filtered"]: [
                (window_start, 21.0),
                (window_start + 43200.0, 21.0),
                (window_end, 21.0),
            ],
            ids["constraint_lower"]: [
                (window_start, 19.0),
                (window_end, 19.0),
            ],
            ids["constraint_upper"]: [
                (window_start, 23.0),
                (window_end, 23.0),
            ],
        }

    monkeypatch.setattr(
        "custom_components.heating_assistant.kpi_history.async_fetch_entity_samples",
        _fake_fetch,
    )

    snapshot = RoomSnapshot(
        slug="living_room",
        room_active=True,
        temperature_filtered=21.0,
        constraint_lower=19.0,
        constraint_upper=23.0,
        setpoint=21.0,
    )
    coord = SimpleNamespace(model=SimpleNamespace(room_names=["living_room"]))
    pct = await async_compute_room_time_in_range_pct(
        make_hass_stub(), coord, "living_room", snapshot, window_end,
    )
    assert pct == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_async_refresh_time_in_range_kpis_returns_per_room(monkeypatch):
    coord = make_minimal_coordinator(room_names=["living_room", "bedroom"])
    coord.filtered_temperatures = {"living_room": 21.0, "bedroom": 20.0}
    coord.measured_temperatures = {"living_room": 21.0, "bedroom": 20.0}
    coord._time_in_range_pct_24h = {}

    async def _fake_compute(_hass, _coord, room_name, _snap, _end):
        return 88.0 if room_name == "living_room" else 75.0

    monkeypatch.setattr(
        "custom_components.heating_assistant.kpi_history.async_compute_room_time_in_range_pct",
        _fake_compute,
    )

    results = await async_refresh_time_in_range_kpis(
        coord.hass, coord, 1_700_086_400.0,
    )
    assert results == {"living_room": 88.0, "bedroom": 75.0}


@pytest.mark.asyncio
async def test_refresh_time_in_range_kpis_rate_limited(monkeypatch):
    from custom_components.heating_assistant.coordinator.core import (
        HeatingAssistantCoordinator,
    )

    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = make_hass_stub()
    coord.model = SimpleNamespace(room_names=["living_room"])
    coord.now_utc = datetime.now(tz=timezone.utc)
    coord._time_in_range_last_refresh_ts = coord.now_utc.timestamp()
    coord._time_in_range_pct_24h = {}

    refresh_mock = AsyncMock(return_value={"living_room": 95.0})
    monkeypatch.setattr(
        "custom_components.heating_assistant.kpi_history.async_refresh_time_in_range_kpis",
        refresh_mock,
    )

    await coord._async_refresh_time_in_range_kpis_if_due()
    refresh_mock.assert_not_awaited()

    coord._time_in_range_last_refresh_ts = (
        coord.now_utc.timestamp() - TIME_IN_RANGE_REFRESH_S - 1.0
    )
    await coord._async_refresh_time_in_range_kpis_if_due()
    refresh_mock.assert_awaited_once()
    assert coord._time_in_range_pct_24h == {"living_room": 95.0}
