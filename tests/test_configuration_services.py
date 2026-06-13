"""Tests for the Configuration-page service handlers.

These back the industrial-UI Configuration menu: display settings, room and
heat-source replacement, and environment/site updates.  They assert the handlers
persist to both ``entry.data`` and ``entry.options`` (so the coordinator's
options-first reads survive a restart) and route runtime updates to the
coordinator.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PRICE_ENTITY,
    CONF_ROOMS,
    CONF_ROOM_NAME,
    DOMAIN,
)


def _capture_handlers(hass):
    handlers: dict = {}

    def _register(domain, name, handler, **_kwargs):
        handlers[name] = handler

    hass.services = SimpleNamespace(
        async_register=_register,
        has_service=lambda *_a, **_k: False,
    )
    init_mod._register_services(hass)
    return handlers


def _make_hass(entry, coordinator, update_calls, monkeypatch):
    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=lambda e, **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(init_mod, "_get_coordinator", lambda _h: coordinator)
    return hass


def _make_coordinator():
    coordinator = MagicMock()
    coordinator._entry = SimpleNamespace(entry_id="entry-1")
    return coordinator


@pytest.mark.asyncio
async def test_update_ui_settings_persists_both_stores(monkeypatch):
    entry = SimpleNamespace(data={}, options={}, entry_id="entry-1")
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_ui_settings"](
        SimpleNamespace(data={CONF_PLOT_HISTORY_HOURS: 24.0, CONF_PLOT_FORECAST_HOURS: 6.0})
    )

    assert len(update_calls) == 1
    call = update_calls[0]
    assert call["data"][CONF_PLOT_HISTORY_HOURS] == 24.0
    assert call["options"][CONF_PLOT_HISTORY_HOURS] == 24.0
    assert call["data"][CONF_PLOT_FORECAST_HOURS] == 6.0
    coordinator.apply_tuning_updates.assert_called_once()


@pytest.mark.asyncio
async def test_update_rooms_writes_full_list_to_both_stores(monkeypatch):
    entry = SimpleNamespace(
        data={CONF_ROOMS: [{CONF_ROOM_NAME: "old"}]},
        options={},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    new_rooms = [
        {CONF_ROOM_NAME: "living_room", "setpoint": 21.0},
        {CONF_ROOM_NAME: "kitchen", "setpoint": 20.0},
    ]
    handlers = _capture_handlers(hass)
    await handlers["update_rooms"](SimpleNamespace(data={CONF_ROOMS: new_rooms}))

    assert len(update_calls) == 1
    call = update_calls[0]
    assert call["data"][CONF_ROOMS] == new_rooms
    assert call["options"][CONF_ROOMS] == new_rooms


@pytest.mark.asyncio
async def test_update_heat_sources_writes_full_list(monkeypatch):
    entry = SimpleNamespace(data={}, options={}, entry_id="entry-1")
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    sources = [{"name": "heater", "type": "electric_heater", "room": "living_room", "max_power": 2000}]
    handlers = _capture_handlers(hass)
    await handlers["update_heat_sources"](SimpleNamespace(data={CONF_HEAT_SOURCES: sources}))

    call = update_calls[0]
    assert call["data"][CONF_HEAT_SOURCES] == sources
    assert call["options"][CONF_HEAT_SOURCES] == sources


@pytest.mark.asyncio
async def test_update_system_config_normalises_and_routes(monkeypatch):
    entry = SimpleNamespace(
        data={CONF_OUTDOOR_TEMP_ENTITY: "sensor.old"},
        options={},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_system_config"](
        SimpleNamespace(data={
            CONF_OUTDOOR_TEMP_ENTITY: "sensor.outdoor",
            CONF_PRICE_ENTITY: "",  # cleared
            CONF_LATITUDE: 55.0,
            CONF_LONGITUDE: 12.0,
        })
    )

    call = update_calls[0]
    assert call["data"][CONF_OUTDOOR_TEMP_ENTITY] == "sensor.outdoor"
    # An empty entity is normalised to "" (never the literal string "None").
    assert call["data"][CONF_PRICE_ENTITY] == ""
    assert call["data"][CONF_LATITUDE] == 55.0
    assert call["data"][CONF_LONGITUDE] == 12.0
    coordinator.apply_tuning_updates.assert_called_once()
    applied = coordinator.apply_tuning_updates.call_args[0][0]
    assert applied[CONF_PRICE_ENTITY] == ""
    assert applied[CONF_LATITUDE] == 55.0
