"""
Heating Assistant integration – entry-point.

Set-up flow
-----------
1. ``async_setup``        – register YAML-sourced room/source configuration.
2. ``async_setup_entry``  – create the coordinator and set up platforms.
3. ``async_unload_entry`` – clean up on removal.

YAML configuration example
---------------------------
``configuration.yaml``::

    heating_assistant:
      rooms:
        - name: living_room
          thermal_mass: 8000000     # J/K
          r_external: 0.04          # K/W
          setpoint: 21.0
          temp_sensor: sensor.living_room_temperature
          # Multiple sensors can be used for averaging:
          # temp_sensors:
          #   - sensor.living_room_temp_north
          #   - sensor.living_room_temp_south
          connections:
            - room: kitchen
              r_value: 0.2
          windows:
            - area: 3.0             # m²
              orientation: 180      # South
              tilt: 90
        - name: kitchen
          thermal_mass: 4000000
          r_external: 0.06
          setpoint: 20.0
          connections:
            - room: living_room
              r_value: 0.2

      heat_sources:
        - name: living_room_heater
          type: electric_heater
          room: living_room
          max_power: 2000           # W
          heater_entity: switch.living_room_heater
        - name: heat_pump
          type: heat_pump
          room: living_room
          max_power: 5000           # W thermal
          cop_rated: 3.5
          cop_temp_ref: 7.0
          min_power: 1000           # W thermal (minimum operating power)
          heater_entity: climate.heat_pump

      outdoor_temp_entity: sensor.outdoor_temperature
      weather_entity: weather.forecast_home       # optional: weather forecast
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store

from .const import (
    CONF_COMFORT_OFFSET,
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_ENERGY_WEIGHT,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_C_SLAB_FRACTION,
    CONF_FACADE_ABSORPTANCE,
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_INFILTRATION_FRACTION,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_R_SA,
    CONF_R_SG,
    CONF_SKY_RADIATIVE_UA,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_TRACKING_WEIGHT,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    SOLAR_EXPOSURE_TO_APERTURE,
    CONF_R_EXTERNAL,
    CONF_R_VALUE,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SCHEDULE,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_START,
    CONF_PERSISTED_SCHEDULES,
    CONF_SETPOINT,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TURN_OFF_DEADBAND,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_TYPE,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_THERMAL_MASS,
    CONF_UPDATE_INTERVAL,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_SENSORS,
    CONF_WINDOW_TILT,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_EFFICIENCY,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_ENERGY_PRICE_WEIGHT,
    DEFAULT_FROST_PROTECTION,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_HORIZON,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    FACADE_COLOUR_TO_ABSORPTANCE,
    FLOOR_TYPE_DEFAULTS,
    DEFAULT_MIN_POWER,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_TRACKING_WEIGHT,
    DEFAULT_THERMAL_MASS,
    DEFAULT_TURN_OFF_DEADBAND,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    DEFAULT_WINDOW_OPEN_DEBOUNCE,
    DEFAULT_WINDOW_OPEN_Q_INFLATION,
    DOMAIN,
    HISTORY_BUFFER_SIZE,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SERVICE_SET_SCHEDULE_ENABLED,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
    UPDATE_INTERVAL,
)
from .coordinator import HeatingAssistantCoordinator
from .yaml_merge import MergedEntry as _MergedEntry, merge_yaml_into_entry_data as _merge_yaml_into_entry_data

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "button"]

SERVICE_SIMULATE_THERMAL_RESPONSE = "simulate_thermal_response"
SERVICE_ESTIMATE_PARAMETERS = "estimate_parameters"
SERVICE_ESTIMATE_PARAMETERS_ML = "estimate_parameters_ml"
SERVICE_REGENERATE_DASHBOARD = "regenerate_dashboard"
SERVICE_COMPUTE_LOGLIK_SLICE = "compute_loglik_slice"
SERVICE_RUN_SYSID_SIMULATION = "run_sysid_simulation"
SERVICE_APPLY_MANUAL_PARAMETERS = "apply_manual_parameters"
SERVICE_RESET_ESTIMATED_PARAMETERS = "reset_estimated_parameters"
# SERVICE_SET_SCHEDULE_ENABLED is imported from .const above

DEFAULT_DASHBOARD_FILENAME = "heating_assistant.yaml"
DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME = "heating_assistant_industrial.yaml"

# ---------------------------------------------------------------------------
# YAML schema
# ---------------------------------------------------------------------------

_WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WINDOW_AREA): vol.Coerce(float),
        vol.Required(CONF_WINDOW_ORIENTATION): vol.Coerce(float),
        vol.Optional(CONF_WINDOW_TILT, default=DEFAULT_WINDOW_TILT): vol.Coerce(float),
    }
)

_CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONNECTED_ROOM): str,
        vol.Required(CONF_R_VALUE): vol.Coerce(float),
    }
)

_SCHEDULE_PERIOD_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCHEDULE_NAME): str,
        vol.Required(CONF_SCHEDULE_START): str,
        vol.Required(CONF_SCHEDULE_END): str,
        vol.Optional(CONF_SCHEDULE_DAYS): [str],
        vol.Optional(CONF_SCHEDULE_SETPOINT): vol.Coerce(float),
        vol.Optional(CONF_SCHEDULE_MODE, default=SCHEDULE_MODE_COMFORT): vol.In(
            [SCHEDULE_MODE_COMFORT, SCHEDULE_MODE_OFF]
        ),
        vol.Optional(
            CONF_SCHEDULE_FROST_PROTECTION, default=DEFAULT_FROST_PROTECTION
        ): vol.Coerce(float),
    }
)

_ROOM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ROOM_NAME): str,
        vol.Optional(CONF_THERMAL_MASS, default=DEFAULT_THERMAL_MASS): vol.Coerce(float),
        vol.Optional(CONF_R_EXTERNAL, default=DEFAULT_R_EXTERNAL): vol.Coerce(float),
        # Sherman–Grimsrud infiltration share (Phase 1 C1).  See
        # const.ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION for typology
        # defaults exposed by the config flow.
        vol.Optional(
            CONF_INFILTRATION_FRACTION,
            default=DEFAULT_INFILTRATION_FRACTION,
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        # Slab / floor parameters (Phase 1 A2 + B1).
        # ``floor_type`` is the typology switch; the three numeric
        # fields below override the typology defaults from
        # const.FLOOR_TYPE_DEFAULTS when explicitly set.  Leave them
        # unset (or null) to use the typology defaults for the chosen
        # floor type.
        vol.Optional(CONF_FLOOR_TYPE, default=DEFAULT_FLOOR_TYPE): vol.In(
            list(FLOOR_TYPE_DEFAULTS)
        ),
        vol.Optional(CONF_C_SLAB_FRACTION): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0),
        ),
        vol.Optional(CONF_R_SA): vol.All(vol.Coerce(float), vol.Range(min=1e-9)),
        vol.Optional(CONF_R_SG): vol.All(vol.Coerce(float), vol.Range(min=1e-9)),
        # Phase 1 C3 / C4 / C5 — finishing-pass envelope corrections.
        # All default off (zero) so existing installs see no behaviour
        # change; opt in per room as desired.  ``facade_colour`` is a
        # convenience preset that resolves into ``facade_absorptance``
        # via ``FACADE_COLOUR_TO_ABSORPTANCE``; an explicit
        # ``facade_absorptance`` always wins.
        vol.Optional(
            CONF_SKY_RADIATIVE_UA, default=DEFAULT_SKY_RADIATIVE_UA,
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional(
            CONF_FACADE_COLOUR, default=DEFAULT_FACADE_COLOUR,
        ): vol.In(list(FACADE_COLOUR_TO_ABSORPTANCE)),
        vol.Optional(CONF_FACADE_ABSORPTANCE): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0),
        ),
        vol.Optional(
            CONF_FACADE_SOLAR_SHARE, default=DEFAULT_FACADE_SOLAR_SHARE,
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional(
            CONF_THERMAL_BRIDGE_PSI_L, default=DEFAULT_THERMAL_BRIDGE_PSI_L,
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
        vol.Optional(CONF_SOLAR_EXPOSURE, default=DEFAULT_SOLAR_EXPOSURE): vol.In(
            list(SOLAR_EXPOSURE_TO_APERTURE),
        ),
        vol.Optional(CONF_SOLAR_FACING, default=DEFAULT_SOLAR_FACING): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=360.0),
        ),
        vol.Optional(CONF_SETPOINT, default=DEFAULT_SETPOINT): vol.Coerce(float),
        vol.Optional(CONF_COMFORT_OFFSET, default=DEFAULT_COMFORT_OFFSET): vol.Coerce(float),
        vol.Optional(CONF_TEMP_SENSOR): str,
        vol.Optional(CONF_TEMP_SENSORS, default=[]): [str],
        vol.Optional(CONF_WINDOW_SENSORS, default=[]): [str],
        vol.Optional(CONF_CONNECTIONS, default=[]): [_CONNECTION_SCHEMA],
        vol.Optional(CONF_WINDOWS, default=[]): [_WINDOW_SCHEMA],
        vol.Optional(CONF_SCHEDULE, default=[]): [_SCHEDULE_PERIOD_SCHEMA],
    }
)

_SOURCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE_NAME): str,
        vol.Required(CONF_SOURCE_TYPE): vol.In(
            [SOURCE_TYPE_ELECTRIC, SOURCE_TYPE_HEAT_PUMP]
        ),
        vol.Required(CONF_SOURCE_ROOM): str,
        vol.Required(CONF_SOURCE_MAX_POWER): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_EFFICIENCY, default=DEFAULT_EFFICIENCY): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_COP_RATED, default=DEFAULT_COP_RATED): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_COP_TEMP_REF, default=DEFAULT_COP_TEMP_REF): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_MIN_POWER, default=DEFAULT_MIN_POWER): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_MAX_TEMP_OFFSET, default=DEFAULT_MAX_TEMP_OFFSET): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_TURN_OFF_DEADBAND, default=DEFAULT_TURN_OFF_DEADBAND): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_SOURCE_COOLING_COP, default=DEFAULT_COOLING_COP): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_SOURCE_COOLING_EFFICIENCY, default=DEFAULT_COOLING_EFFICIENCY): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        # Phase 1 B2 per-source emitter time constant.  When omitted the
        # coordinator picks the typology default from
        # ``SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU`` (electric → 0 s;
        # heat-pump → 60 s).  Users on hydronic radiators driven by
        # either source can override with τ ≈ 600 s here.
        vol.Optional(CONF_SOURCE_EMITTER_TIME_CONSTANT): vol.All(
            vol.Coerce(float), vol.Range(min=0.0),
        ),
        vol.Optional(CONF_SOURCE_HEATING_EFFICIENCY, default=DEFAULT_HEATING_EFFICIENCY): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(CONF_SOURCE_HEATER_ENTITY): str,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_ROOMS, default=[]): [_ROOM_SCHEMA],
                vol.Optional(CONF_HEAT_SOURCES, default=[]): [_SOURCE_SCHEMA],
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): str,
                vol.Optional(CONF_WEATHER_ENTITY): str,
                vol.Optional(CONF_SOLAR_RADIATION_ENTITY): str,
                vol.Optional(CONF_LATITUDE): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE): vol.Coerce(float),
                vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_HORIZON, default=DEFAULT_HORIZON): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=100)
                ),
                vol.Optional(
                    CONF_TRACKING_WEIGHT, default=DEFAULT_TRACKING_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_ENERGY_WEIGHT, default=DEFAULT_ENERGY_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_SMOOTHING_WEIGHT, default=DEFAULT_SMOOTHING_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_SOFT_CONSTRAINT_WEIGHT, default=DEFAULT_SOFT_CONSTRAINT_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_TERMINAL_WEIGHT, default=DEFAULT_TERMINAL_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0)),
                vol.Optional(CONF_SIGMA_W, default=DEFAULT_SIGMA_W): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6, max=10.0)
                ),
                vol.Optional(CONF_SIGMA_V, default=DEFAULT_SIGMA_V): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6, max=10.0)
                ),
                vol.Optional(CONF_SIGMA_B, default=DEFAULT_SIGMA_B): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-8, max=1.0)
                ),
                vol.Optional(
                    CONF_WINDOW_OPEN_DEBOUNCE,
                    default=DEFAULT_WINDOW_OPEN_DEBOUNCE,
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_WINDOW_OPEN_CLOSE_SETTLE,
                    default=DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_WINDOW_OPEN_Q_INFLATION,
                    default=DEFAULT_WINDOW_OPEN_Q_INFLATION,
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=1000.0)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    """Import YAML configuration into the integration's data store."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        hass.data[DOMAIN]["yaml_config"] = config[DOMAIN]

    async def _async_reload_service(call: ServiceCall) -> None:
        """Re-read configuration.yaml and reload all Heating Assistant entries."""
        new_conf = await async_integration_yaml_config(hass, DOMAIN)
        if new_conf is None:
            _LOGGER.warning(
                "Heating Assistant: configuration.yaml failed validation; "
                "keeping previous configuration"
            )
            return

        hass.data[DOMAIN]["yaml_config"] = new_conf.get(DOMAIN, {})

        entries = hass.config_entries.async_entries(DOMAIN)
        if entries:
            await asyncio.gather(
                *(hass.config_entries.async_reload(e.entry_id) for e in entries)
            )

    async_register_admin_service(hass, DOMAIN, SERVICE_RELOAD, _async_reload_service)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply options in-place when possible; reload only for structural changes."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(coordinator, HeatingAssistantCoordinator):
        merged_config = {**dict(entry.data), **dict(entry.options)}
        if coordinator.apply_runtime_reconfiguration(merged_config):
            return
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heating Assistant from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Merge YAML config (if present) into the entry data so the coordinator
    # can see room and heat-source definitions regardless of how they were set.
    entry_data = dict(entry.data)

    # Incorporate rooms/sources that were configured via the options flow
    # (stored in entry.options) into entry_data so the coordinator picks them
    # up.  YAML overrides both when it defines rooms/sources (see below).
    opts = entry.options
    if opts.get(CONF_ROOMS):
        entry_data[CONF_ROOMS] = opts[CONF_ROOMS]
    if opts.get(CONF_HEAT_SOURCES):
        entry_data[CONF_HEAT_SOURCES] = opts[CONF_HEAT_SOURCES]

    yaml_cfg = hass.data[DOMAIN].get("yaml_config", {})
    if yaml_cfg:
        entry_data = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    # Persist merged definitions so entities survive temporary YAML removal.
    new_rooms = entry_data.get(CONF_ROOMS) or []
    new_sources = entry_data.get(CONF_HEAT_SOURCES) or []
    stored_rooms = entry.data.get(CONF_ROOMS) or []
    stored_sources = entry.data.get(CONF_HEAT_SOURCES) or []
    if new_rooms != stored_rooms or new_sources != stored_sources:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **dict(entry.data),
                CONF_ROOMS: new_rooms,
                CONF_HEAT_SOURCES: new_sources,
            },
        )

    # Build a temporary entry-like object with merged data for the coordinator
    merged_entry = _MergedEntry(entry, entry_data)

    coordinator = HeatingAssistantCoordinator(hass, merged_entry)  # type: ignore[arg-type]

    # Restore runtime state stashed by a prior unload (in-memory only; survives
    # a reload but not a full HA restart). Only keys still present in the new
    # configuration are restored — rooms removed by the YAML edit drop their
    # state, which is the right outcome.
    reload_state = hass.data[DOMAIN].get("_reload_state", {}).pop(entry.entry_id, None)
    if reload_state is not None:
        coordinator._history_buffer.extend(reload_state.get("history_buffer", []))
        for room, value in reload_state.get("room_enabled", {}).items():
            if room in coordinator._room_enabled:
                coordinator._room_enabled[room] = value
        for room, value in reload_state.get("schedule_enabled", {}).items():
            if room in coordinator._schedule_enabled:
                coordinator._schedule_enabled[room] = value
        for room, value in reload_state.get("base_setpoint", {}).items():
            if room in coordinator._base_setpoint:
                coordinator._base_setpoint[room] = float(value)
                coordinator.model.rooms[room].setpoint = float(value)
    else:
        # No in-memory state means this is a full HA restart (not a reload).
        # Try to restore the history buffer from persistent storage so that
        # the parameter estimator does not have to wait for another 30+ steps.
        try:
            store = Store(
                hass,
                version=1,
                key=f"{DOMAIN}_history_{entry.entry_id}",
            )
            stored_history = await store.async_load()
            if stored_history and isinstance(stored_history, list):
                coordinator._history_buffer.extend(
                    stored_history[-HISTORY_BUFFER_SIZE:]
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

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        _LOGGER.debug(
            "Heating Assistant: initial data fetch failed. Setup continues and "
            "entities will populate after the next successful update",
            exc_info=True,
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register services (only once for the domain)
    if not hass.services.has_service(DOMAIN, SERVICE_SIMULATE_THERMAL_RESPONSE):
        _register_services(hass)

    # Auto-reload when the user changes options via the integration UI.
    # Attached after the coordinator is stored so the persist-merged
    # async_update_entry call above (which already short-circuits when nothing
    # changed) cannot fire the listener during setup.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Watch only the entities this integration needs (outdoor sensor, weather,
    # room temperature sensors) rather than waiting for EVENT_HOMEASSISTANT_STARTED
    # which blocks on all integrations — including unrelated slow ones.
    # Each listener fires a coordinator refresh as soon as its entity transitions
    # from unknown/unavailable to a valid state.
    cancel_startup = coordinator.setup_startup_listeners()
    if cancel_startup is not None:
        entry.async_on_unload(cancel_startup)

    # Schedule immediate + deadline-aligned refreshes for window open/close
    # events so debounce and settle timings are honoured independently of the
    # coordinator's update interval.
    cancel_window = coordinator.setup_window_listeners()
    if cancel_window is not None:
        entry.async_on_unload(cancel_window)

    # NOTE: Native Lovelace dashboards are kept in code but disabled.
    # The custom JS/CSS panel below is the primary dashboard.
    # written = await _async_auto_write_default_dashboard(hass, entry, coordinator)
    # if written:
    #     await _async_try_register_lovelace_dashboard(hass, written)
    # written_industrial = await _async_auto_write_industrial_dashboard(
    #     hass, entry, coordinator
    # )
    # if written_industrial:
    #     await _async_try_register_lovelace_dashboard(
    #         hass,
    #         written_industrial,
    #         url_path=DASHBOARD_INDUSTRIAL_URL_PATH,
    #         title="Heating Assistant Industrial",
    #         icon="mdi:factory",
    #     )

    # Register custom JS/CSS panel
    try:
        import pathlib

        from homeassistant.components.http import StaticPathConfig

        www_path = pathlib.Path(__file__).parent / "www"
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/ha-industrial-panel", str(www_path), cache_headers=False)]
        )

        from homeassistant.components.frontend import async_register_built_in_panel

        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Heating Assistant",
            sidebar_icon="mdi:radiator",
            frontend_url_path="ha-industrial",
            config={
                "_panel_custom": {
                    "name": "ha-industrial-panel",
                    "js_url": "/ha-industrial-panel/industrial-dashboard.js?v=5",
                    "embed_iframe": False,
                }
            },
            require_admin=False,
        )
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: custom panel registration skipped",
            exc_info=True,
        )

    return True


