"""Configuration-page service handlers for Heating Assistant."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
import homeassistant.helpers.config_validation as cv

from ..const import (
    CONF_COMFORT_OFFSET,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_ENERGY_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    CONF_IDENTIFICATION_HORIZON_HOURS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PRICE_ENTITY,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_TERMINAL_WEIGHT,
    CONF_TRACKING_WEIGHT,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    DOMAIN,
)
from ..persistence import persist_tuning_updates, write_entry_config
from ..room_migration import (
    _apply_renames_to_connections,
    _migrate_room_entities,
    _migrate_room_name_data,
)


def _coordinator(hass: HomeAssistant):
    import importlib

    return importlib.import_module(
        "custom_components.heating_assistant.__init__"
    )._get_coordinator(hass)


_LOGGER = logging.getLogger(__name__)

DEFAULT_DASHBOARD_FILENAME = "heating_assistant.yaml"
DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME = "heating_assistant_industrial.yaml"
SERVICE_REGENERATE_DASHBOARD = "regenerate_dashboard"

_CONTROLLER_TUNING_KEYS = {
    CONF_TRACKING_WEIGHT,
    CONF_ENERGY_WEIGHT,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_TERMINAL_WEIGHT,
    CONF_HORIZON,
    CONF_UPDATE_INTERVAL,
    CONF_COMFORT_OFFSET,
}

_ESTIMATION_PARAM_KEYS = {
    CONF_SIGMA_W,
    CONF_SIGMA_V,
    CONF_SIGMA_B,
    CONF_IDENTIFICATION_HORIZON_HOURS,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_Q_INFLATION,
}

_SYSTEM_PARAM_KEYS = {CONF_IDENTIFICATION_HISTORY_DAYS}


async def handle_update_controller_tuning(hass: HomeAssistant, call: ServiceCall) -> None:
    """Update MPC controller tuning parameters from the dashboard."""
    coordinator = _coordinator(hass)
    updates = {k: v for k, v in call.data.items() if k in _CONTROLLER_TUNING_KEYS}
    if not updates:
        return
    persist_tuning_updates(hass, coordinator, updates)
    coordinator.apply_tuning_updates(updates)
    coordinator.async_update_listeners()


async def handle_update_estimation_params(hass: HomeAssistant, call: ServiceCall) -> None:
    """Update state estimation parameters from the dashboard."""
    coordinator = _coordinator(hass)
    updates = {k: v for k, v in call.data.items() if k in _ESTIMATION_PARAM_KEYS}
    if not updates:
        return
    persist_tuning_updates(hass, coordinator, updates)
    coordinator.apply_tuning_updates(updates)
    coordinator.async_update_listeners()


async def handle_update_ui_settings(hass: HomeAssistant, call: ServiceCall) -> None:
    """Persist industrial-panel display settings (plot history / horizon)."""
    coordinator = _coordinator(hass)
    updates = {
        k: v
        for k, v in call.data.items()
        if k in (CONF_PLOT_HISTORY_HOURS, CONF_PLOT_FORECAST_HOURS)
    }
    if not updates:
        return
    persist_tuning_updates(hass, coordinator, updates)
    coordinator.apply_tuning_updates(updates)
    coordinator.async_update_listeners()


async def handle_update_system_params(hass: HomeAssistant, call: ServiceCall) -> None:
    """Persist system-level parameters (e.g. history retention)."""
    coordinator = _coordinator(hass)
    updates = {k: v for k, v in call.data.items() if k in _SYSTEM_PARAM_KEYS}
    if not updates:
        return
    persist_tuning_updates(hass, coordinator, updates)
    coordinator.apply_tuning_updates(updates)
    coordinator.async_update_listeners()


async def handle_update_rooms(hass: HomeAssistant, call: ServiceCall) -> None:
    """Replace the configured room list (triggers an integration reload).

    When ``renames`` is supplied ({old_name: new_name}) all data keyed by
    the old room name — persisted state, estimated parameters, heat-source
    links, inter-room connections and the room's entities — is migrated to
    the new name so nothing is left orphaned.
    """
    rooms = call.data["rooms"]
    renames = {
        str(k): str(v)
        for k, v in (call.data.get("renames") or {}).items()
        if k and v and str(k) != str(v)
    }
    if not renames:
        coordinator = _coordinator(hass)
        write_entry_config(hass, {CONF_ROOMS: rooms}, coordinator=coordinator)
        return

    coordinator = _coordinator(hass)
    entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
    if entry is None:
        return

    rooms = _apply_renames_to_connections(rooms, renames)
    room_names = [r.get(CONF_ROOM_NAME) for r in rooms if isinstance(r, dict)]
    # Carry the rename map so the post-reload setup can also remap the
    # in-memory runtime state (room/schedule enabled, base setpoints).
    hass.data.setdefault(DOMAIN, {})["_pending_room_renames"] = renames

    try:
        _migrate_room_entities(hass, entry, renames, room_names)
    except Exception:  # pragma: no cover - defensive
        _LOGGER.warning(
            "Heating Assistant: room-rename entity migration failed",
            exc_info=True,
        )

    # Build migration bases.  Heat sources may live exclusively in
    # entry.options (options-first write path), so promote them into the
    # data dict before migration so CONF_HEAT_SOURCES links are always
    # updated regardless of which store holds them.
    data_base = dict(entry.data)
    if CONF_HEAT_SOURCES not in data_base and CONF_HEAT_SOURCES in entry.options:
        data_base[CONF_HEAT_SOURCES] = entry.options[CONF_HEAT_SOURCES]
    data_base[CONF_ROOMS] = rooms

    new_data = _migrate_room_name_data(data_base, renames)
    new_options = _migrate_room_name_data(
        {**dict(entry.options), CONF_ROOMS: rooms}, renames
    )
    new_options[CONF_ROOMS] = rooms
    hass.config_entries.async_update_entry(
        entry, data=new_data, options=new_options
    )


async def handle_update_heat_sources(hass: HomeAssistant, call: ServiceCall) -> None:
    """Replace the configured heat-source list (triggers a reload)."""
    coordinator = _coordinator(hass)
    sources = call.data["heat_sources"]
    write_entry_config(hass, {CONF_HEAT_SOURCES: sources}, coordinator=coordinator)


async def handle_update_system_config(hass: HomeAssistant, call: ServiceCall) -> None:
    """Update environment entities and site location from the dashboard.

    Entity keys are applied in-place by the coordinator's runtime
    reconfiguration; an empty string clears the entity.
    """
    coordinator = _coordinator(hass)
    _entity_keys = (
        CONF_OUTDOOR_TEMP_ENTITY,
        CONF_WEATHER_ENTITY,
        CONF_SOLAR_RADIATION_ENTITY,
        CONF_PRICE_ENTITY,
    )
    updates: Dict[str, Any] = {}
    for key in _entity_keys:
        if key in call.data:
            # Normalise to "" (cleared) rather than None so the coordinator's
            # str() coercion never produces the literal string "None".
            updates[key] = (call.data.get(key) or "").strip()
    for key in (CONF_LATITUDE, CONF_LONGITUDE):
        if key in call.data:
            updates[key] = float(call.data[key])
    if not updates:
        return
    persist_tuning_updates(hass, coordinator, updates)
    coordinator.apply_tuning_updates(updates)
    coordinator.async_update_listeners()


async def handle_regenerate_dashboard(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Regenerate the Heating Assistant Lovelace dashboard.

    When ``dry_run`` is true the generated YAML is returned in the
    service response without touching the filesystem. Otherwise it is
    written to ``<config>/dashboards/<filename>`` (relative paths
    outside the config directory are rejected).
    """
    from ..dashboard import (
        DASHBOARD_VARIANT_CLASSIC,
        DASHBOARD_VARIANT_INDUSTRIAL,
        build_dashboard_variant_from_coordinator,
        dashboard_to_yaml,
    )

    coordinator = _coordinator(hass)
    # ``build_dashboard_from_coordinator`` is a pure read over coordinator
    # state; run it inline to avoid racing with the next update cycle.
    variant = str(call.data.get("variant") or DASHBOARD_VARIANT_CLASSIC)
    if variant not in {DASHBOARD_VARIANT_CLASSIC, DASHBOARD_VARIANT_INDUSTRIAL}:
        raise ValueError("variant must be 'classic' or 'industrial'")
    dashboard = build_dashboard_variant_from_coordinator(coordinator, variant=variant)
    yaml_text: str = await hass.async_add_executor_job(
        dashboard_to_yaml, dashboard
    )

    dry_run = bool(call.data.get("dry_run", False))
    default_filename = (
        DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME
        if variant == DASHBOARD_VARIANT_INDUSTRIAL
        else DEFAULT_DASHBOARD_FILENAME
    )
    filename = str(call.data.get("filename") or default_filename)
    write_path: str | None = None

    if not dry_run:
        base_dir = hass.config.path("dashboards")
        safe_filename = os.path.basename(filename)
        if not safe_filename or safe_filename != filename:
            raise ValueError(
                "filename must be a plain file name, not a path"
            )
        write_path = os.path.join(base_dir, safe_filename)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(write_path, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

    # The write path is returned in the service response (``written_to``)
    # for programmatic callers; no persistent notification is raised.
    return {
        "yaml": yaml_text,
        "variant": variant,
        "rooms": [v["title"] for v in dashboard["views"] if v.get("subview")],
        "written_to": write_path,
    }


def register_configuration_services(hass: HomeAssistant) -> None:
    """Register configuration-page domain services."""
    hass.services.async_register(
        DOMAIN,
        "update_controller_tuning",
        lambda call: handle_update_controller_tuning(hass, call),
        schema=vol.Schema(
            {
                vol.Optional(CONF_TRACKING_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_ENERGY_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_ENERGY_PRICE_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_SMOOTHING_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_SOFT_CONSTRAINT_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_TERMINAL_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_HORIZON): vol.Coerce(int),
                vol.Optional(CONF_UPDATE_INTERVAL): vol.Coerce(int),
                vol.Optional(CONF_COMFORT_OFFSET): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "update_estimation_params",
        lambda call: handle_update_estimation_params(hass, call),
        schema=vol.Schema(
            {
                vol.Optional(CONF_SIGMA_W): vol.Coerce(float),
                vol.Optional(CONF_SIGMA_V): vol.Coerce(float),
                vol.Optional(CONF_SIGMA_B): vol.Coerce(float),
                vol.Optional(CONF_IDENTIFICATION_HORIZON_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5)
                ),
                vol.Optional(CONF_WINDOW_OPEN_DEBOUNCE): vol.Coerce(int),
                vol.Optional(CONF_WINDOW_OPEN_CLOSE_SETTLE): vol.Coerce(int),
                vol.Optional(CONF_WINDOW_OPEN_Q_INFLATION): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "update_ui_settings",
        lambda call: handle_update_ui_settings(hass, call),
        schema=vol.Schema(
            {
                vol.Optional(CONF_PLOT_HISTORY_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0)
                ),
                vol.Optional(CONF_PLOT_FORECAST_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "update_system_params",
        lambda call: handle_update_system_params(hass, call),
        schema=vol.Schema(
            {
                vol.Optional(CONF_IDENTIFICATION_HISTORY_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=7)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "update_rooms",
        lambda call: handle_update_rooms(hass, call),
        schema=vol.Schema(
            {
                vol.Required(CONF_ROOMS): [dict],
                # Optional {old_name: new_name} map so a rename migrates all
                # data keyed by the old room name.
                vol.Optional("renames"): {cv.string: cv.string},
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "update_heat_sources",
        lambda call: handle_update_heat_sources(hass, call),
        schema=vol.Schema({vol.Required(CONF_HEAT_SOURCES): [dict]}),
    )

    hass.services.async_register(
        DOMAIN,
        "update_system_config",
        lambda call: handle_update_system_config(hass, call),
        schema=vol.Schema(
            {
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): cv.string,
                vol.Optional(CONF_WEATHER_ENTITY): cv.string,
                vol.Optional(CONF_SOLAR_RADIATION_ENTITY): cv.string,
                vol.Optional(CONF_PRICE_ENTITY): cv.string,
                vol.Optional(CONF_LATITUDE): vol.All(
                    vol.Coerce(float), vol.Range(min=-90.0, max=90.0)
                ),
                vol.Optional(CONF_LONGITUDE): vol.All(
                    vol.Coerce(float), vol.Range(min=-180.0, max=180.0)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REGENERATE_DASHBOARD,
        lambda call: handle_regenerate_dashboard(hass, call),
        schema=vol.Schema(
            {
                vol.Optional("dry_run", default=False): cv.boolean,
                vol.Optional("filename"): cv.string,
                vol.Optional("variant"): vol.In(["classic", "industrial"]),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
