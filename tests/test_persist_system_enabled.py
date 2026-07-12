"""Tests for persisting the global START/STOP toggle across restarts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.const import (
    CONF_PERSISTED_SYSTEM_ENABLED,
    CONF_ROOM_NAME,
    CONF_ROOMS,
)
from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _make_minimal_coordinator(entry_data=None):
    """Build a bare coordinator with only the fields _init_room_state needs."""
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._entry = SimpleNamespace(
        entry_id="entry-1",
        data=entry_data or {},
    )
    coord.hass = MagicMock()
    coord.model = SimpleNamespace(room_names=["living_room"])
    return coord


def test_init_room_state_defaults_system_enabled_to_false():
    coord = _make_minimal_coordinator()
    coord._init_room_state([{CONF_ROOM_NAME: "living_room"}])
    assert coord._system_enabled is False


def test_init_room_state_restores_persisted_system_enabled():
    coord = _make_minimal_coordinator(
        {CONF_PERSISTED_SYSTEM_ENABLED: True}
    )
    coord._init_room_state([{CONF_ROOM_NAME: "living_room"}])
    assert coord._system_enabled is True


def test_set_system_enabled_persists_to_config_entry():
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_ROOMS: []},
    )
    coord = _make_minimal_coordinator()
    coord._entry = entry
    coord._system_enabled = False

    update_calls = []
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    coord.hass.config_entries.async_update_entry = (
        lambda e, **kwargs: update_calls.append(kwargs)
    )

    coord.set_system_enabled(True)

    assert coord._system_enabled is True
    assert len(update_calls) == 1
    assert update_calls[0]["data"][CONF_PERSISTED_SYSTEM_ENABLED] is True


def test_set_system_enabled_persists_false():
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_PERSISTED_SYSTEM_ENABLED: True},
    )
    coord = _make_minimal_coordinator({CONF_PERSISTED_SYSTEM_ENABLED: True})
    coord._entry = entry
    coord._system_enabled = True

    update_calls = []
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    coord.hass.config_entries.async_update_entry = (
        lambda e, **kwargs: update_calls.append(kwargs)
    )

    coord.set_system_enabled(False)

    assert coord._system_enabled is False
    assert update_calls[0]["data"][CONF_PERSISTED_SYSTEM_ENABLED] is False
