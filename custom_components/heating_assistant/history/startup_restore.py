"""Restore and persist the coordinator history buffer across reloads/restarts."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DOMAIN, HISTORY_BUFFER_SIZE

if TYPE_CHECKING:
    from ..coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_restore_history_buffer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> None:
    """Restore runtime state stashed by a prior unload or cold-start sources."""
    reload_state = hass.data[DOMAIN].get("_reload_state", {}).pop(entry.entry_id, None)
    if reload_state is not None:
        _apply_reload_state(hass, coordinator, reload_state)
        return

    await _restore_from_persistent_sources(hass, entry, coordinator)


def _apply_reload_state(
    hass: HomeAssistant,
    coordinator: HeatingAssistantCoordinator,
    reload_state: Dict[str, Any],
) -> None:
    """Apply in-memory state from a config-entry reload."""
    renames = hass.data[DOMAIN].pop("_pending_room_renames", {}) or {}

    def _remap(state_dict: Dict[str, Any]) -> Dict[str, Any]:
        return {renames.get(k, k): v for k, v in state_dict.items()}

    coordinator._history_buffer.extend(reload_state.get("history_buffer", []))
    if "system_enabled" in reload_state:
        coordinator._system_enabled = bool(reload_state["system_enabled"])
    for room, value in _remap(reload_state.get("room_enabled", {})).items():
        if room in coordinator._room_enabled:
            coordinator._room_enabled[room] = value
    for room, value in _remap(reload_state.get("schedule_enabled", {})).items():
        if room in coordinator._schedule_enabled:
            coordinator._schedule_enabled[room] = value
    for room, value in _remap(reload_state.get("base_setpoint", {})).items():
        if room in coordinator._base_setpoint:
            coordinator._base_setpoint[room] = float(value)
            coordinator.model.rooms[room].setpoint = float(value)


async def _restore_from_persistent_sources(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> None:
    """Cold start: JSONL store → Recorder → legacy JSON store."""
    rebuilt = []
    try:
        rebuilt = await coordinator.id_history_store.async_query_recent(
            HISTORY_BUFFER_SIZE
        )
    except Exception:
        _LOGGER.warning(
            "Heating Assistant: JSONL history store query failed on startup",
            exc_info=True,
        )

    if rebuilt:
        coordinator._history_buffer.extend(rebuilt[-HISTORY_BUFFER_SIZE:])
        _LOGGER.debug(
            "Restored %d history steps from JSONL store",
            len(coordinator._history_buffer),
        )
        return

    try:
        from ..history.seed import async_rebuild_history_from_recorder

        rebuilt = await async_rebuild_history_from_recorder(
            hass, coordinator, HISTORY_BUFFER_SIZE
        )
    except Exception:
        _LOGGER.warning(
            "Heating Assistant: history rebuild from recorder failed",
            exc_info=True,
        )

    if rebuilt:
        coordinator._history_buffer.extend(rebuilt[-HISTORY_BUFFER_SIZE:])
        _LOGGER.debug(
            "Rebuilt %d history steps from the recorder",
            len(coordinator._history_buffer),
        )
        return

    try:
        store = Store(
            hass,
            version=1,
            key=f"{DOMAIN}_history_{entry.entry_id}",
        )
        stored_history = await store.async_load()
        if stored_history and isinstance(stored_history, list):
            from ..history.window import prune_stale_records

            now = getattr(coordinator, "now_utc", None)
            now_ts = now.timestamp() if now is not None else time.time()
            coordinator._history_buffer.extend(
                prune_stale_records(
                    stored_history[-HISTORY_BUFFER_SIZE:],
                    now_ts,
                    HISTORY_BUFFER_SIZE * coordinator.dt,
                )
            )
            _LOGGER.debug(
                "Restored %d history steps from persistent storage",
                len(coordinator._history_buffer),
            )
    except Exception:
        _LOGGER.warning(
            "Heating Assistant: failed to load persisted history buffer",
            exc_info=True,
        )


async def async_stash_reload_state(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> None:
    """Stash coordinator runtime state for the next in-memory reload."""
    reload_state = hass.data[DOMAIN].setdefault("_reload_state", {})
    reload_state[entry.entry_id] = {
        "history_buffer": list(coordinator._history_buffer),
        "system_enabled": coordinator._system_enabled,
        "room_enabled": dict(coordinator._room_enabled),
        "schedule_enabled": dict(coordinator._schedule_enabled),
        "base_setpoint": dict(coordinator._base_setpoint),
    }
    try:
        store = Store(
            hass,
            version=1,
            key=f"{DOMAIN}_history_{entry.entry_id}",
        )
        await store.async_save(list(coordinator._history_buffer))
    except Exception:
        _LOGGER.warning(
            "Heating Assistant: failed to persist history buffer",
            exc_info=True,
        )
