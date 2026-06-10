"""Regression tests for dashboard tuning-parameter persistence.

Guards the bug where tuning / estimation parameters set from the dashboard
reverted to the previously-configured set on restart.  The coordinator reads
these parameters with **options-first** precedence, but the dashboard service
handlers wrote only to ``entry.data``.  Once the options flow had snapshotted
the config into ``entry.options``, that stale options value re-won on every
restart and the dashboard change was silently discarded.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from custom_components.heating_assistant.const import (
    CONF_COMFORT_OFFSET,
    CONF_ROOMS,
    CONF_ROOM_NAME,
    CONF_TRACKING_WEIGHT,
    CONF_SIGMA_W,
    DOMAIN,
)


def _capture_handlers(hass):
    """Run ``_register_services`` and return the registered handler map."""
    handlers: dict = {}

    def _register(domain, name, handler, **_kwargs):
        handlers[name] = handler

    hass.services = SimpleNamespace(
        async_register=_register,
        has_service=lambda *_a, **_k: False,
    )
    init_mod._register_services(hass)
    return handlers


def _make_coordinator():
    coordinator = MagicMock()
    coordinator._entry = SimpleNamespace(entry_id="entry-1")
    return coordinator


def _make_hass(entry, coordinator, update_calls, monkeypatch):
    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=lambda e, **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(init_mod, "_get_coordinator", lambda _h: coordinator)
    return hass


@pytest.mark.asyncio
async def test_controller_tuning_written_to_both_data_and_options(monkeypatch):
    """update_controller_tuning must persist to options (read-first) and data."""
    entry = SimpleNamespace(
        data={CONF_TRACKING_WEIGHT: 1.0},
        options={CONF_TRACKING_WEIGHT: 1.0},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_controller_tuning"](
        SimpleNamespace(data={CONF_TRACKING_WEIGHT: 5.0})
    )

    assert len(update_calls) == 1
    call = update_calls[0]
    # Options is the store the coordinator reads first — it MUST hold the new value
    # so a restart does not revert to the stale previously-configured value.
    assert call["options"][CONF_TRACKING_WEIGHT] == 5.0
    assert call["data"][CONF_TRACKING_WEIGHT] == 5.0
    coordinator.apply_tuning_updates.assert_called_once_with({CONF_TRACKING_WEIGHT: 5.0})


@pytest.mark.asyncio
async def test_estimation_params_written_to_both_data_and_options(monkeypatch):
    """update_estimation_params must persist to options (read-first) and data."""
    entry = SimpleNamespace(
        data={CONF_SIGMA_W: 0.25},
        options={},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_estimation_params"](
        SimpleNamespace(data={CONF_SIGMA_W: 0.5})
    )

    assert len(update_calls) == 1
    call = update_calls[0]
    assert call["options"][CONF_SIGMA_W] == 0.5
    assert call["data"][CONF_SIGMA_W] == 0.5


@pytest.mark.asyncio
async def test_estimation_params_routed_and_persisted(monkeypatch):
    """Estimation-params knobs set from the Tuning UI must reach the
    coordinator and persist to both stores (they are not silently dropped)."""
    entry = SimpleNamespace(data={}, options={}, entry_id="entry-1")
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    est_updates = {
        CONF_SIGMA_W: 0.2,
    }
    await handlers["update_estimation_params"](SimpleNamespace(data=dict(est_updates)))

    assert len(update_calls) == 1
    call = update_calls[0]
    for key, value in est_updates.items():
        assert call["options"][key] == value
        assert call["data"][key] == value
    coordinator.apply_tuning_updates.assert_called_once_with(est_updates)


@pytest.mark.asyncio
async def test_comfort_offset_propagated_to_per_room_config(monkeypatch):
    """comfort_offset must be written into every room's dict so the coordinator
    reads the updated value on restart (it reads per-room, not the global key)."""
    rooms = [
        {CONF_ROOM_NAME: "Living Room", CONF_COMFORT_OFFSET: 2.0},
        {CONF_ROOM_NAME: "Bedroom", CONF_COMFORT_OFFSET: 2.0},
    ]
    entry = SimpleNamespace(
        data={CONF_ROOMS: rooms},
        options={},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_controller_tuning"](
        SimpleNamespace(data={CONF_COMFORT_OFFSET: 3.0})
    )

    assert len(update_calls) == 1
    call = update_calls[0]
    # Global key must be present in both stores.
    assert call["data"][CONF_COMFORT_OFFSET] == 3.0
    assert call["options"][CONF_COMFORT_OFFSET] == 3.0
    # Per-room values in data must be updated so a restart picks up the new offset.
    for room in call["data"][CONF_ROOMS]:
        assert room[CONF_COMFORT_OFFSET] == 3.0, f"Room {room[CONF_ROOM_NAME]} not updated"


@pytest.mark.asyncio
async def test_comfort_offset_updates_options_rooms_when_present(monkeypatch):
    """When options already has CONF_ROOMS, comfort_offset must be propagated there
    too — the coordinator prefers options over data when reading rooms on restart."""
    rooms_data = [{CONF_ROOM_NAME: "Living Room", CONF_COMFORT_OFFSET: 2.0}]
    rooms_opts = [{CONF_ROOM_NAME: "Living Room", CONF_COMFORT_OFFSET: 1.5}]
    entry = SimpleNamespace(
        data={CONF_ROOMS: rooms_data},
        options={CONF_ROOMS: rooms_opts},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_controller_tuning"](
        SimpleNamespace(data={CONF_COMFORT_OFFSET: 2.5})
    )

    call = update_calls[0]
    # Both stores' room lists must carry the updated value.
    assert call["data"][CONF_ROOMS][0][CONF_COMFORT_OFFSET] == 2.5
    assert call["options"][CONF_ROOMS][0][CONF_COMFORT_OFFSET] == 2.5
