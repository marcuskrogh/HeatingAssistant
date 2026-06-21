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
import time
from datetime import timedelta
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
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
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    CONF_PERSISTED_SCHEDULES,
    CONF_SETPOINT,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_IDENTIFICATION_HORIZON_HOURS,
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
    DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
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
    EXCITATION_TYPES,
    HISTORY_BUFFER_SIZE,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SERVICE_CANCEL_EXPERIMENT,
    SERVICE_CREATE_DATASET,
    SERVICE_DELETE_DATASET,
    SERVICE_DELETE_EXPERIMENT,
    SERVICE_SCHEDULE_EXPERIMENT,
    SERVICE_SET_SCHEDULE_ENABLED,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_ELECTRIC_FLOOR,
    SOURCE_TYPE_GAS_HEATER,
    SOURCE_TYPE_GENERIC_THERMOSTAT,
    SOURCE_TYPE_HEAT_PUMP,
    SOURCE_TYPE_HYDRONIC_FLOOR,
    SOURCE_TYPE_HYDRONIC_RADIATOR,
    SOURCE_TYPE_OIL_RADIATOR,
    UPDATE_INTERVAL,
    UI_REFRESH_INTERVAL,
)
from .const import (
    CONF_ENVELOPE_TIGHTNESS,
    CONF_ESTIMATED_PARAMS,
    CONF_GROUND_ALBEDO,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SETPOINTS,
    CONF_PRICE_ENTITY,
    CONF_PRICE_NET_TARIFF,
    CONF_PRICE_SPOT_SURCHARGE,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PLOT_FORECAST_HOURS,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    DEFAULT_IDENTIFICATION_HISTORY_DAYS,
    CONF_SOURCE_HVAC_MODE,
    DEFAULT_ENVELOPE_TIGHTNESS,
    DEFAULT_PLOT_HISTORY_HOURS,
    DEFAULT_PLOT_FORECAST_HOURS,
    DEFAULT_SOURCE_HVAC_MODE,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    SOURCE_HVAC_MODE_COOL,
    SOURCE_HVAC_MODE_HEAT,
    SOURCE_HVAC_MODE_HEAT_COOL,
)
from .coordinator import HeatingAssistantCoordinator
from .yaml_merge import MergedEntry as _MergedEntry, merge_yaml_into_entry_data as _merge_yaml_into_entry_data

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "button", "datetime"]

SERVICE_SIMULATE_THERMAL_RESPONSE = "simulate_thermal_response"
SERVICE_ESTIMATE_PARAMETERS = "estimate_parameters"
SERVICE_ESTIMATE_PARAMETERS_ML = "estimate_parameters_ml"
SERVICE_REGENERATE_DASHBOARD = "regenerate_dashboard"
SERVICE_COMPUTE_LOGLIK_SLICE = "compute_loglik_slice"
SERVICE_RUN_SYSID_SIMULATION = "run_sysid_simulation"
SERVICE_APPLY_MANUAL_PARAMETERS = "apply_manual_parameters"
SERVICE_RESET_ESTIMATED_PARAMETERS = "reset_estimated_parameters"
SERVICE_APPLY_HEATER_SCALES = "apply_heater_scales"
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
        # Per-period overrides written by the schedule editor.  Optional so a
        # period round-tripped through update_rooms (which carries the room's
        # full schedule) validates instead of being rejected as an extra key.
        vol.Optional(CONF_SCHEDULE_COMFORT_OFFSET): vol.Any(None, vol.Coerce(float)),
        vol.Optional(CONF_SCHEDULE_TRACKING_WEIGHT): vol.Any(None, vol.Coerce(float)),
        vol.Optional(CONF_SCHEDULE_ENERGY_WEIGHT): vol.Any(None, vol.Coerce(float)),
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
            [
                SOURCE_TYPE_ELECTRIC,
                SOURCE_TYPE_ELECTRIC_FLOOR,
                SOURCE_TYPE_GAS_HEATER,
                SOURCE_TYPE_GENERIC_THERMOSTAT,
                SOURCE_TYPE_HEAT_PUMP,
                SOURCE_TYPE_HYDRONIC_FLOOR,
                SOURCE_TYPE_HYDRONIC_RADIATOR,
                SOURCE_TYPE_OIL_RADIATOR,
            ]
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
                vol.Optional(
                    CONF_PLOT_HISTORY_HOURS,
                    default=DEFAULT_PLOT_HISTORY_HOURS,
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=168.0)),
                vol.Optional(
                    CONF_PLOT_FORECAST_HOURS,
                    default=DEFAULT_PLOT_FORECAST_HOURS,
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=168.0)),
                vol.Optional(
                    CONF_IDENTIFICATION_HISTORY_DAYS,
                    default=DEFAULT_IDENTIFICATION_HISTORY_DAYS,
                ): vol.All(vol.Coerce(int), vol.Range(min=7, max=365)),
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