DASHBOARD_URL_PATH = "heating-assistant"
DASHBOARD_INDUSTRIAL_URL_PATH = "heating-assistant-industrial"


async def _async_auto_write_default_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> Optional[str]:
    """Write the default dashboard YAML on first setup (or after a format upgrade).

    Skipped when the per-entry marker says we've already auto-written at the
    current format version.  Bumping ``_DASHBOARD_FORMAT_VERSION`` below forces
    a one-time regeneration for all existing installs (e.g. after entity-id
    formula changes).  The file is always overwritten when a regeneration is
    triggered; users who have customised the file directly are expected to use
    the ``regenerate_dashboard`` service instead of relying on the auto-write.

    This is a best-effort convenience: any failure is logged at debug
    level and never propagated, so a missing ``hass.config`` (in tests) or
    a read-only config directory cannot break integration setup.
    """
    import os
    from datetime import datetime, timezone

    from .dashboard import build_dashboard_from_coordinator, dashboard_to_yaml

    # Bump this when the generated entity-id formula or card structure changes
    # in a way that makes old files incorrect.  Existing installs will have
    # their dashboard file overwritten once on the next HA restart.
    _DASHBOARD_FORMAT_VERSION = 5

    try:
        marker_store = Store(
            hass, version=1, key=f"{DOMAIN}_dashboard_marker_{entry.entry_id}"
        )
        marker = await marker_store.async_load()
        config_horizon = coordinator._horizon
        config_dt = coordinator.dt
        if (
            marker
            and marker.get("written_at")
            and marker.get("format_version", 1) >= _DASHBOARD_FORMAT_VERSION
            and marker.get("horizon") == config_horizon
            and marker.get("dt") == config_dt
        ):
            return marker.get("path") if marker.get("path") else None

        base_dir = hass.config.path("dashboards")
        target = os.path.join(base_dir, DEFAULT_DASHBOARD_FILENAME)
        if not marker and os.path.exists(target):
            await marker_store.async_save(
                {
                    "written_at": datetime.now(tz=timezone.utc).isoformat(),
                    "path": target,
                    "format_version": _DASHBOARD_FORMAT_VERSION,
                    "horizon": config_horizon,
                    "dt": config_dt,
                }
            )
            return target

        dashboard = build_dashboard_from_coordinator(coordinator)
        yaml_text = await hass.async_add_executor_job(dashboard_to_yaml, dashboard)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

        await marker_store.async_save(
            {
                "written_at": datetime.now(tz=timezone.utc).isoformat(),
                "path": target,
                "format_version": _DASHBOARD_FORMAT_VERSION,
                "horizon": config_horizon,
                "dt": config_dt,
            }
        )

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant dashboard available",
                "message": (
                    f"Heating Assistant wrote a starter Lovelace dashboard to "
                    f"`{target}`. To add it to the sidebar, open "
                    "**Settings → Dashboards → Add Dashboard → Show YAML "
                    "editor**, paste the file contents, and save. Re-run "
                    "`heating_assistant.regenerate_dashboard` after editing "
                    "rooms to refresh the file."
                ),
                "notification_id": f"{DOMAIN}_dashboard_first_install",
            },
            blocking=False,
        )
        return target
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: auto-write of starter dashboard skipped",
            exc_info=True,
        )
        return None


