"""Round-trip tests for dashboard schedule persistence (SWD-2).

Guards the regression where schedules appeared saved on the detail page but
vanished on navigation and after HA restart.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.services.control as svc_mod
from custom_components.heating_assistant.const import CONF_PERSISTED_SCHEDULES, CONF_ROOM_NAME, CONF_ROOMS
from custom_components.heating_assistant.coordinator import schedule_control
from custom_components.heating_assistant.coordinator.core import HeatingAssistantCoordinator
from custom_components.heating_assistant.coordinator import runtime_reconfig
from custom_components.heating_assistant.schedule import build_schedule
from tests.helpers.coordinator_stubs import make_minimal_coordinator

PERIODS = [
    {
        "name": "Morning",
        "mode": "comfort",
        "start": "06:00",
        "end": "09:00",
        "days": [0, 1, 2, 3, 4],
        "setpoint": 21.0,
        "enabled": True,
        "recurring": True,
        "all_day": False,
    }
]


def _bare_coordinator_with_entry(entry_data: dict):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._entry = SimpleNamespace(entry_id="entry-1", data=dict(entry_data))
    coord.hass = MagicMock()
    coord.hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(entry_id="entry-1", data=dict(entry_data))
    )
    coord.model = SimpleNamespace(
        room_names=["Living Room"],
        rooms={"Living Room": SimpleNamespace(setpoint=21.0, comfort_offset=2.0)},
    )
    coord._room_schedule = {}
    coord._schedule_enabled = {"Living Room": True}
    return coord


@pytest.mark.integration
def test_apply_persisted_schedules_resolves_slug_keys():
    """Persisted schedules keyed by slug must overlay canonical room names."""
    coord = _bare_coordinator_with_entry({})
    coord._room_schedule = {"Living Room": build_schedule([])}

    schedule_control.apply_persisted_schedules(
        coord, {"living_room": PERIODS}
    )

    assert len(coord._room_schedule["Living Room"].periods) == 1
    assert coord._room_schedule["Living Room"].periods[0].name == "Morning"


@pytest.mark.integration
def test_apply_persisted_schedules_skips_ambiguous_slug_keys():
    """Slug lookup must not attach schedules when two rooms share the same slug."""
    coord = _bare_coordinator_with_entry({})
    coord._room_schedule = {
        "Room A": build_schedule([]),
        "Room  A": build_schedule([]),  # slugifies to the same value as "Room A"
    }
    coord.model.room_names = ["Room A", "Room  A"]

    schedule_control.apply_persisted_schedules(
        coord, {"room_a": PERIODS}
    )

    assert len(coord._room_schedule["Room A"].periods) == 0
    assert len(coord._room_schedule["Room  A"].periods) == 0

    schedule_control.apply_persisted_schedules(
        coord, {"Room A": PERIODS}
    )

    assert len(coord._room_schedule["Room A"].periods) == 1
    assert len(coord._room_schedule["Room  A"].periods) == 0


@pytest.mark.integration
def test_init_room_state_prefers_real_entry_over_stale_merged_overlay():
    """MergedEntry.data can lag behind disk; startup must read the real entry."""
    persisted = {CONF_PERSISTED_SCHEDULES: {"Living Room": PERIODS}}
    coord = _bare_coordinator_with_entry(
        {CONF_PERSISTED_SCHEDULES: {}}  # stale in-memory overlay
    )
    real_entry = SimpleNamespace(entry_id="entry-1", data=persisted)
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=real_entry)

    coord._init_room_state(
        [{CONF_ROOM_NAME: "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    )

    assert len(coord._room_schedule["Living Room"].periods) == 1


@pytest.mark.integration
def test_init_room_state_reads_persisted_schedules_from_real_entry():
    """Startup overlay must read authoritative config-entry data, not stale MergedEntry."""
    entry_data = {
        CONF_PERSISTED_SCHEDULES: {"Living Room": PERIODS},
    }
    coord = _bare_coordinator_with_entry({})
    real_entry = SimpleNamespace(entry_id="entry-1", data=entry_data)
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=real_entry)

    coord._init_room_state(
        [{CONF_ROOM_NAME: "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    )

    assert len(coord._room_schedule["Living Room"].periods) == 1


@pytest.mark.integration
def test_serialize_room_schedules_after_persisted_overlay():
    """Coordinator serialization must expose saved periods to WebSocket consumers."""
    coord = _bare_coordinator_with_entry(
        {CONF_PERSISTED_SCHEDULES: {"Living Room": PERIODS}}
    )
    real_entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_PERSISTED_SCHEDULES: {"Living Room": PERIODS}},
    )
    coord.hass.config_entries.async_get_entry = MagicMock(return_value=real_entry)

    coord._init_room_state(
        [{CONF_ROOM_NAME: "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    )

    payload = schedule_control.serialize_room_schedules(coord)
    assert "living_room" in payload
    assert len(payload["living_room"]["periods"]) == 1


@pytest.mark.asyncio
async def test_update_room_schedule_syncs_merged_entry_data(monkeypatch):
    """Service save must update MergedEntry.data so in-session reads stay consistent."""
    rooms = [{"name": "Living Room", "thermal_mass": 4_000_000.0}]
    entry = SimpleNamespace(
        data={CONF_ROOMS: [dict(rooms[0])]},
        options={},
        entry_id="entry-1",
    )
    stored: dict = {}

    def _update_entry(e, **kwargs):
        stored.update(kwargs.get("data", {}))
        entry.data = dict(kwargs.get("data", {}))

    coordinator = make_minimal_coordinator(room_names=["Living Room"])
    coordinator._entry = SimpleNamespace(entry_id="entry-1", data=dict(entry.data))
    coordinator.reload_room_schedule = MagicMock(
        side_effect=lambda name, periods: schedule_control.reload_room_schedule(
            coordinator, name, periods
        )
    )
    coordinator.async_update_listeners = MagicMock()

    hass = SimpleNamespace()
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=_update_entry,
    )
    monkeypatch.setattr(svc_mod, "get_coordinator", lambda _h: coordinator)

    await svc_mod.handle_update_room_schedule(
        hass,
        SimpleNamespace(data={"room_name": "living_room", "periods": PERIODS}),
    )

    assert stored[CONF_PERSISTED_SCHEDULES]["Living Room"] == PERIODS
    assert coordinator._entry.data[CONF_PERSISTED_SCHEDULES]["Living Room"] == PERIODS
    assert len(coordinator._room_schedule["Living Room"].periods) == 1

    payload = schedule_control.serialize_room_schedules(coordinator)
    assert len(payload["living_room"]["periods"]) == 1


@pytest.mark.integration
def test_runtime_reconfig_applies_persisted_schedules_in_place():
    """Persisted schedule writes must overlay the coordinator without a reload."""
    coord = _bare_coordinator_with_entry({CONF_PERSISTED_SCHEDULES: {}})
    coord._room_schedule = {"Living Room": build_schedule([])}
    coord._last_runtime_config = {CONF_PERSISTED_SCHEDULES: {}}

    assert runtime_reconfig.apply_runtime_reconfiguration(
        coord,
        {CONF_PERSISTED_SCHEDULES: {"Living Room": PERIODS}},
    )

    assert len(coord._room_schedule["Living Room"].periods) == 1


@pytest.mark.asyncio
async def test_update_listener_applies_persisted_schedules_without_reload(monkeypatch):
    """Config-entry listener must refresh schedules when only persisted_schedules changes."""
    import custom_components.heating_assistant.__init__ as init_mod
    from custom_components.heating_assistant.const import DOMAIN

    entry_data = {
        CONF_ROOMS: [{CONF_ROOM_NAME: "Living Room", "setpoint": 21.0}],
        CONF_PERSISTED_SCHEDULES: {"Living Room": PERIODS},
    }
    entry = SimpleNamespace(
        entry_id="entry-1",
        data=dict(entry_data),
        options={},
        add_update_listener=lambda _cb: None,
        async_on_unload=lambda _cb: None,
    )
    coordinator = make_minimal_coordinator(room_names=["Living Room"])
    coordinator._entry = SimpleNamespace(entry_id="entry-1", data={})
    coordinator._room_schedule = {"Living Room": build_schedule([])}
    coordinator._last_runtime_config = {
        CONF_ROOMS: entry_data[CONF_ROOMS],
        CONF_PERSISTED_SCHEDULES: {},
    }
    coordinator.async_update_listeners = MagicMock()

    hass = SimpleNamespace()
    hass.data = {DOMAIN: {"entry-1": coordinator, "yaml_config": {}}}
    hass.config_entries = SimpleNamespace(async_reload=MagicMock())
    hass.async_create_task = MagicMock()

    monkeypatch.setattr(init_mod, "_merge_yaml_into_entry_data", lambda data, _yaml: data)

    await init_mod._async_update_listener(hass, entry)

    assert len(coordinator._room_schedule["Living Room"].periods) == 1
    coordinator.async_update_listeners.assert_called_once()
    hass.config_entries.async_reload.assert_not_called()
