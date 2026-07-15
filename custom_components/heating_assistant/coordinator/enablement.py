"""Room setpoint, comfort-offset, and enable/disable helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..const import (
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_ROOM_ENABLED,
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_SYSTEM_ENABLED,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_SETPOINT,
)

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator


def _sync_persisted_key_to_merged_entry(
    coordinator: "HeatingAssistantCoordinator", key: str, value: dict
) -> None:
    """Keep MergedEntry.data aligned with disk after a persisted-key write."""
    merged_data = getattr(coordinator._entry, "data", None)
    if isinstance(merged_data, dict):
        coordinator._entry.data = {**dict(merged_data), key: dict(value)}


def apply_persisted_comfort_offsets(
    coordinator: "HeatingAssistantCoordinator", persisted_offsets: dict
) -> None:
    """Overlay dashboard-persisted comfort offsets onto coordinator state."""
    for room_name, value in (persisted_offsets or {}).items():
        if room_name in coordinator._room_comfort_offset:
            offset = float(value)
            coordinator._room_comfort_offset[room_name] = offset
            coordinator.model.rooms[room_name].comfort_offset = offset


def set_room_setpoint(
    coordinator: HeatingAssistantCoordinator, room_name: str, setpoint: float
) -> None:
    """Update the temperature setpoint for a room.

    The change is recorded as the *base* setpoint (the value the user
    wants when no schedule period is active).  When a schedule period
    is currently active, the change still overrides the live setpoint
    until the next coordinator tick re-applies the schedule.  This way
    users can nudge the temperature mid-period without their request
    being silently dropped.
    """
    if room_name not in coordinator.model.rooms:
        return
    value = float(setpoint)
    coordinator._base_setpoint[room_name] = value
    coordinator.model.rooms[room_name].setpoint = value
    # Persist so the setpoint survives HA restarts and scheduled off periods.
    real_entry = coordinator.hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if real_entry is not None:
        persisted = dict(coordinator._base_setpoint)
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={**dict(real_entry.data), CONF_PERSISTED_SETPOINTS: persisted},
        )
        _sync_persisted_key_to_merged_entry(coordinator, CONF_PERSISTED_SETPOINTS, persisted)


def get_room_setpoint(coordinator: HeatingAssistantCoordinator, room_name: str) -> float:
    """Return the current (live) setpoint for a room."""
    if room_name in coordinator.model.rooms:
        return coordinator.model.rooms[room_name].setpoint
    return DEFAULT_SETPOINT


def get_base_setpoint(coordinator: HeatingAssistantCoordinator, room_name: str) -> float:
    """Return the user-chosen setpoint used outside any schedule period."""
    if room_name in coordinator._base_setpoint:
        return coordinator._base_setpoint[room_name]
    return get_room_setpoint(coordinator, room_name)


def set_room_comfort_offset(
    coordinator: HeatingAssistantCoordinator, room_name: str, comfort_offset: float
) -> None:
    """Update the default comfort-band half-width (°C) for a room.

    Mirrors :meth:`set_room_setpoint`: the value becomes the room's
    default comfort offset — the symmetric ±band around the setpoint the
    controller keeps the room inside — used whenever no schedule period
    overrides it.  It is applied to the live model immediately (so the
    controller corridor updates on the next plan) and persisted so the
    choice survives HA restarts.  When a schedule period is currently
    active with its own ``comfort_offset`` the live value still reverts to
    the schedule on the next coordinator tick, exactly like the setpoint.
    """
    if room_name not in coordinator.model.rooms:
        return
    value = float(comfort_offset)
    coordinator._room_comfort_offset[room_name] = value
    coordinator.model.rooms[room_name].comfort_offset = value
    # Rebuild the controller so the widened/narrowed corridor takes effect.
    coordinator._build_controller()
    # Persist so the offset survives HA restarts (same pattern as setpoints).
    real_entry = coordinator.hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if real_entry is not None:
        persisted = dict(coordinator._room_comfort_offset)
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={
                **dict(real_entry.data),
                CONF_PERSISTED_COMFORT_OFFSETS: persisted,
            },
        )
        _sync_persisted_key_to_merged_entry(coordinator, CONF_PERSISTED_COMFORT_OFFSETS, persisted)


def get_room_comfort_offset(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> float:
    """Return the default comfort-band half-width [°C] for a room."""
    return coordinator._room_comfort_offset.get(room_name, DEFAULT_COMFORT_OFFSET)


def system_enabled(coordinator: HeatingAssistantCoordinator) -> bool:
    """Return whether the heating assistant controller is active."""
    return coordinator._system_enabled


def set_system_enabled(coordinator: HeatingAssistantCoordinator, enabled: bool) -> None:
    """Enable or disable the entire heating assistant controller."""
    coordinator._system_enabled = bool(enabled)
    # Persist so the START/STOP toggle survives a full HA restart.
    real_entry = coordinator.hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if real_entry is not None:
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={
                **dict(real_entry.data),
                CONF_PERSISTED_SYSTEM_ENABLED: coordinator._system_enabled,
            },
        )


def set_room_enabled(
    coordinator: HeatingAssistantCoordinator, room_name: str, enabled: bool
) -> None:
    """Enable or disable heating control for a room (user toggle)."""
    coordinator._room_enabled[room_name] = enabled
    # Persist so the toggle survives a full HA restart (same pattern as setpoints).
    real_entry = coordinator.hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if real_entry is not None:
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={
                **dict(real_entry.data),
                CONF_PERSISTED_ROOM_ENABLED: dict(coordinator._room_enabled),
            },
        )


def is_room_enabled(coordinator: HeatingAssistantCoordinator, room_name: str) -> bool:
    """Return whether heating control should run for a room *right now*.

    The room runs when the user has not switched it off **and** no
    active comfort schedule period requests it to be off.  Schedule and
    user toggle are tracked independently so toggling one cannot
    silently override the other.
    """
    if not coordinator._room_enabled.get(room_name, True):
        return False
    if coordinator._schedule_disabled.get(room_name, False):
        return False
    return True
