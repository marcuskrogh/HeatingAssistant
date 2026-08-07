"""Config-entry persistence helpers for dashboard and service updates."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.core import HomeAssistant

from .const import CONF_COMFORT_OFFSET, CONF_PERSISTED_COMFORT_OFFSETS, CONF_ROOM_NAME, CONF_ROOMS
from .coordinator import HeatingAssistantCoordinator
from .services.context import get_coordinator


def persist_tuning_updates(
    hass: HomeAssistant,
    coordinator: HeatingAssistantCoordinator,
    updates: Dict[str, Any],
) -> None:
    """Persist dashboard tuning changes so they survive a reload/restart.

    The coordinator reads tuning/estimation parameters with **options-first**
    precedence (see ``HeatingAssistantCoordinator.__init__``): an ``options``
    value always shadows the matching ``data`` value.  The options flow
    ("Configure") snapshots the whole config into ``entry.options`` the first
    time it is saved, so writing dashboard updates to ``entry.data`` alone left
    a stale ``entry.options`` value that re-won on the next restart — the
    parameters silently reverted to the previously-configured set.

    Writing the updates to **both** stores keeps them consistent and ensures the
    options-first read picks up the latest dashboard values after a restart.

    ``CONF_COMFORT_OFFSET`` is a special case: the Tuning dashboard sends it as
    a single global value that applies to every room, but the coordinator reads
    it **per-room** from the rooms list (``CONF_ROOMS[i][CONF_COMFORT_OFFSET]``),
    not from a top-level key.  Writing only the top-level key therefore has no
    effect on restart.  We propagate the value into every room entry in both
    stores so that a restart correctly reflects the user's intent.
    """
    entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if entry is None:
        return
    new_data = {**dict(entry.data), **updates}
    new_options = {**dict(entry.options), **updates}

    if CONF_COMFORT_OFFSET in updates:
        new_co = float(updates[CONF_COMFORT_OFFSET])
        new_data[CONF_ROOMS] = [
            {**r, CONF_COMFORT_OFFSET: new_co}
            for r in new_data.get(CONF_ROOMS, [])
        ]
        # Propagate into CONF_PERSISTED_COMFORT_OFFSETS so that the global
        # tuning value also takes effect after a restart.  Without this the
        # persisted per-room values would silently win over the global setting
        # on the next startup, making in-session and post-restart behaviour
        # inconsistent.
        new_data[CONF_PERSISTED_COMFORT_OFFSETS] = {
            r[CONF_ROOM_NAME]: new_co
            for r in new_data.get(CONF_ROOMS, [])
            if CONF_ROOM_NAME in r
        }
        # Update options rooms only when they already exist; if options has no
        # CONF_ROOMS yet the coordinator falls back to the updated data rooms.
        if CONF_ROOMS in new_options:
            new_options[CONF_ROOMS] = [
                {**r, CONF_COMFORT_OFFSET: new_co}
                for r in new_options.get(CONF_ROOMS, [])
            ]

    hass.config_entries.async_update_entry(
        entry, data=new_data, options=new_options
    )


def write_entry_config(
    hass: HomeAssistant,
    updates: Dict[str, Any],
    *,
    coordinator: HeatingAssistantCoordinator | None = None,
) -> None:
    """Write ``updates`` to both entry.data and entry.options.

    Writing to both stores keeps the coordinator's options-first reads
    consistent across restarts.  The registered update-listener reloads the
    integration when a structural key (rooms / heat sources) changes and
    applies the rest in-place.
    """
    if coordinator is None:
        coordinator = get_coordinator(hass)
    entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if entry is None:
        return
    new_data = {**dict(entry.data), **updates}
    new_options = {**dict(entry.options), **updates}
    hass.config_entries.async_update_entry(
        entry, data=new_data, options=new_options
    )
