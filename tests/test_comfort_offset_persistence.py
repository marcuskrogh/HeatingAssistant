"""Round-trip tests for dashboard comfort-offset persistence (SWD-13)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.const import CONF_PERSISTED_COMFORT_OFFSETS
from custom_components.heating_assistant.coordinator import enablement
from custom_components.heating_assistant.coordinator.core import HeatingAssistantCoordinator


def _bare_coordinator_with_entry(entry_data: dict):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._entry = SimpleNamespace(entry_id="entry-1", data=dict(entry_data))
    coord.hass = MagicMock()
    coord.hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(entry_id="entry-1", data=dict(entry_data))
    )
    coord.hass.config_entries.async_update_entry = MagicMock()
    coord.model = SimpleNamespace(
        room_names=["Living Room"],
        rooms={"Living Room": SimpleNamespace(setpoint=21.0, comfort_offset=2.0)},
    )
    coord._room_comfort_offset = {"Living Room": 2.0}
    coord._build_controller = MagicMock()
    return coord


@pytest.mark.integration
def test_apply_persisted_comfort_offsets_overlays_coordinator():
    coord = _bare_coordinator_with_entry({})
    enablement.apply_persisted_comfort_offsets(
        coord, {"Living Room": 3.5}
    )
    assert coord._room_comfort_offset["Living Room"] == 3.5
    assert coord.model.rooms["Living Room"].comfort_offset == 3.5


@pytest.mark.integration
def test_init_room_state_reads_comfort_offsets_from_real_entry():
    """Startup must prefer disk entry over stale MergedEntry for comfort offsets."""
    entry_data = {CONF_PERSISTED_COMFORT_OFFSETS: {"Living Room": 4.0}}
    coord = _bare_coordinator_with_entry({CONF_PERSISTED_COMFORT_OFFSETS: {}})
    real_entry = SimpleNamespace(entry_id="entry-1", data=entry_data)
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=real_entry)
    coord._room_schedule = {}
    coord._schedule_enabled = {"Living Room": True}
    coord._room_enabled = {"Living Room": True}
    coord._schedule_disabled = {"Living Room": False}
    coord._base_setpoint = {"Living Room": 21.0}
    coord._window_sensors = {}
    coord._window_state = {}
    coord._window_state_since = {}
    coord._effective_setpoint = {}
    coord._entry.data[CONF_PERSISTED_COMFORT_OFFSETS] = {}

    from custom_components.heating_assistant.const import CONF_ROOM_NAME

    coord._init_room_state(
        [{CONF_ROOM_NAME: "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    )

    assert coord._room_comfort_offset["Living Room"] == 4.0


@pytest.mark.integration
def test_set_room_comfort_offset_syncs_merged_entry_data():
    coord = _bare_coordinator_with_entry({})
    entry = SimpleNamespace(entry_id="entry-1", data={})
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    enablement.set_room_comfort_offset(coord, "Living Room", 3.0)

    assert coord._entry.data[CONF_PERSISTED_COMFORT_OFFSETS]["Living Room"] == 3.0
