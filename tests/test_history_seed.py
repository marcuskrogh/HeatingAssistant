from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.history.seed import (
    _earliest_usable_ts,
    _entity_ids,
    _samples_from_states,
    async_fetch_history_range,
    async_rebuild_history_from_recorder,
    build_records_from_history,
)
from tests.helpers.coordinator_stubs import make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


class S:
    """Minimal stand-in for a recorder State."""

    def __init__(self, state, ts):
        self.state = state
        self.last_updated = datetime.fromtimestamp(ts, tz=timezone.utc)
        self.last_changed = self.last_updated


IDS = {
    "room_names": ["living"],
    "source_names": ["heater"],
    "temp": ["t_living"],
    "solar": ["s_living"],
    "control": ["c_heater"],
    "outdoor": "outdoor",
}


def _install_recorder_stubs(monkeypatch, history: dict, *, fetch_raises: bool = False):
    """Stub HA recorder imports used by the async seed helpers."""
    recorder_mod = types.ModuleType("homeassistant.components.recorder")
    history_mod = types.ModuleType("homeassistant.components.recorder.history")

    recorder_instance = MagicMock()

    async def _run_job(fn):
        if fetch_raises:
            raise RuntimeError("recorder fetch failed")
        return fn()

    recorder_instance.async_add_executor_job = AsyncMock(side_effect=_run_job)
    recorder_mod.get_instance = MagicMock(return_value=recorder_instance)
    history_mod.get_significant_states = MagicMock(return_value=history)

    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", recorder_mod)
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder.history", history_mod
    )
    return history_mod.get_significant_states


def test_samples_filters_non_numeric():
    states = [
        S("20.0", 0),
        S("unavailable", 900),
        S("unknown", 1800),
        S("21.5", 2700),
        S("", 3600),
    ]
    samples = _samples_from_states(states)
    assert samples == [(0.0, 20.0), (2700.0, 21.5)]


def test_samples_uses_last_changed_when_last_updated_missing():
    state = SimpleNamespace(state="19.5", last_updated=None, last_changed=S("x", 100).last_changed)
    assert _samples_from_states([state]) == [(100.0, 19.5)]


def test_samples_skips_states_without_timestamp():
    state = SimpleNamespace(state="20.0", last_updated=None, last_changed=None)
    assert _samples_from_states([state]) == []


def test_entity_ids_from_coordinator():
    coord = make_minimal_coordinator(room_names=["Living Room", "Bedroom"])
    coord.heat_sources = [
        SimpleNamespace(name="Main Heater"),
        SimpleNamespace(name="UFH Loop"),
    ]

    ids = _entity_ids(coord)

    assert ids["room_names"] == ["Living Room", "Bedroom"]
    assert ids["source_names"] == ["Main Heater", "UFH Loop"]
    assert ids["temp"] == [
        "sensor.heating_assistant_living_room_temperature_measured",
        "sensor.heating_assistant_bedroom_temperature_measured",
    ]
    assert ids["solar"] == [
        "sensor.heating_assistant_living_room_solar_gain_measured",
        "sensor.heating_assistant_bedroom_solar_gain_measured",
    ]
    assert ids["control"] == [
        "sensor.heating_assistant_main_heater_control_action",
        "sensor.heating_assistant_ufh_loop_control_action",
    ]
    assert ids["outdoor"] == "sensor.heating_assistant_outdoor_temperature_measured"


def test_build_records_basic():
    history = {
        "t_living": [S("20.0", 0), S("21.0", 1800)],
        "outdoor": [S("5.0", 0)],
        "c_heater": [S("50", 0)],  # 50% -> 0.5
        "s_living": [S("100", 0)],
    }
    grid = [0.0, 900.0, 1800.0]
    recs = build_records_from_history(history, IDS, grid)
    assert len(recs) == 3
    assert recs[0] == {
        "y": [20.0],
        "u": [0.5],
        "d_outdoor": 5.0,
        "d_solar": {"living": 100.0},
        "timestamp": 0.0,
    }
    assert recs[1]["y"] == [20.0]  # ZOH holds 20 until t=1800
    assert recs[2]["y"] == [21.0]
    assert all(r["u"] == [0.5] for r in recs)


