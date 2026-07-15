"""Round-trip tests for per-room schedule suspend persistence."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.const import CONF_PERSISTED_SCHEDULE_ENABLED
from custom_components.heating_assistant.coordinator import schedule_control
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
        rooms={
            "Living Room": SimpleNamespace(
                setpoint=21.0, comfort_offset=2.0, temperature=20.0
            )
        },
    )
    coord._room_schedule = {}
    coord._schedule_enabled = {"Living Room": True}
    coord._room_enabled = {"Living Room": True}
    coord._schedule_disabled = {"Living Room": False}
    coord._base_setpoint = {"Living Room": 21.0}
    coord._room_comfort_offset = {"Living Room": 2.0}
    coord._effective_setpoint = {}
    return coord


@pytest.mark.integration
def test_set_schedule_enabled_persists_to_config_entry():
    coord = _bare_coordinator_with_entry({})
    entry = SimpleNamespace(entry_id="entry-1", data={})
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    schedule_control.set_schedule_enabled(coord, "Living Room", False)

    assert coord._schedule_enabled["Living Room"] is False
    coord.hass.config_entries.async_update_entry.assert_called_once()
    call_kwargs = coord.hass.config_entries.async_update_entry.call_args.kwargs
    assert call_kwargs["data"][CONF_PERSISTED_SCHEDULE_ENABLED]["Living Room"] is False
    assert coord._entry.data[CONF_PERSISTED_SCHEDULE_ENABLED]["Living Room"] is False


@pytest.mark.integration
def test_apply_persisted_schedule_enabled_overlays_coordinator():
    coord = _bare_coordinator_with_entry({})
    schedule_control.apply_persisted_schedule_enabled(
        coord, {"Living Room": False}
    )
    assert coord._schedule_enabled["Living Room"] is False


@pytest.mark.integration
def test_init_room_state_reads_schedule_enabled_from_real_entry():
    entry_data = {CONF_PERSISTED_SCHEDULE_ENABLED: {"Living Room": False}}
    coord = _bare_coordinator_with_entry({CONF_PERSISTED_SCHEDULE_ENABLED: {}})
    real_entry = SimpleNamespace(entry_id="entry-1", data=entry_data)
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=real_entry)
    coord._room_schedule = {}
    coord._window_sensors = {}
    coord._window_state = {}
    coord._window_state_since = {}

    from custom_components.heating_assistant.const import CONF_ROOM_NAME

    coord._init_room_state(
        [{CONF_ROOM_NAME: "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    )

    assert coord._schedule_enabled["Living Room"] is False
