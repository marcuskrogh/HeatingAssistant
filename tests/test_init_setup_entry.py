"""Tests for integration setup-entry resilience and persistence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_ROOMS,
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
