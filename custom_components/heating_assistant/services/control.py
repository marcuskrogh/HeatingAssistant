"""Control service handlers for Heating Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from ..const import (
    CONF_PERSISTED_SCHEDULES,
    DOMAIN,
    SERVICE_SET_SCHEDULE_ENABLED,
    SERVICE_SET_ROOM_ENABLED,
    SERVICE_SET_ROOM_SETPOINT,
)
from .context import get_coordinator

# Keep in sync with climate.MIN_TEMP (frost-protection floor).
_FROST_PROTECTION_FLOOR = 5.0


def _resolve_room_name(coordinator, room_name: str) -> str:
    """Resolve a frontend slug or exact name to the canonical configured room."""
    from ..naming import slugify as _slugify

    for name in coordinator.model.room_names:
        if name == room_name or _slugify(name) == room_name:
            return name
    raise ValueError(f"Room '{room_name}' not found in configuration")


async def handle_set_schedule_enabled(hass: HomeAssistant, call: ServiceCall) -> None:
    """Suspend or resume the comfort schedule for one or more rooms."""
    coordinator = get_coordinator(hass)
    enabled = bool(call.data["enabled"])
    room_name = call.data.get("room_name")
    if room_name:
        targets = [room_name]
    else:
        targets = list(coordinator.model.room_names)

    for name in targets:
        if name not in coordinator.model.rooms:
            continue
        coordinator.set_schedule_enabled(name, enabled)

    # Push an immediate state_changed event so the overview tiles refresh.
    # The new schedule state is visible on those tiles, so no persistent
    # notification is raised.
    coordinator.async_update_listeners()


async def handle_set_system_enabled(hass: HomeAssistant, call: ServiceCall) -> None:
    """Enable or disable the heating assistant controller globally."""
    coordinator = get_coordinator(hass)
    enabled = call.data["enabled"]
    coordinator.set_system_enabled(enabled)
    if enabled:
        # Engage the controller immediately on START rather than waiting for
        # the next scheduled tick — this runs the MPC and pushes commands to
        # the heaters right away so the action feels responsive.
        await coordinator.async_request_refresh()
    else:
        coordinator.async_update_listeners()


async def handle_update_room_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    """Update the schedule for a single room and persist to config entry."""
    coordinator = get_coordinator(hass)
    room_name: str = call.data["room_name"]
    periods: list = call.data["periods"]

    canonical_name = _resolve_room_name(coordinator, room_name)

    entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if entry is None:
        raise ValueError("Config entry not found")

    # Rebuild schedule in coordinator and update entity states BEFORE
    # persisting — this ensures the HA state machine has the correct
    # attributes before _async_update_listener fires.
    coordinator.reload_room_schedule(canonical_name, periods)
    coordinator.async_update_listeners()

    # Persist schedules in a dedicated key that won't be overwritten by
    # YAML/options merging during integration reload (same pattern as
    # CONF_PERSISTED_SETPOINTS).
    persisted: dict = dict(entry.data.get(CONF_PERSISTED_SCHEDULES) or {})
    persisted[canonical_name] = periods

    hass.config_entries.async_update_entry(
        entry,
        data={**dict(entry.data), CONF_PERSISTED_SCHEDULES: persisted},
    )

    # Keep the coordinator's MergedEntry overlay in sync so in-session reads
    # (startup overlay, runtime snapshots) see the same persisted_schedules
    # as the on-disk config entry.
    merged_data = getattr(coordinator._entry, "data", None)
    if isinstance(merged_data, dict):
        coordinator._entry.data = {
            **dict(merged_data),
            CONF_PERSISTED_SCHEDULES: persisted,
        }


async def handle_set_room_comfort_offset(hass: HomeAssistant, call: ServiceCall) -> None:
    """Set the default comfort-band half-width for a single room.

    Called from the dashboard climate cards so users can widen or narrow a
    room's comfort corridor without editing a schedule.  Resolves the
    canonical room name from the slug sent by the frontend, applies the
    change to the live model and persists it (mirroring set_room_setpoint).
    """
    coordinator = get_coordinator(hass)
    room_name: str = call.data["room_name"]
    comfort_offset: float = call.data["comfort_offset"]

    canonical_name = _resolve_room_name(coordinator, room_name)
    coordinator.set_room_comfort_offset(canonical_name, comfort_offset)
    coordinator.async_update_listeners()


async def handle_set_room_setpoint(hass: HomeAssistant, call: ServiceCall) -> None:
    """Set the target temperature for a single room."""
    coordinator = get_coordinator(hass)
    room_name: str = call.data["room_name"]
    setpoint: float = call.data["setpoint"]

    canonical_name = _resolve_room_name(coordinator, room_name)
    coordinator.set_room_setpoint(canonical_name, float(setpoint))
    coordinator.async_update_listeners()


async def handle_set_room_enabled(hass: HomeAssistant, call: ServiceCall) -> None:
    """Enable or disable heating control for a single room."""
    from ..const import DEFAULT_SETPOINT

    coordinator = get_coordinator(hass)
    room_name: str = call.data["room_name"]
    enabled: bool = call.data["enabled"]

    canonical_name = _resolve_room_name(coordinator, room_name)
    coordinator.set_room_enabled(canonical_name, enabled)
    if enabled and call.data.get("restore_default_setpoint"):
        if coordinator.get_room_setpoint(canonical_name) <= _FROST_PROTECTION_FLOOR:
            coordinator.set_room_setpoint(canonical_name, DEFAULT_SETPOINT)
    # Push room_enabled / room_active to the dashboard immediately so the
    # climate-card power toggle does not revert while a full MPC cycle runs.
    coordinator.async_update_listeners()
    await coordinator.async_request_refresh()


def register_control_services(hass: HomeAssistant) -> None:
    """Register control domain services."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_ENABLED,
        lambda call: handle_set_schedule_enabled(hass, call),
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Required("enabled"): cv.boolean,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "set_system_enabled",
        lambda call: handle_set_system_enabled(hass, call),
        schema=vol.Schema({vol.Required("enabled"): cv.boolean}),
    )

    hass.services.async_register(
        DOMAIN,
        "update_room_schedule",
        lambda call: handle_update_room_schedule(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("periods"): [
                    vol.Schema(
                        {
                            vol.Required("name"): cv.string,
                            vol.Required("mode"): vol.In(["comfort", "off"]),
                            vol.Required("start"): cv.string,
                            vol.Required("end"): cv.string,
                            vol.Optional("days"): [vol.Any(vol.Coerce(int), cv.string)],
                            vol.Optional("setpoint"): vol.Coerce(float),
                            vol.Optional("frost_protection", default=12.0): vol.Coerce(float),
                            vol.Optional("comfort_offset"): vol.Coerce(float),
                            vol.Optional("tracking_weight"): vol.Coerce(float),
                            vol.Optional("energy_weight"): vol.Coerce(float),
                            vol.Optional("all_day", default=False): cv.boolean,
                            vol.Optional("enabled", default=True): cv.boolean,
                            vol.Optional("recurring", default=True): cv.boolean,
                            vol.Optional("start_date"): cv.string,
                            vol.Optional("end_date"): cv.string,
                        }
                    )
                ],
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "set_room_comfort_offset",
        lambda call: handle_set_room_comfort_offset(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("comfort_offset"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ROOM_SETPOINT,
        lambda call: handle_set_room_setpoint(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("setpoint"): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ROOM_ENABLED,
        lambda call: handle_set_room_enabled(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("enabled"): cv.boolean,
                vol.Optional("restore_default_setpoint", default=False): cv.boolean,
            }
        ),
    )
