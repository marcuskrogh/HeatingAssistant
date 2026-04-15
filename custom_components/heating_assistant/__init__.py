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
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_DT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_R_EXTERNAL,
    CONF_R_VALUE,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SETPOINT,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TYPE,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_THERMAL_MASS,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_DT,
    DEFAULT_EFFICIENCY,
    DEFAULT_HORIZON,
    DEFAULT_MIN_POWER,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_THERMAL_MASS,
    DEFAULT_WINDOW_TILT,
    DOMAIN,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
    UPDATE_INTERVAL,
)
from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor"]

SERVICE_SIMULATE_THERMAL_RESPONSE = "simulate_thermal_response"
SERVICE_ESTIMATE_PARAMETERS = "estimate_parameters"

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

_ROOM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ROOM_NAME): str,
        vol.Optional(CONF_THERMAL_MASS, default=DEFAULT_THERMAL_MASS): vol.Coerce(float),
        vol.Optional(CONF_R_EXTERNAL, default=DEFAULT_R_EXTERNAL): vol.Coerce(float),
        vol.Optional(CONF_SETPOINT, default=DEFAULT_SETPOINT): vol.Coerce(float),
        vol.Optional(CONF_TEMP_SENSOR): str,
        vol.Optional(CONF_TEMP_SENSORS, default=[]): [str],
        vol.Optional(CONF_CONNECTIONS, default=[]): [_CONNECTION_SCHEMA],
        vol.Optional(CONF_WINDOWS, default=[]): [_WINDOW_SCHEMA],
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
                vol.Optional(CONF_LATITUDE): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE): vol.Coerce(float),
                vol.Optional(CONF_DT, default=DEFAULT_DT): vol.Coerce(int),
                vol.Optional(CONF_HORIZON, default=DEFAULT_HORIZON): vol.Coerce(int),
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

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heating Assistant from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Merge YAML config (if present) into the entry data so the coordinator
    # can see room and heat-source definitions regardless of how they were set.
    entry_data = dict(entry.data)
    yaml_cfg = hass.data[DOMAIN].get("yaml_config", {})
    if yaml_cfg:
        entry_data.setdefault(CONF_ROOMS, yaml_cfg.get(CONF_ROOMS, []))
        entry_data.setdefault(CONF_HEAT_SOURCES, yaml_cfg.get(CONF_HEAT_SOURCES, []))
        # Use YAML outdoor entity if the config entry value is empty/missing.
        # setdefault would not overwrite the empty-string default from the
        # config-flow, so we need an explicit check here.
        if not entry_data.get(CONF_OUTDOOR_TEMP_ENTITY):
            entry_data[CONF_OUTDOOR_TEMP_ENTITY] = yaml_cfg.get(
                CONF_OUTDOOR_TEMP_ENTITY, ""
            )
        if not entry_data.get(CONF_WEATHER_ENTITY):
            entry_data[CONF_WEATHER_ENTITY] = yaml_cfg.get(
                CONF_WEATHER_ENTITY, ""
            )
        entry_data.setdefault(CONF_DT, yaml_cfg.get(CONF_DT, DEFAULT_DT))
        entry_data.setdefault(CONF_HORIZON, yaml_cfg.get(CONF_HORIZON, DEFAULT_HORIZON))
        if CONF_LATITUDE not in entry_data and CONF_LATITUDE in yaml_cfg:
            entry_data[CONF_LATITUDE] = yaml_cfg[CONF_LATITUDE]
        if CONF_LONGITUDE not in entry_data and CONF_LONGITUDE in yaml_cfg:
            entry_data[CONF_LONGITUDE] = yaml_cfg[CONF_LONGITUDE]

    # Build a temporary entry-like object with merged data for the coordinator
    merged_entry = _MergedEntry(entry, entry_data)

    coordinator = HeatingAssistantCoordinator(hass, merged_entry)  # type: ignore[arg-type]
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register services (only once for the domain)
    if not hass.services.has_service(DOMAIN, SERVICE_SIMULATE_THERMAL_RESPONSE):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


class _MergedEntry:
    """Thin wrapper that presents merged entry data to the coordinator."""

    def __init__(self, entry: ConfigEntry, data: Dict[str, Any]) -> None:
        self._entry = entry
        self.data = data
        self.options = entry.options
        self.entry_id = entry.entry_id
        self.title = entry.title


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