def test_build_records_skips_before_data():
    history = {
        "t_living": [S("20.0", 1800)],  # temp only from t=1800
        "outdoor": [S("5.0", 0)],
        "c_heater": [],
        "s_living": [],
    }
    grid = [0.0, 900.0, 1800.0, 2700.0]
    recs = build_records_from_history(history, IDS, grid)
    # First two grid points have no temperature yet -> skipped.
    assert [r["timestamp"] for r in recs] == [1800.0, 2700.0]
    # Missing control/solar default to 0.
    assert recs[0]["u"] == [0.0]
    assert recs[0]["d_solar"] == {"living": 0.0}


def test_build_records_skips_when_outdoor_missing():
    history = {
        "t_living": [S("20.0", 0)],
        "outdoor": [],
        "c_heater": [],
        "s_living": [],
    }
    recs = build_records_from_history(history, IDS, [0.0, 900.0])
    assert recs == []


def test_build_records_empty_grid_returns_empty_list():
    history = {"t_living": [S("20.0", 0)], "outdoor": [S("5.0", 0)]}
    assert build_records_from_history(history, IDS, []) == []


def test_build_records_skips_when_any_room_temp_missing():
    ids = {
        "room_names": ["a", "b"],
        "source_names": ["h1"],
        "temp": ["t_a", "t_b"],
        "solar": ["s_a", "s_b"],
        "control": ["c1"],
        "outdoor": "out",
    }
    history = {
        "t_a": [S("20", 0)],
        "t_b": [],  # second room never measured
        "out": [S("3", 0)],
        "c1": [],
        "s_a": [],
        "s_b": [],
    }
    assert build_records_from_history(history, ids, [0.0, 900.0]) == []


def test_build_records_defaults_missing_entity_history_to_zero():
    history = {
        "t_living": [S("20.0", 0)],
        "outdoor": [S("5.0", 0)],
    }
    recs = build_records_from_history(history, IDS, [0.0])
    assert recs[0]["u"] == [0.0]
    assert recs[0]["d_solar"] == {"living": 0.0}


def test_earliest_usable_ts():
    history = {"t_living": [S("20.0", 500)], "outdoor": [S("5.0", 1200)]}
    # earliest instant both essentials exist = max(500, 1200) = 1200
    assert _earliest_usable_ts(history, IDS) == 1200.0
    # missing essential -> None
    assert _earliest_usable_ts({"t_living": [], "outdoor": [S("5", 0)]}, IDS) is None


def test_multi_room_ordering():
    ids = {
        "room_names": ["a", "b"],
        "source_names": ["h1", "h2"],
        "temp": ["t_a", "t_b"],
        "solar": ["s_a", "s_b"],
        "control": ["c1", "c2"],
        "outdoor": "out",
    }
    history = {
        "t_a": [S("20", 0)],
        "t_b": [S("18", 0)],
        "out": [S("3", 0)],
        "c1": [S("100", 0)],
        "c2": [S("0", 0)],
        "s_a": [S("200", 0)],
        "s_b": [S("50", 0)],
    }
    recs = build_records_from_history(history, ids, [0.0])
    assert recs[0]["y"] == [20.0, 18.0]
    assert recs[0]["u"] == [1.0, 0.0]
    assert recs[0]["d_solar"] == {"a": 200.0, "b": 50.0}


