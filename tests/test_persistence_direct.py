"""Direct tests for persistence.py (previously only reached with its functions
monkeypatched out in the configuration-service tests)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.persistence as persistence
from custom_components.heating_assistant.const import (
    CONF_COMFORT_OFFSET,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_ROOM_NAME,
    CONF_ROOMS,
)
from custom_components.heating_assistant.persistence import (
    persist_tuning_updates,
    write_entry_config,
)

pytestmark = pytest.mark.integration


def _make_env(data=None, options=None):
    entry = SimpleNamespace(entry_id="entry-1", data=data or {}, options=options or {})
    update_entry = MagicMock()
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda entry_id: entry if entry_id == entry.entry_id else None,
            async_update_entry=update_entry,
        )
    )
    coordinator = SimpleNamespace(_entry=entry)
    return hass, coordinator, entry, update_entry


def _written(update_entry):
    assert update_entry.call_count == 1
    kwargs = update_entry.call_args.kwargs
    return kwargs["data"], kwargs["options"]


def test_persist_tuning_updates_writes_both_stores():
    hass, coordinator, _, update_entry = _make_env(
        data={"horizon_hours": 12, "kept": "d"}, options={"horizon_hours": 8}
    )

    persist_tuning_updates(hass, coordinator, {"horizon_hours": 24})

    data, options = _written(update_entry)
    assert data["horizon_hours"] == 24
    assert data["kept"] == "d"
    # Options must be updated too: options-first reads would otherwise revert
    # the change on the next restart.
    assert options["horizon_hours"] == 24


def test_comfort_offset_propagates_to_rooms_and_persisted_map():
    rooms = [
        {CONF_ROOM_NAME: "living", CONF_COMFORT_OFFSET: 0.0},
        {CONF_ROOM_NAME: "kitchen", CONF_COMFORT_OFFSET: 1.0},
    ]
    hass, coordinator, _, update_entry = _make_env(
        data={CONF_ROOMS: rooms},
        options={CONF_ROOMS: [dict(r) for r in rooms]},
    )

    persist_tuning_updates(hass, coordinator, {CONF_COMFORT_OFFSET: 0.5})

    data, options = _written(update_entry)
    assert [r[CONF_COMFORT_OFFSET] for r in data[CONF_ROOMS]] == [0.5, 0.5]
    assert data[CONF_PERSISTED_COMFORT_OFFSETS] == {"living": 0.5, "kitchen": 0.5}
    assert [r[CONF_COMFORT_OFFSET] for r in options[CONF_ROOMS]] == [0.5, 0.5]


def test_comfort_offset_leaves_options_rooms_absent():
    hass, coordinator, _, update_entry = _make_env(
        data={CONF_ROOMS: [{CONF_ROOM_NAME: "living"}]},
        options={},
    )

    persist_tuning_updates(hass, coordinator, {CONF_COMFORT_OFFSET: 0.5})

    _, options = _written(update_entry)
    # When options has no rooms list the coordinator falls back to data rooms;
    # persist must not invent one.
    assert CONF_ROOMS not in options


def test_missing_entry_is_noop():
    hass, coordinator, entry, update_entry = _make_env()
    entry.entry_id = "gone"
    coordinator._entry = SimpleNamespace(entry_id="other")

    persist_tuning_updates(hass, coordinator, {"x": 1})

    update_entry.assert_not_called()


def test_write_entry_config_updates_both_stores():
    hass, coordinator, _, update_entry = _make_env(data={"a": 1}, options={"b": 2})

    write_entry_config(hass, {"c": 3}, coordinator=coordinator)

    data, options = _written(update_entry)
    assert data == {"a": 1, "c": 3}
    assert options == {"b": 2, "c": 3}


def test_write_entry_config_resolves_coordinator(monkeypatch):
    hass, coordinator, _, update_entry = _make_env(data={"a": 1})
    monkeypatch.setattr(persistence, "get_coordinator", lambda h: coordinator)

    write_entry_config(hass, {"c": 3})

    data, _ = _written(update_entry)
    assert data == {"a": 1, "c": 3}
