"""YAML configuration schema for the Heating Assistant integration."""

from __future__ import annotations

import voluptuous as vol

from .const import (
    ALL_SOURCE_TYPES,
    CONF_COMFORT_OFFSET,
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_C_SLAB_FRACTION,
    CONF_ENERGY_WEIGHT,
    CONF_FACADE_ABSORPTANCE,
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    CONF_INFILTRATION_FRACTION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MPC_MODE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_R_EXTERNAL,
    CONF_R_SA,
    CONF_R_SG,
    CONF_R_VALUE,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SCHEDULE,
    CONF_SCHEDULE_ALL_DAY,
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_END_DATE,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_RECURRING,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_START_DATE,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SETPOINT,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TURN_OFF_DEADBAND,
    CONF_SOURCE_TYPE,
    CONF_SKY_RADIATIVE_UA,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_THERMAL_MASS,
    CONF_TRACKING_WEIGHT,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_AREA,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_SENSORS,
    CONF_WINDOW_TILT,
    CONF_WINDOWS,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_EFFICIENCY,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_HORIZON,
    DEFAULT_IDENTIFICATION_HISTORY_DAYS,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_MPC_MODE,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_MIN_POWER,
    DEFAULT_PLOT_FORECAST_HOURS,
    DEFAULT_PLOT_HISTORY_HOURS,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    DEFAULT_THERMAL_MASS,
    DEFAULT_TRACKING_WEIGHT,
    DEFAULT_TURN_OFF_DEADBAND,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    DEFAULT_WINDOW_OPEN_DEBOUNCE,
    DEFAULT_WINDOW_OPEN_Q_INFLATION,
    DEFAULT_WINDOW_TILT,
    DOMAIN,
    FACADE_COLOUR_TO_ABSORPTANCE,
    FLOOR_TYPE_DEFAULTS,
    MPC_MODES,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SOLAR_EXPOSURE_TO_APERTURE,
)

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
        vol.Optional(CONF_SCHEDULE_FROST_PROTECTION): vol.Any(None, vol.Coerce(float)),
        # Per-period overrides written by the schedule editor.  Optional so a
        # period round-tripped through update_rooms (which carries the room's
        # full schedule) validates instead of being rejected as an extra key.
        vol.Optional(CONF_SCHEDULE_COMFORT_OFFSET): vol.Any(None, vol.Coerce(float)),
        vol.Optional(CONF_SCHEDULE_TRACKING_WEIGHT): vol.Any(None, vol.Coerce(float)),
        vol.Optional(CONF_SCHEDULE_ENERGY_WEIGHT): vol.Any(None, vol.Coerce(float)),
        vol.Optional(CONF_SCHEDULE_ALL_DAY, default=False): bool,
        vol.Optional(CONF_SCHEDULE_ENABLED, default=True): bool,
        vol.Optional(CONF_SCHEDULE_RECURRING, default=True): bool,
        vol.Optional(CONF_SCHEDULE_START_DATE): str,
        vol.Optional(CONF_SCHEDULE_END_DATE): str,
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
        vol.Required(CONF_SOURCE_TYPE): vol.In(list(ALL_SOURCE_TYPES)),
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
                    vol.Coerce(int), vol.Range(min=60)
                ),
                vol.Optional(CONF_HORIZON, default=DEFAULT_HORIZON): vol.All(
                    vol.Coerce(int), vol.Range(min=1)
                ),
                vol.Optional(CONF_MPC_MODE, default=DEFAULT_MPC_MODE): vol.In(
                    list(MPC_MODES)
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
                    vol.Coerce(float), vol.Range(min=1e-6)
                ),
                vol.Optional(CONF_SIGMA_V, default=DEFAULT_SIGMA_V): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6)
                ),
                vol.Optional(CONF_SIGMA_B, default=DEFAULT_SIGMA_B): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-8)
                ),
                vol.Optional(
                    CONF_WINDOW_OPEN_DEBOUNCE,
                    default=DEFAULT_WINDOW_OPEN_DEBOUNCE,
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_WINDOW_OPEN_CLOSE_SETTLE,
                    default=DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_WINDOW_OPEN_Q_INFLATION,
                    default=DEFAULT_WINDOW_OPEN_Q_INFLATION,
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0)),
                vol.Optional(
                    CONF_PLOT_HISTORY_HOURS,
                    default=DEFAULT_PLOT_HISTORY_HOURS,
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0)),
                vol.Optional(
                    CONF_PLOT_FORECAST_HOURS,
                    default=DEFAULT_PLOT_FORECAST_HOURS,
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_IDENTIFICATION_HISTORY_DAYS,
                    default=DEFAULT_IDENTIFICATION_HISTORY_DAYS,
                ): vol.All(vol.Coerce(int), vol.Range(min=7)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)