async def _async_auto_write_industrial_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> Optional[str]:
    """Write the industrial dashboard YAML as an additional dashboard."""
    import os
    from datetime import datetime, timezone

    from .dashboard import (
        DASHBOARD_VARIANT_INDUSTRIAL,
        build_dashboard_variant_from_coordinator,
        dashboard_to_yaml,
    )

    _DASHBOARD_FORMAT_VERSION = 1

    try:
        marker_store = Store(
            hass,
            version=1,
            key=f"{DOMAIN}_industrial_dashboard_marker_{entry.entry_id}",
        )
        marker = await marker_store.async_load()
        config_horizon = coordinator._horizon
        config_dt = coordinator.dt
        if (
            marker
            and marker.get("written_at")
            and marker.get("format_version", 1) >= _DASHBOARD_FORMAT_VERSION
            and marker.get("horizon") == config_horizon
            and marker.get("dt") == config_dt
        ):
            return marker.get("path") if marker.get("path") else None

        base_dir = hass.config.path("dashboards")
        target = os.path.join(base_dir, DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME)
        if not marker and os.path.exists(target):
            await marker_store.async_save(
                {
                    "written_at": datetime.now(tz=timezone.utc).isoformat(),
                    "path": target,
                    "format_version": _DASHBOARD_FORMAT_VERSION,
                    "horizon": config_horizon,
                    "dt": config_dt,
                }
            )
            return target

        dashboard = build_dashboard_variant_from_coordinator(
            coordinator,
            variant=DASHBOARD_VARIANT_INDUSTRIAL,
        )
        yaml_text = await hass.async_add_executor_job(dashboard_to_yaml, dashboard)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

        await marker_store.async_save(
            {
                "written_at": datetime.now(tz=timezone.utc).isoformat(),
                "path": target,
                "format_version": _DASHBOARD_FORMAT_VERSION,
                "horizon": config_horizon,
                "dt": config_dt,
            }
        )
        return target
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: auto-write of industrial dashboard skipped",
            exc_info=True,
        )
        return None