@pytest.mark.asyncio
async def test_async_fetch_history_range_mocked_recorder(monkeypatch):
    coord = make_minimal_coordinator(room_names=["living_room"], dt=900)
    history = {
        "sensor.heating_assistant_living_room_temperature_measured": [S("20.0", 1_000.0)],
        "sensor.heating_assistant_outdoor_temperature_measured": [S("5.0", 1_000.0)],
        "sensor.heating_assistant_living_room_solar_gain_measured": [S("80", 1_000.0)],
        "sensor.heating_assistant_control_action": [],
    }
    mock_get_states = _install_recorder_stubs(monkeypatch, history)
    hass = SimpleNamespace()

    records = await async_fetch_history_range(hass, coord, 1_000.0, 2_800.0)

    assert len(records) >= 1
    assert records[0]["y"] == [20.0]
    assert records[0]["d_outdoor"] == 5.0
    mock_get_states.assert_called_once()
    _, start, end, entity_ids, *_rest = mock_get_states.call_args[0]
    assert start == datetime.fromtimestamp(100.0, tz=timezone.utc)
    assert end == datetime.fromtimestamp(3700.0, tz=timezone.utc)
    assert "sensor.heating_assistant_living_room_temperature_measured" in entity_ids


@pytest.mark.asyncio
async def test_async_fetch_history_range_returns_empty_when_recorder_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "homeassistant.components.recorder", raising=False)
    coord = make_minimal_coordinator()
    hass = SimpleNamespace()

    assert await async_fetch_history_range(hass, coord, 0.0, 900.0) == []


@pytest.mark.asyncio
async def test_async_fetch_history_range_returns_empty_on_fetch_failure(monkeypatch):
    coord = make_minimal_coordinator(room_names=["living_room"], dt=900)
    history = {
        "sensor.heating_assistant_living_room_temperature_measured": [S("20.0", 0)],
        "sensor.heating_assistant_outdoor_temperature_measured": [S("5.0", 0)],
    }
    _install_recorder_stubs(monkeypatch, history, fetch_raises=True)
    hass = SimpleNamespace()

    assert await async_fetch_history_range(hass, coord, 0.0, 900.0) == []


@pytest.mark.asyncio
async def test_async_rebuild_history_from_recorder(monkeypatch):
    coord = make_minimal_coordinator(room_names=["living_room"], dt=900)
    coord.heat_sources = [SimpleNamespace(name="heater")]
    history = {
        "sensor.heating_assistant_living_room_temperature_measured": [S("20.0", 0.0)],
        "sensor.heating_assistant_outdoor_temperature_measured": [S("5.0", 0.0)],
        "sensor.heating_assistant_living_room_solar_gain_measured": [S("10", 0.0)],
        "sensor.heating_assistant_heater_control_action": [S("25", 0.0)],
    }
    mock_get_states = _install_recorder_stubs(monkeypatch, history)
    hass = SimpleNamespace()

    records = await async_rebuild_history_from_recorder(hass, coord, max_records=3)

    assert len(records) <= 3
    assert records
    assert records[-1]["y"] == [20.0]
    assert records[-1]["u"] == [0.25]
    mock_get_states.assert_called_once()


@pytest.mark.asyncio
async def test_async_rebuild_history_from_recorder_empty_room_names():
    coord = make_minimal_coordinator(room_names=[])
    coord.model.room_names = []
    hass = SimpleNamespace()

    assert await async_rebuild_history_from_recorder(hass, coord, max_records=10) == []


@pytest.mark.asyncio
async def test_async_rebuild_history_from_recorder_invalid_dt():
    coord = make_minimal_coordinator(dt=0)
    hass = SimpleNamespace()

    assert await async_rebuild_history_from_recorder(hass, coord, max_records=10) == []


@pytest.mark.asyncio
async def test_async_rebuild_history_from_recorder_no_usable_history(monkeypatch):
    coord = make_minimal_coordinator(room_names=["living_room"], dt=900)
    history = {
        "sensor.heating_assistant_living_room_temperature_measured": [],
        "sensor.heating_assistant_outdoor_temperature_measured": [S("5.0", 0.0)],
    }
    _install_recorder_stubs(monkeypatch, history)
    hass = SimpleNamespace()

    assert await async_rebuild_history_from_recorder(hass, coord, max_records=10) == []
