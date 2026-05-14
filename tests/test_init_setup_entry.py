"""Tests for integration setup-entry resilience and persistence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_ROOMS,
    DEFAULT_HORIZON,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_setup_entry_continues_on_first_refresh_failure(monkeypatch):
    entry = SimpleNamespace(
        data={
            CONF_ROOMS: [],
            CONF_HEAT_SOURCES: [],
        },
        options={},
        entry_id="entry-1",
        title="Heating Assistant",
        add_update_listener=MagicMock(return_value=lambda: None),
        async_on_unload=MagicMock(),
    )

    yaml_cfg = {
        CONF_ROOMS: [{"name": "living_room"}],
        CONF_HEAT_SOURCES: [{"name": "heater"}],
    }

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {"yaml_config": yaml_cfg}}
    hass.services = SimpleNamespace(has_service=MagicMock(return_value=True))
    hass.config_entries = SimpleNamespace(
        async_update_entry=MagicMock(),
        async_forward_entry_setups=AsyncMock(),
    )

    class _FailingCoordinator:
        def __init__(self, *_args, **_kwargs):
            pass

        async def async_config_entry_first_refresh(self):
            raise init_mod.ConfigEntryNotReady()

    monkeypatch.setattr(init_mod, "HeatingAssistantCoordinator", _FailingCoordinator)

    assert await init_mod.async_setup_entry(hass, entry) is True

    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    hass.config_entries.async_update_entry.assert_called_once()
    update_call = hass.config_entries.async_update_entry.call_args
    assert update_call.kwargs["data"][CONF_ROOMS] == yaml_cfg[CONF_ROOMS]
    assert (
        update_call.kwargs["data"][CONF_HEAT_SOURCES]
        == yaml_cfg[CONF_HEAT_SOURCES]
    )


@pytest.mark.asyncio
async def test_setup_entry_uses_rooms_from_options_when_no_yaml(monkeypatch):
    """Rooms saved via the options flow (entry.options) must reach the coordinator."""
    options_rooms = [{"name": "bedroom", "thermal_mass": 4000000.0}]
    entry = SimpleNamespace(
        data={CONF_ROOMS: [], CONF_HEAT_SOURCES: []},
        options={CONF_ROOMS: options_rooms},
        entry_id="entry-2",
        title="Heating Assistant",
        add_update_listener=MagicMock(return_value=lambda: None),
        async_on_unload=MagicMock(),
    )

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}  # no yaml_config
    hass.services = SimpleNamespace(has_service=MagicMock(return_value=True))
    hass.config_entries = SimpleNamespace(
        async_update_entry=MagicMock(),
        async_forward_entry_setups=AsyncMock(),
    )

    captured: dict = {}

    class _CapturingCoordinator:
        def __init__(self, _hass, merged_entry, *_args, **_kwargs):
            captured["entry_data"] = merged_entry.data

        async def async_config_entry_first_refresh(self):
            pass

    monkeypatch.setattr(init_mod, "HeatingAssistantCoordinator", _CapturingCoordinator)

    assert await init_mod.async_setup_entry(hass, entry) is True

    assert captured["entry_data"][CONF_ROOMS] == options_rooms


@pytest.mark.asyncio
async def test_setup_entry_yaml_overrides_options_rooms(monkeypatch):
    """YAML rooms must still win over options-flow rooms."""
    options_rooms = [{"name": "bedroom"}]
    yaml_rooms = [{"name": "living_room"}, {"name": "kitchen"}]

    entry = SimpleNamespace(
        data={CONF_ROOMS: [], CONF_HEAT_SOURCES: []},
        options={CONF_ROOMS: options_rooms},
        entry_id="entry-3",
        title="Heating Assistant",
        add_update_listener=MagicMock(return_value=lambda: None),
        async_on_unload=MagicMock(),
    )

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {"yaml_config": {CONF_ROOMS: yaml_rooms}}}
    hass.services = SimpleNamespace(has_service=MagicMock(return_value=True))
    hass.config_entries = SimpleNamespace(
        async_update_entry=MagicMock(),
        async_forward_entry_setups=AsyncMock(),
    )

    captured: dict = {}

    class _CapturingCoordinator:
        def __init__(self, _hass, merged_entry, *_args, **_kwargs):
            captured["entry_data"] = merged_entry.data

        async def async_config_entry_first_refresh(self):
            pass

    monkeypatch.setattr(init_mod, "HeatingAssistantCoordinator", _CapturingCoordinator)

    assert await init_mod.async_setup_entry(hass, entry) is True

    assert captured["entry_data"][CONF_ROOMS] == yaml_rooms


@pytest.mark.asyncio
async def test_coordinator_uses_horizon_from_options(monkeypatch):
    """CONF_HORIZON set via the options flow must reach the coordinator."""
    import custom_components.heating_assistant.coordinator as coord_mod

    entry = SimpleNamespace(
        data={CONF_ROOMS: [], CONF_HEAT_SOURCES: [], CONF_HORIZON: DEFAULT_HORIZON},
        options={CONF_HORIZON: 42},
        entry_id="entry-h1",
        title="Heating Assistant",
    )

    hass = SimpleNamespace()
    hass.config = SimpleNamespace(latitude=55.0, longitude=10.0)

    class _FakeController:
        def __init__(self, *_a, **_kw):
            pass

    monkeypatch.setattr(coord_mod, "HeatingMPCController", _FakeController)

    coord = coord_mod.HeatingAssistantCoordinator.__new__(
        coord_mod.HeatingAssistantCoordinator
    )
    coord.__init__(hass, entry)

    assert coord._horizon == 42, f"Expected 42, got {coord._horizon}"


@pytest.mark.asyncio
async def test_coordinator_falls_back_to_data_horizon_when_options_absent(monkeypatch):
    """When options does not set CONF_HORIZON, entry.data value must be used."""
    import custom_components.heating_assistant.coordinator as coord_mod

    entry = SimpleNamespace(
        data={CONF_ROOMS: [], CONF_HEAT_SOURCES: [], CONF_HORIZON: 75},
        options={},
        entry_id="entry-h2",
        title="Heating Assistant",
    )

    hass = SimpleNamespace()
    hass.config = SimpleNamespace(latitude=55.0, longitude=10.0)

    class _FakeController:
        def __init__(self, *_a, **_kw):
            pass

    monkeypatch.setattr(coord_mod, "HeatingMPCController", _FakeController)

    coord = coord_mod.HeatingAssistantCoordinator.__new__(
        coord_mod.HeatingAssistantCoordinator
    )
    coord.__init__(hass, entry)

    assert coord._horizon == 75, f"Expected 75, got {coord._horizon}"