async def _async_try_register_lovelace_dashboard(
    hass: HomeAssistant,
    yaml_path: str,
    *,
    url_path: str = DASHBOARD_URL_PATH,
    title: str = "Heating Assistant",
    icon: str = "mdi:home-thermometer",
) -> None:
    """Best-effort registration of the YAML file as a Lovelace dashboard.

    Hooks into ``hass.data["lovelace"]`` to add a YAML-mode dashboard whose
    source is the file we just wrote, so the entry appears in the sidebar
    without the user having to paste anything. The whole block is wrapped
    in ``try``/``except`` because we depend on a semi-private HA surface
    that occasionally moves between releases. On any failure we leave the
    existing persistent notification as the fallback path.
    """
    import os

    try:
        from homeassistant.components.lovelace.dashboard import LovelaceYAML

        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None:
            return

        # ``LovelaceData`` (modern HA) exposes ``dashboards``; older
        # snapshots stored it as ``hass.data["lovelace"]["dashboards"]``.
        dashboards = getattr(lovelace_data, "dashboards", None)
        if dashboards is None and isinstance(lovelace_data, dict):
            dashboards = lovelace_data.get("dashboards")
        if dashboards is None:
            return
        if url_path in dashboards:
            return  # already registered (e.g. by the user)

        rel_filename = os.path.relpath(yaml_path, hass.config.path())
        config = {
            "mode": "yaml",
            "icon": icon,
            "title": title,
            "filename": rel_filename,
            "url_path": url_path,
            "show_in_sidebar": True,
            "require_admin": False,
        }
        dashboards[url_path] = LovelaceYAML(
            hass, url_path, config
        )

        try:
            from homeassistant.components import frontend

            frontend.async_register_built_in_panel(
                hass,
                component_name="lovelace",
                sidebar_title=config["title"],
                sidebar_icon=config["icon"],
                frontend_url_path=url_path,
                config={"mode": "yaml"},
                require_admin=False,
                update=False,
            )
        except Exception:
            _LOGGER.debug(
                "Heating Assistant: sidebar panel registration skipped",
                exc_info=True,
            )
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: Lovelace dashboard auto-registration skipped",
            exc_info=True,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN].get(entry.entry_id)
        if isinstance(coordinator, HeatingAssistantCoordinator):
            reload_state = hass.data[DOMAIN].setdefault("_reload_state", {})
            reload_state[entry.entry_id] = {
                "history_buffer": list(coordinator._history_buffer),
                "room_enabled": dict(coordinator._room_enabled),
                "schedule_enabled": dict(coordinator._schedule_enabled),
                "base_setpoint": dict(coordinator._base_setpoint),
            }
            # Persist the history buffer to HA's persistent storage so that it
            # survives a full Home Assistant restart (not just an in-memory reload).
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
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------

