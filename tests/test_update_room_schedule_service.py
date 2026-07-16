"""Regression tests for the ``update_room_schedule`` service persistence.

These guard the bug where a saved room schedule disappeared on reload because
the service wrote the periods only to ``entry.data[CONF_ROOMS]`` while rooms
configured via the UI options flow live in ``entry.options[CONF_ROOMS]`` — which
overrides and is copied back over ``entry.data`` on every setup, silently
discarding the schedule.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
import custom_components.heating_assistant.services.control as svc_mod
from custom_components.heating_assistant.const import (
    CONF_PERSISTED_SCHEDULES,
    CONF_ROOMS,
    CONF_SCHEDULE,
    DOMAIN,
)


def _capture_handler(hass):
    """Run ``_register_services`` and return the update_room_schedule handler."""
    handlers: dict = {}

    def _register(domain, name, handler, **_kwargs):
        handlers[name] = handler

    hass.services = SimpleNamespace(async_register=_register)
    init_mod._register_services(hass)
    return handlers["update_room_schedule"]


def _make_coordinator(room_names=None):
    coordinator = MagicMock()
    coordinator._entry = SimpleNamespace(entry_id="entry-1", data={})
    names = room_names or ["Living Room"]
    coordinator.model = SimpleNamespace(room_names=names)
    return coordinator


PERIODS = [
    {
        "name": "Morning",
        "schedule_type": "weekly_recurring",
        "time_mode": "window",
        "mode": "comfort",
        "start": "06:00",
        "end": "09:00",
        "days": [0, 1, 2, 3, 4],
        "setpoint": 21.0,
    }
]


@pytest.mark.asyncio
async def test_schedule_persisted_to_options_when_rooms_in_options(monkeypatch):
    """When rooms live in options, the schedule must be written to options too."""
    rooms = [{"name": "Living Room", "thermal_mass": 4_000_000.0}]
    entry = SimpleNamespace(
        data={CONF_ROOMS: [dict(rooms[0])]},
        options={CONF_ROOMS: [dict(rooms[0])]},
        entry_id="entry-1",
    )

    coordinator = _make_coordinator(["Living Room"])
    update_calls: list = []

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=lambda e, **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(svc_mod, "get_coordinator", lambda _h: coordinator)

    handler = _capture_handler(hass)
    await handler(SimpleNamespace(data={"room_name": "living_room", "periods": PERIODS}))

    assert len(update_calls) == 1
    call = update_calls[0]
    # Schedules persist in a dedicated key so YAML/options merging cannot discard them.
    assert call["data"][CONF_PERSISTED_SCHEDULES]["Living Room"] == PERIODS
    assert "options" not in call

    coordinator.reload_room_schedule.assert_called_once_with("Living Room", PERIODS)
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_schedule_persisted_to_data_when_options_empty(monkeypatch):
    """When rooms live only in data, options must be left untouched."""
    rooms = [{"name": "Bedroom", "thermal_mass": 3_000_000.0}]
    entry = SimpleNamespace(
        data={CONF_ROOMS: [dict(rooms[0])]},
        options={},
        entry_id="entry-1",
    )

    coordinator = _make_coordinator(["Bedroom"])
    update_calls: list = []

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=lambda e, **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(svc_mod, "get_coordinator", lambda _h: coordinator)

    handler = _capture_handler(hass)
    await handler(SimpleNamespace(data={"room_name": "bedroom", "periods": PERIODS}))

    assert len(update_calls) == 1
    call = update_calls[0]
    assert call["data"][CONF_PERSISTED_SCHEDULES]["Bedroom"] == PERIODS
    assert "options" not in call


@pytest.mark.asyncio
async def test_unknown_room_raises(monkeypatch):
    entry = SimpleNamespace(
        data={CONF_ROOMS: [{"name": "Kitchen"}]},
        options={},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator(["Kitchen"])

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=lambda e, **kwargs: None,
    )
    monkeypatch.setattr(svc_mod, "get_coordinator", lambda _h: coordinator)

    handler = _capture_handler(hass)
    with pytest.raises(ValueError):
        await handler(
            SimpleNamespace(data={"room_name": "nonexistent", "periods": PERIODS})
        )