def _register_websocket_api(hass: HomeAssistant) -> None:
    """Register WebSocket commands for the dashboard frontend."""
    from homeassistant.components import websocket_api

    from .dashboard import slugify as _slugify

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_schedules"}
    )
    @websocket_api.async_response
    async def ws_get_schedules(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return current schedule data directly from the coordinator."""
        coordinator = _get_coordinator(hass)
        schedules: dict = {}
        for room_name, room_schedule in coordinator._room_schedule.items():
            if room_schedule and not room_schedule.is_empty:
                schedules[_slugify(room_name)] = {
                    "enabled": coordinator._schedule_enabled.get(room_name, True),
                    "periods": [
                        {
                            "name": p.name,
                            "start": p.start.strftime("%H:%M"),
                            "end": p.end.strftime("%H:%M"),
                            "mode": p.mode,
                            "setpoint": p.setpoint,
                            "frost_protection": p.frost_protection,
                            "days": sorted(p.days),
                            "comfort_offset": p.comfort_offset,
                            "tracking_weight": p.tracking_weight,
                            "energy_weight": p.energy_weight,
                        }
                        for p in room_schedule.periods
                    ],
                }
        connection.send_result(msg["id"], {"room_schedules": schedules})

    websocket_api.async_register_command(hass, ws_get_schedules)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_controller_config"}
    )
    @websocket_api.async_response
    async def ws_get_controller_config(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return current controller tuning parameters directly from the coordinator."""
        try:
            c = _get_coordinator(hass)
            # float()/int() casts required: coordinator attrs may be numpy scalars,
            # which HA's JSON serialiser cannot handle.
            # update_interval: DataUpdateCoordinator stores self._update_interval as
            # a timedelta (its property setter overwrites our int), so read the public
            # property and convert via total_seconds().
            ui = c.update_interval
            config = {
                "comfort_offset": float(next(
                    iter(getattr(c, "_room_comfort_offset", {}).values()), 2.0
                )),
                "tracking_weight": float(getattr(c, "_tracking_weight", DEFAULT_TRACKING_WEIGHT)),
                "energy_weight": float(getattr(c, "_energy_weight", DEFAULT_ENERGY_WEIGHT)),
                "energy_price_weight": float(getattr(c, "_energy_price_weight", DEFAULT_ENERGY_PRICE_WEIGHT)),
                "smoothing_weight": float(getattr(c, "_smoothing_weight", DEFAULT_SMOOTHING_WEIGHT)),
                "soft_constraint_weight": float(getattr(c, "_soft_constraint_weight", DEFAULT_SOFT_CONSTRAINT_WEIGHT)),
                "soft_constraint_linear_weight": float(getattr(c, "_soft_constraint_linear_weight", DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT)),
                "terminal_weight": float(getattr(c, "_terminal_weight", DEFAULT_TERMINAL_WEIGHT)),
                "horizon": int(getattr(c, "_horizon", DEFAULT_HORIZON)),
                "update_interval": int(ui.total_seconds() if hasattr(ui, "total_seconds") else ui),
                "window_open_debounce": int(getattr(c, "_window_open_debounce", DEFAULT_WINDOW_OPEN_DEBOUNCE)),
                "window_open_close_settle": int(getattr(c, "_window_open_close_settle", DEFAULT_WINDOW_OPEN_CLOSE_SETTLE)),
                "window_open_q_inflation": float(getattr(c, "_window_open_q_inflation", DEFAULT_WINDOW_OPEN_Q_INFLATION)),
            }
            _LOGGER.debug("Heating Assistant: get_controller_config -> %s", config)
            connection.send_result(msg["id"], {"config": config})
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_controller_config WS failed: %s", err)
            connection.send_error(msg["id"], "config_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_controller_config)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "heating_assistant/get_forecasts",
            # Optional display horizon (hours) requested by the dashboard.  When
            # absent or 0 the full controller horizon is used; when larger than
            # the controller horizon the final actuation is held flat and the
            # trajectory is simulated forward (see build_forecast_payload).
            vol.Optional("plot_forecast_hours"): vol.Coerce(float),
        }
    )
    @websocket_api.async_response
    async def ws_get_forecasts(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return current forecast arrays directly from the coordinator.

        Forecast data is no longer stored in sensor attributes (to avoid the
        HA Recorder 16 KB size limit), so the dashboard frontend fetches it
        via this endpoint instead.
        """
        try:
            coordinator = _get_coordinator(hass)
            plot_hours = msg.get("plot_forecast_hours")
            plot_steps: Optional[int] = None
            if plot_hours is not None and float(plot_hours) > 0:
                dt = coordinator.dt or DEFAULT_UPDATE_INTERVAL
                import math
                plot_steps = max(1, math.ceil(float(plot_hours) * 3600.0 / float(dt)))
            payload = coordinator.build_forecast_payload(
                plot_forecast_steps=plot_steps
            )
            connection.send_result(msg["id"], payload)
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_forecasts WS failed: %s", err)
            connection.send_error(msg["id"], "forecasts_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_forecasts)

    _PREVIEW_TUNING_SCHEMA = {
        vol.Required("type"): "heating_assistant/preview_tuning_forecast",
        vol.Optional("plot_forecast_hours"): vol.Coerce(float),
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
    _PREVIEW_TUNING_KEYS = {
        CONF_TRACKING_WEIGHT, CONF_ENERGY_WEIGHT, CONF_ENERGY_PRICE_WEIGHT,
        CONF_SMOOTHING_WEIGHT, CONF_SOFT_CONSTRAINT_WEIGHT,
        CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT, CONF_TERMINAL_WEIGHT,
        CONF_HORIZON, CONF_UPDATE_INTERVAL, CONF_COMFORT_OFFSET,
    }

    @websocket_api.websocket_command(_PREVIEW_TUNING_SCHEMA)
    @websocket_api.async_response
    async def ws_preview_tuning_forecast(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Run a one-off MPC solve with proposed tuning and return forecast plots."""
        import math
        from datetime import datetime, timezone

        try:
            coordinator = _get_coordinator(hass)
            tuning = {
                k: msg[k] for k in _PREVIEW_TUNING_KEYS if k in msg
            }
            plot_hours = msg.get("plot_forecast_hours")
            plot_steps: Optional[int] = None
            if plot_hours is not None and float(plot_hours) > 0:
                preview_dt = float(
                    tuning.get(CONF_UPDATE_INTERVAL, coordinator._update_interval_s)
                )
                plot_steps = max(
                    1, math.ceil(float(plot_hours) * 3600.0 / preview_dt)
                )

            now = getattr(coordinator, "now_utc", None) or datetime.now(
                tz=timezone.utc
            )
            cloud_cover_raw = coordinator._read_cloud_cover_now()
            cloud_cover_now = coordinator._smooth_cloud_cover(cloud_cover_raw)
            cloud_forecast = await coordinator._async_read_cloud_forecast(
                cloud_cover_now=cloud_cover_now
            )
            wind_forecast = await coordinator._async_read_wind_forecast(
                coordinator._read_wind_speed_now()
            )
            ghi_now, ghi_forecast = coordinator._read_ghi(now)
            if cloud_cover_now is None and cloud_forecast:
                cloud_cover_now = max(0.0, min(1.0, float(cloud_forecast[0])))

            weather = {
                "cloud_forecast": cloud_forecast,
                "cloud_cover_now": cloud_cover_now,
                "ghi_now": ghi_now,
                "ghi_forecast": ghi_forecast,
                "wind_forecast": wind_forecast,
            }

            payload = await hass.async_add_executor_job(
                coordinator.preview_tuning_forecast,
                tuning,
                plot_steps,
                weather,
            )
            connection.send_result(msg["id"], payload)
        except Exception as err:
            _LOGGER.error(
                "Heating Assistant: preview_tuning_forecast WS failed: %s", err
            )
            connection.send_error(
                msg["id"], "preview_tuning_failed", str(err)
            )

    websocket_api.async_register_command(hass, ws_preview_tuning_forecast)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_ui_settings"}
    )
    @websocket_api.async_response
    async def ws_get_ui_settings(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return the industrial-panel display settings (plot windows)."""
        try:
            c = _get_coordinator(hass)
            connection.send_result(
                msg["id"],
                {
                    "ui_settings": {
                        CONF_PLOT_HISTORY_HOURS: float(
                            getattr(c, "_plot_history_hours", DEFAULT_PLOT_HISTORY_HOURS)
                        ),
                        CONF_PLOT_FORECAST_HOURS: float(
                            getattr(c, "_plot_forecast_hours", DEFAULT_PLOT_FORECAST_HOURS)
                        ),
                    }
                },
            )
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_ui_settings WS failed: %s", err)
            connection.send_error(msg["id"], "ui_settings_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_ui_settings)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_model_config"}
    )
    @websocket_api.async_response
    async def ws_get_model_config(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return the full editable model configuration for the Configuration UI.

        Reads the live config entry (options shadowing data, matching the
        coordinator's precedence) so the dashboard always edits the values that
        are actually in force.  Returns the rooms list, heat-source list, the
        environment/system entities, the display settings, and the enum choices
        used to populate dropdowns.
        """
        try:
            c = _get_coordinator(hass)
            entry = hass.config_entries.async_get_entry(c._entry.entry_id)
            data = dict(entry.data) if entry else {}
            options = dict(entry.options) if entry else {}

            def _merged(key, default=None):
                if key in options:
                    return options[key]
                return data.get(key, default)

            rooms = _merged(CONF_ROOMS, []) or []
            sources = _merged(CONF_HEAT_SOURCES, []) or []

            system = {
                CONF_OUTDOOR_TEMP_ENTITY: _merged(CONF_OUTDOOR_TEMP_ENTITY) or "",
                CONF_WEATHER_ENTITY: _merged(CONF_WEATHER_ENTITY) or "",
                CONF_SOLAR_RADIATION_ENTITY: _merged(CONF_SOLAR_RADIATION_ENTITY) or "",
                CONF_PRICE_ENTITY: _merged(CONF_PRICE_ENTITY) or "",
                CONF_LATITUDE: _merged(CONF_LATITUDE, hass.config.latitude),
                CONF_LONGITUDE: _merged(CONF_LONGITUDE, hass.config.longitude),
            }

            ui = {
                CONF_PLOT_HISTORY_HOURS: float(
                    _merged(CONF_PLOT_HISTORY_HOURS, DEFAULT_PLOT_HISTORY_HOURS)
                ),
                CONF_PLOT_FORECAST_HOURS: float(
                    _merged(CONF_PLOT_FORECAST_HOURS, DEFAULT_PLOT_FORECAST_HOURS)
                ),
            }

            system_params = {
                CONF_IDENTIFICATION_HISTORY_DAYS: int(
                    _merged(CONF_IDENTIFICATION_HISTORY_DAYS, DEFAULT_IDENTIFICATION_HISTORY_DAYS)
                ),
            }

            enums = {
                "floor_types": list(FLOOR_TYPE_DEFAULTS.keys()),
                "facade_colours": list(FACADE_COLOUR_TO_ABSORPTANCE.keys()),
                "solar_exposures": list(SOLAR_EXPOSURE_TO_APERTURE.keys()),
                "envelope_tightness": list(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION.keys()
                ),
                "envelope_tightness_map": dict(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION
                ),
                "source_types": [
                    SOURCE_TYPE_ELECTRIC,
                    SOURCE_TYPE_ELECTRIC_FLOOR,
                    SOURCE_TYPE_GAS_HEATER,
                    SOURCE_TYPE_GENERIC_THERMOSTAT,
                    SOURCE_TYPE_HEAT_PUMP,
                    SOURCE_TYPE_HYDRONIC_FLOOR,
                    SOURCE_TYPE_HYDRONIC_RADIATOR,
                    SOURCE_TYPE_OIL_RADIATOR,
                ],
                "hvac_modes": [
                    SOURCE_HVAC_MODE_HEAT,
                    SOURCE_HVAC_MODE_COOL,
                    SOURCE_HVAC_MODE_HEAT_COOL,
                ],
            }

            connection.send_result(
                msg["id"],
                {
                    "rooms": rooms,
                    "heat_sources": sources,
                    "system": system,
                    "ui_settings": ui,
                    "system_params": system_params,
                    "enums": enums,
                },
            )
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_model_config WS failed: %s", err)
            connection.send_error(msg["id"], "model_config_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_model_config)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "heating_assistant/list_datasets",
            vol.Optional("room_slug"): str,
        }
    )
    @websocket_api.async_response
    async def ws_list_datasets(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return metadata for stored identification datasets (no records).

        When *room_slug* is present only datasets for that room are returned.
        """
        try:
            coordinator = _get_coordinator(hass)
            store = getattr(coordinator, "dataset_store", None)
            room_slug = msg.get("room_slug")
            datasets = store.list_meta(room_slug=room_slug) if store is not None else []
            connection.send_result(msg["id"], {"datasets": datasets})
        except Exception as err:
            _LOGGER.error("Heating Assistant: list_datasets WS failed: %s", err)
            connection.send_error(msg["id"], "datasets_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_list_datasets)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "heating_assistant/get_dataset",
            vol.Required("dataset_id"): str,
        }
    )
    @websocket_api.async_response
    async def ws_get_dataset(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return a single stored dataset including its snapshotted records."""
        try:
            coordinator = _get_coordinator(hass)
            store = getattr(coordinator, "dataset_store", None)
            dataset = store.get(msg["dataset_id"]) if store is not None else None
            connection.send_result(msg["id"], {"dataset": dataset})
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_dataset WS failed: %s", err)
            connection.send_error(msg["id"], "dataset_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_dataset)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/list_experiments"}
    )
    @websocket_api.async_response
    async def ws_list_experiments(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return all scheduled / running / completed experiments."""
        try:
            coordinator = _get_coordinator(hass)
            manager = getattr(coordinator, "experiment_manager", None)
            experiments = manager.to_list() if manager is not None else []
            connection.send_result(msg["id"], {"experiments": experiments})
        except Exception as err:
            _LOGGER.error("Heating Assistant: list_experiments WS failed: %s", err)
            connection.send_error(msg["id"], "experiments_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_list_experiments)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply options in-place when possible; reload only for structural changes."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(coordinator, HeatingAssistantCoordinator):
        entry_data = dict(entry.data)
        opts = entry.options
        if opts.get(CONF_ROOMS):
            entry_data[CONF_ROOMS] = opts[CONF_ROOMS]
        if opts.get(CONF_HEAT_SOURCES):
            entry_data[CONF_HEAT_SOURCES] = opts[CONF_HEAT_SOURCES]
        yaml_cfg = hass.data.get(DOMAIN, {}).get("yaml_config", {})
        if yaml_cfg:
            entry_data = _merge_yaml_into_entry_data(entry_data, yaml_cfg)
        merged_config = {**entry_data, **dict(opts)}
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

    # Set up the integration-managed identification history store so it is
    # available before the first update cycle and before history is restored.
    from .identification_history import IdentificationHistoryStore
    _history_days = int(
        merged_entry.options.get(
            CONF_IDENTIFICATION_HISTORY_DAYS,
            merged_entry.data.get(CONF_IDENTIFICATION_HISTORY_DAYS, DEFAULT_IDENTIFICATION_HISTORY_DAYS),
        )
    )
    coordinator.id_history_store = IdentificationHistoryStore(
        hass, entry.entry_id, _history_days
    )
    await coordinator.id_history_store.async_setup()

    # Set up the named-dataset and experiment stores, and restore any persisted
    # experiments so a scheduled experiment survives a restart of Home Assistant.
    from .datasets import DatasetStore
    from .experiments import ExperimentStore
    coordinator.dataset_store = DatasetStore(hass, entry.entry_id)
    await coordinator.dataset_store.async_load()
    coordinator.experiment_store = ExperimentStore(hass, entry.entry_id)
    coordinator.experiment_manager = await coordinator.experiment_store.async_load()

    # Restore runtime state stashed by a prior unload (in-memory only; survives
    # a reload but not a full HA restart). Only keys still present in the new
    # configuration are restored — rooms removed by the YAML edit drop their
    # state, which is the right outcome.
    reload_state = hass.data[DOMAIN].get("_reload_state", {}).pop(entry.entry_id, None)
    if reload_state is not None:
        # A room rename queued by update_rooms remaps the stashed runtime state
        # (keyed by the old name) onto the new name so the toggle / setpoint
        # state follows the rename instead of resetting to defaults.
        _renames = hass.data[DOMAIN].pop("_pending_room_renames", {}) or {}

        def _remap(state_dict: Dict[str, Any]) -> Dict[str, Any]:
            return {_renames.get(k, k): v for k, v in state_dict.items()}

        coordinator._history_buffer.extend(reload_state.get("history_buffer", []))
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
    else:
        # No in-memory state means this is a full HA restart (not a reload).
        # Priority: JSONL store (integration-managed, long-term) → HA Recorder
        # (covers the period before the JSONL store existed) → legacy JSON store.
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
        else:
            # JSONL store empty (first run or files deleted): try HA Recorder.
            try:
                from .history_seed import async_rebuild_history_from_recorder

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
            else:
                # Fall back to the persisted buffer (recorder unavailable or empty).
                try:
                    store = Store(
                        hass,
                        version=1,
                        key=f"{DOMAIN}_history_{entry.entry_id}",
                    )
                    stored_history = await store.async_load()
                    if stored_history and isinstance(stored_history, list):
                        # Drop records older than the buffer's nominal time span so a
                        # previous session's stale (e.g. week-old) data cannot survive
                        # across a restart, linger at the front of the count-bounded
                        # deque, and pollute the identification diagnostics.
                        from .history_window import prune_stale_records

                        _now = getattr(coordinator, "now_utc", None)
                        _now_ts = _now.timestamp() if _now is not None else time.time()
                        coordinator._history_buffer.extend(
                            prune_stale_records(
                                stored_history[-HISTORY_BUFFER_SIZE:],
                                _now_ts,
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

    # Register WebSocket API (only once for the domain)
    if not hass.data[DOMAIN].get("_ws_registered"):
        _register_websocket_api(hass)
        hass.data[DOMAIN]["_ws_registered"] = True

    # Auto-reload when the user changes options via the integration UI.
    # Attached after the coordinator is stored so the persist-merged
    # async_update_entry call above (which already short-circuits when nothing
    # changed) cannot fire the listener during setup.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Push the current coordinator attribute values into every registered
    # entity's HA state immediately after setup.  Without this call,
    # ControllerConfigSensor (and others) only write state when the coordinator
    # runs its next scheduled tick, which can be up to update_interval seconds
    # away.  The Tuning page reads hass.states on load, so without this call
    # the entity state may be missing until the first coordinator tick fires.
    coordinator.async_update_listeners()

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

    # Fast UI refresh: re-read measurements / setpoints / solar and push them to
    # the dashboard on a short cadence so KPIs, measurement cards, and plots stay
    # live between scheduled MPC ticks.  The MPC and EKF advance strictly at the
    # coordinator's update_interval; this loop never runs the controller.  The
    # cadence is capped at the update interval so it never fires more often than
    # the MPC when a very short interval is configured.
    ui_refresh_seconds = min(UI_REFRESH_INTERVAL, coordinator.update_interval_seconds)
    if ui_refresh_seconds > 0:

        async def _async_ui_refresh(_now) -> None:
            await coordinator.async_refresh_ui()

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _async_ui_refresh,
                timedelta(seconds=ui_refresh_seconds),
            )
        )

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

        # Register the custom icon set so it is available on every HA page
        # (including the sidebar) before the frontend renders.  The icon JS
        # lives in www/ and is served under the static path above.
        _sidebar_icon = "mdi:radiator"
        try:
            from homeassistant.components.frontend import async_register_extra_urls

            async_register_extra_urls(
                hass, ["/ha-industrial-panel/heating-assistant-icons.js"]
            )
            _sidebar_icon = "heating-assistant:logo"
        except (ImportError, AttributeError):
            _LOGGER.debug(
                "Heating Assistant: async_register_extra_urls unavailable, "
                "falling back to mdi:radiator sidebar icon",
            )

        from homeassistant.components.frontend import async_register_built_in_panel

        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Heating Assistant",
            sidebar_icon=_sidebar_icon,
            frontend_url_path="ha-industrial",
            config={
                "_panel_custom": {
                    "name": "ha-industrial-panel",
                    # This ?v= token is the SINGLE source of truth for the
                    # frontend cache-bust version.  industrial-dashboard.js reads
                    # this exact token off its own URL (import.meta.url) and reuses
                    # it for every dynamically-imported submodule, so the entry
                    # point and its submodules can never drift out of sync.  Bump
                    # this token (and nothing else) on every frontend change to
                    # force browsers/service-workers to fetch fresh assets.
                    "js_url": "/ha-industrial-panel/industrial-dashboard.js?v=75",
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


async def _get_history_for_window(
    hass: HomeAssistant,
    coordinator: HeatingAssistantCoordinator,
    window_start: Optional[float],
    window_end: Optional[float],
) -> List[Dict[str, Any]]:
    """Return history records for a time window.

    Priority order:
    1. In-memory buffer when it already covers the requested window (fast path).
    2. Integration-managed JSONL store (covers up to ``retention_days`` back).
    3. HA Recorder (fallback for the period before the JSONL store existed).
    4. In-memory buffer as a last resort (with a debug log).
    """
    buf = list(coordinator.history_buffer)

    if window_start is None or window_end is None:
        return buf

    oldest_buf_ts = float(buf[0]["timestamp"]) if buf else None
    if oldest_buf_ts is not None and oldest_buf_ts <= window_start:
        return buf  # fast path: buffer already covers the window

    # Try the integration-managed JSONL store first.
    if coordinator.id_history_store is not None:
        try:
            records = await coordinator.id_history_store.async_query_range(
                window_start, window_end
            )
            if records:
                return records
        except Exception:
            _LOGGER.warning(
                "ID history store query failed for window [%s, %s]",
                window_start, window_end, exc_info=True,
            )

    # Fall back to HA Recorder (works for the initial period before the store
    # was populated, or if the store files were deleted).
    try:
        from .history_seed import async_fetch_history_range

        records = await async_fetch_history_range(
            hass, coordinator, window_start, window_end
        )
        if records:
            return records
    except Exception:
        _LOGGER.debug(
            "Recorder fallback for window [%s, %s] failed",
            window_start, window_end, exc_info=True,
        )

    _LOGGER.debug(
        "No long-term history found for window [%s, %s]; using in-memory buffer",
        window_start, window_end,
    )
    return buf


def _records_for_dataset(
    coordinator: HeatingAssistantCoordinator,
    dataset_id: Optional[str],
) -> Optional[List[Dict[str, Any]]]:
    """Return the snapshotted records for a stored dataset, or ``None``.

    Used by the identification / simulation services so a stored dataset can be
    referenced directly (by ``dataset_id``) instead of a time window — the data
    is read from the dataset's own permanent snapshot, so it works even after the
    rolling history that produced it has been pruned.
    """
    if not dataset_id:
        return None
    store = getattr(coordinator, "dataset_store", None)
    if store is None:
        return None
    records = store.get_records(dataset_id)
    return records if records else None


def _records_for_datasets(
    coordinator: HeatingAssistantCoordinator,
    dataset_ids: Optional[List[str]],
) -> Optional[List[Dict[str, Any]]]:
    """Return the concatenated records of several stored datasets, or ``None``.

    Each dataset's snapshotted records are gathered and merged into a single,
    timestamp-sorted history list.  The estimator splits the merged history into
    contiguous segments wherever the inter-record gap is large, so combining
    disjoint datasets (e.g. several overnight experiments) just yields several
    independent identification segments — exactly what we want for a joint fit.
    """
    if not dataset_ids:
        return None
    store = getattr(coordinator, "dataset_store", None)
    if store is None:
        return None
    merged: List[Dict[str, Any]] = []
    for ds_id in dataset_ids:
        recs = store.get_records(ds_id)
        if recs:
            merged.extend(recs)
    if not merged:
        return None
    merged.sort(key=lambda r: float(r.get("timestamp", 0.0)))
    return merged


def _persist_tuning_updates(
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


# Per-room thermal-model parameters the System Identification panel can preview
# in the EKF reconstruction / open-loop simulation.  Each is passed as a
# ``<param>_<room_slug>`` service-data key (e.g. ``thermal_mass_living_room``)
# so a parameter set can be tried without applying it to the live model.
_SIM_ROOM_PARAM_KEYS = (
    "thermal_mass",
    "r_external",
    "internal_gain",
    "solar_scale",
    "c_air_fraction",
    "r_aw_fraction",
    # t_wall_initial is identified per-dataset but never shown in the UI
    # parameter list. It is injected automatically from sysid_results by the
    # EKF and open-loop handlers below so reconstruction uses the last
    # identified value without any user interaction.
)


def _extract_sim_room_params(
    call: ServiceCall,
    room_names: Any,
) -> Dict[str, Dict[str, float]]:
    """Collect per-room parameter overrides from a simulation service call.

    Reads every ``<param>_<room_slug>`` key present in ``call.data`` into a
    ``{room_name: {param: value}}`` mapping so the EKF reconstruction and
    open-loop simulation use the full set of values currently shown in the UI,
    not just ``thermal_mass`` / ``r_external``.
    """
    from .dashboard import slugify  # noqa: PLC0415

    room_params: Dict[str, Dict[str, float]] = {}
    for room_name in room_names:
        room_key = slugify(room_name)
        overrides: Dict[str, float] = {}
        for param in _SIM_ROOM_PARAM_KEYS:
            key = f"{param}_{room_key}"
            if key in call.data:
                overrides[param] = float(call.data[key])
        if overrides:
            room_params[room_name] = overrides
    return room_params


def _inject_identified_t_wall_initial(
    room_params: Dict[str, Dict[str, float]],
    coordinator: HeatingAssistantCoordinator,
) -> None:
    """Add identified t_wall_initial from sysid_results into room_params.

    The wall envelope initial temperature is identified per dataset but is
    not shown in the UI parameter list.  This function ensures that every
    EKF/open-loop reconstruction run automatically uses the last identified
    value for each room, falling back to the steady-state seed when no
    identified value is available.  Explicit overrides already present in
    room_params (e.g. from a direct service call) are never overwritten.
    """
    for room_name in coordinator.model.room_names:
        sysid = coordinator.sysid_results.get(room_name, {})
        t_wall = sysid.get("t_wall_initial")
        if t_wall is not None:
            room_params.setdefault(room_name, {}).setdefault(
                "t_wall_initial", float(t_wall)
            )


def _effective_heater_scales(
    call: ServiceCall,
    coordinator: HeatingAssistantCoordinator,
) -> Dict[str, float]:
    """Resolve the heater power scales a simulation should use.

    Prefers the explicit ``heater_scales`` mapping supplied by the
    identification panel (so manual edits take effect without clicking Apply);
    falls back to the last auto-identified scales cached on the coordinator for
    direct service calls that omit it.
    """
    ui_scales = call.data.get("heater_scales") or {}
    if ui_scales:
        return {str(k): float(v) for k, v in ui_scales.items()}
    return dict(getattr(coordinator, "_last_identified_heater_scales", {}))


def _patched_heat_sources(
    coordinator: HeatingAssistantCoordinator,
    scales: Dict[str, float],
) -> Any:
    """Return heat sources with *scales* applied to shallow copies.

    The live heat sources are never mutated — when ``scales`` is empty the live
    list is returned unchanged (fast path), otherwise each affected source is
    shallow-copied and its ``power_scale`` overridden for the simulation only.
    """
    if not scales:
        return coordinator.heat_sources
    import copy as _copy  # noqa: PLC0415

    patched = [_copy.copy(src) for src in coordinator.heat_sources]
    for src in patched:
        if src.name in scales:
            src.power_scale = float(scales[src.name])
    return patched


# ---------------------------------------------------------------------------
# Room-rename migration
# ---------------------------------------------------------------------------
# When a room is renamed from the Configuration page, everything keyed by the
# old room name must follow it to the new name — otherwise the renamed room
# starts blank and the old data is orphaned.  These helpers migrate the
# config-entry data (persisted state, estimated parameters, heat-source room
# links) and inter-room connection references.  The entity registry is migrated
# separately (best-effort) so the room keeps its history and entity ids.

#: ``entry.data`` keys whose value is a ``{room_name: ...}`` dict.
_ROOM_KEYED_STATE_KEYS = (
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SCHEDULES,
)


def _remap_keys(d: Any, renames: Dict[str, str]) -> Any:
    """Return ``d`` with any top-level keys present in ``renames`` remapped."""
    if not isinstance(d, dict):
        return d
    return {renames.get(k, k): v for k, v in d.items()}


def _migrate_room_name_data(data: Dict[str, Any], renames: Dict[str, str]) -> Dict[str, Any]:
    """Migrate every room-name-keyed structure in a ``data``/``options`` dict.

    Covers persisted per-room state, the nested ``rooms`` map inside the
    estimated-parameters snapshot, and the ``room`` link on each heat source.
    Returns a new dict; the input is not mutated.
    """
    if not renames:
        return dict(data)
    new = dict(data)

    for key in _ROOM_KEYED_STATE_KEYS:
        if isinstance(new.get(key), dict):
            new[key] = _remap_keys(new[key], renames)

    estimated = new.get(CONF_ESTIMATED_PARAMS)
    if isinstance(estimated, dict) and isinstance(estimated.get("rooms"), dict):
        estimated = dict(estimated)
        estimated["rooms"] = _remap_keys(estimated["rooms"], renames)
        new[CONF_ESTIMATED_PARAMS] = estimated

    sources = new.get(CONF_HEAT_SOURCES)
    if isinstance(sources, list):
        new[CONF_HEAT_SOURCES] = [
            {**s, CONF_SOURCE_ROOM: renames[s[CONF_SOURCE_ROOM]]}
            if isinstance(s, dict) and s.get(CONF_SOURCE_ROOM) in renames
            else s
            for s in sources
        ]
    return new


def _apply_renames_to_connections(
    rooms: List[Dict[str, Any]], renames: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Re-point inter-room connection targets to renamed rooms."""
    if not renames:
        return rooms
    out: List[Dict[str, Any]] = []
    for room in rooms:
        if isinstance(room, dict) and isinstance(room.get(CONF_CONNECTIONS), list):
            room = {
                **room,
                CONF_CONNECTIONS: [
                    {**c, CONF_CONNECTED_ROOM: renames[c[CONF_CONNECTED_ROOM]]}
                    if isinstance(c, dict) and c.get(CONF_CONNECTED_ROOM) in renames
                    else c
                    for c in room[CONF_CONNECTIONS]
                ],
            }
        out.append(room)
    return out


def _migrate_room_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    renames: Dict[str, str],
    all_room_names: List[str],
) -> None:
    """Best-effort: move this integration's per-room entities to the new name.

    Updates each affected registry entry's ``unique_id`` (and ``entity_id`` slug)
    in place so the room keeps its recorder history, dashboards and automations
    instead of orphaning the old entities and creating fresh ones.  Any failure
    is logged and swallowed — a rename must never be blocked by registry quirks.
    """
    from homeassistant.helpers import entity_registry as er

    from .dashboard import slugify as _slugify

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    existing_uids = {e.unique_id for e in entries}
    other_names = [n for n in all_room_names if n]

    for ent in entries:
        for old, new in renames.items():
            prefix = f"{DOMAIN}_{old}_"
            if not ent.unique_id.startswith(prefix):
                continue
            # Skip when a longer room name also prefixes this id (e.g. renaming
            # "living" must not capture "living_room"'s entities).
            if any(
                other != old
                and len(other) > len(old)
                and ent.unique_id.startswith(f"{DOMAIN}_{other}_")
                for other in other_names
            ):
                break
            new_uid = f"{DOMAIN}_{new}_" + ent.unique_id[len(prefix):]
            if new_uid in existing_uids:
                break
            updates: Dict[str, Any] = {"new_unique_id": new_uid}
            old_slug = _slugify(old)
            new_slug = _slugify(new)
            old_eid_part = f"{DOMAIN}_{old_slug}_"
            if old_slug != new_slug and old_eid_part in ent.entity_id:
                updates["new_entity_id"] = ent.entity_id.replace(
                    old_eid_part, f"{DOMAIN}_{new_slug}_", 1
                )
            try:
                registry.async_update_entity(ent.entity_id, **updates)
                existing_uids.add(new_uid)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.warning(
                    "Heating Assistant: failed migrating entity %s on room rename",
                    ent.entity_id,
                    exc_info=True,
                )
            break


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services for setup assistance."""

    async def handle_simulate(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass)
        result = coordinator.simulate_thermal_response(
            room_name=call.data["room_name"],
            initial_temp=call.data["initial_temp"],
            outdoor_temp=call.data["outdoor_temp"],
            heating_power=call.data["heating_power"],
            duration_hours=call.data["duration_hours"],
        )
        # Fire an event so automations can consume the result via a trigger,
        # and return it as service-response data so it shows up inline in
        # Developer Tools → Actions rather than as a persistent notification.
        hass.bus.async_fire(
            f"{DOMAIN}_simulation_result",
            result,
        )
        return result

    async def handle_estimate(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass)
        result = coordinator.estimate_parameters(
            room_name=call.data["room_name"],
            heating_power=call.data["heating_power"],
            outdoor_temp=call.data["outdoor_temp"],
            initial_temp=call.data["initial_temp"],
            final_temp=call.data["final_temp"],
            duration_seconds=call.data["duration_seconds"],
        )
        # Fire an event for automations and return the result as service
        # response data (Developer Tools → Actions); no notification is raised.
        hass.bus.async_fire(
            f"{DOMAIN}_estimation_result",
            result,
        )
        return result

    async def handle_estimate_ml(call: ServiceCall) -> None:
        """Run ML parameter estimation using the Kalman filter log-likelihood."""
        coordinator = _get_coordinator(hass)
        apply_params: bool = call.data.get("apply_parameters", False)
        horizon_hours: Optional[float] = call.data.get("horizon_hours")
        locked_params: Optional[Dict] = call.data.get("locked_params")
        window_start_ml: Optional[float] = (
            float(call.data["window_start"]) if "window_start" in call.data else None
        )
        window_end_ml: Optional[float] = (
            float(call.data["window_end"]) if "window_end" in call.data else None
        )

        # Resolve the data the estimator runs over, in priority order:
        #   1. ``dataset_ids`` — joint identification over several stored
        #      datasets (their records are merged into one history).
        #   2. ``dataset_id`` — a single stored dataset's snapshot.
        #   3. an explicit ``window_start``/``window_end`` (JSONL / Recorder).
        # Each takes precedence over the trailing ``horizon_hours`` window.
        history_override = _records_for_datasets(
            coordinator, call.data.get("dataset_ids")
        )
        if history_override is None:
            history_override = _records_for_dataset(
                coordinator, call.data.get("dataset_id")
            )
        if history_override is None and window_start_ml is not None and window_end_ml is not None:
            history_override = await _get_history_for_window(
                hass, coordinator, window_start_ml, window_end_ml
            )

        result = await coordinator.async_estimate_parameters_ml(
            apply_params=apply_params,
            horizon_hours=horizon_hours if history_override is None else None,
            locked_params=locked_params,
            history_override=history_override,
        )

        # Update sysid_results so that the dashboard sensor entities reflect
        # the newly identified parameters immediately (without requiring a
        # separate sysid simulation run).
        if result.get("success"):
            dt = coordinator.dt
            horizon_steps = (
                max(1, int(float(horizon_hours) * 3600.0 / dt))
                if horizon_hours is not None
                else None
            )
            internal_gains = result.get("estimated_internal_gains", {})
            solar_scales = result.get("estimated_solar_scales", {})
            envelope_splits = result.get("estimated_envelope_splits", {})
            t_wall_initial = result.get("estimated_t_wall_initial", {})
            heater_scales = result.get("estimated_heater_scales", {})

            # Map each room to the identified scales of the heaters in it so the
            # per-room identification page can list them like any other param.
            sources_by_room: Dict[str, Dict[str, float]] = {}
            for src in coordinator.heat_sources:
                room = getattr(src, "room", None)
                if room is None or src.name not in heater_scales:
                    continue
                sources_by_room.setdefault(room, {})[src.name] = float(
                    heater_scales[src.name]
                )

            for room_name, params in result.get("estimated_params", {}).items():
                existing = coordinator.sysid_results.get(room_name, {})
                existing["thermal_mass"] = params.get("thermal_mass")
                existing["r_external"] = params.get("r_external")
                # Surface the full identified parameter set so the room-level
                # identification page can review and apply every parameter
                # (not just C / R_ext) in one place.
                if room_name in internal_gains:
                    existing["internal_gain"] = internal_gains[room_name]
                if room_name in solar_scales:
                    existing["solar_scale"] = solar_scales[room_name]
                if room_name in envelope_splits:
                    splits = envelope_splits[room_name]
                    if "c_air_fraction" in splits:
                        existing["c_air_fraction"] = splits["c_air_fraction"]
                    if "r_aw_fraction" in splits:
                        existing["r_aw_fraction"] = splits["r_aw_fraction"]
                if room_name in t_wall_initial:
                    existing["t_wall_initial"] = t_wall_initial[room_name]
                if room_name in sources_by_room:
                    existing["heater_scales"] = sources_by_room[room_name]
                if horizon_steps is not None:
                    existing["horizon_steps"] = horizon_steps
                coordinator.sysid_results[room_name] = existing

            # Cache identified heater scales on the coordinator so the system-wide
            # config sensor keeps exposing them too.
            coordinator._last_identified_heater_scales = dict(heater_scales)

            coordinator.async_update_listeners()

        # Fire an event so automations can consume the full result. The
        # outcome is surfaced in the UI through the per-room parameter and
        # diagnostic sensors, so no persistent notification is raised.
        hass.bus.async_fire(
            f"{DOMAIN}_ml_estimation_result",
            {
                k: v
                for k, v in result.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
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
        supports_response=SupportsResponse.OPTIONAL,
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
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS_ML,
        handle_estimate_ml,
        schema=vol.Schema(
            {
                vol.Optional("apply_parameters", default=False): cv.boolean,
                vol.Optional("horizon_hours"): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0)
                ),
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                vol.Optional("locked_params"): dict,
                # Identify from a stored dataset's snapshotted records, taking
                # precedence over window_start/window_end and horizon_hours.
                vol.Optional("dataset_id"): cv.string,
                # Joint identification over several stored datasets (their
                # records are merged); takes precedence over dataset_id.
                vol.Optional("dataset_ids"): [cv.string],
            }
        ),
    )

    async def handle_run_open_loop_simulation(call: ServiceCall) -> None:
        """Run open-loop simulation diagnostic and report RMSE per room."""
        from .model_diagnostics import compute_open_loop_predictions
        from .sysid import _build_sim_model

        coordinator = _get_coordinator(hass)
        segment_length = int(call.data.get("segment_length", 30))
        horizon_hours: Optional[float] = call.data.get("horizon_hours")
        window_start_ol: Optional[float] = (
            float(call.data["window_start"]) if "window_start" in call.data else None
        )
        window_end_ol: Optional[float] = (
            float(call.data["window_end"]) if "window_end" in call.data else None
        )

        # A stored dataset takes precedence over a window / horizon and supplies
        # its snapshotted records directly.
        dataset_records = _records_for_dataset(coordinator, call.data.get("dataset_id"))
        if dataset_records is not None:
            history = dataset_records
        # Explicit window: fetch from JSONL store / Recorder if needed.
        elif window_start_ol is not None and window_end_ol is not None:
            history = await _get_history_for_window(
                hass, coordinator, window_start_ol, window_end_ol
            )
            from .history_window import select_window_by_timestamps
            history = select_window_by_timestamps(history, window_start_ol, window_end_ol)
        elif horizon_hours is not None:
            history = list(coordinator.history_buffer)
            from .history_window import select_recent_window
            history = select_recent_window(
                history, float(horizon_hours) * 3600.0, coordinator.dt
            )
        else:
            history = list(coordinator.history_buffer)

        # Build per-room parameter overrides from service data (same keys as
        # run_sysid_simulation) so the open-loop diagnostic uses the full
        # parameter set the user configured in the identification panel.
        room_params = _extract_sim_room_params(call, coordinator.model.room_names)

        # When room-parameter overrides are given, build a temporary
        # HouseThermalSDE from a patched model copy so the open-loop
        # simulation uses the parameters currently visible in the UI, not
        # the last applied set.  Without overrides the live controller
        # system is used directly (fast path, no copy needed).
        room_names = coordinator.model.room_names
        n_rooms = len(room_names)
        dt = coordinator.dt

        # Patch heat-source copies with the heater power scales currently shown
        # in the UI (falling back to the last auto-identified scales) so the
        # open-loop simulation uses them even when the user hasn't clicked Apply.
        heater_scales = _effective_heater_scales(call, coordinator)
        base_heat_sources = _patched_heat_sources(coordinator, heater_scales)

        if room_params or heater_scales:
            from .controller import HouseThermalSDE  # noqa: PLC0415
            sigma_w: float = float(call.data.get(
                "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
            ))
            sigma_v: float = float(call.data.get(
                "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
            ))
            try:
                sim_model = _build_sim_model(
                    coordinator.model, room_params, room_names
                )
                system = HouseThermalSDE(
                    sim_model,
                    base_heat_sources,
                    dt,
                    sigma_w=sigma_w,
                    sigma_v=sigma_v,
                    augment_offsets=False,
                )
            except Exception as exc:
                _LOGGER.error(
                    "Open-loop simulation: failed to build patched system: %s",
                    exc, exc_info=True,
                )
                return
        else:
            system = coordinator.controller._system

        # Quickly identify the wall-envelope initial temperature for this
        # specific dataset so the continuous simulation starts from a
        # physically informed wall state.  All structural parameters are
        # locked to the values already in use; only t_wall_init is free.
        # A single L-BFGS-B pass is used — no multistart — so this is fast.
        t_wall_initial_identified: Optional[Dict[str, float]] = None
        try:
            from .parameter_estimator import KalmanMLEstimator
            fast_estimator = KalmanMLEstimator(
                rooms=list(coordinator.model.rooms.values()),
                sources=coordinator.heat_sources,
                dt=dt,
            )
            t_wall_initial_identified = await hass.async_add_executor_job(
                fast_estimator.estimate_wall_initial_only,
                history,
                room_params if room_params else None,
            )
            _LOGGER.debug(
                "Fast t_wall_init estimate for open-loop sim: %s",
                t_wall_initial_identified,
            )
        except Exception as exc:
            _LOGGER.warning(
                "Open-loop: fast t_wall_init estimation failed (%s); "
                "falling back to air-temperature seed.", exc,
            )

        # Results are surfaced in the UI via the per-room OpenLoopRMSESensor
        # entities, so this service writes its output to the coordinator cache
        # rather than raising a persistent notification.
        #
        # Use continuous mode (segment_length=None) for the main simulation so
        # the plot shows no artificial discontinuities: the wall/envelope state
        # is never re-initialised mid-run, it just evolves freely from the
        # starting condition.  The multi-horizon RMSE analysis below uses fixed
        # segment lengths specifically to measure N-step-ahead accuracy.
        try:
            result = await hass.async_add_executor_job(
                compute_open_loop_predictions,
                history,
                system,
                room_names,
                n_rooms,
                dt,
                None,
                t_wall_initial_identified,
            )

            if "error" not in result:
                per_room = result.get("per_room", {})

                # Multi-horizon open-loop RMSE: re-run the simulation at
                # ~4 h / 12 h / 24 h segment lengths so the diagnostic shows
                # how prediction error grows with horizon — the quantity
                # that matters for price-driven anticipatory heating, where
                # the plan spans many hours.  Each run reuses the same
                # history and model; only the segment slicing differs.
                rmse_by_horizon: Dict[str, Dict[str, Any]] = {
                    name: {} for name in room_names
                }
                for hours in (4, 12, 24):
                    steps = max(2, int(round(hours * 3600.0 / dt)))
                    res_h = await hass.async_add_executor_job(
                        compute_open_loop_predictions,
                        history, system, room_names, n_rooms,
                        dt, steps,
                    )
                    if "error" in res_h:
                        continue
                    for name, room_res in res_h.get("per_room", {}).items():
                        rmse_by_horizon[name][f"{hours}h"] = room_res.get("rmse")
                for name in room_names:
                    if name in per_room:
                        per_room[name]["rmse_by_horizon"] = rmse_by_horizon[name]

                # Write per-room results to coordinator cache so that
                # OpenLoopRMSESensor entities can read them without any
                # blocking computation on the event loop.
                coordinator.open_loop_results.update(per_room)
                coordinator.async_update_listeners()

        except Exception as exc:
            _LOGGER.error("Open-loop simulation failed: %s", exc, exc_info=True)

    async def handle_analyze_model_fit(call: ServiceCall) -> ServiceResponse:
        """Analyze model-fit quality for all or a specific room.

        The fit metrics (R², RMSE, MAE, bias, …) are continuously exposed by
        the per-room ``…_model_fit_quality`` sensor. This service refreshes
        those sensors and returns the full report as service-response data so
        Developer Tools → Actions shows it inline; no persistent notification
        is raised.
        """
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
        except Exception as exc:
            _LOGGER.error("Model fit analysis failed: %s", exc, exc_info=True)
            return {"error": str(exc)}

        # Filter to specific room if requested
        if room_name_filter and room_name_filter in report.get("rooms", {}):
            report["rooms"] = {room_name_filter: report["rooms"][room_name_filter]}

        # Refresh the model-fit sensors so the dashboard reflects the latest data.
        coordinator.async_update_listeners()
        return report

    async def handle_validate_parameters(call: ServiceCall) -> ServiceResponse:
        """Validate thermal parameters for all or a specific room.

        Returns the validation result as service-response data (visible in
        Developer Tools → Actions); no persistent notification is raised.
        """
        from .model_diagnostics import validate_parameters

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        rooms_to_check = (
            [room_name_filter]
            if room_name_filter and room_name_filter in coordinator.model.rooms
            else coordinator.model.room_names
        )

        rooms: Dict[str, Any] = {}
        for room_name in rooms_to_check:
            room = coordinator.model.rooms[room_name]
            try:
                validation = validate_parameters(
                    room_name, room.thermal_mass, room.r_external
                )
                rooms[room_name] = {
                    "valid": all([
                        validation.mass_valid,
                        validation.r_external_valid,
                        validation.time_constant_valid,
                    ]),
                    "thermal_mass": validation.thermal_mass,
                    "thermal_mass_valid": validation.mass_valid,
                    "r_external": validation.r_external,
                    "r_external_valid": validation.r_external_valid,
                    "time_constant_hours": validation.time_constant_hours,
                    "time_constant_valid": validation.time_constant_valid,
                    "warnings": list(validation.warnings),
                }
            except Exception as exc:
                _LOGGER.error("Parameter validation failed for %s: %s", room_name, exc)
                rooms[room_name] = {"error": str(exc)}

        return {"rooms": rooms}

    async def handle_controller_performance(call: ServiceCall) -> ServiceResponse:
        """Generate a controller performance report for all or a specific room.

        Returns the report as service-response data (visible in Developer
        Tools → Actions); no persistent notification is raised.
        """
        from .model_diagnostics import compute_controller_performance

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        rooms_to_check = (
            [room_name_filter]
            if room_name_filter and room_name_filter in coordinator.model.rooms
            else coordinator.model.room_names
        )

        rooms: Dict[str, Any] = {}
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
                rooms[room_name] = {"error": "insufficient_data"}
                continue

            try:
                perf = compute_controller_performance(
                    temperatures, room.setpoint, room_name
                )
                rooms[room_name] = {
                    "setpoint": room.setpoint,
                    "mean_tracking_error": perf.mean_tracking_error,
                    "tracking_error_std": perf.tracking_error_std,
                    "time_above_setpoint": perf.time_above_setpoint,
                    "time_below_setpoint": perf.time_below_setpoint,
                    "time_in_deadband": perf.time_in_deadband,
                    "max_overshoot": perf.max_overshoot,
                    "max_undershoot": perf.max_undershoot,
                    "n_samples": perf.n_samples,
                }
            except Exception as exc:
                _LOGGER.error("Controller performance analysis failed for %s: %s", room_name, exc)
                rooms[room_name] = {"error": str(exc)}

        return {"rooms": rooms}

    hass.services.async_register(
        DOMAIN,
        "analyze_model_fit",
        handle_analyze_model_fit,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
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
        supports_response=SupportsResponse.OPTIONAL,
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
        supports_response=SupportsResponse.OPTIONAL,
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
                # Explicit window overrides horizon when both start and end are
                # provided. Values are UNIX timestamps (seconds since epoch).
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                vol.Optional("sigma_w"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
                vol.Optional("sigma_v"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=10.0)
                ),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            },
            # Allow per-room parameter overrides keyed as <param>_<room_slug>
            # (thermal_mass_<room>, r_external_<room>, internal_gain_<room>,
            # solar_scale_<room>, c_air_fraction_<room>, r_aw_fraction_<room>).
            extra=vol.ALLOW_EXTRA,
        ),
    )

    async def handle_run_sysid_simulation(call: ServiceCall) -> None:
        """Run system-identification open-loop simulation with configurable params."""
        from .sysid import run_sysid_simulation

        coordinator = _get_coordinator(hass)
        room_name_filter: Optional[str] = call.data.get("room_name")
        horizon_hours: float = float(call.data.get("horizon_hours", 6.0))
        sigma_w: float = float(call.data.get(
            "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
        ))
        sigma_v: float = float(call.data.get(
            "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
        ))

        # Explicit window: if window_start / window_end are supplied (UNIX
        # timestamps), they override the horizon-based trailing window.
        window_start: Optional[float] = (
            float(call.data["window_start"]) if "window_start" in call.data else None
        )
        window_end: Optional[float] = (
            float(call.data["window_end"]) if "window_end" in call.data else None
        )
        window_spec = (
            (window_start, window_end)
            if window_start is not None and window_end is not None
            else None
        )

        dt = coordinator.dt  # seconds; handles runtime overrides correctly
        horizon_steps = max(1, int(horizon_hours * 3600.0 / dt))

        # Build per-room parameter overrides from service data (full parameter
        # set, keyed by ``<param>_<room_slug>``), then inject the last identified
        # wall envelope initial temperature so reconstruction always starts from
        # a physically informed wall state without requiring any UI interaction.
        room_params = _extract_sim_room_params(call, coordinator.model.room_names)
        _inject_identified_t_wall_initial(room_params, coordinator)

        # Fetch history: a stored dataset (``dataset_id``) supplies its
        # snapshotted records directly; otherwise use the JSONL store / Recorder
        # for out-of-buffer windows.  With a dataset the whole snapshot is used
        # (window_spec cleared) since it was captured for exactly this purpose.
        dataset_records = _records_for_dataset(coordinator, call.data.get("dataset_id"))
        if dataset_records is not None:
            history = dataset_records
            window_spec = None
        else:
            history = await _get_history_for_window(
                hass, coordinator, window_start, window_end
            )

        # Patch heat-source copies with the heater power scales currently shown
        # in the UI (falling back to the last auto-identified scales) so the EKF
        # reconstruction uses them even when the user hasn't clicked Apply yet.
        heater_scales = _effective_heater_scales(call, coordinator)
        sim_heat_sources = _patched_heat_sources(coordinator, heater_scales)

        try:
            result = await hass.async_add_executor_job(
                run_sysid_simulation,
                history,
                coordinator.model,
                sim_heat_sources,
                coordinator.model.room_names,
                dt,
                horizon_steps,
                room_params,
                sigma_w,
                sigma_v,
                window_spec,
            )

            if "error" not in result:
                per_room = result.get("per_room", {})

                # Tag each per-room result with the horizon and window so the
                # sensor can surface them without knowing dt or call.data.
                for room_data in per_room.values():
                    room_data["horizon_steps"] = result.get("horizon_steps", horizon_steps)
                    if window_start is not None:
                        room_data["window_start"] = window_start
                    if window_end is not None:
                        room_data["window_end"] = window_end

                # Filter to requested room if specified.  The frontend
                # sends the room slug (e.g. "bedroom") while per_room keys are
                # the canonical room names (e.g. "Bedroom") stored in the
                # model.  Accept both exact and slug matches so rooms with
                # capital letters or spaces are not silently dropped.
                if room_name_filter:
                    from .dashboard import slugify as _slugify  # noqa: PLC0415
                    per_room = {
                        k: v for k, v in per_room.items()
                        if k == room_name_filter or _slugify(k) == room_name_filter
                    }

                # Results are surfaced via the per-room SysID diagnostic
                # sensors, so this service stores them on the coordinator and
                # refreshes listeners instead of raising a notification.
                coordinator.sysid_results.update(per_room)
                coordinator.async_update_listeners()

        except Exception as exc:
            _LOGGER.error("SysID simulation failed: %s", exc, exc_info=True)

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
                # Explicit identification window as UNIX timestamps [s].
                # When both are provided, horizon_hours is ignored and only data
                # in [window_start, window_end] is used for identification.
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            },
            # Allow per-room parameter overrides keyed as <param>_<room_slug>
            # (thermal_mass_<room>, r_external_<room>, internal_gain_<room>,
            # solar_scale_<room>, c_air_fraction_<room>, r_aw_fraction_<room>).
            extra=vol.ALLOW_EXTRA,
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

        for name in targets:
            if name not in coordinator.model.rooms:
                continue
            coordinator.set_schedule_enabled(name, enabled)

        # Push an immediate state_changed event so the overview tiles refresh.
        # The new schedule state is visible on those tiles, so no persistent
        # notification is raised.
        coordinator.async_update_listeners()

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

    async def handle_set_system_enabled(call: ServiceCall) -> None:
        """Enable or disable the heating assistant controller globally."""
        coordinator = _get_coordinator(hass)
        enabled = call.data["enabled"]
        coordinator.set_system_enabled(enabled)
        if enabled:
            # Engage the controller immediately on START rather than waiting for
            # the next scheduled tick — this runs the MPC and pushes commands to
            # the heaters right away so the action feels responsive.
            await coordinator.async_request_refresh()
        else:
            coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "set_system_enabled",
        handle_set_system_enabled,
        schema=vol.Schema({vol.Required("enabled"): cv.boolean}),
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
        CONF_IDENTIFICATION_HORIZON_HOURS,
        CONF_WINDOW_OPEN_DEBOUNCE, CONF_WINDOW_OPEN_CLOSE_SETTLE,
        CONF_WINDOW_OPEN_Q_INFLATION,
    }

    async def handle_update_controller_tuning(call: ServiceCall) -> None:
        """Update MPC controller tuning parameters from the dashboard."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _CONTROLLER_TUNING_KEYS}
        if not updates:
            return
        _persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_controller_tuning",
        handle_update_controller_tuning,
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

    async def handle_apply_heater_scales(call: ServiceCall) -> None:
        """Apply heater power-scale factors identified by ML estimation.

        When called with no ``scales`` argument the last identified scales
        cached on the coordinator (from the most-recent ``estimate_parameters_ml``
        run) are used.  An explicit ``scales`` dict can override this for
        testing or manual correction.
        """
        coordinator = _get_coordinator(hass)
        scales: Optional[Dict[str, float]] = call.data.get("scales")
        if not scales:
            scales = getattr(coordinator, "_last_identified_heater_scales", {})
        if not scales:
            _LOGGER.warning(
                "apply_heater_scales: no scales provided and none identified yet; "
                "run estimate_parameters_ml first."
            )
            return
        coordinator.apply_heater_scales(scales)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_HEATER_SCALES,
        handle_apply_heater_scales,
        schema=vol.Schema(
            {
                vol.Optional("scales"): {cv.string: vol.Coerce(float)},
            }
        ),
    )

    async def handle_update_estimation_params(call: ServiceCall) -> None:
        """Update state estimation parameters from the dashboard."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _ESTIMATION_PARAM_KEYS}
        if not updates:
            return
        _persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_estimation_params",
        handle_update_estimation_params,
        schema=vol.Schema(
            {
                vol.Optional(CONF_SIGMA_W): vol.Coerce(float),
                vol.Optional(CONF_SIGMA_V): vol.Coerce(float),
                vol.Optional(CONF_SIGMA_B): vol.Coerce(float),
                vol.Optional(CONF_IDENTIFICATION_HORIZON_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5, max=72.0)
                ),
                vol.Optional(CONF_WINDOW_OPEN_DEBOUNCE): vol.Coerce(int),
                vol.Optional(CONF_WINDOW_OPEN_CLOSE_SETTLE): vol.Coerce(int),
                vol.Optional(CONF_WINDOW_OPEN_Q_INFLATION): vol.Coerce(float),
            }
        ),
    )

    # ------------------------------------------------------------------
    # Configuration page services (industrial UI "Configuration" menu)
    # ------------------------------------------------------------------

    async def handle_update_ui_settings(call: ServiceCall) -> None:
        """Persist industrial-panel display settings (plot history / horizon)."""
        coordinator = _get_coordinator(hass)
        updates = {
            k: v
            for k, v in call.data.items()
            if k in (CONF_PLOT_HISTORY_HOURS, CONF_PLOT_FORECAST_HOURS)
        }
        if not updates:
            return
        _persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_ui_settings",
        handle_update_ui_settings,
        schema=vol.Schema(
            {
                vol.Optional(CONF_PLOT_HISTORY_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0, max=168.0)
                ),
                vol.Optional(CONF_PLOT_FORECAST_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=168.0)
                ),
            }
        ),
    )

    _SYSTEM_PARAM_KEYS = {CONF_IDENTIFICATION_HISTORY_DAYS}

    async def handle_update_system_params(call: ServiceCall) -> None:
        """Persist system-level parameters (e.g. history retention)."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _SYSTEM_PARAM_KEYS}
        if not updates:
            return
        _persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_system_params",
        handle_update_system_params,
        schema=vol.Schema(
            {
                vol.Optional(CONF_IDENTIFICATION_HISTORY_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=7, max=365)
                ),
            }
        ),
    )

    def _write_entry_config(updates: Dict[str, Any]) -> None:
        """Write ``updates`` to both entry.data and entry.options.

        Writing to both stores keeps the coordinator's options-first reads
        consistent across restarts.  The registered update-listener reloads the
        integration when a structural key (rooms / heat sources) changes and
        applies the rest in-place.
        """
        coordinator = _get_coordinator(hass)
        entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
        if entry is None:
            return
        new_data = {**dict(entry.data), **updates}
        new_options = {**dict(entry.options), **updates}
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )

    async def handle_update_rooms(call: ServiceCall) -> None:
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
            _write_entry_config({CONF_ROOMS: rooms})
            return

        coordinator = _get_coordinator(hass)
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

    # The Configuration UI sends back the WHOLE rooms list — the room being
    # edited plus every other room carried verbatim from the backend.  Those
    # carried rooms already passed the canonical room schema when first written,
    # and they hold values the round-trip schema cannot reproduce: stored weekday
    # indices are ints (not the schema's ``[str]``), schedule periods carry
    # editor-only keys, and identification writes out-of-schema fields like
    # ``solar_scale``.  Re-imposing the room schema on this data only rejected or
    # mangled valid rooms, so the service accepts the rooms list as opaque dicts
    # and leaves validation/coercion to the coordinator (``build_house_model``),
    # which already tolerates partial rooms and now drops dangling sub-records.
    hass.services.async_register(
        DOMAIN,
        "update_rooms",
        handle_update_rooms,
        schema=vol.Schema(
            {
                vol.Required(CONF_ROOMS): [dict],
                # Optional {old_name: new_name} map so a rename migrates all
                # data keyed by the old room name.
                vol.Optional("renames"): {cv.string: cv.string},
            }
        ),
    )

    async def handle_update_heat_sources(call: ServiceCall) -> None:
        """Replace the configured heat-source list (triggers a reload)."""
        sources = call.data["heat_sources"]
        _write_entry_config({CONF_HEAT_SOURCES: sources})

    hass.services.async_register(
        DOMAIN,
        "update_heat_sources",
        handle_update_heat_sources,
        # Heat sources are likewise carried verbatim from the backend and may
        # hold out-of-schema fields (e.g. an identified ``power_scale``), so the
        # list is accepted as opaque dicts; the coordinator validates on build.
        schema=vol.Schema({vol.Required(CONF_HEAT_SOURCES): [dict]}),
    )

    async def handle_update_system_config(call: ServiceCall) -> None:
        """Update environment entities and site location from the dashboard.

        Entity keys are applied in-place by the coordinator's runtime
        reconfiguration; an empty string clears the entity.
        """
        coordinator = _get_coordinator(hass)
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
        _persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_system_config",
        handle_update_system_config,
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
        heater_scales = call.data.get("heater_scales")
        coordinator.store_identified_parameters(
            room_name, thermal_mass, r_external, source=source, rmse=rmse,
            internal_gain=call.data.get("internal_gain"),
            solar_scale=call.data.get("solar_scale"),
            c_air_fraction=call.data.get("c_air_fraction"),
            r_aw_fraction=call.data.get("r_aw_fraction"),
            heater_scales=dict(heater_scales) if heater_scales else None,
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
                vol.Optional("internal_gain"): vol.Coerce(float),
                vol.Optional("solar_scale"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("c_air_fraction"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("r_aw_fraction"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
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

    async def handle_delete_parameter_history(call: ServiceCall) -> None:
        """Delete a single entry from the parameter history."""
        coordinator = _get_coordinator(hass)
        history_index: int = call.data["history_index"]
        coordinator.delete_parameter_history(history_index)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "delete_parameter_history",
        handle_delete_parameter_history,
        schema=vol.Schema(
            {
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

        # Rooms can live in either ``entry.options`` (when configured via the
        # UI options flow) or ``entry.data`` (YAML / initial config flow).
        # Check both stores for room name resolution.
        opts = entry.options
        source_rooms = opts.get(CONF_ROOMS) if opts.get(CONF_ROOMS) else entry.data.get(CONF_ROOMS)
        rooms_list = source_rooms or []

        canonical_name: str | None = None
        for room_cfg in rooms_list:
            cfg_name = room_cfg.get(CONF_ROOM_NAME, "")
            if cfg_name == room_name or _slugify(cfg_name) == room_name:
                canonical_name = cfg_name
                break

        if canonical_name is None:
            raise ValueError(f"Room '{room_name}' not found in configuration")

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

    async def handle_set_room_comfort_offset(call: ServiceCall) -> None:
        """Set the default comfort-band half-width for a single room.

        Called from the dashboard climate cards so users can widen or narrow a
        room's comfort corridor without editing a schedule.  Resolves the
        canonical room name from the slug sent by the frontend, applies the
        change to the live model and persists it (mirroring set_room_setpoint).
        """
        from .dashboard import slugify as _slugify

        coordinator = _get_coordinator(hass)
        room_name: str = call.data["room_name"]
        comfort_offset: float = call.data["comfort_offset"]

        canonical_name: str | None = None
        for name in coordinator.model.room_names:
            if name == room_name or _slugify(name) == room_name:
                canonical_name = name
                break

        if canonical_name is None:
            raise ValueError(f"Room '{room_name}' not found in configuration")

        coordinator.set_room_comfort_offset(canonical_name, comfort_offset)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "set_room_comfort_offset",
        handle_set_room_comfort_offset,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("comfort_offset"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=5.0)
                ),
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

        # The write path is returned in the service response (``written_to``)
        # for programmatic callers; no persistent notification is raised.
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
        without re-running the computation, giving Lovelace button presses
        immediate feedback through the sensor state.

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
            return {
                "room": room_name,
                "error": "history_too_short_or_unknown_room",
            }

        # The computed grid is stored on the per-room ``…_loglik_slice`` sensor
        # (and returned here for callers using ``return_response: true``), so no
        # persistent notification is raised.
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

    # ------------------------------------------------------------------
    # System-identification experiments and stored datasets
    # ------------------------------------------------------------------

    def _resolve_room(coordinator: "HeatingAssistantCoordinator", name: str) -> str:
        """Return the canonical room name for a name-or-slug, or raise."""
        from .dashboard import slugify as _slugify

        for rn in coordinator.model.room_names:
            if rn == name or _slugify(rn) == name:
                return rn
        raise ValueError(f"Room '{name}' not found in configuration")

    async def handle_schedule_experiment(call: ServiceCall) -> ServiceResponse:
        """Schedule a system-identification experiment for one room/time window."""
        from .dashboard import slugify as _slugify
        from .experiments import Experiment, validate_signal_params
        from .const import (
            DEFAULT_EXCITATION_PERIOD_S,
            DEFAULT_EXCITATION_STEP_PCT,
            DEFAULT_EXCITATION_TYPE,
            DEFAULT_EXPERIMENT_MAX_TEMP,
            DEFAULT_EXPERIMENT_MIN_TEMP,
            DEFAULT_EXPERIMENT_SETTLE_S,
            MAX_EXPERIMENT_DURATION_S,
        )

        coordinator = _get_coordinator(hass)
        canonical = _resolve_room(coordinator, call.data["room_name"])

        start_ts = float(call.data["start"])
        end_ts = float(call.data["end"])
        if end_ts <= start_ts:
            raise ValueError("Experiment end must be after start")
        duration = end_ts - start_ts
        if duration > MAX_EXPERIMENT_DURATION_S:
            raise ValueError("Experiment duration exceeds the 7-day maximum")

        signal_type = str(call.data.get("signal_type", DEFAULT_EXCITATION_TYPE))
        step_pct = float(call.data.get("step_pct", DEFAULT_EXCITATION_STEP_PCT))
        period_s = float(call.data.get("period_s", DEFAULT_EXCITATION_PERIOD_S))
        validate_signal_params(signal_type, step_pct, period_s)

        # Settle / response buffer: excitation stops this long before the window
        # ends so the heater's influence is absorbed within the captured data.
        # Default to the configured buffer but never more than half the window so
        # a short experiment still gets meaningful excitation; an explicit value
        # must leave at least some excitation time.
        settle_raw = call.data.get("settle_s")
        if settle_raw is None:
            settle_s = min(DEFAULT_EXPERIMENT_SETTLE_S, duration / 2.0)
        else:
            settle_s = float(settle_raw)
            if settle_s < 0.0:
                raise ValueError("settle_s must be non-negative")
            if settle_s >= duration:
                raise ValueError("settle_s must be shorter than the experiment duration")

        exp = Experiment(
            room_name=canonical,
            room_slug=_slugify(canonical),
            start_ts=start_ts,
            end_ts=end_ts,
            name=str(call.data.get("name", "")),
            signal_type=signal_type,
            step_pct=step_pct,
            period_s=period_s,
            settle_s=settle_s,
            min_temp=float(call.data.get("min_temp", DEFAULT_EXPERIMENT_MIN_TEMP)),
            max_temp=float(call.data.get("max_temp", DEFAULT_EXPERIMENT_MAX_TEMP)),
            auto_save=bool(call.data.get("auto_save", True)),
        )
        coordinator.schedule_experiment(exp)
        coordinator.async_update_listeners()
        return {"experiment_id": exp.id}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCHEDULE_EXPERIMENT,
        handle_schedule_experiment,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("start"): vol.Coerce(float),
                vol.Required("end"): vol.Coerce(float),
                vol.Optional("name"): cv.string,
                vol.Optional("signal_type"): vol.In(list(EXCITATION_TYPES)),
                vol.Optional("step_pct"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("period_s"): vol.All(
                    vol.Coerce(float), vol.Range(min=60.0)
                ),
                vol.Optional("settle_s"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("min_temp"): vol.Coerce(float),
                vol.Optional("max_temp"): vol.Coerce(float),
                vol.Optional("auto_save"): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def handle_cancel_experiment(call: ServiceCall) -> None:
        """Cancel (or remove) a scheduled / running experiment."""
        coordinator = _get_coordinator(hass)
        coordinator.cancel_experiment(call.data["experiment_id"])
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_EXPERIMENT,
        handle_cancel_experiment,
        schema=vol.Schema({vol.Required("experiment_id"): cv.string}),
    )

    async def handle_delete_experiment(call: ServiceCall) -> None:
        """Delete an experiment outright, regardless of its status."""
        coordinator = _get_coordinator(hass)
        coordinator.delete_experiment(call.data["experiment_id"])
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_EXPERIMENT,
        handle_delete_experiment,
        schema=vol.Schema({vol.Required("experiment_id"): cv.string}),
    )

    async def handle_create_dataset(call: ServiceCall) -> ServiceResponse:
        """Snapshot a custom data window into a named, permanent dataset."""
        from .datasets import build_dataset
        from .history_window import select_window_by_timestamps
        from .dashboard import slugify as _slugify
        from .const import DATASET_SOURCE_MANUAL

        coordinator = _get_coordinator(hass)
        if coordinator.dataset_store is None:
            raise ValueError("Dataset store is not available")

        name = str(call.data["name"]).strip()
        if not name:
            raise ValueError("Dataset name must not be empty")
        window_start = float(call.data["window_start"])
        window_end = float(call.data["window_end"])
        if window_end <= window_start:
            raise ValueError("window_end must be after window_start")

        canonical: Optional[str] = None
        room_name = call.data.get("room_name")
        if room_name:
            canonical = _resolve_room(coordinator, room_name)
        room_slug = _slugify(canonical) if canonical else None

        # Pull the actual records for the window (JSONL store → Recorder
        # fallback), then clip to the exact bounds.
        records = await _get_history_for_window(
            hass, coordinator, window_start, window_end
        )
        records = select_window_by_timestamps(records, window_start, window_end)
        if not records:
            raise ValueError("No observation data found in the selected window")

        dataset = build_dataset(
            name,
            records,
            room_name=canonical,
            room_slug=room_slug,
            source=DATASET_SOURCE_MANUAL,
            notes=str(call.data.get("notes", "")),
            window_start=window_start,
            window_end=window_end,
        )
        await coordinator.dataset_store.async_add(dataset)
        coordinator.async_update_listeners()
        return {
            "dataset_id": dataset["id"],
            "record_count": dataset["record_count"],
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_DATASET,
        handle_create_dataset,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Required("window_start"): vol.Coerce(float),
                vol.Required("window_end"): vol.Coerce(float),
                vol.Optional("room_name"): cv.string,
                vol.Optional("notes"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def handle_delete_dataset(call: ServiceCall) -> None:
        """Delete a stored dataset by id."""
        coordinator = _get_coordinator(hass)
        if coordinator.dataset_store is None:
            return
        await coordinator.dataset_store.async_delete(call.data["dataset_id"])
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_DATASET,
        handle_delete_dataset,
        schema=vol.Schema({vol.Required("dataset_id"): cv.string}),
    )