def _get_coordinator(hass: HomeAssistant) -> HeatingAssistantCoordinator:
    """Return the first available coordinator instance."""
    for entry_id, obj in hass.data.get(DOMAIN, {}).items():
        if isinstance(obj, HeatingAssistantCoordinator):
            return obj
    raise ValueError("No Heating Assistant coordinator found")


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services for setup assistance."""

    async def handle_simulate(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        result = coordinator.simulate_thermal_response(
            room_name=call.data["room_name"],
            initial_temp=call.data["initial_temp"],
            outdoor_temp=call.data["outdoor_temp"],
            heating_power=call.data["heating_power"],
            duration_hours=call.data["duration_hours"],
        )
        # Fire an event so automations or the UI can consume the result
        hass.bus.async_fire(
            f"{DOMAIN}_simulation_result",
            result,
        )
        # Also create a persistent notification for easy access
        if "error" in result:
            message = f"**Error:** {result['error']}"
        else:
            traj = result.get("trajectory", [])
            traj_lines = "\n".join(
                f"  {p['time_minutes']} min → {p['temperature']} °C"
                for p in traj[-10:]  # last 10 data points
            )
            message = (
                f"**Room:** {call.data['room_name']}\n"
                f"**Heating power:** {call.data['heating_power']} W\n"
                f"**Outdoor temp:** {call.data['outdoor_temp']} °C\n"
                f"**Start temp:** {call.data['initial_temp']} °C\n\n"
                f"**Final temperature:** {result['final_temperature']} °C\n"
                f"**Steady-state temperature:** {result['steady_state_temperature']} °C\n"
                f"**Time constant:** {result['time_constant_hours']} hours\n\n"
                f"**Trajectory (last points):**\n{traj_lines}"
            )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Thermal Response Simulation",
                "message": message,
                "notification_id": f"{DOMAIN}_simulation",
            },
            blocking=False,
        )

    async def handle_estimate(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        result = coordinator.estimate_parameters(
            room_name=call.data["room_name"],
            heating_power=call.data["heating_power"],
            outdoor_temp=call.data["outdoor_temp"],
            initial_temp=call.data["initial_temp"],
            final_temp=call.data["final_temp"],
            duration_seconds=call.data["duration_seconds"],
        )
        hass.bus.async_fire(
            f"{DOMAIN}_estimation_result",
            result,
        )
        if "error" in result:
            message = f"**Error:** {result['error']}"
        else:
            message = (
                f"**Room:** {call.data['room_name']}\n\n"
                f"**Estimated thermal_mass:** {result['estimated_thermal_mass']:,.0f} J/K\n"
                f"**Estimated r_external:** {result['estimated_r_external']} K/W\n\n"
                f"**Current thermal_mass:** {result['current_thermal_mass']:,.0f} J/K\n"
                f"**Current r_external:** {result['current_r_external']} K/W\n\n"
                f"**Notes:** {result['notes']}"
            )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Parameter Estimation",
                "message": message,
                "notification_id": f"{DOMAIN}_estimation",
            },
            blocking=False,
        )

    async def handle_estimate_ml(call: ServiceCall) -> None:
        """Run ML parameter estimation using the Kalman filter log-likelihood."""
        coordinator = _get_coordinator(hass)
        apply_params: bool = call.data.get("apply_parameters", False)
        result = await coordinator.async_estimate_parameters_ml(
            apply_params=apply_params,
        )

        # Update sysid_results so that the dashboard sensor entities reflect
        # the newly identified parameters immediately (without requiring a
        # separate sysid simulation run).
        if result.get("success"):
            for room_name, params in result.get("estimated_params", {}).items():
                existing = coordinator.sysid_results.get(room_name, {})
                existing["thermal_mass"] = params.get("thermal_mass")
                existing["r_external"] = params.get("r_external")
                coordinator.sysid_results[room_name] = existing
            coordinator.async_update_listeners()

        hass.bus.async_fire(
            f"{DOMAIN}_ml_estimation_result",
            {
                k: v
                for k, v in result.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
        )
        if not result["success"]:
            message = (
                f"**Status:** {result['message']}\n\n"
                f"**Steps in buffer:** {result['n_steps']}"
            )
        else:
            lines = []
            estimated_internal_gains = result.get("estimated_internal_gains", {})
            for room, params in result["estimated_params"].items():
                curr = result["current_params"][room]
                ig = estimated_internal_gains.get(room)
                ig_str = f"\n  internal\\_gain: {ig:+.1f} W" if ig is not None else ""
                lines.append(
                    f"**{room}**\n"
                    f"  thermal\\_mass: {params['thermal_mass']:,.0f} J/K "
                    f"(was {curr['thermal_mass']:,.0f})\n"
                    f"  r\\_external: {params['r_external']:.5f} K/W "
                    f"(was {curr['r_external']:.5f})"
                    f"{ig_str}"
                )
            identifiable_sources = result.get("identifiable_sources", [])
            heater_scales = result.get("estimated_heater_scales", {})
            if identifiable_sources:
                scale_lines = [
                    f"  {name}: {heater_scales[name]:.3f}×"
                    for name in identifiable_sources
                    if name in heater_scales
                ]
                if scale_lines:
                    lines.append("**Heater power-scale factors:**\n" + "\n".join(scale_lines))
            applied_str = (
                "Parameters applied and persisted (survive restart)."
                if apply_params
                else "Dry run – parameters NOT applied."
            )
            ll_str = (
                f"{result['log_likelihood']:.1f}"
                if result["log_likelihood"] is not None
                else "n/a"
            )
            message = (
                f"**{applied_str}**\n\n"
                + "\n\n".join(lines)
                + f"\n\n**Data steps used:** {result['n_steps']}\n"
                f"**Log-likelihood:** {ll_str}\n"
                f"**Status:** {result['message']}"
            )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – ML Parameter Estimation",
                "message": message,
                "notification_id": f"{DOMAIN}_ml_estimation",
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULATE_THERMAL_RESPONSE,
        handle_simulate,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("duration_hours"): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS,
        handle_estimate,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("final_temp"): vol.Coerce(float),
                vol.Required("duration_seconds"): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS_ML,
        handle_estimate_ml,
        schema=vol.Schema(
            {
                vol.Optional("apply_parameters", default=False): cv.boolean,
            }
        ),
    )

    async def handle_run_open_loop_simulation(call: ServiceCall) -> None:
        """Run open-loop simulation diagnostic and report RMSE per room."""
        from .model_diagnostics import compute_open_loop_predictions

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")
        segment_length = int(call.data.get("segment_length", 30))
        horizon_hours: Optional[float] = call.data.get("horizon_hours")

        history = list(coordinator.history_buffer)

        # Filter history to requested time horizon (timestamp-based).
        if horizon_hours is not None and history:
            last_ts = float(history[-1].get("timestamp", 0.0))
            cutoff_ts = last_ts - float(horizon_hours) * 3600.0
            history = [h for h in history if float(h.get("timestamp", 0.0)) >= cutoff_ts]

        system = coordinator.controller._system
        room_names = coordinator.model.room_names
        n_rooms = len(room_names)

        try:
            result = await hass.async_add_executor_job(
                compute_open_loop_predictions,
                history,
                system,
                room_names,
                n_rooms,
                float(UPDATE_INTERVAL),
                segment_length,
            )

            if "error" in result:
                message = f"**Error:** {result['error']}"
            else:
                per_room = result.get("per_room", {})

                # Write per-room results to coordinator cache so that
                # OpenLoopRMSESensor entities can read them without any
                # blocking computation on the event loop.
                coordinator.open_loop_results.update(per_room)
                coordinator.async_update_listeners()

                lines = [
                    f"**Segment length:** {result['segment_length']} steps "
                    f"({result['segment_length']} min)\n"
                    f"**Segments evaluated:** {result['n_segments']}\n"
                ]
                rooms_to_report = (
                    [room_name_filter]
                    if room_name_filter and room_name_filter in per_room
                    else list(per_room.keys())
                )
                for room in rooms_to_report:
                    data = per_room.get(room, {})
                    rmse = data.get("rmse")
                    mae = data.get("mae")
                    if rmse is None:
                        lines.append(f"**{room}:** no data")
                        continue
                    quality = (
                        "excellent" if rmse < 0.2
                        else "acceptable" if rmse < 0.5
                        else "poor – re-run parameter estimation"
                    )
                    lines.append(
                        f"**{room}**\n"
                        f"  Open-loop RMSE: {rmse:.3f} °C ({quality})\n"
                        f"  Open-loop MAE:  {mae:.3f} °C"
                    )

                message = "\n\n".join(lines)

        except Exception as exc:
            _LOGGER.error("Open-loop simulation failed: %s", exc, exc_info=True)
            message = f"**Error:** {exc}"

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Open-Loop Simulation",
                "message": message,
                "notification_id": f"{DOMAIN}_open_loop_sim",
            },
            blocking=False,
        )

    async def handle_analyze_model_fit(call: ServiceCall) -> None:
        """Analyze model fit quality for all or specific room."""
        from .model_diagnostics import generate_model_fit_report

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        # Build room parameters dict
        room_params = {}
        setpoints = {}
        for name, room in coordinator.model.rooms.items():
            room_params[name] = (room.thermal_mass, room.r_external)
            setpoints[name] = room.setpoint

        try:
            report = generate_model_fit_report(
                coordinator.history_buffer,
                coordinator.model.room_names,
                room_params,
                setpoints,
            )

            # Filter to specific room if requested
            if room_name_filter and room_name_filter in report.get("rooms", {}):
                filtered_rooms = {room_name_filter: report["rooms"][room_name_filter]}
                report["rooms"] = filtered_rooms

            # Format message
            if "error" in report:
                message = f"**Error:** {report['error']}"
            else:
                lines = [f"**Data steps analyzed:** {report['n_steps']}\n"]
                for room, data in report["rooms"].items():
                    if "error" in data:
                        lines.append(f"**{room}:** {data['error']}")
                        continue

                    fit = data.get("fit_metrics", {})
                    lines.append(
                        f"**{room}**\n"
                        f"  R² score: {fit.get('r_squared', 'n/a')}\n"
                        f"  RMSE: {fit.get('rmse', 'n/a')} °C\n"
                        f"  MAE: {fit.get('mae', 'n/a')} °C\n"
                        f"  Bias: {fit.get('bias', 'n/a')} °C\n"
                        f"  Max error: {fit.get('max_error', 'n/a')} °C\n"
                        f"  Autocorr (lag-1): {fit.get('residual_autocorr_lag1', 'n/a')}"
                    )

                message = "\n\n".join(lines)

            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Heating Assistant – Model Fit Analysis",
                    "message": message,
                    "notification_id": f"{DOMAIN}_model_fit",
                },
                blocking=False,
            )
        except Exception as exc:
            _LOGGER.error("Model fit analysis failed: %s", exc, exc_info=True)
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Heating Assistant – Model Fit Analysis",
                    "message": f"**Error:** {exc}",
                    "notification_id": f"{DOMAIN}_model_fit",
                },
                blocking=False,
            )

    async def handle_validate_parameters(call: ServiceCall) -> None:
        """Validate thermal parameters for all or specific room."""
        from .model_diagnostics import validate_parameters

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        rooms_to_check = (
            [room_name_filter]
            if room_name_filter and room_name_filter in coordinator.model.rooms
            else coordinator.model.room_names
        )

        lines = []
        for room_name in rooms_to_check:
            room = coordinator.model.rooms[room_name]
            try:
                validation = validate_parameters(
                    room_name, room.thermal_mass, room.r_external
                )

                status = "✓ Valid" if all([
                    validation.mass_valid,
                    validation.r_external_valid,
                    validation.time_constant_valid,
                ]) else "⚠ Warnings"

                lines.append(
                    f"**{room_name}** – {status}\n"
                    f"  Thermal mass: {validation.thermal_mass:,.0f} J/K "
                    f"({'✓' if validation.mass_valid else '⚠'})\n"
                    f"  R external: {validation.r_external:.5f} K/W "
                    f"({'✓' if validation.r_external_valid else '⚠'})\n"
                    f"  Time constant: {validation.time_constant_hours:.2f} hours "
                    f"({'✓' if validation.time_constant_valid else '⚠'})"
                )

                if validation.warnings:
                    lines.append("  **Warnings:**")
                    for warning in validation.warnings:
                        lines.append(f"    • {warning}")

            except Exception as exc:
                _LOGGER.error("Parameter validation failed for %s: %s", room_name, exc)
                lines.append(f"**{room_name}:** Error – {exc}")

        message = "\n\n".join(lines)

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Parameter Validation",
                "message": message,
                "notification_id": f"{DOMAIN}_param_validation",
            },
            blocking=False,
        )

    async def handle_controller_performance(call: ServiceCall) -> None:
        """Generate controller performance report for all or specific room."""
        from .model_diagnostics import compute_controller_performance

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        rooms_to_check = (
            [room_name_filter]
            if room_name_filter and room_name_filter in coordinator.model.rooms
            else coordinator.model.room_names
        )

        lines = []
        for room_name in rooms_to_check:
            room = coordinator.model.rooms[room_name]

            # Extract temperature history for this room
            room_idx = coordinator.model.room_names.index(room_name)
            temperatures = []
            for record in coordinator.history_buffer:
                y = record.get("y", [])
                if room_idx < len(y):
                    temperatures.append(y[room_idx])

            if len(temperatures) < 2:
                lines.append(f"**{room_name}:** Insufficient data")
                continue

            try:
                perf = compute_controller_performance(
                    temperatures, room.setpoint, room_name
                )

                lines.append(
                    f"**{room_name}** (setpoint: {room.setpoint} °C)\n"
                    f"  Mean tracking error: {perf.mean_tracking_error:+.3f} °C\n"
                    f"  Tracking error std: {perf.tracking_error_std:.3f} °C\n"
                    f"  Time above setpoint: {perf.time_above_setpoint * 100:.1f}%\n"
                    f"  Time below setpoint: {perf.time_below_setpoint * 100:.1f}%\n"
                    f"  Time in deadband (±0.5°C): {perf.time_in_deadband * 100:.1f}%\n"
                    f"  Max overshoot: {perf.max_overshoot:.2f} °C\n"
                    f"  Max undershoot: {perf.max_undershoot:.2f} °C\n"
                    f"  Samples: {perf.n_samples}"
                )

            except Exception as exc:
                _LOGGER.error("Controller performance analysis failed for %s: %s", room_name, exc)
                lines.append(f"**{room_name}:** Error – {exc}")

        message = "\n\n".join(lines)

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Controller Performance",
                "message": message,
                "notification_id": f"{DOMAIN}_controller_perf",
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN,
        "analyze_model_fit",
        handle_analyze_model_fit,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "validate_parameters",
        handle_validate_parameters,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "controller_performance_report",
        handle_controller_performance,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "run_open_loop_simulation",
        handle_run_open_loop_simulation,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("segment_length", default=30): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=120)
                ),
                vol.Optional("horizon_hours"): vol.Coerce(float),
            }
        ),
    )

    async def handle_run_sysid_simulation(call: ServiceCall) -> None:
        """Run system-identification open-loop simulation with configurable params."""
        from .sysid import run_sysid_simulation
        from .dashboard import slugify

        coordinator = _get_coordinator(hass)
        room_name_filter: Optional[str] = call.data.get("room_name")
        horizon_hours: float = float(call.data.get("horizon_hours", 6.0))
        sigma_w: float = float(call.data.get(
            "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
        ))
        sigma_v: float = float(call.data.get(
            "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
        ))

        dt = coordinator.dt  # seconds; handles runtime overrides correctly
        horizon_steps = max(1, int(horizon_hours * 3600.0 / dt))

        # Build per-room parameter overrides from service data
        room_params: Dict[str, Dict[str, float]] = {}
        for room_name in coordinator.model.room_names:
            overrides: Dict[str, float] = {}
            room_key = slugify(room_name)
            # Accept both the exact room name and a slug form as service data keys
            tm_key = f"thermal_mass_{room_key}"
            re_key = f"r_external_{room_key}"
            if tm_key in call.data:
                overrides["thermal_mass"] = float(call.data[tm_key])
            if re_key in call.data:
                overrides["r_external"] = float(call.data[re_key])
            if overrides:
                room_params[room_name] = overrides

        history = list(coordinator.history_buffer)

        try:
            result = await hass.async_add_executor_job(
                run_sysid_simulation,
                history,
                coordinator.model,
                coordinator.heat_sources,
                coordinator.model.room_names,
                dt,
                horizon_steps,
                room_params,
                sigma_w,
                sigma_v,
            )

            if "error" in result:
                message = f"**Error:** {result['error']}"
            else:
                per_room = result.get("per_room", {})

                # Tag each per-room result with the horizon so the sensor can
                # derive horizon_hours without knowing dt.
                for room_data in per_room.values():
                    room_data["horizon_steps"] = result.get("horizon_steps", horizon_steps)

                # Filter to requested room if specified
                if room_name_filter:
                    per_room = {
                        k: v for k, v in per_room.items()
                        if k == room_name_filter
                    }

                coordinator.sysid_results.update(per_room)
                coordinator.async_update_listeners()

                lines = [
                    f"**Horizon:** {horizon_hours} h "
                    f"({result.get('horizon_steps', horizon_steps)} steps)\n"
                    f"**σ_w:** {sigma_w}  **σ_v:** {sigma_v}"
                ]
                for room, data in per_room.items():
                    rmse = data.get("rmse")
                    mae = data.get("mae")
                    tm = data.get("thermal_mass")
                    re = data.get("r_external")
                    quality = (
                        "excellent" if rmse is not None and rmse < 0.2
                        else "acceptable" if rmse is not None and rmse < 0.5
                        else "poor"
                    )
                    lines.append(
                        f"**{room}**\n"
                        f"  thermal\\_mass: {tm:,.0f} J/K  r\\_external: {re:.5f} K/W\n"
                        f"  RMSE: {rmse:.3f} °C ({quality})  MAE: {mae:.3f} °C"
                        if rmse is not None and tm is not None and re is not None
                        else f"**{room}:** no data"
                    )

                message = "\n\n".join(lines)

        except Exception as exc:
            _LOGGER.error("SysID simulation failed: %s", exc, exc_info=True)
            message = f"**Error:** {exc}"

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – System Identification Simulation",
                "message": message,
                "notification_id": f"{DOMAIN}_sysid_simulation",
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_SYSID_SIMULATION,
        handle_run_sysid_simulation,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("horizon_hours", default=6.0): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5, max=72.0)
                ),
                vol.Optional("sigma_w"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
                vol.Optional("sigma_v"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
            },
            extra=vol.ALLOW_EXTRA,  # allow thermal_mass_<room>, r_external_<room> keys
        ),
    )

    async def handle_set_schedule_enabled(call: ServiceCall) -> None:
        """Suspend or resume the comfort schedule for one or more rooms."""
        coordinator = _get_coordinator(hass)
        enabled = bool(call.data["enabled"])
        room_name = call.data.get("room_name")
        if room_name:
            targets = [room_name]
        else:
            targets = list(coordinator.model.room_names)

        applied: list[str] = []
        skipped: list[str] = []
        for name in targets:
            if name not in coordinator.model.rooms:
                skipped.append(name)
                continue
            coordinator.set_schedule_enabled(name, enabled)
            applied.append(name)

        action = "resumed" if enabled else "suspended"
        lines = [f"**Schedules {action} for:** {', '.join(applied) if applied else '(none)'}"]
        if skipped:
            lines.append(f"**Unknown rooms ignored:** {', '.join(skipped)}")
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Comfort Schedule",
                "message": "\n".join(lines),
                "notification_id": f"{DOMAIN}_schedule_toggle",
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_ENABLED,
        handle_set_schedule_enabled,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Required("enabled"): cv.boolean,
            }
        ),
    )

    # ------------------------------------------------------------------
    # Runtime configuration services (called from the Heating Assistant UI)
    # ------------------------------------------------------------------

    _CONTROLLER_TUNING_KEYS = {
        CONF_TRACKING_WEIGHT, CONF_ENERGY_WEIGHT, CONF_ENERGY_PRICE_WEIGHT,
        CONF_SMOOTHING_WEIGHT, CONF_SOFT_CONSTRAINT_WEIGHT,
        CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT, CONF_TERMINAL_WEIGHT,
        CONF_HORIZON, CONF_UPDATE_INTERVAL, CONF_COMFORT_OFFSET,
    }

    _ESTIMATION_PARAM_KEYS = {
        CONF_SIGMA_W, CONF_SIGMA_V, CONF_SIGMA_B,
        CONF_WINDOW_OPEN_DEBOUNCE, CONF_WINDOW_OPEN_CLOSE_SETTLE,
        CONF_WINDOW_OPEN_Q_INFLATION,
    }

    async def handle_update_controller_tuning(call: ServiceCall) -> None:
        """Update MPC controller tuning parameters from the dashboard."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _CONTROLLER_TUNING_KEYS}
        if not updates:
            return
        entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
        if entry:
            new_data = {**dict(entry.data), **updates}
            hass.config_entries.async_update_entry(entry, data=new_data)
        coordinator.apply_tuning_updates(updates)

    hass.services.async_register(
        DOMAIN,
        "update_controller_tuning",
        handle_update_controller_tuning,
        schema=vol.Schema(
            {
                vol.Optional(CONF_TRACKING_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
                vol.Optional(CONF_ENERGY_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
                vol.Optional(CONF_ENERGY_PRICE_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10000.0)
                ),
                vol.Optional(CONF_SMOOTHING_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
                vol.Optional(CONF_SOFT_CONSTRAINT_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10000.0)
                ),
                vol.Optional(CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1000000.0)
                ),
                vol.Optional(CONF_TERMINAL_WEIGHT): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0, max=10000.0)
                ),
                vol.Optional(CONF_HORIZON): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=480)
                ),
                vol.Optional(CONF_UPDATE_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_COMFORT_OFFSET): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=5.0)
                ),
            }
        ),
    )

    async def handle_apply_manual_parameters(call: ServiceCall) -> None:
        """Apply manually tuned thermal parameters for a single room."""
        coordinator = _get_coordinator(hass)
        room_name: str = call.data["room_name"]
        thermal_mass: float = call.data["thermal_mass"]
        r_external: float = call.data["r_external"]
        coordinator.apply_manual_parameters(room_name, thermal_mass, r_external)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_MANUAL_PARAMETERS,
        handle_apply_manual_parameters,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("thermal_mass"): vol.All(
                    vol.Coerce(float), vol.Range(min=1000.0)
                ),
                vol.Required("r_external"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0001)
                ),
            }
        ),
    )

    async def handle_update_estimation_params(call: ServiceCall) -> None:
        """Update state estimation parameters from the dashboard."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _ESTIMATION_PARAM_KEYS}
        if not updates:
            return
        entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
        if entry:
            new_data = {**dict(entry.data), **updates}
            hass.config_entries.async_update_entry(entry, data=new_data)
        coordinator.apply_tuning_updates(updates)

    hass.services.async_register(
        DOMAIN,
        "update_estimation_params",
        handle_update_estimation_params,
        schema=vol.Schema(
            {
                vol.Optional(CONF_SIGMA_W): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6, max=10.0)
                ),
                vol.Optional(CONF_SIGMA_V): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6, max=10.0)
                ),
                vol.Optional(CONF_SIGMA_B): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-8, max=1.0)
                ),
                vol.Optional(CONF_WINDOW_OPEN_DEBOUNCE): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=3600)
                ),
                vol.Optional(CONF_WINDOW_OPEN_CLOSE_SETTLE): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=3600)
                ),
                vol.Optional(CONF_WINDOW_OPEN_Q_INFLATION): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0, max=1000.0)
                ),
            }
        ),
    )

    async def handle_reset_estimated_parameters(call: ServiceCall) -> None:
        """Reset the active model back to configured (YAML) default parameters."""
        coordinator = _get_coordinator(hass)
        coordinator.reset_estimated_parameters()
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ESTIMATED_PARAMETERS,
        handle_reset_estimated_parameters,
        schema=vol.Schema({}),
    )

    # ------------------------------------------------------------------
    # Parameter history services
    # ------------------------------------------------------------------

    async def handle_store_identified_parameters(call: ServiceCall) -> None:
        """Store identified parameters with history tracking."""
        coordinator = _get_coordinator(hass)
        room_name: str = call.data["room_name"]
        thermal_mass: float = call.data["thermal_mass"]
        r_external: float = call.data["r_external"]
        source: str = call.data.get("source", "manual")
        rmse: Optional[float] = call.data.get("rmse")
        coordinator.store_identified_parameters(
            room_name, thermal_mass, r_external, source=source, rmse=rmse
        )
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "store_identified_parameters",
        handle_store_identified_parameters,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("thermal_mass"): vol.All(
                    vol.Coerce(float), vol.Range(min=1000.0)
                ),
                vol.Required("r_external"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0001)
                ),
                vol.Optional("source", default="manual"): vol.In(["ml", "manual"]),
                vol.Optional("rmse"): vol.Coerce(float),
            }
        ),
    )

    async def handle_revert_parameters(call: ServiceCall) -> None:
        """Revert parameters to a previous history entry."""
        coordinator = _get_coordinator(hass)
        room_name: str = call.data["room_name"]
        history_index: int = call.data["history_index"]
        coordinator.revert_parameters(room_name, history_index)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "revert_parameters",
        handle_revert_parameters,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("history_index"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=9)
                ),
            }
        ),
    )

    async def handle_update_room_schedule(call: ServiceCall) -> None:
        """Update the schedule for a single room and persist to config entry."""
        from .dashboard import slugify as _slugify

        coordinator = _get_coordinator(hass)
        room_name: str = call.data["room_name"]
        periods: list = call.data["periods"]

        # Resolve the canonical room name from the slug sent by the frontend.
        entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
        if entry is None:
            raise ValueError("Config entry not found")

        rooms_list = entry.data.get(CONF_ROOMS) or []
        canonical_name: str | None = None
        for room_cfg in rooms_list:
            cfg_name = room_cfg.get(CONF_ROOM_NAME, "")
            if cfg_name == room_name or _slugify(cfg_name) == room_name:
                canonical_name = cfg_name
                break

        if canonical_name is None:
            raise ValueError(f"Room '{room_name}' not found in configuration")

        # Persist schedules in a dedicated key that won't be overwritten by
        # YAML/options merging during integration reload (same pattern as
        # CONF_PERSISTED_SETPOINTS).
        persisted: dict = dict(entry.data.get(CONF_PERSISTED_SCHEDULES) or {})
        persisted[canonical_name] = periods

        hass.config_entries.async_update_entry(
            entry,
            data={**dict(entry.data), CONF_PERSISTED_SCHEDULES: persisted},
        )

        # Rebuild schedule in coordinator
        coordinator.reload_room_schedule(canonical_name, periods)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_room_schedule",
        handle_update_room_schedule,
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
                        }
                    )
                ],
            }
        ),
    )

    async def handle_regenerate_dashboard(call: ServiceCall) -> ServiceResponse:
        """Regenerate the Heating Assistant Lovelace dashboard.

        When ``dry_run`` is true the generated YAML is returned in the
        service response without touching the filesystem. Otherwise it is
        written to ``<config>/dashboards/<filename>`` (relative paths
        outside the config directory are rejected).
        """
        import os

        from .dashboard import (
            DASHBOARD_VARIANT_CLASSIC,
            DASHBOARD_VARIANT_INDUSTRIAL,
            build_dashboard_variant_from_coordinator,
            dashboard_to_yaml,
        )

        coordinator = _get_coordinator(hass)
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

            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Heating Assistant dashboard regenerated",
                    "message": (
                        f"Wrote {variant} dashboard YAML to `{write_path}`. "
                        "Add a `lovelace.dashboards` entry referencing this file "
                        "or paste the contents into a new dashboard via "
                        "Settings → Dashboards."
                    ),
                    "notification_id": f"{DOMAIN}_dashboard_regenerated",
                },
                blocking=False,
            )

        return {
            "yaml": yaml_text,
            "variant": variant,
            "rooms": [v["title"] for v in dashboard["views"] if v.get("subview")],
            "written_to": write_path,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_REGENERATE_DASHBOARD,
        handle_regenerate_dashboard,
        schema=vol.Schema(
            {
                vol.Optional("dry_run", default=False): cv.boolean,
                vol.Optional("filename"): cv.string,
                vol.Optional("variant"): vol.In(["classic", "industrial"]),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def handle_compute_loglik_slice(call: ServiceCall) -> dict:
        """Compute a 2-D log-likelihood slice for a room.

        The slice is stored on the coordinator and exposed via the
        per-room ``…_loglik_slice`` sensor so dashboards can visualise it
        without re-running the computation.  A persistent notification
        reports the peak log-likelihood so Lovelace button presses give
        immediate feedback.

        The service intentionally has no ``supports_response`` registration
        so Lovelace button cards can fire it without the frontend requiring
        ``return_response=true``.  The full grid is always available via
        ``state_attr('sensor.heating_assistant_<room>_loglik_slice',
        'log_likelihood')``.
        """
        coordinator = _get_coordinator(hass)
        room_name = call.data["room_name"]
        n_grid = int(call.data.get("n_grid", 11))
        span_log = float(call.data.get("span_log", 1.0))

        result = await coordinator.async_compute_loglik_slice(
            room_name, n_grid=n_grid, span_log=span_log
        )
        if result is None:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Heating Assistant – log-likelihood slice",
                    "message": (
                        f"Could not compute the slice for `{room_name}`. The "
                        "history buffer may be too short, or the room is not "
                        "configured."
                    ),
                    "notification_id": f"{DOMAIN}_loglik_slice_{room_name}",
                },
                blocking=False,
            )
            return {
                "room": room_name,
                "error": "history_too_short_or_unknown_room",
            }

        # Surface a compact summary so a dashboard call gives the user
        # immediate feedback even when they didn't ask for the full grid.
        grid = result.get("log_likelihood") or []
        flat = [v for row in grid for v in row if isinstance(v, (int, float))]
        peak = max(flat) if flat else None
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – log-likelihood slice",
                "message": (
                    f"Computed {n_grid}×{n_grid} (log C, log R_ext) grid for "
                    f"**{room_name}** (span ±{span_log} log-units).\n\n"
                    f"Peak log-likelihood: "
                    f"{'%.2f' % peak if peak is not None else 'n/a'}\n\n"
                    "Use `sensor.heating_assistant_"
                    f"{room_name.lower().replace(' ', '_')}_loglik_slice` "
                    "for the full grid, or call this service from Developer "
                    "Tools with `return_response: true`."
                ),
                "notification_id": f"{DOMAIN}_loglik_slice_{room_name}",
            },
            blocking=False,
        )
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPUTE_LOGLIK_SLICE,
        handle_compute_loglik_slice,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Optional("n_grid", default=11): vol.All(
                    vol.Coerce(int), vol.Range(min=3, max=41)
                ),
                vol.Optional("span_log", default=1.0): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=4.0)
                ),
            }
        ),
    )
