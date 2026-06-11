"""
Data-update coordinator for the Heating Assistant integration.

The coordinator
- reads measured temperatures from HA sensor entities,
- reads the outdoor temperature from a configured sensor entity,
- computes solar gains from the solar model,
- runs the MPC controller to get optimal heat-source set-points,
- writes the set-points back to the HA heater entities (or stores them for
  the climate/sensor platforms to consume).
"""

from __future__ import annotations

import dataclasses
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_COMFORT_OFFSET,
    CONF_ENERGY_WEIGHT,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_PRICE_ENTITY,
    CONF_PRICE_NET_TARIFF,
    CONF_PRICE_SPOT_SURCHARGE,
    CONF_ESTIMATED_PARAMS,
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_TRACKING_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    CONF_SOLAR_SCALE,
    CONF_C_AIR_FRACTION,
    CONF_R_AW_FRACTION,
    CONF_GROUND_ALBEDO,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    DEFAULT_SOLAR_SCALE,
    DEFAULT_C_AIR_FRACTION,
    DEFAULT_R_AW_FRACTION,
    DEFAULT_GROUND_ALBEDO,
    SOLAR_EXPOSURE_TO_APERTURE,
    CONF_ROOMS,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_HVAC_MODE,
    DEFAULT_SOURCE_HVAC_MODE,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_TYPE,
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_C_SLAB_FRACTION,
    CONF_FACADE_ABSORPTANCE,
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_INFILTRATION_FRACTION,
    CONF_R_SA,
    CONF_R_SG,
    CONF_R_VALUE,
    CONF_R_EXTERNAL,
    CONF_ROOM_NAME,
    CONF_SCHEDULE,
    CONF_SKY_RADIATIVE_UA,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_SETPOINT,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_WINDOW_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_THERMAL_MASS,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_ENERGY_PRICE_WEIGHT,
    DEFAULT_PRICE_NET_TARIFF,
    DEFAULT_PRICE_SPOT_SURCHARGE,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_EFFICIENCY,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_HORIZON,
    DEFAULT_TRACKING_WEIGHT,
    DEFAULT_MIN_POWER,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    CONF_IDENTIFICATION_HORIZON_HOURS,
    DEFAULT_IDENTIFICATION_HORIZON_HOURS,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_IDLE_OFFSET,
    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    DEFAULT_WINDOW_OPEN_DEBOUNCE,
    DEFAULT_WINDOW_OPEN_Q_INFLATION,
    DEFAULT_FACADE_ABSORPTANCE,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    FACADE_COLOUR_TO_ABSORPTANCE,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_THERMAL_MASS,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DOMAIN,
    ESTIMATION_HISTORY_SIZE,
    HISTORY_BUFFER_SIZE,
    CLOUD_SMOOTHING_TAU_S,
    RUNTIME_STATE_SAVE_DELAY_S,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
    SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU,
    UPDATE_INTERVAL,
)
from .heat_sources import ElectricHeater, HeatPump, HeatSource
from .thermal_model import HouseModel, Room, RoomConnection, Window
from .controller import HeatingMPCController
from .ground_temp import ground_temperature
from .solar_model import (
    room_solar_gains,
    room_solar_gains_from_exposure,
    horizontal_irradiance,
)
from . import solar_forecast as _solar_fc
from .schedule import (
    EffectiveControlParams,
    EffectiveSetpoint,
    RoomSchedule,
    build_schedule,
    control_params_at,
    next_transition,
    resolve_effective_control_params,
    resolve_effective_setpoint,
)
from . import weather as _weather

_LOGGER = logging.getLogger(__name__)

# Number of consecutive coordinator cycles with no valid outdoor temperature
# before an UpdateFailed is raised (only applies when no prior reading was ever
# obtained — i.e., the entity was never reachable since startup).
# With the default 15-minute update interval this gives ~45 minutes of grace.
_OUTDOOR_TEMP_MAX_STARTUP_FAILURES = 3


@dataclasses.dataclass
class ControlTrajectory:
    """Per-step schedule-projected control parameters for the MPC horizon.

    All arrays have shape ``(N,)`` where N is the prediction horizon.
    Room-keyed dicts contain one array per configured room.
    """

    setpoints: Dict[str, "np.ndarray"]        # room → per-step setpoint [°C]
    comfort_offsets: Dict[str, "np.ndarray"]  # room → per-step corridor half-width [°C]
    q_scales: Dict[str, "np.ndarray"]         # room → per-step Q multiplier [–]
    r_scales: Dict[str, "np.ndarray"]         # room → per-step R multiplier [–]
    enabled_steps: Dict[str, "np.ndarray"]    # room → per-step enabled flag [bool]


def _coerce_interval_seconds(value: Any) -> float:
    """Return a numeric interval in seconds from int/float/str/timedelta values."""
    if isinstance(value, timedelta):
        return float(value.total_seconds())
    return float(value)




def _coerce_opt_float(value: Any) -> Optional[float]:
    """Return ``float(value)`` or ``None`` for missing/blank/unparsable input."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_house_model(rooms_cfg: List[Dict[str, Any]]) -> HouseModel:
    """
    Construct a :class:`HouseModel` from the YAML / config-entry rooms list.
    """
    rooms = []
    for rc in rooms_cfg:
        connections = [
            RoomConnection(
                connected_room=c[CONF_CONNECTED_ROOM],
                r_value=c[CONF_R_VALUE],
            )
            for c in rc.get(CONF_CONNECTIONS, [])
        ]
        windows = [
            Window(
                area=w[CONF_WINDOW_AREA],
                orientation=w[CONF_WINDOW_ORIENTATION],
                tilt=w.get(CONF_WINDOW_TILT, DEFAULT_WINDOW_TILT),
            )
            for w in rc.get(CONF_WINDOWS, [])
        ]
        rooms.append(
            Room(
                name=rc[CONF_ROOM_NAME],
                thermal_mass=rc.get(CONF_THERMAL_MASS, DEFAULT_THERMAL_MASS),
                r_external=rc.get(CONF_R_EXTERNAL, DEFAULT_R_EXTERNAL),
                connections=connections,
                windows=windows,
                setpoint=rc.get(CONF_SETPOINT, DEFAULT_SETPOINT),
                comfort_offset=rc.get(CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET),
                infiltration_fraction=rc.get(
                    CONF_INFILTRATION_FRACTION, DEFAULT_INFILTRATION_FRACTION,
                ),
                # Phase 1 A2: optional floor / slab parameters.
                # ``floor_type`` drives the typology defaults applied
                # inside ``Room.__init__`` for the three slab numerics
                # (``c_slab_fraction``, ``r_sa``, ``r_sg``); per-field
                # overrides take precedence over the typology defaults
                # when both are present.
                floor_type=rc.get(CONF_FLOOR_TYPE, DEFAULT_FLOOR_TYPE),
                c_slab_fraction=rc.get(CONF_C_SLAB_FRACTION),
                r_sa=rc.get(CONF_R_SA),
                r_sg=rc.get(CONF_R_SG),
                # Phase 1 C3 / C4 / C5 — finishing-pass envelope
                # corrections.  ``facade_colour`` resolves into
                # ``facade_absorptance`` via the colour preset map;
                # an explicit ``facade_absorptance`` always wins.
                # All three default off (zero) so existing installs
                # see no behaviour change.
                sky_radiative_ua=rc.get(
                    CONF_SKY_RADIATIVE_UA, DEFAULT_SKY_RADIATIVE_UA,
                ),
                facade_absorptance=rc.get(
                    CONF_FACADE_ABSORPTANCE,
                    FACADE_COLOUR_TO_ABSORPTANCE.get(
                        rc.get(CONF_FACADE_COLOUR, DEFAULT_FACADE_COLOUR),
                        DEFAULT_FACADE_ABSORPTANCE,
                    ),
                ),
                facade_solar_share=rc.get(
                    CONF_FACADE_SOLAR_SHARE, DEFAULT_FACADE_SOLAR_SHARE,
                ),
                thermal_bridge_psi_l=rc.get(
                    CONF_THERMAL_BRIDGE_PSI_L, DEFAULT_THERMAL_BRIDGE_PSI_L,
                ),
                # Optional per-room solar-exposure preset — the no-geometry
                # fallback used when the room has no enumerated windows.
                solar_exposure_aperture=SOLAR_EXPOSURE_TO_APERTURE.get(
                    rc.get(CONF_SOLAR_EXPOSURE, DEFAULT_SOLAR_EXPOSURE),
                    SOLAR_EXPOSURE_TO_APERTURE[DEFAULT_SOLAR_EXPOSURE],
                ),
                solar_facing=rc.get(CONF_SOLAR_FACING, DEFAULT_SOLAR_FACING),
                # Identified multiplicative correction on the modelled
                # solar gain (refined by the parameter estimator).
                solar_scale=rc.get(CONF_SOLAR_SCALE, DEFAULT_SOLAR_SCALE),
                # 2R2C envelope split fractions (typology defaults; the
                # parameter estimator refines them when identifiable).
                c_air_fraction=rc.get(
                    CONF_C_AIR_FRACTION, DEFAULT_C_AIR_FRACTION,
                ),
                r_aw_fraction=rc.get(
                    CONF_R_AW_FRACTION, DEFAULT_R_AW_FRACTION,
                ),
            )
        )
    return HouseModel(rooms)


def build_heat_sources(
    sources_cfg: List[Dict[str, Any]],
) -> List[HeatSource]:
    """
    Construct heat-source objects from the configuration list.
    """
    sources: List[HeatSource] = []
    for sc in sources_cfg:
        src_type = sc[CONF_SOURCE_TYPE]
        name = sc[CONF_SOURCE_NAME]
        room = sc[CONF_SOURCE_ROOM]
        max_power = sc[CONF_SOURCE_MAX_POWER]
        entity = sc.get(CONF_SOURCE_HEATER_ENTITY)
        # Phase 1 B2 emitter-filter time constant.  Per-source override
        # via ``emitter_time_constant``; otherwise the typology default
        # from ``SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU`` (electric → 0;
        # heat-pump → 60 s).  Users on hydronic radiators can override
        # with τ ≈ 600 s.
        tau_em = float(sc.get(
            CONF_SOURCE_EMITTER_TIME_CONSTANT,
            SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU.get(src_type, 0.0),
        ))

        if src_type == SOURCE_TYPE_ELECTRIC:
            sources.append(
                ElectricHeater(
                    name=name,
                    room=room,
                    max_power=max_power,
                    efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_EFFICIENCY),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type == SOURCE_TYPE_HEAT_PUMP:
            sources.append(
                HeatPump(
                    name=name,
                    room=room,
                    max_power=max_power,
                    cop_rated=sc.get(CONF_SOURCE_COP_RATED, DEFAULT_COP_RATED),
                    cop_temp_ref=sc.get(CONF_SOURCE_COP_TEMP_REF, DEFAULT_COP_TEMP_REF),
                    min_power=sc.get(CONF_SOURCE_MIN_POWER, DEFAULT_MIN_POWER),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    hvac_mode=sc.get(CONF_SOURCE_HVAC_MODE, DEFAULT_SOURCE_HVAC_MODE),
                    cooling_cop=sc.get(CONF_SOURCE_COOLING_COP, DEFAULT_COOLING_COP),
                    cooling_efficiency=sc.get(CONF_SOURCE_COOLING_EFFICIENCY, DEFAULT_COOLING_EFFICIENCY),
                    heating_efficiency=sc.get(CONF_SOURCE_HEATING_EFFICIENCY, DEFAULT_HEATING_EFFICIENCY),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        else:
            _LOGGER.warning("Unknown heat source type %r – skipping %r", src_type, name)
    return sources


class HeatingAssistantCoordinator(DataUpdateCoordinator):
    """
    Central coordinator that runs the MPC controller periodically and
    distributes results to the climate and sensor platforms.
    """

    #: Class-level default so partially-constructed instances (tests,
    #: legacy persistence paths) always have a ground albedo.
    _ground_albedo: float = DEFAULT_GROUND_ALBEDO

    _RELOAD_REQUIRED_CONFIG_KEYS: Set[str] = {
        CONF_ROOMS,
        CONF_HEAT_SOURCES,
        CONF_HORIZON,
        CONF_UPDATE_INTERVAL,
    }
    _PERSISTED_STATE_KEYS: Set[str] = {
        CONF_PERSISTED_SETPOINTS,
        CONF_PERSISTED_SCHEDULES,
        CONF_ESTIMATED_PARAMS,
    }
    _RUNTIME_RECONFIG_KEYS: Set[str] = {
        CONF_OUTDOOR_TEMP_ENTITY,
        CONF_WEATHER_ENTITY,
        CONF_SOLAR_RADIATION_ENTITY,
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_TRACKING_WEIGHT,
        CONF_ENERGY_WEIGHT,
        CONF_SMOOTHING_WEIGHT,
        CONF_SOFT_CONSTRAINT_WEIGHT,
        CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
        CONF_TERMINAL_WEIGHT,
        CONF_SIGMA_W,
        CONF_SIGMA_V,
        CONF_SIGMA_B,
        CONF_WINDOW_OPEN_DEBOUNCE,
        CONF_WINDOW_OPEN_CLOSE_SETTLE,
        CONF_WINDOW_OPEN_Q_INFLATION,
    }

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        self._entry = entry
        data = entry.data
        options = entry.options

        self._latitude: float = data.get(CONF_LATITUDE, hass.config.latitude)
        self._longitude: float = data.get(CONF_LONGITUDE, hass.config.longitude)
        self._outdoor_entity: Optional[str] = options.get(CONF_OUTDOOR_TEMP_ENTITY) or data.get(CONF_OUTDOOR_TEMP_ENTITY)
        self._weather_entity: Optional[str] = options.get(CONF_WEATHER_ENTITY) or data.get(CONF_WEATHER_ENTITY)
        self._solar_radiation_entity: Optional[str] = options.get(CONF_SOLAR_RADIATION_ENTITY) or data.get(CONF_SOLAR_RADIATION_ENTITY)
        # Site-level ground reflectance for the ground-reflected irradiance
        # term (grass ~0.2, snow ~0.7-0.8).
        self._ground_albedo: float = float(
            options.get(CONF_GROUND_ALBEDO,
                        data.get(CONF_GROUND_ALBEDO, DEFAULT_GROUND_ALBEDO))
        )
        self._price_entity: Optional[str] = options.get(CONF_PRICE_ENTITY) or data.get(CONF_PRICE_ENTITY)
        self._price_net_tariff: float = float(
            options.get(CONF_PRICE_NET_TARIFF, data.get(CONF_PRICE_NET_TARIFF, DEFAULT_PRICE_NET_TARIFF))
        )
        self._price_spot_surcharge: float = float(
            options.get(CONF_PRICE_SPOT_SURCHARGE, data.get(CONF_PRICE_SPOT_SURCHARGE, DEFAULT_PRICE_SPOT_SURCHARGE))
        )
        # The update interval drives how often the coordinator ticks, the EKF
        # measurement step, and the OCP ZOH step — all three must be equal for
        # the MPC predictions to match physical reality.  Options take precedence
        # over initial data so that the user can reconfigure via the UI without
        # re-creating the entry.  Falls back to DEFAULT_UPDATE_INTERVAL when absent.
        # Old config entries that stored a separate "dt" key are silently ignored;
        # the update_interval is the single source of truth.
        self._update_interval_s: int = int(
            _coerce_interval_seconds(
                options.get(CONF_UPDATE_INTERVAL)
                or data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            )
        )
        self._horizon: int = int(
            options.get(CONF_HORIZON)
            or data.get(CONF_HORIZON, DEFAULT_HORIZON)
        )
        # Tuning weights, like update_interval/horizon above, honour ``options``
        # first and fall back to initial ``data`` so values reconfigured at
        # runtime survive a reload.  ``_opt`` returns the options value when the
        # key is present (even if falsy, e.g. tracking_weight = 0.0), otherwise
        # the data value, otherwise the default.
        def _opt(key: str, default: Any) -> Any:
            if key in options:
                return options[key]
            return data.get(key, default)

        self._tracking_weight: float = float(_opt(CONF_TRACKING_WEIGHT, DEFAULT_TRACKING_WEIGHT))
        self._energy_weight: float = float(_opt(CONF_ENERGY_WEIGHT, DEFAULT_ENERGY_WEIGHT))
        self._energy_price_weight: float = float(
            _opt(CONF_ENERGY_PRICE_WEIGHT, DEFAULT_ENERGY_PRICE_WEIGHT)
        )
        self._smoothing_weight: float = float(_opt(CONF_SMOOTHING_WEIGHT, DEFAULT_SMOOTHING_WEIGHT))
        self._soft_constraint_weight: float = float(_opt(CONF_SOFT_CONSTRAINT_WEIGHT, DEFAULT_SOFT_CONSTRAINT_WEIGHT))
        self._soft_constraint_linear_weight: float = float(_opt(CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT, DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT))
        self._terminal_weight: float = float(_opt(CONF_TERMINAL_WEIGHT, DEFAULT_TERMINAL_WEIGHT))
        self._sigma_w: float = float(
            options.get(CONF_SIGMA_W, data.get(CONF_SIGMA_W, DEFAULT_SIGMA_W))
        )
        self._sigma_v: float = float(
            options.get(CONF_SIGMA_V, data.get(CONF_SIGMA_V, DEFAULT_SIGMA_V))
        )
        self._sigma_b: float = float(
            options.get(CONF_SIGMA_B, data.get(CONF_SIGMA_B, DEFAULT_SIGMA_B))
        )
        self._identification_horizon_hours: float = float(
            options.get(
                CONF_IDENTIFICATION_HORIZON_HOURS,
                data.get(CONF_IDENTIFICATION_HORIZON_HOURS, DEFAULT_IDENTIFICATION_HORIZON_HOURS),
            )
        )
        # Most-recently identified heater power-scales from an ML estimation run
        # (populated regardless of whether apply_params was True).  Keyed by
        # source name; value is the raw scale factor (dimensionless, 1.0 = 100%).
        self._last_identified_heater_scales: Dict[str, float] = {}
        self._window_open_debounce: float = float(
            options.get(
                CONF_WINDOW_OPEN_DEBOUNCE,
                data.get(CONF_WINDOW_OPEN_DEBOUNCE, DEFAULT_WINDOW_OPEN_DEBOUNCE),
            )
        )
        self._window_open_close_settle: float = float(
            options.get(
                CONF_WINDOW_OPEN_CLOSE_SETTLE,
                data.get(
                    CONF_WINDOW_OPEN_CLOSE_SETTLE,
                    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
                ),
            )
        )
        self._window_open_q_inflation: float = float(
            options.get(
                CONF_WINDOW_OPEN_Q_INFLATION,
                data.get(
                    CONF_WINDOW_OPEN_Q_INFLATION,
                    DEFAULT_WINDOW_OPEN_Q_INFLATION,
                ),
            )
        )
        self._last_runtime_config: Dict[str, Any] = {**dict(data), **dict(options)}
        self._pending_runtime_reconfiguration: Dict[str, Any] = {}

        # Prefer options over data for rooms so that edits made through the
        # "Configure" options flow (which writes to entry.options) are picked
        # up on restart.  Falls back to entry.data for fresh installs where
        # options has never been saved yet.
        rooms_cfg: List[Dict[str, Any]] = options.get(CONF_ROOMS) or data.get(CONF_ROOMS, [])
        sources_cfg: List[Dict[str, Any]] = data.get(CONF_HEAT_SOURCES, [])

        self.model: HouseModel = build_house_model(rooms_cfg)
        self.heat_sources: List[HeatSource] = build_heat_sources(sources_cfg)
        # Cache room → list[HeatSource] index so per-room sensors don't have to
        # filter the full source list on every attribute access.  Must be
        # rebuilt whenever ``self.heat_sources`` is reassigned (see
        # ``_rebuild_sources_by_room``).
        self._sources_by_room: Dict[str, List[HeatSource]] = {}
        self._rebuild_sources_by_room()

        # Single UTC "now" stamped at the start of each ``_async_update_data``
        # cycle and reused by every sensor that needs to anchor a forecast
        # trace to the current instant.  Initialised eagerly so sensors don't
        # have to handle a None case before the first cycle.
        self.now_utc: datetime = datetime.now(tz=timezone.utc)

        # Weather-forecast fetch health (consumed by the diagnostic
        # WeatherForecastStatusSensor).  ``weather_last_error`` is the last
        # exception message or ``"no_data"`` when the service returned an
        # empty payload; ``None`` means the most recent fetch succeeded.
        self.weather_last_error: Optional[str] = None
        self.weather_last_error_at: Optional[datetime] = None
        self.weather_last_success_at: Optional[datetime] = None
        self.weather_consecutive_failures: int = 0
        # Throttle WARN logs so a persistently-broken weather entity doesn't
        # flood the log.  We re-warn whenever the failure count crosses a
        # boundary (1, 2, 5, 10, 50, 100, …).
        self._weather_warn_thresholds: tuple = (1, 2, 5, 10, 50, 100, 500, 1000)

        # Solar-forecast fetch health + state (consumed by the diagnostic
        # SolarRadiationStatusSensor).  ``_solar_provider`` records which schema
        # the last successful parse matched; ``solar_source`` records whether
        # the forecast GHI or the analytical model drove the most recent
        # cycle's solar gains.
        self.solar_fc_last_error: Optional[str] = None
        self.solar_fc_last_error_at: Optional[datetime] = None
        self.solar_fc_last_success_at: Optional[datetime] = None
        self.solar_fc_consecutive_failures: int = 0
        self._solar_provider: str = "none"
        self.solar_source: str = "analytical"
        self.ghi_now: Optional[float] = None
        self.ghi_forecast: List[Optional[float]] = []
        # Effective GHI for display [W/m²]: the measured/forecast value when one
        # is available, else the clear-sky model attenuated by cloud cover. This
        # is always computable from the site location, so the overview's solar
        # irradiance KPI never goes blank just because no irradiance sensor is
        # configured.
        self.ghi_now_effective: Optional[float] = None

        # Restore persisted estimated parameters so that identified values
        # survive a full Home Assistant restart (not just an in-memory reload).
        # These are applied directly to the model objects before the MPC
        # controller is constructed so the controller starts with the correct
        # parameter values.
        self._estimation_timestamp: Optional[str] = None
        self._estimation_log_likelihood: Optional[float] = None
        # Rolling buffer of the last N estimation runs (most recent last).
        # Each entry mirrors the snapshot persisted in entry.data plus the
        # ``applied`` flag so dashboards can show a history of decisions.
        self._estimation_history: deque = deque(maxlen=ESTIMATION_HISTORY_SIZE)
        # Most recent log-likelihood slice per room, populated by
        # ``async_compute_loglik_slice`` and consumed by the matching
        # ``LoglikSliceSensor``.
        self._loglik_slices: Dict[str, Dict[str, Any]] = {}
        stored_est: Optional[Dict[str, Any]] = data.get(CONF_ESTIMATED_PARAMS)
        if stored_est:
            self._restore_estimated_parameters(stored_est)

        self._temp_sensors: Dict[str, List[str]] = self._build_temp_sensor_map(rooms_cfg)

        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

        self._init_room_state(rooms_cfg)
        self._init_runtime_buffers(hass)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._update_interval_s),
        )

    # ------------------------------------------------------------------
    # __init__ helpers (S4)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_temp_sensor_map(
        rooms_cfg: List[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        """Return ``{room_name: [entity_id, ...]}`` for temperature readouts.

        Supports both the singular ``temp_sensor`` and plural ``temp_sensors``
        keys; deduplicates while preserving the YAML-provided order.
        """
        mapping: Dict[str, List[str]] = {}
        for rc in rooms_cfg:
            room_name = rc[CONF_ROOM_NAME]
            sensors: List[str] = []
            if CONF_TEMP_SENSORS in rc:
                sensors.extend(rc[CONF_TEMP_SENSORS])
            if CONF_TEMP_SENSOR in rc:
                single = rc[CONF_TEMP_SENSOR]
                if single not in sensors:
                    sensors.append(single)
            if sensors:
                mapping[room_name] = sensors
        return mapping

    @staticmethod
    def _build_window_sensor_map(
        rooms_cfg: List[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        """Return ``{room_name: [binary_sensor_id, ...]}`` for window/door override."""
        mapping: Dict[str, List[str]] = {}
        for rc in rooms_cfg:
            room_name = rc[CONF_ROOM_NAME]
            sensors = [s for s in rc.get(CONF_WINDOW_SENSORS, []) if isinstance(s, str)]
            # Deduplicate while preserving order.
            deduped: List[str] = []
            for sensor_id in sensors:
                if sensor_id not in deduped:
                    deduped.append(sensor_id)
            if deduped:
                mapping[room_name] = deduped
        return mapping

    def _init_room_state(self, rooms_cfg: List[Dict[str, Any]]) -> None:
        """Initialise per-room flags and parsed comfort schedules.

        ``_room_enabled`` / ``_schedule_disabled`` decouple the user toggle
        from the schedule-imposed disable so the two concerns never clobber
        each other.  ``_base_setpoint`` is the value the climate UI restores
        to when no schedule period is active; ``_schedule_enabled`` lets the
        user suspend the schedule per room at runtime without losing the
        configured rules.
        """
        room_names = self.model.room_names
        # The controller starts STOPPED after every (re)start.  The user must
        # explicitly press START in the dashboard to engage the MPC backend.
        # State estimation and logging still run while stopped; only the MPC
        # optimisation and actuator commands are gated on this flag.
        self._system_enabled: bool = False
        self._room_enabled: Dict[str, bool] = {name: True for name in room_names}
        self._schedule_disabled: Dict[str, bool] = {name: False for name in room_names}

        self._room_schedule: Dict[str, RoomSchedule] = {}
        self._base_setpoint: Dict[str, float] = {}
        self._schedule_enabled: Dict[str, bool] = {}
        # Per-room default comfort_offset, used as fallback when no schedule
        # period overrides it and to seed the control trajectory builder.
        self._room_comfort_offset: Dict[str, float] = {}
        self._window_sensors: Dict[str, List[str]] = self._build_window_sensor_map(rooms_cfg)
        self._window_state: Dict[str, str] = {name: "closed" for name in room_names}
        self._window_state_since: Dict[str, datetime] = {}
        # Last applied effective control params per room — exposed for diagnostics.
        self._effective_setpoint: Dict[str, EffectiveControlParams] = {}
        for rc in rooms_cfg:
            room_name = rc[CONF_ROOM_NAME]
            self._room_schedule[room_name] = build_schedule(rc.get(CONF_SCHEDULE))
            self._base_setpoint[room_name] = float(
                rc.get(CONF_SETPOINT, DEFAULT_SETPOINT)
            )
            self._room_comfort_offset[room_name] = float(
                rc.get(CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET)
            )
            self._schedule_enabled[room_name] = True

        # Overlay with user-modified setpoints persisted across restarts.
        persisted: Dict[str, Any] = self._entry.data.get(CONF_PERSISTED_SETPOINTS, {})
        for room_name, value in persisted.items():
            if room_name in self._base_setpoint:
                self._base_setpoint[room_name] = float(value)
                self.model.rooms[room_name].setpoint = float(value)

        # Overlay with user-modified comfort offsets persisted across restarts.
        persisted_offsets: Dict[str, Any] = self._entry.data.get(
            CONF_PERSISTED_COMFORT_OFFSETS, {}
        )
        for room_name, value in persisted_offsets.items():
            if room_name in self._room_comfort_offset:
                self._room_comfort_offset[room_name] = float(value)
                self.model.rooms[room_name].comfort_offset = float(value)

        # Overlay with user-modified schedules persisted across restarts.
        persisted_schedules: Dict[str, Any] = self._entry.data.get(CONF_PERSISTED_SCHEDULES, {})
        for room_name, periods_raw in persisted_schedules.items():
            if room_name in self._room_schedule:
                self._room_schedule[room_name] = build_schedule(periods_raw)

    def _read_binary_sensor_on(self, entity_id: str) -> bool:
        """Return True when the given binary sensor is currently ``on``."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return str(state.state).lower() == "on"

    def _set_window_state(
        self,
        room_name: str,
        state: str,
        now_utc: datetime,
    ) -> None:
        """Set per-room window state and timestamp the transition."""
        self._window_state[room_name] = state
        self._window_state_since[room_name] = now_utc

    def _update_window_state_machine(self, now_utc: datetime) -> None:
        """Advance the per-room window state machine for Phase 3 W1."""
        for room_name in self.model.room_names:
            sensors = self._window_sensors.get(room_name, [])
            if not sensors:
                self._window_state[room_name] = "closed"
                self._window_state_since.pop(room_name, None)
                continue

            any_open = any(self._read_binary_sensor_on(entity_id) for entity_id in sensors)
            state = self._window_state.get(room_name, "closed")
            since = self._window_state_since.get(room_name, now_utc)
            elapsed = (now_utc - since).total_seconds()

            if state == "closed":
                if any_open:
                    self._set_window_state(room_name, "pending_open", now_utc)
                continue
            if state == "pending_open":
                if not any_open:
                    self._set_window_state(room_name, "closed", now_utc)
                elif elapsed >= self._window_open_debounce:
                    self._set_window_state(room_name, "open", now_utc)
                continue
            if state == "open":
                if not any_open:
                    self._set_window_state(room_name, "pending_closed", now_utc)
                continue
            if state == "pending_closed":
                if any_open:
                    self._set_window_state(room_name, "open", now_utc)
                elif elapsed >= self._window_open_close_settle:
                    self._set_window_state(room_name, "closed", now_utc)

    def get_window_state(self, room_name: str) -> str:
        """Return the current window override state for a room."""
        return self._window_state.get(room_name, "closed")

    def is_window_override_active(self, room_name: str) -> bool:
        """Return True while the room's heater must be held off for a window.

        The override is active for the whole period the heater is suppressed:
        from the moment the open-debounce expires (state ``open``) until the
        close-settle timer expires (leaving ``pending_closed`` for ``closed``).
        It is **not** active during ``pending_open`` — opening a window starts
        the debounce timer but the heater keeps running until it elapses — nor
        once the room returns to ``closed``.
        """
        return self.get_window_state(room_name) in ("open", "pending_closed")

    def _init_runtime_buffers(self, hass: HomeAssistant) -> None:
        """Initialise per-cycle and visualisation state.

        Everything here is updated each ``_async_update_data`` tick and read
        by sensor entities between ticks; the empty initial values are what
        the sensors see before the first cycle completes.

        ``hass`` is passed in explicitly because this runs *before*
        ``super().__init__`` (which is what assigns ``self.hass``), so the
        runtime store has to be built from the argument rather than the
        not-yet-set attribute.
        """
        # Latest control actions (source_name → fraction in [u_min, u_max]).
        # These are the actions actually commanded to the heaters: rooms under
        # an open-window override are clamped to 0 here.
        self.actions: Dict[str, float] = {}

        # Latest *unconstrained* MPC optimum (source_name → fraction), mirrored
        # from controller.mpc_actions each successful solve.  Unlike
        # ``self.actions`` these are not zeroed for window-override rooms, so
        # when a window-close settle timer expires between scheduled solves the
        # coordinator can bring the heater back online at the level the MPC was
        # planning all along.
        self._mpc_shadow_actions: Dict[str, float] = {}

        # Visualization data
        self.solar_gains: Dict[str, float] = {}
        # Current cloud-cover fraction in [0, 1], or None when unavailable;
        # used to attenuate the clear-sky solar model.
        self.cloud_cover: Optional[float] = None
        # Low-pass (EMA) state of the cloud cover.  The weather entity reports an
        # instantaneous value that can jump between cycles and is often
        # unavailable on the first cycle after a restart; the filtered value
        # keeps the solar attenuation continuous and is persisted across
        # restarts so the first post-restart cycle does not spike.
        self._cloud_cover_filtered: Optional[float] = None
        self._runtime_state_loaded: bool = False
        self._runtime_store: Store = Store(
            hass, version=1, key=f"{DOMAIN}_runtime_{self._entry.entry_id}"
        )
        self.outdoor_temp: Optional[float] = None
        # Last valid outdoor temperature reading.  When outdoor_temp is
        # transiently None (entity unavailable mid-run) this value is used as a
        # constant persistence forecast so MPC keeps running.  Reset to None
        # only on a full coordinator re-initialisation; never cleared mid-run.
        self._last_valid_outdoor_temp: Optional[float] = None
        # Counter for consecutive startup cycles where outdoor_temp is None
        # AND no prior valid reading exists.  Triggers UpdateFailed after
        # _OUTDOOR_TEMP_MAX_STARTUP_FAILURES cycles.  Reset to 0 on first
        # valid reading and never incremented again while persistence is used.
        self._outdoor_temp_startup_failures: int = 0
        # Latest outdoor wind speed [m/s] from the weather entity.  Used
        # by the Sherman–Grimsrud infiltration overlay (Phase 1 C1).
        # ``None`` until the first coordinator cycle / when the weather
        # entity does not expose wind data.
        self.wind_speed: Optional[float] = None
        # Latest ground temperature [°C] computed by the built-in
        # sinusoidal model (Phase 1 A2 — see ``ground_temp.py``).  Used
        # by the slab block of the per-room thermal model.  ``None``
        # until the first cycle; the controller has its own annual-
        # mean default until then.
        self.ground_temp: Optional[float] = None
        self.heat_flows: Dict[str, Dict[str, float]] = {}
        self.predictions: list = []
        self.linearised_predictions: list = []
        self.outdoor_forecast: List[float] = []
        self.solar_forecast: list = []
        self.heating_schedule: list = []
        # Per-room averaged raw measurements (populated each cycle from
        # configured temp_sensors); kept separate from filter output so the
        # visualisation can show measurement vs. estimate.
        self.measured_temperatures: Dict[str, float] = {}
        # Per-room Kalman-filtered state x̂⁺ after each compute(); cleared
        # when the MPC solver fails so sensor entities report ``unknown``.
        self.filtered_temperatures: Dict[str, float] = {}

        # Per-room EKF-reconstructed wall/mass-node temperatures [°C] and
        # their posterior stds [°C] — the 2R2C observability health signal
        # (the wall node is not measured, so a non-contracting std flags an
        # observability problem).
        self.wall_temperatures: Dict[str, float] = {}
        self.wall_temperature_stds: Dict[str, float] = {}

        # Per-room online internal-gain estimates [W] (nominal + Δĝ) after each
        # compute(); empty until the first successful solve / when it fails.
        self.estimated_internal_gains: Dict[str, float] = {}

        # Rooms that have received at least one valid temperature measurement
        # since the coordinator started.  Rooms absent from this set have
        # never had a sensor reading (e.g. HA entity still "unknown" during
        # startup) and must not influence the EKF initial state or actuate
        # heat until a real measurement arrives.
        self._rooms_ever_measured: set = set()

        # Rolling observation history for ML parameter estimation.
        # Each entry is a dict: {y, u, d_outdoor, d_solar, timestamp}.
        self._history_buffer: deque = deque(maxlen=HISTORY_BUFFER_SIZE)

        # Cache populated by run_open_loop_simulation service.
        # Keyed by room_name; each value is the per-room dict returned by
        # compute_open_loop_predictions (rmse, mae, simulation list).
        # OpenLoopRMSESensor reads from here instead of computing on the
        # event loop.
        self.open_loop_results: Dict[str, Any] = {}

        # Cache populated by run_sysid_simulation service.
        # Keyed by room_name; each value is the per-room dict returned by
        # sysid.run_sysid_simulation (simulation, rmse, mae, params).
        # SysIdSimulationSensor reads from here.
        self.sysid_results: Dict[str, Any] = {}

        # Schedule-projected per-step control parameters for the current
        # MPC horizon.  Populated each cycle by _compute_control_trajectory
        # and read by setpoint / constraint sensors to plot the time-varying
        # comfort corridor on the dashboard.  None before the first cycle or
        # when schedule computation fails.
        self._control_trajectory: Optional["ControlTrajectory"] = None

        # Electricity price forecast for the current horizon [currency/kWh].
        # None when no price entity is configured or the fetch fails.
        self.price_forecast: Optional[List[float]] = None

        # UNIX timestamp (seconds) of the last successful MPC solve.  Set inside
        # _async_update_data after controller.compute() succeeds and exposed via
        # the mpc_performance sensor so the dashboard countdown is anchored to
        # the actual internal MPC schedule — NOT the entity's HA last_updated,
        # which is also bumped by the fast UI refresh that runs between solves.
        self._last_mpc_run_ts: Optional[float] = None


    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def dt(self) -> float:
        """Return the OCP/EKF time step (= update interval) in seconds."""
        return _coerce_interval_seconds(self._update_interval_s)

    @property
    def update_interval_seconds(self) -> int:
        """Return the coordinator / EKF update period in seconds."""
        return self._update_interval_s

    @property
    def history_buffer(self) -> deque:
        """Return a view of the rolling observation history buffer."""
        return self._history_buffer

    # ------------------------------------------------------------------
    # Cloud-cover smoothing & runtime-state persistence
    # ------------------------------------------------------------------
    async def _ensure_runtime_state_loaded(self) -> None:
        """Load persisted smoothed runtime weather state once, on first use.

        Seeds the cloud-cover EMA from the value saved by the previous session
        so the first cycle after a restart attenuates the solar model instead of
        emitting an unattenuated clear-sky spike.
        """
        if self._runtime_state_loaded:
            return
        self._runtime_state_loaded = True
        try:
            data = await self._runtime_store.async_load()
        except Exception:  # pragma: no cover - defensive; never block a cycle
            data = None
        if isinstance(data, dict) and self._cloud_cover_filtered is None:
            cc = data.get("cloud_cover_filtered")
            if cc is not None:
                try:
                    self._cloud_cover_filtered = max(0.0, min(1.0, float(cc)))
                except (TypeError, ValueError):
                    pass

    def _smooth_cloud_cover(self, cc_obs: Optional[float]) -> Optional[float]:
        """Exponentially smooth the live cloud-cover observation.

        Clouds change gradually, so the instantaneous weather reading is
        low-pass filtered with a ``CLOUD_SMOOTHING_TAU_S`` time constant.  The
        EMA is seeded on the first valid observation (no startup transient).
        When the observation is missing (entity briefly unavailable) the last
        filtered value is held rather than reverting to an unattenuated model.
        """
        self._cloud_cover_filtered = _weather.smooth_cloud_cover_step(
            self._cloud_cover_filtered, cc_obs, self.dt, CLOUD_SMOOTHING_TAU_S
        )
        return self._cloud_cover_filtered

    def _save_runtime_state(self) -> None:
        """Persist the smoothed cloud cover (throttled) so it survives restarts."""
        try:
            self._runtime_store.async_delay_save(
                lambda: {"cloud_cover_filtered": self._cloud_cover_filtered},
                RUNTIME_STATE_SAVE_DELAY_S,
            )
        except Exception:  # pragma: no cover - defensive; never block a cycle
            pass

    @property
    def estimated_params_snapshot(self) -> Optional[Dict[str, Any]]:
        """Return the persisted estimation snapshot from the real entry.data.

        Returns ``None`` when no estimation has been persisted yet.  This
        property fetches the real ``ConfigEntry`` from HA so that it always
        reflects the latest written snapshot even after an in-session update.
        """
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            return real_entry.data.get(CONF_ESTIMATED_PARAMS)
        return None

    # ------------------------------------------------------------------
    # Estimation helpers
    # ------------------------------------------------------------------

    def _restore_estimated_parameters(self, snapshot: Dict[str, Any]) -> None:
        """Apply a persisted estimation snapshot to the in-memory model objects.

        Called from ``__init__`` before the MPC controller is built so the
        controller always starts with the most recently identified values.
        Note: ``self.hass`` is **not** available at this point — this method
        only modifies Python objects in memory.

        Two on-disk snapshot layouts are supported:

        * the flat layout (``{"rooms": ..., "sources": ..., "connections": ...}``)
          written by the ML estimation path, and
        * the nested layout (``{"active": {"rooms": ...}, "history": [...]}``)
          written by ``store_identified_parameters`` / ``revert_parameters``.

        New snapshots from the nested path also mirror the flat keys at the top
        level (see those methods), but older entries only carry the nested
        form, so unwrap ``active`` here when the flat ``rooms`` key is absent.
        """
        if "rooms" not in snapshot and isinstance(snapshot.get("active"), dict):
            snapshot = snapshot["active"]
        for room_name, params in snapshot.get("rooms", {}).items():
            if room_name not in self.model.rooms:
                continue
            room = self.model.rooms[room_name]
            if "thermal_mass" in params:
                room.thermal_mass = float(params["thermal_mass"])
            if "r_external" in params:
                room.r_external = float(params["r_external"])
            if "internal_gain" in params:
                room.internal_gain = float(params["internal_gain"])
            if "solar_scale" in params:
                room.solar_scale = float(params["solar_scale"])
            if "c_air_fraction" in params:
                room.c_air_fraction = float(params["c_air_fraction"])
            if "r_aw_fraction" in params:
                room.r_aw_fraction = float(params["r_aw_fraction"])

        for src_name, src_params in snapshot.get("sources", {}).items():
            for src in self.heat_sources:
                if src.name == src_name:
                    src.power_scale = float(src_params.get("power_scale", 1.0))

        for key, r_val in snapshot.get("connections", {}).items():
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            room_a, room_b = parts
            for name in (room_a, room_b):
                if name not in self.model.rooms:
                    continue
                other = room_b if name == room_a else room_a
                for conn in self.model.rooms[name].connections:
                    if conn.connected_room == other:
                        conn.r_value = float(r_val)

        # Rebuild state-space matrices to reflect the updated parameters.
        # Must call rebuild_derived_parameters() first so the cached per-room
        # arrays (_leakage_area, _B_sky_offset) are re-derived from the new
        # room.thermal_mass / room.r_external values before _build_matrices()
        # reads them.
        self.model.rebuild_derived_parameters()
        (
            self.model._C,
            self.model._A,
            self.model._B_ext,
        ) = self.model._build_matrices()

        self._estimation_timestamp = snapshot.get("estimated_at")
        self._estimation_log_likelihood = snapshot.get("log_likelihood")
        _LOGGER.info("Restored persisted estimated parameters from entry.data")

    def _rebuild_sources_by_room(self) -> None:
        """Refresh the room → heat-sources index after ``self.heat_sources``
        is (re)assigned.  Sensors read from this cache instead of filtering
        the full list on every attribute access.
        """
        index: Dict[str, List[HeatSource]] = {}
        for src in self.heat_sources:
            index.setdefault(src.room, []).append(src)
        self._sources_by_room = index

    def _build_controller(self) -> None:
        """Build or rebuild the MPC controller from current settings."""
        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

    def apply_runtime_reconfiguration(self, config: Dict[str, Any]) -> bool:
        """Queue non-structural config changes for the next regular tick.

        Returns ``True`` when the change can be applied in-place, ``False``
        when a full integration reload is required.
        """
        if not hasattr(self, "_last_runtime_config"):
            self._last_runtime_config = {}
        if not hasattr(self, "_pending_runtime_reconfiguration"):
            self._pending_runtime_reconfiguration = {}

        new_config = dict(config)
        changed_keys = {
            key
            for key in set(self._last_runtime_config) | set(new_config)
            if self._last_runtime_config.get(key) != new_config.get(key)
        }
        changed_keys -= self._PERSISTED_STATE_KEYS
        self._last_runtime_config = new_config

        if not changed_keys:
            return True

        if changed_keys & self._RELOAD_REQUIRED_CONFIG_KEYS:
            return False

        for key in changed_keys & self._RUNTIME_RECONFIG_KEYS:
            self._pending_runtime_reconfiguration[key] = new_config.get(key)
        return True

    def _apply_pending_runtime_reconfiguration(self) -> None:
        """Apply queued runtime config updates at the start of a normal cycle."""
        pending_state = getattr(self, "_pending_runtime_reconfiguration", None)
        if not pending_state:
            return

        pending = dict(pending_state)
        self._pending_runtime_reconfiguration.clear()
        rebuild_controller = False

        if CONF_OUTDOOR_TEMP_ENTITY in pending:
            self._outdoor_entity = str(pending.get(CONF_OUTDOOR_TEMP_ENTITY, ""))
        if CONF_WEATHER_ENTITY in pending:
            self._weather_entity = str(pending.get(CONF_WEATHER_ENTITY, ""))
        if CONF_SOLAR_RADIATION_ENTITY in pending:
            self._solar_radiation_entity = str(pending.get(CONF_SOLAR_RADIATION_ENTITY, "")) or None
        if CONF_LATITUDE in pending:
            self._latitude = float(pending.get(CONF_LATITUDE, self._latitude))
        if CONF_LONGITUDE in pending:
            self._longitude = float(pending.get(CONF_LONGITUDE, self._longitude))
        if CONF_TRACKING_WEIGHT in pending:
            self._tracking_weight = float(
                pending.get(CONF_TRACKING_WEIGHT, self._tracking_weight)
            )
            rebuild_controller = True
        if CONF_SOFT_CONSTRAINT_WEIGHT in pending:
            self._soft_constraint_weight = float(
                pending.get(CONF_SOFT_CONSTRAINT_WEIGHT, self._soft_constraint_weight)
            )
            rebuild_controller = True
        if CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT in pending:
            self._soft_constraint_linear_weight = float(
                pending.get(CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT, self._soft_constraint_linear_weight)
            )
            rebuild_controller = True
        if CONF_ENERGY_WEIGHT in pending:
            self._energy_weight = float(
                pending.get(CONF_ENERGY_WEIGHT, self._energy_weight)
            )
            rebuild_controller = True
        if CONF_SMOOTHING_WEIGHT in pending:
            self._smoothing_weight = float(
                pending.get(CONF_SMOOTHING_WEIGHT, self._smoothing_weight)
            )
            rebuild_controller = True
        if CONF_TERMINAL_WEIGHT in pending:
            self._terminal_weight = float(
                pending.get(CONF_TERMINAL_WEIGHT, self._terminal_weight)
            )
            rebuild_controller = True
        if CONF_SIGMA_W in pending:
            self._sigma_w = float(pending.get(CONF_SIGMA_W, self._sigma_w))
            rebuild_controller = True
        if CONF_SIGMA_V in pending:
            self._sigma_v = float(pending.get(CONF_SIGMA_V, self._sigma_v))
            rebuild_controller = True
        if CONF_SIGMA_B in pending:
            self._sigma_b = float(pending.get(CONF_SIGMA_B, self._sigma_b))
            rebuild_controller = True
        if CONF_IDENTIFICATION_HORIZON_HOURS in pending:
            self._identification_horizon_hours = float(
                pending.get(CONF_IDENTIFICATION_HORIZON_HOURS, self._identification_horizon_hours)
            )
        if CONF_WINDOW_OPEN_DEBOUNCE in pending:
            self._window_open_debounce = float(
                pending.get(CONF_WINDOW_OPEN_DEBOUNCE, self._window_open_debounce)
            )
        if CONF_WINDOW_OPEN_CLOSE_SETTLE in pending:
            self._window_open_close_settle = float(
                pending.get(
                    CONF_WINDOW_OPEN_CLOSE_SETTLE, self._window_open_close_settle,
                )
            )
        if CONF_WINDOW_OPEN_Q_INFLATION in pending:
            self._window_open_q_inflation = float(
                pending.get(
                    CONF_WINDOW_OPEN_Q_INFLATION, self._window_open_q_inflation,
                )
            )
        if CONF_HORIZON in pending:
            self._horizon = int(pending.get(CONF_HORIZON, self._horizon))
            rebuild_controller = True
        if CONF_ENERGY_PRICE_WEIGHT in pending:
            self._energy_price_weight = float(
                pending.get(CONF_ENERGY_PRICE_WEIGHT, self._energy_price_weight)
            )
            rebuild_controller = True
        if CONF_UPDATE_INTERVAL in pending:
            self._update_interval_s = int(
                pending.get(CONF_UPDATE_INTERVAL, self._update_interval_s)
            )
            self.update_interval = timedelta(seconds=self._update_interval_s)
        if CONF_COMFORT_OFFSET in pending:
            new_offset = float(pending.get(CONF_COMFORT_OFFSET, 2.0))
            for room_name in self._room_comfort_offset:
                self._room_comfort_offset[room_name] = new_offset
            rebuild_controller = True

        if rebuild_controller:
            self._build_controller()

    def apply_tuning_updates(self, updates: Dict[str, Any]) -> None:
        """Apply tuning parameter changes immediately (called from services)."""
        for key, value in updates.items():
            self._pending_runtime_reconfiguration[key] = value
        self._apply_pending_runtime_reconfiguration()

    def sources_for_room(self, room_name: str) -> List[HeatSource]:
        """Return the cached list of heat sources for ``room_name`` (empty if
        none), without copying.  Sensors should not mutate the returned list.
        """
        return self._sources_by_room.get(room_name, [])

    def build_forecast_payload(
        self, room_names: Optional[List[str]] = None
    ) -> dict:
        """Build the full forecast payload for WebSocket delivery.

        Returns per-room trajectory/forecast arrays plus the outdoor and price
        forecasts.  This is the authoritative source used by the custom
        ``heating_assistant/get_forecasts`` WS command; sensor attributes only
        keep lightweight metadata to stay under HA Recorder's 16 KB limit.
        """
        from .dashboard import slugify as _slugify

        if room_names is None:
            room_names = self.model.room_names

        now = getattr(self, "now_utc", None) or datetime.now(tz=timezone.utc)
        dt = self.dt
        predictions = self.predictions
        outdoor_forecast = self.outdoor_forecast
        solar_forecast = self.solar_forecast
        heating_schedule = self.heating_schedule
        linearised_predictions = self.linearised_predictions
        price_forecast = self.price_forecast or []
        traj = getattr(self, "_control_trajectory", None)

        rooms_payload: dict = {}
        for room_name in room_names:
            room = self.model.rooms[room_name]
            step_sp = traj.setpoints.get(room_name) if traj is not None else None
            step_off = traj.comfort_offsets.get(room_name) if traj is not None else None
            step_enabled = traj.enabled_steps.get(room_name) if traj is not None else None
            comfort_offset = float(getattr(room, "comfort_offset", 2.0))

            current_heating = sum(
                getattr(s, "current_power", 0.0)
                for s in self.sources_for_room(room_name)
            )
            current_solar = self.solar_gains.get(room_name, 0.0)
            filtered_now = self.filtered_temperatures.get(room_name)
            now_temp = (
                round(float(filtered_now), 2)
                if filtered_now is not None
                else round(room.temperature, 2)
            )
            now_sp = round(float(room.setpoint), 2)
            now_enabled = self.is_room_enabled(room_name)

            forecast: List[Dict[str, Any]] = [{
                "time": now.isoformat(),
                "temperature": now_temp,
                "heating_power": round(current_heating, 1),
                "solar_gain": round(current_solar, 1),
                "outdoor_temp": (
                    None if self.outdoor_temp is None
                    else round(self.outdoor_temp, 2)
                ),
                "setpoint": now_sp if now_enabled else None,
                "constraint_upper": round(now_sp + comfort_offset, 2) if now_enabled else None,
                "constraint_lower": round(now_sp - comfort_offset, 2) if now_enabled else None,
                "enabled": now_enabled,
            }]

            trajectory: List[float] = []
            n_sched = len(heating_schedule)
            n_solar = len(solar_forecast)
            n_outdoor = len(outdoor_forecast)
            n_lin = len(linearised_predictions)
            for i, pred in enumerate(predictions):
                temp = pred.get(room_name)
                step_time = now + timedelta(seconds=dt * (i + 1))
                have_traj = (
                    step_sp is not None and step_off is not None
                    and i < len(step_sp) and i < len(step_off)
                )
                have_enabled = (step_enabled is not None and i < len(step_enabled))
                step_on = bool(step_enabled[i]) if have_enabled else now_enabled
                s = (
                    round(float(step_sp[i]), 2) if have_traj
                    else round(float(room.setpoint), 2)
                )
                o = float(step_off[i]) if have_traj else comfort_offset
                entry: Dict[str, Any] = {
                    "time": step_time.isoformat(),
                    "setpoint": s if step_on else None,
                    "constraint_upper": round(s + o, 2) if step_on else None,
                    "constraint_lower": round(s - o, 2) if step_on else None,
                    "enabled": step_on,
                }
                if temp is not None:
                    temp_r = round(temp, 2)
                    trajectory.append(temp_r)
                    entry["temperature"] = temp_r
                if i < n_sched:
                    entry["heating_power"] = round(
                        heating_schedule[i].get(room_name, 0.0), 1
                    )
                if i < n_solar:
                    entry["solar_gain"] = round(
                        solar_forecast[i].get(room_name, 0.0), 1
                    )
                if i < n_outdoor:
                    entry["outdoor_temp"] = round(outdoor_forecast[i], 2)
                if i < n_lin:
                    lin_temp = linearised_predictions[i].get(room_name)
                    if lin_temp is not None:
                        entry["linearised_temperature"] = round(lin_temp, 2)
                forecast.append(entry)

            # Heating and cooling bounds are asymmetric: a heat pump's cooling
            # capacity is derived from its electrical input × cooling COP, which
            # differs from the rated thermal heating output.  Expose both so the
            # power chart can draw the true (non-symmetric) corridor.
            room_sources = self.sources_for_room(room_name)
            max_power = sum(s.max_power for s in room_sources)
            outdoor_now = self.outdoor_temp if self.outdoor_temp is not None else 0.0
            max_cooling_power = sum(
                -s.cooling_power(outdoor_now)
                for s in room_sources
                if getattr(s, "can_cool", False) and hasattr(s, "cooling_power")
            )

            rooms_payload[_slugify(room_name)] = {
                "trajectory": trajectory,
                "forecast": forecast,
                "setpoint": now_sp,
                "comfort_offset": comfort_offset,
                "horizon_steps": len(predictions),
                "step_seconds": dt,
                "horizon_minutes": round(len(predictions) * dt / 60, 1),
                "max_power": max_power if max_power > 0 else None,
                "max_cooling_power": max_cooling_power if max_cooling_power > 0 else None,
            }

        # Outdoor forecast
        outdoor_data: List[Dict[str, Any]] = [{
            "time": now.isoformat(),
            "outdoor_temp": (
                None if self.outdoor_temp is None
                else round(self.outdoor_temp, 2)
            ),
        }]
        for i, temp in enumerate(outdoor_forecast):
            step_time = now + timedelta(seconds=dt * (i + 1))
            outdoor_data.append({
                "time": step_time.isoformat(),
                "outdoor_temp": round(temp, 2),
            })

        # Price forecast
        price_data: List[Dict[str, Any]] = []
        for i, price in enumerate(price_forecast):
            step_time = now + timedelta(seconds=dt * i)
            try:
                price_data.append({
                    "time": step_time.isoformat(),
                    "price": round(float(price), 5),
                })
            except (TypeError, ValueError):
                pass

        return {
            "rooms": rooms_payload,
            "outdoor_forecast": outdoor_data,
            "price_forecast": price_data,
            "step_seconds": dt,
            "horizon_steps": len(predictions),
        }

    def reset_estimated_parameters(self) -> None:
        """Discard persisted estimation and revert the live model to the
        configured (YAML / config-entry default) parameter values.

        The ``CONF_ESTIMATED_PARAMS`` key is removed from ``entry.data`` so
        subsequent restarts also use the original values.  The MPC controller
        is rebuilt immediately.
        """
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            new_data = {
                k: v for k, v in real_entry.data.items()
                if k != CONF_ESTIMATED_PARAMS
            }
            self.hass.config_entries.async_update_entry(real_entry, data=new_data)

        # Rebuild the model from the original YAML / config values (these are
        # stored in _entry.data independent of any in-memory estimation).
        rooms_cfg: List[Dict[str, Any]] = self._entry.data.get(CONF_ROOMS, [])
        sources_cfg: List[Dict[str, Any]] = self._entry.data.get(CONF_HEAT_SOURCES, [])
        self.model = build_house_model(rooms_cfg)
        self.heat_sources = build_heat_sources(sources_cfg)

        # Restore user-controlled setpoints so that "Reset to Defaults"
        # only affects estimated thermal parameters (thermal_mass,
        # r_external) and not the user's chosen comfort setpoints.
        for _room_name, _sp in self._base_setpoint.items():
            if _room_name in self.model.rooms:
                self.model.rooms[_room_name].setpoint = _sp
        self._rebuild_sources_by_room()

        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

        self._estimation_timestamp = None
        self._estimation_log_likelihood = None
        _LOGGER.info("Estimated parameters reset to configured defaults")

    def apply_manual_parameters(
        self,
        room_name: str,
        thermal_mass: float,
        r_external: float,
    ) -> None:
        """Apply manually tuned parameters for a single room.

        Delegates to ``store_identified_parameters`` so that all parameter
        applications go through the history mechanism.
        """
        self.store_identified_parameters(
            room_name, thermal_mass, r_external, source="manual"
        )

    def apply_heater_scales(
        self,
        heater_scales: Dict[str, float],
    ) -> None:
        """Apply heater power-scale factors to heat sources and rebuild MPC.

        Parameters
        ----------
        heater_scales:
            Mapping of source name → dimensionless scale factor (1.0 = 100%).
            Sources not present in the dict are left unchanged.
        """
        if not heater_scales:
            return

        applied: Dict[str, float] = {}
        for src in self.heat_sources:
            if src.name in heater_scales:
                src.power_scale = float(heater_scales[src.name])
                applied[src.name] = src.power_scale

        if not applied:
            return

        # Rebuild MPC controller so the new scales feed into the QP.
        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

        # Persist the updated power scales in the estimated-params snapshot so
        # they survive a restart.
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            snap = dict(self.estimated_params_snapshot or {})
            snap["sources"] = {
                src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
                for src in self.heat_sources
            }
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snap},
            )

        _LOGGER.info("Applied heater power scales: %s", applied)

    def store_identified_parameters(
        self,
        room_name: str,
        thermal_mass: float,
        r_external: float,
        source: str = "manual",
        rmse: Optional[float] = None,
        internal_gain: Optional[float] = None,
        solar_scale: Optional[float] = None,
        c_air_fraction: Optional[float] = None,
        r_aw_fraction: Optional[float] = None,
        heater_scales: Optional[Dict[str, float]] = None,
    ) -> None:
        """Store and apply identified parameters with history tracking.

        Applies the full set of per-room thermal-model parameters — not just
        ``thermal_mass`` / ``r_external`` but also the internal gain, solar
        scale and 2R2C envelope split fractions — plus the per-source heater
        power scales.  Each optional argument is applied only when provided so
        callers can update a subset.

        1. Get current active params as a history entry.
        2. Push current active to history list (prepend).
        3. Cap history at 10 entries.
        4. Set new params as active.
        5. Apply to the model (room parameters + heater scales).
        6. Persist via ``async_update_entry``.
        7. Rebuild MPC controller.
        """
        if room_name not in self.model.rooms:
            raise ValueError(f"Room '{room_name}' not found in model")

        now_iso = datetime.now(timezone.utc).isoformat()

        # --- Read existing persisted structure ---
        existing_snap = {}
        try:
            existing_snap = self.estimated_params_snapshot or {}
        except Exception:
            pass

        # Migrate old format (flat snapshot) to new format with active/history
        if "active" not in existing_snap:
            # Old format: { "rooms": {...}, "estimated_at": ... }
            old_active = {
                "rooms": existing_snap.get("rooms", {}),
                "estimated_at": existing_snap.get("estimated_at", now_iso),
                "source": "manual",
            }
            history: list = []
        else:
            old_active = dict(existing_snap["active"])
            history = list(existing_snap.get("history", []))

        # Push current active to history (prepend)
        if old_active.get("rooms"):
            history.insert(0, old_active)
            # Cap history at 10 entries
            history = history[:10]

        # --- Apply to in-memory model ---
        room = self.model.rooms[room_name]
        room.thermal_mass = float(thermal_mass)
        room.r_external = float(r_external)
        if internal_gain is not None:
            room.internal_gain = float(internal_gain)
        if solar_scale is not None:
            room.solar_scale = float(solar_scale)
        if c_air_fraction is not None:
            room.c_air_fraction = float(c_air_fraction)
        if r_aw_fraction is not None:
            room.r_aw_fraction = float(r_aw_fraction)

        # Apply per-source heater power scales (only sources that were supplied).
        if heater_scales:
            for src in self.heat_sources:
                if src.name in heater_scales:
                    src.power_scale = float(heater_scales[src.name])

        self.model.rebuild_derived_parameters()
        (
            self.model._C,
            self.model._A,
            self.model._B_ext,
        ) = self.model._build_matrices()

        # Rebuild MPC controller
        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

        # --- Build new active snapshot ---
        # Persist the full per-room parameter set (not just C / R_ext) so the
        # restart-time restore re-applies the identified envelope split, solar
        # scale and internal gain rather than reverting them to defaults.
        new_active: Dict[str, Any] = {
            "rooms": {
                name: {
                    "thermal_mass": r.thermal_mass,
                    "r_external": r.r_external,
                    "internal_gain": float(getattr(r, "internal_gain", 0.0)),
                    "solar_scale": float(getattr(r, "solar_scale", 1.0)),
                    "c_air_fraction": float(getattr(r, "c_air_fraction", 0.05)),
                    "r_aw_fraction": float(getattr(r, "r_aw_fraction", 0.05)),
                }
                for name, r in self.model.rooms.items()
            },
            "estimated_at": now_iso,
            "source": source,
        }
        if rmse is not None:
            new_active["rmse"] = rmse

        self._estimation_timestamp = now_iso

        # --- Persist with new structure ---
        # The ``active``/``history`` layout drives the dashboard's revert
        # history.  We also mirror the flat ``rooms``/``sources``/``connections``
        # keys at the top level because ``_restore_estimated_parameters`` (run on
        # restart/reload) and the dashboard sensors read that flat layout — without
        # the mirror, manually-stored parameters were never restored and silently
        # reverted to the configured room defaults.
        snapshot: Dict[str, Any] = {
            "active": new_active,
            "history": history,
            "rooms": new_active["rooms"],
            "sources": {
                src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
                for src in self.heat_sources
            },
            "connections": {},
            "estimated_at": now_iso,
            "log_likelihood": None,
        }

        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snapshot},
            )
        _LOGGER.info(
            "Stored identified parameters for room '%s': thermal_mass=%.0f J/K, "
            "r_external=%.5f K/W (source=%s)",
            room_name,
            thermal_mass,
            r_external,
            source,
        )

    def revert_parameters(self, room_name: str, history_index: int) -> None:
        """Revert to a previous parameter set from history.

        1. Get the history entry at the given index.
        2. Make it the new active.
        3. Push the current active to history.
        4. Apply to model + rebuild MPC.
        """
        if room_name not in self.model.rooms:
            raise ValueError(f"Room '{room_name}' not found in model")

        existing_snap = {}
        try:
            existing_snap = self.estimated_params_snapshot or {}
        except Exception:
            pass

        # Migrate old format if needed
        if "active" not in existing_snap:
            raise ValueError("No parameter history available (old format)")

        current_active = dict(existing_snap["active"])
        history = list(existing_snap.get("history", []))

        if history_index < 0 or history_index >= len(history):
            raise ValueError(
                f"history_index {history_index} out of range "
                f"(0..{len(history) - 1})"
            )

        # Pop the target entry from history
        target_entry = history.pop(history_index)

        # Push current active to history (prepend)
        history.insert(0, current_active)
        # Cap history at 10 entries
        history = history[:10]

        # Apply target parameters to in-memory model
        target_rooms = target_entry.get("rooms", {})
        if room_name in target_rooms:
            params = target_rooms[room_name]
            room = self.model.rooms[room_name]
            room.thermal_mass = float(params["thermal_mass"])
            room.r_external = float(params["r_external"])
        else:
            raise ValueError(
                f"Room '{room_name}' not found in history entry"
            )

        self.model.rebuild_derived_parameters()
        (
            self.model._C,
            self.model._A,
            self.model._B_ext,
        ) = self.model._build_matrices()

        # Rebuild MPC controller
        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

        # Build the new active reflecting actual model state
        now_iso = datetime.now(timezone.utc).isoformat()
        new_active: Dict[str, Any] = {
            "rooms": {
                name: {
                    "thermal_mass": r.thermal_mass,
                    "r_external": r.r_external,
                    "internal_gain": float(getattr(r, "internal_gain", 0.0)),
                }
                for name, r in self.model.rooms.items()
            },
            "estimated_at": now_iso,
            "source": target_entry.get("source", "reverted"),
        }
        self._estimation_timestamp = now_iso

        # Persist.  Mirror the flat rooms/sources/connections keys at the top
        # level so the restart-time restore and the dashboard sensors (which read
        # the flat layout) see the reverted values — see store_identified_parameters.
        snapshot: Dict[str, Any] = {
            "active": new_active,
            "history": history,
            "rooms": new_active["rooms"],
            "sources": {
                src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
                for src in self.heat_sources
            },
            "connections": {},
            "estimated_at": now_iso,
            "log_likelihood": None,
        }
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snapshot},
            )
        _LOGGER.info(
            "Reverted parameters for room '%s' to history index %d",
            room_name,
            history_index,
        )

    def reload_room_schedule(self, room_name: str, periods_raw: list) -> None:
        """Rebuild a single room's schedule from raw period definitions.

        Called by the ``update_room_schedule`` service after the config entry
        has been persisted with the new periods list.
        """
        new_schedule = build_schedule(periods_raw)
        self._room_schedule[room_name] = new_schedule

    async def _async_update_data(self) -> Dict[str, Any]:
        """
        Called by HA periodically.  Reads sensors, runs the controller,
        and returns a snapshot of the system state.
        """
        try:
            # 0. Stamp a single UTC "now" for this cycle so every sensor that
            #    anchors a forecast trace to the current instant uses the same
            #    timestamp (and avoids redundant ``datetime.now()`` calls in
            #    each sensor's ``extra_state_attributes`` getter).
            self.now_utc = datetime.now(tz=timezone.utc)
            self._apply_pending_runtime_reconfiguration()

            # 1. Update measured room temperatures from HA sensor states.
            #    When multiple sensors are configured for a room, use the
            #    average of all valid readings.
            self.measured_temperatures = {}
            for room_name, entity_ids in self._temp_sensors.items():
                readings: List[float] = []
                for entity_id in entity_ids:
                    state = self.hass.states.get(entity_id)
                    if state and state.state not in ("unknown", "unavailable"):
                        try:
                            readings.append(float(state.state))
                        except (ValueError, TypeError):
                            _LOGGER.warning(
                                "Cannot parse temperature from entity %s: %r",
                                entity_id,
                                state.state,
                            )
                if readings:
                    averaged = sum(readings) / len(readings)
                    self.model.rooms[room_name].temperature = averaged
                    self.measured_temperatures[room_name] = averaged

            # Update the set of rooms that have ever had a valid reading.
            self._rooms_ever_measured.update(self.measured_temperatures.keys())

            # Rooms with sensors configured but no reading yet (typically
            # during HA startup before entities leave "unknown" state) must
            # not drive heating or seed the EKF with the 20 °C model default.
            rooms_not_ready: set = {
                name for name in self._temp_sensors
                if name not in self._rooms_ever_measured
            }
            if rooms_not_ready:
                _LOGGER.debug(
                    "Waiting for first valid temperature reading for: %s",
                    ", ".join(sorted(rooms_not_ready)),
                )

            # 1b. Apply comfort schedules: resolve the active period for each
            #     room and update the live setpoint / enabled flag accordingly.
            #     Done after measurements are read so frost-protection logic
            #     sees the current temperature.
            now_local = datetime.now()
            try:
                self._apply_schedule(now_local)
                control_traj = self._compute_control_trajectory(
                    now_local, self._horizon, float(self.dt)
                )
                self._control_trajectory = control_traj
            except Exception:
                _LOGGER.warning(
                    "Failed to apply schedule or compute control trajectory; "
                    "falling back to static control for this cycle",
                    exc_info=True,
                )
                control_traj = None
                self._control_trajectory = None
            self._update_window_state_machine(self.now_utc)

            # 2. Read outdoor temperature
            outdoor_temp = self._read_outdoor_temp()
            self.outdoor_temp = outdoor_temp  # sensor reports None = "unknown"

            if outdoor_temp is not None:
                # Fresh valid reading — update persistence cache and reset counter.
                self._last_valid_outdoor_temp = outdoor_temp
                self._outdoor_temp_startup_failures = 0
            elif self._last_valid_outdoor_temp is not None:
                # Transient failure mid-run: persist the last valid reading so
                # MPC continues without interruption.  The sensor entity will
                # report "unknown" while self.outdoor_temp is None.
                _LOGGER.debug(
                    "Outdoor temperature entity unavailable; "
                    "persisting last known value %.1f °C for MPC",
                    self._last_valid_outdoor_temp,
                )
                outdoor_temp = self._last_valid_outdoor_temp
            else:
                # No valid reading has ever been obtained — still starting up
                # or entity is misconfigured / permanently unavailable.
                self._outdoor_temp_startup_failures += 1
                if self._outdoor_temp_startup_failures >= _OUTDOOR_TEMP_MAX_STARTUP_FAILURES:
                    raise UpdateFailed(
                        "Outdoor temperature is unavailable: neither "
                        f"outdoor_temp_entity {self._outdoor_entity!r} nor "
                        f"weather_entity {self._weather_entity!r} has produced "
                        f"a valid reading after "
                        f"{self._outdoor_temp_startup_failures} coordinator cycles. "
                        "Check that the configured entities exist and are "
                        "reporting a numeric state."
                    )
                _LOGGER.debug(
                    "Outdoor temperature unavailable — startup cycle %d/%d; "
                    "skipping MPC until entity becomes ready",
                    self._outdoor_temp_startup_failures,
                    _OUTDOOR_TEMP_MAX_STARTUP_FAILURES,
                )
                if not self.actions:
                    self.actions = {src.name: 0.0 for src in self.heat_sources}
                # Solar gain does not depend on the outdoor temperature, so
                # publish it even on this startup path instead of leaving the
                # dashboard's solar value at 0 until the outdoor entity comes
                # online.  Seed the cloud-cover EMA from persistence first so the
                # clear-sky model is attenuated.
                await self._ensure_runtime_state_loaded()
                self._publish_current_solar_gains()
                return {
                    "temperatures": dict(self.model.temperatures),
                    "outdoor_temp": None,
                    "actions": dict(self.actions),
                    "solar_gains": dict(self.solar_gains),
                    "predictions": [],
                    "heat_flows": {},
                    "outdoor_forecast": [],
                    "solar_forecast": [],
                    "heating_schedule": [],
                }

            # 2b. Read electricity price forecast from Nord Pool / price entity.
            self.price_forecast = await self._async_read_price_forecast(self.now_utc)

            # 2c. Read weather forecast for outdoor temperature prediction
            #     and cloud-cover (used to attenuate the clear-sky solar model)
            outdoor_forecast = await self._async_read_weather_forecast()
            await self._ensure_runtime_state_loaded()
            # Low-pass the live cloud cover so the solar attenuation is
            # continuous and robust to the weather entity being briefly
            # unavailable right after a restart (which previously produced an
            # unattenuated clear-sky spike on the first cycle).
            cloud_cover_raw = self._read_cloud_cover_now()
            cloud_cover_now = self._smooth_cloud_cover(cloud_cover_raw)
            # Anchor the forecast on the smoothed current value so the current
            # solar gain and its forecast transition continuously into each other.
            cloud_forecast = await self._async_read_cloud_forecast(cloud_cover_now=cloud_cover_now)
            # Cold-start fallback: with no live reading and no persisted EMA,
            # adopt the first forecast value rather than an unattenuated model.
            if cloud_cover_now is None and cloud_forecast:
                cloud_cover_now = max(0.0, min(1.0, float(cloud_forecast[0])))
                self._cloud_cover_filtered = cloud_cover_now

            # 2c'. Read the optional solar forecast and derive a GHI series.
            #      When no entity is configured (or it is unavailable / stale /
            #      unparseable / lacks the peak power needed to scale PV power to
            #      irradiance) both values are ``None`` and the solar model falls
            #      back to the cloud-cover attenuation above — today's behaviour.
            ghi_now, ghi_forecast = self._read_ghi(self.now_utc)

            # 2d. Read current wind speed for the Sherman–Grimsrud
            #     infiltration overlay.  When the weather entity does not
            #     expose ``wind_speed`` this returns ``None`` and the
            #     controller's external conductance falls back to its
            #     typical-conditions baseline.  The wind *forecast* (when
            #     the weather entity provides one) is also parsed and
            #     handed to the controller: the QP linearisation uses its
            #     horizon mean and the prediction rollout the per-step
            #     values.
            wind_speed_now = self._read_wind_speed_now()
            self.wind_speed = wind_speed_now
            if hasattr(self.controller, "set_wind_speed"):
                self.controller.set_wind_speed(wind_speed_now)
            wind_forecast = await self._async_read_wind_forecast(wind_speed_now)
            # Attenuate the (optional) sky radiative cooling drift by the
            # current cloud cover — clear nights cool harder than overcast.
            if hasattr(self.controller, "set_cloud_cover"):
                self.controller.set_cloud_cover(cloud_cover_now)
            self.model.set_cloud_cover(cloud_cover_now)
            if hasattr(self.controller, "set_room_process_noise_covariance_scales"):
                q_scale = {
                    room_name: (
                        self._window_open_q_inflation
                        if self.is_window_override_active(room_name)
                        else 1.0
                    )
                    for room_name in self.model.room_names
                }
                self.controller.set_room_process_noise_covariance_scales(q_scale)

            # 2e. Compute the current ground temperature from the
            #     built-in sinusoidal model (Phase 1 A2).  No external
            #     data needed — only the day of year.  Pushed to the
            #     controller and held constant over the cycle, in line
            #     with the wind-speed plumbing above.  Use
            #     ``self.now_utc`` (stamped at the start of this update
            #     cycle) rather than a fresh ``datetime.now`` so the
            #     value stays consistent with every other "now" the
            #     cycle uses.
            ground_temp_now = ground_temperature(self.now_utc)
            self.ground_temp = ground_temp_now
            if hasattr(self.controller, "set_ground_temp"):
                self.controller.set_ground_temp(ground_temp_now)

            # 3. Compute current solar gains for visualization.  The forecast
            #    GHI (when available) drives the intensity and takes precedence
            #    over cloud cover; a room with no enumerated windows falls back
            #    to its single solar-exposure aperture.
            now = self.now_utc
            self.cloud_cover = cloud_cover_now
            self.ghi_now = ghi_now
            self.ghi_forecast = list(ghi_forecast or [])
            self.solar_source = (
                "forecast" if ghi_forecast or ghi_now is not None
                else "analytical"
            )
            # Effective GHI for display: fall back to the modeled clear-sky GHI
            # (cloud-attenuated) when no sensor value is available. Mirrors the
            # intensity the analytical gains path uses, so the KPI and the gains
            # tell a consistent story.
            try:
                self.ghi_now_effective = horizontal_irradiance(
                    now, self._latitude, self._longitude,
                    cloud_cover=cloud_cover_now, ghi=ghi_now,
                )
            except Exception:  # pragma: no cover - defensive; never block a cycle
                self.ghi_now_effective = ghi_now
            self.solar_gains = {
                name: self._room_solar_gain(
                    name, now, cloud_cover_now, ghi_now
                )
                for name in self.model.room_names
            }
            # Persist the smoothed cloud cover so the next restart seeds from it.
            self._save_runtime_state()

            # 4. Run MPC controller
            # Collect heat sources whose rooms are currently off so the
            # controller can zero them out in both the first-step action and
            # the full predicted heating schedule before returning.  Also
            # disable sources for rooms that have never had a valid sensor
            # reading so we never actuate based on the 20 °C model default.
            disabled_src_names = {
                src.name
                for src in self.heat_sources
                if not self.is_room_enabled(src.room)
                or self.is_window_override_active(src.room)
                or src.room in rooms_not_ready
            }
            try:
                self.actions = self.controller.compute(
                    outdoor_temp=outdoor_temp,
                    solar_gains=self.solar_gains,
                    now=now,
                    outdoor_forecast=outdoor_forecast,
                    cloud_forecast=cloud_forecast,
                    cloud_cover_now=cloud_cover_now,
                    ghi_forecast=ghi_forecast,
                    ghi_now=ghi_now,
                    wind_forecast=wind_forecast,
                    disabled_sources=disabled_src_names or None,
                    control_trajectory=control_traj,
                    price_forecast=self.price_forecast,
                    # While stopped, run state estimation only — the MPC
                    # optimisation is skipped so no control trajectory is solved.
                    run_optimization=self._system_enabled,
                )
                # Mirror the unconstrained MPC optimum (before window-override
                # zeroing) so a heater can resume at the planned level the
                # instant its window-close settle timer expires between solves.
                self._mpc_shadow_actions = dict(self.controller.mpc_actions)
                self.predictions = self.controller.predictions
                self.linearised_predictions = self.controller.linearised_predictions
                self.outdoor_forecast = self.controller.outdoor_forecast
                self.solar_forecast = self.controller.solar_forecast
                self.heating_schedule = self.controller.heating_schedule
                self.filtered_temperatures = dict(self.controller.filtered_temperatures)
                # EKF-reconstructed wall/mass-node temperatures and their
                # posterior stds — the 2R2C observability health signals.
                self.wall_temperatures = dict(
                    getattr(self.controller, "wall_temperatures", {}) or {}
                )
                self.wall_temperature_stds = dict(
                    getattr(self.controller, "wall_temperature_stds", {}) or {}
                )
                # Online internal-gain estimates [W] per room (nominal + Δĝ).
                self.estimated_internal_gains = dict(
                    self.controller.estimated_internal_gains
                )
                # Rooms that have never had a valid reading expose "unknown"
                # so the dashboard falls back to the raw sensor value.
                for _r in rooms_not_ready:
                    self.filtered_temperatures.pop(_r, None)
                # Mirror the price forecast that was actually used so sensors can
                # expose it (may differ from self.price_forecast if the controller
                # truncated / padded it).
                self.price_forecast = self.controller.price_forecast or self.price_forecast

                # Capture Kalman innovation for diagnostics (may be None on first step)
                # controller.last_innovation is populated by compute() after splitting
                # the EKF predict/update steps to record ν = y − hm(x̂⁻).
                kalman_innovation: Optional[List[float]] = self.controller.last_innovation

                # Record the instant the MPC actually solved so the dashboard
                # countdown reflects the true internal schedule.  Only set on a
                # successful solve — a failed cycle leaves the previous value so
                # the countdown keeps ticking toward the next scheduled attempt.
                # While stopped the MPC does not solve, so the timestamp is left
                # untouched and the countdown stays paused.
                if self._system_enabled:
                    self._last_mpc_run_ts = now.timestamp()
            except Exception:
                _LOGGER.warning(
                    "Failed to compute MPC actions; clearing forecast data so "
                    "dashboards show a visible gap at the failure point",
                    exc_info=True,
                )
                # Keep previous actions if available; otherwise default to all-off
                # so the applied heater commands stay safe.
                if not self.actions:
                    self.actions = {src.name: 0.0 for src in self.heat_sources}

                # Pass the weather forecast through (it's read independently of
                # the MPC), but clear all forecast/filtered fields so the
                # visualization sensors expose "unknown" instead of fabricating
                # a thermal-model trajectory.  This makes failures plot as a
                # visible gap rather than a silent fake forecast.
                if outdoor_forecast:
                    self.outdoor_forecast = list(outdoor_forecast[:self._horizon])
                    if len(self.outdoor_forecast) < self._horizon:
                        self.outdoor_forecast.extend(
                            [outdoor_temp] * (self._horizon - len(self.outdoor_forecast))
                        )
                else:
                    self.outdoor_forecast = [outdoor_temp] * self._horizon
                self.predictions = []
                self.linearised_predictions = []
                self.heating_schedule = []
                self.solar_forecast = []
                self.filtered_temperatures = {}
                self.estimated_internal_gains = {}
                kalman_innovation = None

            # Dispatch-layer W1 override: clamp all sources in open-window
            # rooms to u=0 before history write and actuator commands.
            for src in self.heat_sources:
                if self.is_window_override_active(src.room):
                    self.actions[src.name] = 0.0

            # System STOPPED: the MPC optimisation was skipped above (only the
            # CD-EKF state estimation ran), so there is no fresh control
            # trajectory and the dashboard stop switch means we must not push
            # any signal to the heater entities this cycle.  Replace the
            # commanded actions and power with what each heater is actually
            # delivering — read back from its real entity state — and tell the
            # EKF the true applied input (via notify_applied_u inside the
            # helper) so the next cycle's state estimate stays grounded in
            # reality instead of drifting on inputs that were never applied.
            if not self._system_enabled:
                self.actions = self._read_delivered_actions(outdoor_temp)

            # 5. Store heat-flow breakdown (independent of MPC solve success)
            self.heat_flows = self.model.compute_heat_flows(outdoor_temp)

            # 6. Record observation in the rolling history buffer for ML
            #    parameter estimation and model fit analysis.
            #
            # Rate-limit to the scheduled update interval.  Window events no
            # longer call _async_update_data (they use _async_push_window_override
            # directly), but startup listeners may still trigger a refresh shortly
            # after the coordinator's own first scheduled tick.  Guard against
            # that by skipping the append when fewer than 0.5 × update_interval
            # seconds have elapsed since the last history entry.
            _last_history_ts = (
                float(self._history_buffer[-1].get("timestamp", 0.0))
                if self._history_buffer else 0.0
            )
            _should_record_history = (
                now.timestamp() - _last_history_ts >= 0.5 * self._update_interval_s
            )

            if _should_record_history:
                # y_pred alignment: the MPC predictions[0] is the one-step-ahead
                # prediction for time k+1 (not time k).  To get a properly aligned
                # diagnostic (prediction vs measurement at the *same* timestep) we
                # store that forward prediction as "y_pred_for_next" and retrieve
                # the *previous* record's "y_pred_for_next" as the aligned y_pred
                # for the current step.
                y_pred_aligned = None
                if len(self._history_buffer) > 0:
                    y_pred_aligned = self._history_buffer[-1].get("y_pred_for_next")

                # Compute the one-step-ahead prediction for the NEXT cycle.
                y_pred_for_next: List[float]
                if self.predictions and len(self.predictions) > 0:
                    first_pred = self.predictions[0]
                    y_pred_for_next = [
                        first_pred.get(name, self.model.rooms[name].temperature)
                        for name in self.model.room_names
                    ]
                else:
                    y_pred_for_next = [
                        self.model.rooms[name].temperature
                        for name in self.model.room_names
                    ]

                self._history_buffer.append({
                    # Store the raw sensor measurements as y so the open-loop
                    # simulation and EKF diagnostics start from what the sensor
                    # actually read, not from any model-internal estimate.  For
                    # rooms without a sensor configured the model temperature is
                    # used as a fallback, but those rooms have no meaningful
                    # simulation target anyway.
                    "y": [
                        self.measured_temperatures.get(
                            name, self.model.rooms[name].temperature
                        )
                        for name in self.model.room_names
                    ],
                    # y_pred: prediction made at k-1 FOR step k — aligned with y.
                    # None for the very first record (no prior prediction exists yet).
                    "y_pred": y_pred_aligned,
                    # y_pred_for_next: prediction made NOW (at k) FOR step k+1.
                    # Retrieved by the next cycle as y_pred_aligned.
                    "y_pred_for_next": y_pred_for_next,
                    "u": [
                        self.actions.get(src.name, 0.0)
                        for src in self.heat_sources
                    ],
                    "d_outdoor": outdoor_temp,
                    "d_solar": dict(self.solar_gains),
                    "cloud_cover": cloud_cover_now,
                    # Scalar only — the full horizon-length GHI array is kept off
                    # the bounded history buffer (it lives on the diagnostic
                    # SolarRadiationStatusSensor instead) to limit recorder growth.
                    "ghi_now": ghi_now,
                    "solar_source": self.solar_source,
                    "timestamp": now.timestamp(),
                    # Kalman innovation ν = y − C x̂⁻  (None on the very first step)
                    "kalman_innovation": kalman_innovation,
                })

            # 7. Write set-points to heater entities. Keep the latest
            # forecast/prediction entities available even if HA service calls
            # fail for a specific heater entity.
            #
            # Skipped entirely while the system is stopped: the MPC optimisation
            # does not run, so there is nothing to apply — heaters are left in
            # whatever state they were last commanded to, since the dashboard
            # stop relinquishes control.
            if self._system_enabled:
                try:
                    await self._apply_actions(outdoor_temp)
                except Exception:
                    _LOGGER.warning(
                        "Failed to apply computed heater actions; keeping forecast "
                        "and prediction entities available",
                        exc_info=True,
                    )

            return {
                "temperatures": dict(self.model.temperatures),
                "outdoor_temp": outdoor_temp,
                "actions": dict(self.actions),
                "solar_gains": dict(self.solar_gains),
                "predictions": list(self.predictions),
                "heat_flows": dict(self.heat_flows),
                "outdoor_forecast": list(self.outdoor_forecast),
                "solar_forecast": list(self.solar_forecast),
                "heating_schedule": list(self.heating_schedule),
            }

        except Exception as exc:
            raise UpdateFailed(f"Heating Assistant update failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def setup_startup_listeners(self) -> Optional[Callable]:
        """Watch the specific entities this integration needs, not all of HA.

        Triggers a coordinator refresh as soon as each of the outdoor-temp
        sensor, weather entity, or room-temperature sensors transitions from
        ``unknown``/``unavailable`` to a valid state, without waiting for
        ``EVENT_HOMEASSISTANT_STARTED`` which blocks on all integrations
        (including unrelated slow ones).

        Returns a cancel callable suitable for ``entry.async_on_unload``,
        or ``None`` when there are no entities to watch.
        """
        _UNAVAILABLE = {"unknown", "unavailable"}

        entity_ids: List[str] = []
        if self._outdoor_entity:
            entity_ids.append(self._outdoor_entity)
        if self._weather_entity:
            entity_ids.append(self._weather_entity)
        for sensors in self._temp_sensors.values():
            for sid in sensors:
                if sid not in entity_ids:
                    entity_ids.append(sid)

        if not entity_ids:
            return None

        @callback
        def _on_entity_ready(event) -> None:
            new_state = event.data.get("new_state")
            if new_state is None or new_state.state in _UNAVAILABLE:
                return
            old_state = event.data.get("old_state")
            # Only trigger on transitions from unavailable/unknown → valid.
            if old_state is not None and old_state.state not in _UNAVAILABLE:
                return
            _LOGGER.debug(
                "Startup: entity %s became available — requesting coordinator refresh",
                event.data.get("entity_id"),
            )
            self.hass.async_create_task(self.async_request_refresh())

        return async_track_state_change_event(self.hass, entity_ids, _on_entity_ready)

    def setup_window_listeners(self) -> Optional[Callable]:
        """Set up window-sensor listeners for event-driven debounce/settle timing.

        The state machine is advanced DIRECTLY from the event callbacks rather
        than relying on coordinator refreshes to check elapsed time.  Actuator
        commands are pushed via ``_async_push_window_override()`` rather than
        ``async_request_refresh()`` so the MPC and EKF are never triggered by
        window events — they run strictly at the scheduled update interval.

        * Sensor opens  → ``closed → pending_open`` and start the
          ``window_open_debounce`` timer.  The heater keeps running until it
          elapses; only then does the room advance to ``open`` and the
          heater-off override get pushed.
        * Sensor closes before the debounce elapses → cancel the timer and
          revert to ``closed``; the heater was never turned off.
        * All room sensors close while ``open`` → ``open → pending_closed``
          and start the ``window_open_close_settle`` timer.  The heater stays
          off until it elapses; only then does the room return to ``closed``
          and the heater resume at the actuation the MPC kept solving for in
          the background.
        * A sensor re-opens while ``pending_closed`` → cancel the settle timer
          and return to ``open`` immediately; the heater stays off.

        Each running timer cancels only on the transition that ends it, so a
        second sensor opening/closing in the same direction leaves the
        in-flight debounce/settle timer untouched rather than restarting it.

        Returns a cancel callable suitable for ``entry.async_on_unload``,
        or ``None`` when no window sensors are configured.
        """
        # Build sensor_id → room_name reverse lookup.
        sensor_to_room: Dict[str, str] = {
            sid: room_name
            for room_name, sensors in self._window_sensors.items()
            for sid in sensors
        }
        all_sensor_ids = list(sensor_to_room)
        if not all_sensor_ids:
            return None

        # Pending timer cancel-handles keyed by room_name (one timer per room).
        _pending: Dict[str, Callable] = {}

        @callback
        def _on_window_changed(event) -> None:
            sensor_id = event.data.get("entity_id", "")
            room_name = sensor_to_room.get(sensor_id)
            if room_name is None:
                return

            new_state_obj = event.data.get("new_state")
            old_state_obj = event.data.get("old_state")
            if new_state_obj is None:
                return

            new_open = str(new_state_obj.state).lower() == "on"
            old_open = (
                old_state_obj is not None
                and str(old_state_obj.state).lower() == "on"
            )
            if new_open == old_open:
                return

            now_utc = datetime.now(tz=timezone.utc)

            def _cancel_pending() -> None:
                """Cancel this room's in-flight debounce/settle timer, if any."""
                if room_name in _pending:
                    _pending.pop(room_name)()

            current = self._window_state.get(room_name, "closed")
            sensors = self._window_sensors.get(room_name, [])
            any_open_now = any(self._read_binary_sensor_on(s) for s in sensors)

            if new_open:
                # ── A sensor in this room just opened ──────────────────────
                if current in ("open", "pending_open"):
                    # Already open, or already counting down the open debounce:
                    # a second window opening changes nothing and must NOT
                    # restart the in-flight timer.
                    return

                if current == "pending_closed":
                    # Re-opened during the close-settle window — stop the
                    # settle timer and return to the open override at once.
                    # The heater stays off; no debounce is re-run.
                    _cancel_pending()
                    self._set_window_state(room_name, "open", now_utc)
                    _LOGGER.debug(
                        "Window sensor %s re-opened during settle for %s — "
                        "back to open (heater stays off)",
                        sensor_id, room_name,
                    )
                    self.hass.async_create_task(self._async_push_window_override())
                    return

                # current == "closed": start the open-debounce timer.  The
                # heater keeps running until it elapses.
                _cancel_pending()
                self._set_window_state(room_name, "pending_open", now_utc)
                _LOGGER.debug(
                    "Window sensor %s opened for %s — pending_open (debounce %.0fs)",
                    sensor_id, room_name, self._window_open_debounce,
                )

                @callback
                def _advance_open(_now=None, _rn=room_name) -> None:
                    _pending.pop(_rn, None)
                    _now_inner = datetime.now(tz=timezone.utc)
                    sensors_in = self._window_sensors.get(_rn, [])
                    any_open = any(self._read_binary_sensor_on(s) for s in sensors_in)
                    if any_open and self._window_state.get(_rn) == "pending_open":
                        self._set_window_state(_rn, "open", _now_inner)
                        _LOGGER.debug(
                            "Window debounce elapsed for %s — heater override active",
                            _rn,
                        )
                    self.hass.async_create_task(self._async_push_window_override())

                _pending[room_name] = async_call_later(
                    self.hass, self._window_open_debounce, _advance_open
                )

            else:
                # ── A sensor in this room just closed ──────────────────────
                if any_open_now:
                    # Another sensor in the room is still open — the override
                    # state and any running timer are unaffected.
                    return

                if current == "pending_open":
                    # Closed before the open debounce elapsed — stop the timer
                    # and revert to closed; the heater was never turned off.
                    _cancel_pending()
                    self._set_window_state(room_name, "closed", now_utc)
                    _LOGGER.debug(
                        "Window sensor %s closed before debounce for %s — reverted",
                        sensor_id, room_name,
                    )
                    self.hass.async_create_task(self._async_push_window_override())
                    return

                if current == "open":
                    # All sensors closed while open — start the close-settle
                    # timer.  The heater stays off until it elapses.
                    _cancel_pending()
                    self._set_window_state(room_name, "pending_closed", now_utc)
                    _LOGGER.debug(
                        "Window sensor %s closed for %s — pending_closed (settle %.0fs)",
                        sensor_id, room_name, self._window_open_close_settle,
                    )

                    @callback
                    def _advance_closed(_now=None, _rn=room_name) -> None:
                        _pending.pop(_rn, None)
                        _now_inner = datetime.now(tz=timezone.utc)
                        sensors_in = self._window_sensors.get(_rn, [])
                        any_open2 = any(
                            self._read_binary_sensor_on(s) for s in sensors_in
                        )
                        if (
                            not any_open2
                            and self._window_state.get(_rn) == "pending_closed"
                        ):
                            self._set_window_state(_rn, "closed", _now_inner)
                            _LOGGER.debug(
                                "Window settle elapsed for %s — heater override "
                                "cleared, resuming MPC actuation",
                                _rn,
                            )
                        self.hass.async_create_task(self._async_push_window_override())

                    _pending[room_name] = async_call_later(
                        self.hass, self._window_open_close_settle, _advance_closed
                    )
                # current in ("closed", "pending_closed"): nothing to do.

        cancel_listener = async_track_state_change_event(
            self.hass, all_sensor_ids, _on_window_changed
        )

        def _cleanup() -> None:
            cancel_listener()
            for cancel_cb in list(_pending.values()):
                cancel_cb()
            _pending.clear()

        return _cleanup

    def _read_outdoor_temp(self) -> Optional[float]:
        return _weather.read_outdoor_temp(
            self.hass, self._outdoor_entity, self._weather_entity
        )

    async def _async_read_weather_forecast(self) -> Optional[List[float]]:
        """Read outdoor temperature forecast from a weather entity.

        Returns a list of N outdoor temperature values aligned to the MPC
        horizon steps, or None if no weather entity is configured or no
        forecast data is available.
        """
        forecast_data = await self._async_get_forecast_entries()
        if not forecast_data:
            return None
        return _weather.parse_temperature_forecast(forecast_data, self._horizon, self.dt)

    async def _async_read_cloud_forecast(
        self, cloud_cover_now: Optional[float] = None
    ) -> Optional[List[float]]:
        """Read cloud-cover forecast (fraction in [0, 1]) from the weather entity.

        When ``cloud_cover_now`` is provided it is used as an anchor in the
        interpolation (smooth ramp from the current observation to the first
        forecast entry) and as a persistence fallback when no forecast data
        is available.
        """
        forecast_data = await self._async_get_forecast_entries()
        return _weather.parse_cloud_forecast(
            forecast_data or [], self._horizon, self.dt,
            current_cloud_cover=cloud_cover_now,
        )

    async def _async_read_wind_forecast(
        self, wind_speed_now: Optional[float] = None
    ) -> Optional[List[float]]:
        """Read the wind-speed forecast [m/s] from the weather entity.

        ``wind_speed_now`` (already in m/s) anchors the interpolation at
        k = 0.  Returns ``None`` when the forecast carries no usable
        ``wind_speed`` field — the controller then holds the current wind
        over the horizon, exactly the previous behaviour.
        """
        forecast_data = await self._async_get_forecast_entries()
        if not forecast_data:
            return None
        unit = None
        if self._weather_entity:
            state = self.hass.states.get(self._weather_entity)
            if state is not None:
                unit = state.attributes.get("wind_speed_unit")
        return _weather.parse_wind_forecast(
            forecast_data, self._horizon, self.dt,
            now=self.now_utc, current_wind=wind_speed_now, unit=unit,
        )

    async def _async_get_forecast_entries(self) -> Optional[list]:
        """Fetch raw forecast entries from the configured weather entity and
        record success / failure on the coordinator for the diagnostic
        ``WeatherForecastStatusSensor``.
        """
        forecast_data, error = await _weather.fetch_forecast_entries(
            self.hass, self._weather_entity
        )
        if not self._weather_entity:
            # Not configured — neither success nor failure.
            return None
        if forecast_data is not None:
            self._record_weather_success()
            return forecast_data
        self._record_weather_failure(error or "unknown error")
        return None

    async def _async_read_price_forecast(
        self, now: datetime
    ) -> Optional[List[float]]:
        """Read the electricity price forecast from the configured price entity.

        Three attribute formats are supported, tried in order:

        **Path A – timestamped dicts (Nord Pool HACS v2)**
            ``raw_today`` / ``raw_tomorrow`` are lists of dicts with an
            explicit ``start`` ISO-8601 datetime and a ``value``/``price``/
            ``total`` key.  Each horizon step is mapped to the price whose
            ``start <= step_time``, so the transition to the next hourly price
            happens at the exact wall-clock moment regardless of the MPC step
            size.

        **Path B – plain hourly lists (Nord Pool HACS v1 / generic)**
            ``today`` / ``tomorrow`` (or ``prices_today`` / ``prices_tomorrow``)
            are 24-element lists where index ``h`` holds the price for local
            hour ``h``.  The number of steps already elapsed within the
            current hour is taken into account so that the price change
            propagates at the correct MPC step rather than always at the top
            of the hour.

        **Fallback – current sensor state**
            The numeric state of the price entity is repeated over the full
            horizon.

        Returns a list of N prices [currency/kWh] aligned to horizon steps,
        or None when no price entity is configured or the sensor is unavailable.
        """
        if not self._price_entity:
            return None
        state = self.hass.states.get(self._price_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None

        attrs = state.attributes
        adder = self._price_net_tariff + self._price_spot_surcharge
        N = self._horizon
        dt_s = float(self.dt)

        # ── Path A: timestamped dicts with explicit ``start`` key ─────────
        #
        # Nord Pool HACS v2 exposes raw_today / raw_tomorrow as lists of dicts
        # like {"start": "<iso-dt>", "end": "<iso-dt>", "value": <float>}.
        # We sort them by start time and, for each horizon step, walk forward
        # to find the last entry whose start <= step_time.  This is immune to
        # sub-hourly rounding because we compare real wall-clock times rather
        # than doing hour-index arithmetic.
        raw_today = attrs.get("raw_today") or []
        raw_tomorrow = attrs.get("raw_tomorrow") or []
        if raw_today or raw_tomorrow:
            timed_prices: List[Tuple[datetime, float]] = []
            for entry in list(raw_today) + list(raw_tomorrow):
                if not isinstance(entry, dict):
                    continue
                start_str = entry.get("start")
                v = entry.get("value") or entry.get("price") or entry.get("total")
                if start_str is None or v is None:
                    continue
                try:
                    start_dt = datetime.fromisoformat(str(start_str))
                    timed_prices.append((start_dt, float(v)))
                except (TypeError, ValueError):
                    continue
            if timed_prices:
                timed_prices.sort(key=lambda tp: tp[0])
                aligned_a: List[float] = []
                for k in range(N):
                    step_time = now + timedelta(seconds=dt_s * k)
                    # Initialise with the first available price in case now is
                    # before all known entries.
                    price = timed_prices[0][1]
                    for start_dt, p in timed_prices:
                        if start_dt <= step_time:
                            price = p
                        else:
                            break
                    aligned_a.append(max(0.0, price + adder))
                return aligned_a

        # ── Path B: plain hourly lists ────────────────────────────────────
        #
        # today / tomorrow (or prices_today / prices_tomorrow) are 24-element
        # lists where index h holds the price for local hour h.  We correct
        # for sub-hourly position by counting how many MPC steps have already
        # elapsed within the current hour (elapsed_steps).  Without this
        # correction the code would treat now as always being at the top of
        # the hour, causing the transition to the next hourly price to arrive
        # up to (steps_per_hour − 1) steps too late.
        today_raw = attrs.get("today") or attrs.get("prices_today") or []
        tomorrow_raw = attrs.get("tomorrow") or attrs.get("prices_tomorrow") or []

        today_prices: List[float] = []
        tomorrow_prices: List[float] = []

        for entry in today_raw:
            if isinstance(entry, (int, float)):
                today_prices.append(float(entry))
            elif isinstance(entry, dict):
                v = entry.get("value") or entry.get("price") or entry.get("total")
                if v is not None:
                    try:
                        today_prices.append(float(v))
                    except (TypeError, ValueError):
                        pass

        for entry in tomorrow_raw:
            if isinstance(entry, (int, float)):
                tomorrow_prices.append(float(entry))
            elif isinstance(entry, dict):
                v = entry.get("value") or entry.get("price") or entry.get("total")
                if v is not None:
                    try:
                        tomorrow_prices.append(float(v))
                    except (TypeError, ValueError):
                        pass

        all_prices = today_prices + tomorrow_prices
        if all_prices:
            now_local = now.astimezone()
            current_hour = now_local.hour
            steps_per_hour = max(1, round(3600.0 / dt_s))
            # Steps already elapsed within the current hour.
            elapsed_steps = int(
                (now_local.minute * 60 + now_local.second) / dt_s
            )
            aligned_b: List[float] = []
            for k in range(N):
                hour_idx = current_hour + (elapsed_steps + k) // steps_per_hour
                raw = (
                    all_prices[hour_idx]
                    if hour_idx < len(all_prices)
                    else all_prices[-1]
                )
                aligned_b.append(max(0.0, raw + adder))
            return aligned_b

        # ── Fallback: current sensor state repeated over full horizon ─────
        try:
            current_price = float(state.state)
        except (ValueError, TypeError):
            return None
        return [max(0.0, current_price + adder)] * N

    def _record_weather_success(self) -> None:
        """Mark the most recent forecast fetch as successful."""
        if self.weather_consecutive_failures > 0:
            _LOGGER.info(
                "Weather forecast recovered for %s after %d consecutive failures",
                self._weather_entity,
                self.weather_consecutive_failures,
            )
        self.weather_last_error = None
        self.weather_last_error_at = None
        self.weather_last_success_at = self.now_utc
        self.weather_consecutive_failures = 0

    def _record_weather_failure(self, reason: str) -> None:
        """Record a forecast-fetch failure and log on threshold crossings."""
        self.weather_consecutive_failures += 1
        self.weather_last_error = reason
        self.weather_last_error_at = self.now_utc
        if self.weather_consecutive_failures in self._weather_warn_thresholds:
            _LOGGER.warning(
                "Weather forecast unavailable for %s (failure #%d): %s",
                self._weather_entity,
                self.weather_consecutive_failures,
                reason,
            )

    def _read_cloud_cover_now(self) -> Optional[float]:
        return _weather.read_cloud_cover_now(self.hass, self._weather_entity)

    def _read_wind_speed_now(self) -> Optional[float]:
        return _weather.read_wind_speed_now(self.hass, self._weather_entity)

    def _record_solar_fc_success(self) -> None:
        """Mark the most recent solar-forecast read as successful."""
        if self.solar_fc_consecutive_failures > 0:
            _LOGGER.info(
                "Solar forecast recovered for %s after %d consecutive failures",
                self._solar_radiation_entity,
                self.solar_fc_consecutive_failures,
            )
        self.solar_fc_last_error = None
        self.solar_fc_last_error_at = None
        self.solar_fc_last_success_at = self.now_utc
        self.solar_fc_consecutive_failures = 0

    def _record_solar_fc_failure(self, reason: str) -> None:
        """Record a solar-forecast read failure and log on threshold crossings."""
        self.solar_fc_consecutive_failures += 1
        self.solar_fc_last_error = reason
        self.solar_fc_last_error_at = self.now_utc
        if self.solar_fc_consecutive_failures in self._weather_warn_thresholds:
            _LOGGER.warning(
                "Solar forecast unavailable for %s (failure #%d): %s",
                self._solar_radiation_entity,
                self.solar_fc_consecutive_failures,
                reason,
            )

    def _read_ghi(
        self, now: datetime,
    ) -> Tuple[Optional[float], Optional[List[Optional[float]]]]:
        """Read the solar-radiation entity and derive a GHI series [W/m²].

        Returns ``(ghi_now, ghi_forecast)``.  Both are ``None`` when no entity
        is configured, it is unavailable / stale, or it carries no usable
        irradiance — in which case the solar model falls back to the analytical
        clear-sky model (today's behaviour).
        """
        self._solar_provider = "none"
        if not self._solar_radiation_entity:
            return None, None

        state = self.hass.states.get(self._solar_radiation_entity)
        if state is None or getattr(state, "state", None) in _solar_fc._UNAVAILABLE_STATES:
            self._record_solar_fc_failure("entity unavailable")
            return None, None
        if _solar_fc.is_stale(state, now):
            self._record_solar_fc_failure("forecast stale")
            return None, None

        series = _solar_fc.parse_irradiance_forecast(state)
        value_now = _solar_fc.read_irradiance_now(
            self.hass, self._solar_radiation_entity, now=now,
        )
        if not series and value_now is None:
            self._record_solar_fc_failure("no irradiance data")
            return None, None

        self._solar_provider = "irradiance"
        forecast, ghi_now = _solar_fc.compute_ghi_series(
            series, value_now, self._horizon, float(self.dt), now,
        )
        if forecast is None and ghi_now is None:
            self._record_solar_fc_failure("no usable irradiance")
            return None, None

        self._record_solar_fc_success()
        return ghi_now, forecast

    def _room_solar_gain(
        self,
        name: str,
        now: datetime,
        cloud_cover: Optional[float],
        ghi: Optional[float],
    ) -> float:
        """Per-room solar gain [W]: windows when present, else exposure preset.

        Mirrors the controller's per-room selection so the coordinator's
        current-step ``solar_gains`` snapshot matches the forecast path.
        """
        room = self.model.rooms[name]
        if room.windows:
            return room_solar_gains(
                room.windows, now, self._latitude, self._longitude,
                cloud_cover=cloud_cover, ghi=ghi, albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            )
        return room_solar_gains_from_exposure(
            room.solar_exposure_aperture, room.solar_facing,
            now, self._latitude, self._longitude,
            cloud_cover=cloud_cover, ghi=ghi, albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
        )

    def _resolve_display_cloud_cover(self) -> Optional[float]:
        """Best-effort cloud cover for an out-of-cycle solar-gain snapshot.

        Prefers the value the last full MPC cycle published, then the EMA
        (seeded from persistence on restart), then a fresh live read.  Returns
        ``None`` only when there is genuinely no cloud information available.
        """
        cloud_cover = self.cloud_cover
        if cloud_cover is None:
            cloud_cover = self._cloud_cover_filtered
        if cloud_cover is None:
            cloud_cover = self._read_cloud_cover_now()
        return cloud_cover

    def _publish_current_solar_gains(self) -> None:
        """Recompute and publish ``self.solar_gains`` / ``self.cloud_cover`` for now.

        Solar gain depends only on the time, the site location, the cloud cover
        and (optionally) the GHI — never on the outdoor temperature.  Keeping
        this out of the outdoor-temperature gate means the dashboard shows the
        real, sun-driven (and cloud-attenuated) value right after a restart
        instead of dropping to 0 while the outdoor entity is still unavailable.
        """
        cloud_cover = self._resolve_display_cloud_cover()
        self.cloud_cover = cloud_cover
        try:
            self.solar_gains = {
                name: self._room_solar_gain(
                    name, self.now_utc, cloud_cover, self.ghi_now
                )
                for name in self.model.room_names
            }
        except Exception:  # pragma: no cover - defensive; never block a cycle
            _LOGGER.debug("Solar-gain recompute failed", exc_info=True)

    # Backwards-compatible aliases for any caller / test that imported the
    # static helpers from the coordinator before the U3 weather extraction.
    _parse_weather_forecast = staticmethod(_weather.parse_temperature_forecast)
    _parse_cloud_forecast = staticmethod(_weather.parse_cloud_forecast)
    _interpolate_forecast = staticmethod(_weather.interpolate_forecast)

    # ------------------------------------------------------------------
    # Delivered-power read-back (used while the system is stopped)
    # ------------------------------------------------------------------

    def _read_delivered_actions(self, outdoor_temp: float) -> Dict[str, float]:
        """Return the *actually delivered* control fraction for every source.

        Used when the dashboard stop switch is off: instead of the controller's
        freshly computed (but un-applied) optimal actions, read each heater
        entity's real state back from Home Assistant and derive the fraction it
        is currently delivering.  Each source's ``current_power`` is updated to
        match so the power sensors report what the hardware is doing, and the
        controller's EKF is notified of the true applied input via
        :meth:`HeatingController.notify_applied_u` so predictions stay
        consistent on the next cycle.

        ``switch`` and ``number`` heaters report an exact delivered fraction.
        For ``climate`` heaters the instantaneous modulation is not observable,
        so it is estimated from the entity's reported ``hvac_action`` and the
        gap between its target and current temperature (inverting the
        proportional setpoint this integration writes when commanding it).
        """
        controller = getattr(self, "controller", None)
        applied: Dict[str, float] = {}
        for src in self.heat_sources:
            frac = self._read_delivered_fraction(src)
            self._set_source_power(src, frac, outdoor_temp)
            applied[src.name] = frac
            if controller is not None:
                controller.notify_applied_u(src.name, frac)
        return applied

    def _read_delivered_fraction(self, src: HeatSource) -> float:
        """Read one heat source's actually-delivered control fraction.

        Returns a value in ``[-1, 1]`` (negative = active cooling).  Returns
        ``0.0`` when the heater entity is missing, unavailable, or off.
        """
        entity_id = src.heater_entity
        if not entity_id:
            return 0.0
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return 0.0
        domain = entity_id.split(".")[0]
        if domain == "switch":
            return 1.0 if state.state == "on" else 0.0
        if domain == "number":
            value = _coerce_opt_float(state.state)
            if value is None:
                return 0.0
            return max(0.0, min(1.0, value / 100.0))
        if domain == "climate":
            return self._read_delivered_fraction_climate(src, state)
        return 0.0

    def _read_delivered_fraction_climate(self, src: HeatSource, state: Any) -> float:
        """Best-effort delivered fraction for a climate heater.

        Modulating climate entities do not report instantaneous power, so the
        magnitude is inferred from the gap between the entity's own target and
        current temperature — the inverse of the proportional setpoint this
        integration writes when commanding the unit — gated by the reported
        ``hvac_action`` so an idle/off unit reads as zero.
        """
        if state.state == "off":
            return 0.0
        attrs = getattr(state, "attributes", {}) or {}
        action = attrs.get("hvac_action")
        if action in ("off", "idle"):
            return 0.0

        target = _coerce_opt_float(attrs.get("temperature"))
        current = _coerce_opt_float(attrs.get("current_temperature"))
        offset = float(getattr(src, "max_temp_offset", 0.0) or 0.0)
        gap: Optional[float] = None
        if target is not None and current is not None and offset > 0.0:
            gap = (target - current) / offset

        # Cooling: only meaningful for sources that can actively cool.
        if action == "cooling" or (gap is not None and gap < 0.0):
            if getattr(src, "can_cool", False):
                mag = abs(gap) if gap is not None else 1.0
                return -max(0.0, min(1.0, mag))
            return 0.0

        # Heating.
        if gap is not None:
            return max(0.0, min(1.0, gap))
        # No usable temperature telemetry: trust hvac_action if it reports the
        # unit is heating, otherwise assume it is delivering nothing.
        return 1.0 if action == "heating" else 0.0

    def _set_source_power(
        self, src: HeatSource, frac: float, outdoor_temp: float
    ) -> None:
        """Set ``src.current_power`` from a delivered fraction in ``[-1, 1]``."""
        if frac >= 0.0:
            # set_power clamps to [0, 1] and stores thermal_power as current.
            src.set_power(frac, outdoor_temp)
        elif hasattr(src, "cooling_power"):
            src._current_power = src.cooling_power(outdoor_temp) * min(1.0, abs(frac))
        else:
            src._current_power = 0.0

    def _refresh_live_state(self) -> Optional[float]:
        """Re-read live inputs and recompute cheap visualisation state.

        This is the lightweight counterpart to ``_async_update_data`` used by
        the fast UI refresh and the window-override push.  It updates everything
        the dashboard needs to stay live between scheduled MPC ticks WITHOUT
        running the MPC controller or advancing the EKF:

          * measured room temperatures (from HA sensor states),
          * the active schedule (live setpoints / enabled flags),
          * the window state machine,
          * the outdoor temperature,
          * solar gains and the heat-flow breakdown (pure model evaluations).

        The MPC-derived fields (predictions, filtered temperatures, forecasts,
        heating schedule) are deliberately left untouched — they only advance at
        the scheduled update interval.

        Returns the effective outdoor temperature (the fresh reading, or the last
        valid value when the entity is transiently unavailable), or ``None`` when
        no valid reading has ever been obtained.
        """
        # Stamp a fresh "now" so forecast-timestamp calculations in entity
        # extra_state_attributes use the current time, not the last tick's.
        self.now_utc = datetime.now(tz=timezone.utc)

        # Measured room temperatures.
        self.measured_temperatures = {}
        for room_name, entity_ids in self._temp_sensors.items():
            readings: List[float] = []
            for entity_id in entity_ids:
                state = self.hass.states.get(entity_id)
                if state and state.state not in ("unknown", "unavailable"):
                    try:
                        readings.append(float(state.state))
                    except (ValueError, TypeError):
                        pass
            if readings:
                avg = sum(readings) / len(readings)
                self.model.rooms[room_name].temperature = avg
                self.measured_temperatures[room_name] = avg
        self._rooms_ever_measured.update(self.measured_temperatures.keys())

        # Live setpoints / enabled flags from the active schedule.
        try:
            self._apply_schedule(datetime.now())
        except Exception:
            _LOGGER.debug("UI refresh: schedule apply failed", exc_info=True)

        # Advance the window debounce/settle state machine.
        self._update_window_state_machine(self.now_utc)

        # Outdoor temperature: expose the raw reading (None → "unknown") but use
        # the last valid value for the model evaluations below.
        _outdoor = self._read_outdoor_temp()
        self.outdoor_temp = _outdoor
        if _outdoor is not None:
            self._last_valid_outdoor_temp = _outdoor
        outdoor_temp = _outdoor if _outdoor is not None else self._last_valid_outdoor_temp

        # Solar gains (clear-sky / forecast model — no MPC involved).  Computed
        # unconditionally because solar gain does not depend on the outdoor
        # temperature: gating it on a valid outdoor reading made the value drop
        # to 0 right after a restart while the outdoor entity was still
        # unavailable.  ``_publish_current_solar_gains`` resolves a non-None
        # cloud cover (last published value, then the EMA seeded from
        # persistence, then a fresh live read) so the clear-sky model is
        # attenuated and never emits an unattenuated spike either.
        self._publish_current_solar_gains()

        if outdoor_temp is not None:
            # Instantaneous heat-flow breakdown (this one genuinely needs the
            # outdoor temperature).
            try:
                self.heat_flows = self.model.compute_heat_flows(outdoor_temp)
            except Exception:
                _LOGGER.debug("UI refresh: heat-flow recompute failed", exc_info=True)

        return outdoor_temp

    async def async_refresh_ui(self) -> None:
        """Refresh live UI state without running the MPC.

        Scheduled on a fast cadence (see ``setup_entry``) so the dashboard's
        measurements, setpoints, solar gains, and KPI cards stay live between
        scheduled MPC ticks.  The MPC and EKF advance strictly at the coordinator's
        ``update_interval``; this path never touches them.
        """
        # Seed the cloud-cover EMA from persisted runtime state before the very
        # first refresh so the fast path attenuates the clear-sky model from the
        # start.  Without this, a fast refresh that runs before the first full
        # MPC cycle (common right after a restart) has no cloud cover to apply
        # and emits an unattenuated clear-sky solar-gain spike.
        await self._ensure_runtime_state_loaded()
        try:
            self._refresh_live_state()
        except Exception:
            _LOGGER.warning("Fast UI refresh failed", exc_info=True)
            return
        self.async_update_listeners()

    async def _async_push_window_override(self) -> None:
        """Apply window overrides, push actuator commands, and refresh entity states.

        Called directly by window-sensor callbacks so that the heater-off (window
        open) or heater-on (window closed) command is issued immediately, without
        triggering a coordinator refresh and without disturbing the MPC or EKF.

        Live inputs are re-read via ``_refresh_live_state()`` so entities report
        current values, and ``async_update_listeners()`` is always called at the
        end so the UI refreshes immediately regardless of whether actuator
        commands could be applied.

        The MPC runs strictly at the scheduled update interval via the coordinator
        timer.  This method is the sole path for window events to affect actuators
        between those scheduled ticks.
        """
        outdoor_temp = self._refresh_live_state()

        # Push actuator commands only when we have an MPC solution and a valid
        # outdoor temperature.  Missing either means we're still in startup or
        # the outdoor sensor is transiently unavailable — in both cases the last
        # commanded state is the safest thing to leave the heaters in.
        if self.actions and outdoor_temp is not None:
            # A room whose window-override just cleared (open/pending_closed →
            # closed) must come back on at the actuation the MPC kept solving
            # for while the heater was off, not the 0 it was clamped to.  Pull
            # that value from the shadow optimum before the override clamp
            # below.  Rooms disabled for other reasons (user toggle / schedule)
            # are still forced off by ``_apply_actions``.
            for src in self.heat_sources:
                if (
                    not self.is_window_override_active(src.room)
                    and self.is_room_enabled(src.room)
                    and src.name in self._mpc_shadow_actions
                ):
                    self.actions[src.name] = self._mpc_shadow_actions[src.name]

            for src in self.heat_sources:
                if self.is_window_override_active(src.room):
                    self.actions[src.name] = 0.0

            if self._system_enabled:
                try:
                    await self._apply_actions(outdoor_temp)
                except Exception:
                    _LOGGER.warning(
                        "Window override: failed to push actuator commands",
                        exc_info=True,
                    )

        # Always notify entity subscribers so the UI immediately reflects both
        # the new window-override state and the freshly read sensor values.
        self.async_update_listeners()

    async def _apply_actions(self, outdoor_temp: float) -> None:
        """Write the computed set-point fractions to heater entities via HA services."""
        for src in self.heat_sources:
            fraction = self.actions.get(src.name, 0.0)
            room_enabled = self.is_room_enabled(src.room)
            window_override_active = self.is_window_override_active(src.room)
            effective_room_enabled = self._system_enabled and room_enabled and not window_override_active
            controller = getattr(self, "controller", None)

            # If the room is disabled, force fraction to 0 (turn off).
            if not effective_room_enabled:
                fraction = 0.0
                if controller is not None:
                    controller.notify_applied_u(src.name, 0.0)

            entity_id = src.heater_entity
            if not entity_id:
                continue
            # Map fraction to 0–100 % and call climate/number/switch service
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            domain = entity_id.split(".")[0]
            if domain == "climate":
                # For climate entities we need to ensure the heat pump
                # actually modulates to the desired power output.
                #
                # Heat pumps regulate output based on the gap between
                # their internal temperature reading and their setpoint.
                # We read the heat pump's own temperature from the
                # climate entity's ``current_temperature`` attribute and
                # add an offset proportional to the desired power
                # fraction so that the heat pump delivers the computed
                # thermal output.
                #
                # Heat pump climate entities use a three-state strategy:
                #   - fraction < 0  → cool mode (active cooling), with
                #     "cool" preferred, then "dry", then "fan_only"
                #   - fraction == 0 AND room_temp > setpoint
                #     → cool mode to remove heat (same mode preference order)
                #   - fraction == 0 AND room_temp ≤ setpoint
                #     → stay in heat mode, set target below internal temp
                #       (HP idles with no temperature gap)
                #   - fraction > 0  → heat mode, offset-based setpoint
                if isinstance(src, HeatPump):
                    if not effective_room_enabled:
                        src.set_power(0.0, outdoor_temp)
                        if controller is not None:
                            controller.notify_applied_u(src.name, 0.0)
                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": "off"},
                            blocking=False,
                        )
                        continue

                    # Read the HP's own internal temperature sensor.
                    hp_internal_temp: Optional[float] = None
                    attrs = getattr(state, "attributes", {})
                    raw = attrs.get("current_temperature")
                    if raw is not None:
                        try:
                            hp_internal_temp = float(raw)
                        except (ValueError, TypeError):
                            pass

                    base_temp = (
                        hp_internal_temp
                        if hp_internal_temp is not None
                        else self.model.rooms[src.room].temperature
                    )

                    # Map the configured hvac_mode to the best HA mode string
                    # the entity actually supports.
                    supported_modes = attrs.get("hvac_modes", [])
                    if src.hvac_mode == "heat_cool":
                        if "heat_cool" in supported_modes:
                            hvac_mode_str = "heat_cool"
                        elif "auto" in supported_modes:
                            hvac_mode_str = "auto"
                        else:
                            hvac_mode_str = "heat_cool"
                    elif src.hvac_mode == "cool":
                        if "cool" in supported_modes:
                            hvac_mode_str = "cool"
                        elif "dry" in supported_modes:
                            hvac_mode_str = "dry"
                        else:
                            hvac_mode_str = "fan_only"
                    else:
                        hvac_mode_str = "heat"

                    # Proportional setpoint: base_temp + fraction × max_temp_offset.
                    # fraction is already clamped to [u_min, u_max] by the controller.
                    target_temp = src.target_temperature(fraction, base_temp)

                    await self.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": hvac_mode_str},
                        blocking=False,
                    )
                    await self.hass.services.async_call(
                        "climate",
                        "set_temperature",
                        {"entity_id": entity_id, "temperature": target_temp},
                        blocking=False,
                    )
                else:
                    # Non-heat-pump climate entity (e.g. electric heater
                    # with a built-in thermostat).
                    #
                    # Read the entity's own internal temperature so we
                    # can guarantee the applied setpoint is below it
                    # whenever the room is above the desired setpoint.
                    entity_temp: Optional[float] = None
                    attrs = getattr(state, "attributes", {})
                    raw_temp = attrs.get("current_temperature")
                    if raw_temp is not None:
                        try:
                            entity_temp = float(raw_temp)
                        except (ValueError, TypeError):
                            pass
                    if entity_temp is None:
                        entity_temp = self.model.rooms[src.room].temperature

                    room_temp = self.model.rooms[src.room].temperature
                    room_setpoint = self.get_room_setpoint(src.room)

                    if not effective_room_enabled:
                        # Room explicitly disabled – turn the entity off.
                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": "off"},
                            blocking=False,
                        )
                    elif fraction > 0.0 and room_temp <= room_setpoint:
                        # Active heating: room is at or below setpoint.
                        # When fraction > 0 but room_temp > setpoint the
                        # code intentionally falls through to the else
                        # branch (cooling protection) to prevent the
                        # heater from firing.
                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": "heat"},
                            blocking=False,
                        )
                        target_temp = max(
                            room_setpoint,
                            src.target_temperature(fraction, entity_temp),
                        )
                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": target_temp},
                            blocking=False,
                        )
                    else:
                        # Idle or cooling protection.  The room is above
                        # setpoint or the MPC requests no heat.  In either
                        # case the entity's internal setpoint is placed
                        # below the entity's own temperature reading so
                        # the heater's built-in thermostat never fires,
                        # even if its internal sensor disagrees with the
                        # HA room sensor.  Commands are re-issued every
                        # update cycle to track sensor drift.
                        #
                        # The offset grows proportionally with how far the
                        # room temperature exceeds the setpoint so that
                        # heaters that regulate on their own internal sensor
                        # are pushed further away from firing the more the
                        # room overshoots.
                        overshoot = max(0.0, room_temp - room_setpoint)
                        target_temp = entity_temp - (DEFAULT_IDLE_OFFSET + overshoot)

                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": "heat"},
                            blocking=False,
                        )
                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": target_temp},
                            blocking=False,
                        )
            elif domain == "number":
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {
                        "entity_id": entity_id,
                        # Disabled/off-scheduled room should always apply zero output.
                        "value": 0 if not effective_room_enabled else round(fraction * 100),
                    },
                    blocking=False,
                )
            elif domain == "switch":
                # Disabled/off-scheduled room should always switch the unit off.
                if not effective_room_enabled:
                    service = "turn_off"
                else:
                    service = "turn_on" if fraction > 0.5 else "turn_off"
                await self.hass.services.async_call(
                    "switch",
                    service,
                    {"entity_id": entity_id},
                    blocking=False,
                )

    # ------------------------------------------------------------------
    # Setpoint helpers (called by climate platform)
    # ------------------------------------------------------------------

    def set_room_setpoint(self, room_name: str, setpoint: float) -> None:
        """Update the temperature setpoint for a room.

        The change is recorded as the *base* setpoint (the value the user
        wants when no schedule period is active).  When a schedule period
        is currently active, the change still overrides the live setpoint
        until the next coordinator tick re-applies the schedule.  This way
        users can nudge the temperature mid-period without their request
        being silently dropped.
        """
        if room_name not in self.model.rooms:
            return
        value = float(setpoint)
        self._base_setpoint[room_name] = value
        self.model.rooms[room_name].setpoint = value
        # Persist so the setpoint survives HA restarts and scheduled off periods.
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={**dict(real_entry.data), CONF_PERSISTED_SETPOINTS: dict(self._base_setpoint)},
            )

    def get_room_setpoint(self, room_name: str) -> float:
        """Return the current (live) setpoint for a room."""
        if room_name in self.model.rooms:
            return self.model.rooms[room_name].setpoint
        return DEFAULT_SETPOINT

    def get_base_setpoint(self, room_name: str) -> float:
        """Return the user-chosen setpoint used outside any schedule period."""
        if room_name in self._base_setpoint:
            return self._base_setpoint[room_name]
        return self.get_room_setpoint(room_name)

    def set_room_comfort_offset(self, room_name: str, comfort_offset: float) -> None:
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
        if room_name not in self.model.rooms:
            return
        value = float(comfort_offset)
        self._room_comfort_offset[room_name] = value
        self.model.rooms[room_name].comfort_offset = value
        # Rebuild the controller so the widened/narrowed corridor takes effect.
        self._build_controller()
        # Persist so the offset survives HA restarts (same pattern as setpoints).
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={
                    **dict(real_entry.data),
                    CONF_PERSISTED_COMFORT_OFFSETS: dict(self._room_comfort_offset),
                },
            )

    def get_room_comfort_offset(self, room_name: str) -> float:
        """Return the default comfort-band half-width [°C] for a room."""
        return self._room_comfort_offset.get(room_name, DEFAULT_COMFORT_OFFSET)

    # ------------------------------------------------------------------
    # System-level enable/disable
    # ------------------------------------------------------------------

    @property
    def system_enabled(self) -> bool:
        """Return whether the heating assistant controller is active."""
        return self._system_enabled

    def set_system_enabled(self, enabled: bool) -> None:
        """Enable or disable the entire heating assistant controller."""
        self._system_enabled = bool(enabled)

    # ------------------------------------------------------------------
    # Room enable/disable helpers (called by climate platform)
    # ------------------------------------------------------------------

    def set_room_enabled(self, room_name: str, enabled: bool) -> None:
        """Enable or disable heating control for a room (user toggle)."""
        self._room_enabled[room_name] = enabled

    def is_room_enabled(self, room_name: str) -> bool:
        """Return whether heating control should run for a room *right now*.

        The room runs when the user has not switched it off **and** no
        active comfort schedule period requests it to be off.  Schedule and
        user toggle are tracked independently so toggling one cannot
        silently override the other.
        """
        if not self._room_enabled.get(room_name, True):
            return False
        if self._schedule_disabled.get(room_name, False):
            return False
        return True

    # ------------------------------------------------------------------
    # Comfort schedule helpers
    # ------------------------------------------------------------------

    def _apply_schedule(self, now: datetime) -> None:
        """Resolve the active schedule for every room and update live state.

        Sets ``room.setpoint`` to the period's effective value and toggles
        ``_schedule_disabled`` so heat sources stop running during ``off``
        periods.  The user's manual on/off toggle (``_room_enabled``) is
        preserved — both flags are AND'ed together by
        :meth:`is_room_enabled`.

        Rooms whose schedule has been suspended (``_schedule_enabled`` is
        False) are left at their base setpoint and the schedule-disable is
        cleared.
        """
        for room_name in self.model.room_names:
            schedule = self._room_schedule.get(room_name)
            base = self._base_setpoint.get(
                room_name, self.model.rooms[room_name].setpoint
            )
            measured = self.model.rooms[room_name].temperature
            default_offset = self._room_comfort_offset.get(room_name, DEFAULT_COMFORT_OFFSET)

            if schedule is None or schedule.is_empty or not self._schedule_enabled.get(
                room_name, True
            ):
                effective = EffectiveControlParams(
                    setpoint=base,
                    comfort_offset=default_offset,
                    tracking_weight=1.0,
                    energy_weight=1.0,
                    enabled=True,
                    period_name=None,
                    mode=None,
                )
            else:
                effective = resolve_effective_control_params(
                    schedule=schedule,
                    base_setpoint=base,
                    measured_temp=measured,
                    now=now,
                    default_comfort_offset=default_offset,
                )

            self.model.rooms[room_name].setpoint = effective.setpoint
            self.model.rooms[room_name].comfort_offset = effective.comfort_offset
            self._schedule_disabled[room_name] = not effective.enabled
            self._effective_setpoint[room_name] = effective

    def _compute_control_trajectory(
        self,
        now_local: datetime,
        N: int,
        dt_seconds: float,
    ) -> ControlTrajectory:
        """Build per-step control parameters for the full MPC horizon.

        For each room and each horizon step k the resolved comfort setpoint,
        corridor half-width, tracking-weight multiplier and energy-weight
        multiplier are projected forward by consulting the room's schedule at
        ``now_local + k * dt_seconds``.

        Off periods are transparent: when a future step falls in an ``off``
        period the last known comfort values are carried forward unchanged.
        This produces the same reference the static (non-schedule-aware) MPC
        would use while leaving the ``disabled_sources`` zeroing mechanism
        fully responsible for off-period execution.
        """
        traj = ControlTrajectory(
            setpoints={},
            comfort_offsets={},
            q_scales={},
            r_scales={},
            enabled_steps={},
        )

        for room_name in self.model.room_names:
            schedule = self._room_schedule.get(room_name)
            base_sp = self._base_setpoint.get(room_name, DEFAULT_SETPOINT)
            default_offset = self._room_comfort_offset.get(room_name, DEFAULT_COMFORT_OFFSET)

            sp_seq = np.empty(N, dtype=float)
            off_seq = np.empty(N, dtype=float)
            qw_seq = np.empty(N, dtype=float)
            rw_seq = np.empty(N, dtype=float)
            enabled_seq = np.ones(N, dtype=bool)

            # Anchor: current effective params (already resolved by _apply_schedule)
            current = self._effective_setpoint.get(room_name)
            last_sp = current.setpoint if current is not None else base_sp
            last_off = current.comfort_offset if current is not None else default_offset
            last_qw = current.tracking_weight if current is not None else 1.0
            last_rw = current.energy_weight if current is not None else 1.0

            for k in range(N):
                t_k = now_local + timedelta(seconds=k * dt_seconds)
                if not self._schedule_enabled.get(room_name, True):
                    # Schedule suspended — use current effective values throughout.
                    pass
                else:
                    params = control_params_at(
                        schedule=schedule,
                        base_setpoint=base_sp,
                        t_future=t_k,
                        default_comfort_offset=default_offset,
                    )
                    if params is not None:
                        last_sp = params.setpoint
                        last_off = params.comfort_offset
                        last_qw = params.tracking_weight
                        last_rw = params.energy_weight
                    else:
                        # Off period: carry forward values but mark step as disabled.
                        enabled_seq[k] = False

                sp_seq[k] = last_sp
                off_seq[k] = last_off
                qw_seq[k] = last_qw
                rw_seq[k] = last_rw

            # Manual off (user toggle) disables all horizon steps.
            if not self._room_enabled.get(room_name, True):
                enabled_seq[:] = False

            traj.setpoints[room_name] = sp_seq
            traj.comfort_offsets[room_name] = off_seq
            traj.q_scales[room_name] = qw_seq
            traj.r_scales[room_name] = rw_seq
            traj.enabled_steps[room_name] = enabled_seq

        return traj

    def has_schedule(self, room_name: str) -> bool:
        """Return True when ``room_name`` has at least one schedule period."""
        schedule = self._room_schedule.get(room_name)
        return bool(schedule and not schedule.is_empty)

    def is_schedule_enabled(self, room_name: str) -> bool:
        """Return whether the comfort schedule is active for the room.

        A True result with no configured periods means the schedule "would
        run" if periods were defined; use :meth:`has_schedule` to test for
        actual configuration.
        """
        return self._schedule_enabled.get(room_name, True)

    def set_schedule_enabled(self, room_name: str, enabled: bool) -> None:
        """Suspend or resume the comfort schedule for one room.

        Suspending the schedule restores the room's base setpoint and
        re-enables heating immediately, so e.g. an evening "off" period
        can be skipped without editing YAML.  The configured periods are
        preserved and resume control on the next call to ``set_schedule_enabled``
        or after a Home Assistant restart.
        """
        self._schedule_enabled[room_name] = bool(enabled)
        # Re-apply now so the next tick already reflects the change in the
        # MPC reference; otherwise the user would have to wait one cycle.
        self._apply_schedule(datetime.now())

    def active_schedule_period(self, room_name: str) -> Optional[EffectiveControlParams]:
        """Return the most recently resolved effective setpoint for the room."""
        return self._effective_setpoint.get(room_name)

    def next_schedule_transition(self, room_name: str) -> Optional[datetime]:
        """Return the timestamp of the next schedule boundary for the room."""
        schedule = self._room_schedule.get(room_name)
        if schedule is None or schedule.is_empty:
            return None
        return next_transition(schedule, datetime.now())

    # ------------------------------------------------------------------
    # Setup helpers (used by services)
    # ------------------------------------------------------------------

    def simulate_thermal_response(
        self,
        room_name: str,
        initial_temp: float,
        outdoor_temp: float,
        heating_power: float,
        duration_hours: float,
    ) -> Dict[str, Any]:
        """
        Run a standalone thermal simulation to show how a room responds to
        constant heating power.  Useful during setup to verify that the
        configured thermal parameters and heater power are realistic.

        Returns a dict with keys:
            trajectory : list of {time_minutes: float, temperature: float}
            final_temperature : float
            time_constant_hours : float
            steady_state_temperature : float
        """
        if room_name not in self.model.rooms:
            return {"error": f"Room {room_name!r} not found"}

        dt = 60.0  # 1-minute steps for smooth curves
        steps = int(duration_hours * 3600 / dt)

        initial_temps = {
            name: initial_temp if name == room_name else outdoor_temp
            for name in self.model.room_names
        }
        heat_schedule = [
            {name: heating_power if name == room_name else 0.0
             for name in self.model.room_names}
            for _ in range(steps)
        ]
        outdoor_temps = [outdoor_temp] * steps
        solar_schedule = [{name: 0.0 for name in self.model.room_names}] * steps

        preds = self.model.predict(
            horizon=steps,
            dt=dt,
            heat_schedule=heat_schedule,
            outdoor_temps=outdoor_temps,
            solar_gain_schedule=solar_schedule,
            initial_temps=initial_temps,
        )

        # Sample every 5 minutes for readability
        trajectory = []
        for i, pred in enumerate(preds):
            minutes = (i + 1) * dt / 60.0
            if i % 5 == 0 or i == len(preds) - 1:
                trajectory.append({
                    "time_minutes": round(minutes, 1),
                    "temperature": round(pred[room_name], 2),
                })

        tau = self.model.time_constant(room_name)
        t_ss = self.model.steady_state_temperature(
            room_name, heating_power, outdoor_temp,
        )

        return {
            "trajectory": trajectory,
            "final_temperature": round(preds[-1][room_name], 2),
            "time_constant_hours": round(tau / 3600, 2),
            "steady_state_temperature": round(t_ss, 2),
        }

    def estimate_parameters(
        self,
        room_name: str,
        heating_power: float,
        outdoor_temp: float,
        initial_temp: float,
        final_temp: float,
        duration_seconds: float,
    ) -> Dict[str, Any]:
        """
        Estimate ``thermal_mass`` and ``r_external`` from an observed
        heating or cooling experiment.

        The user heats a room with known power and observes the temperature
        change over a known time.  This method back-calculates the thermal
        mass (from the rate of temperature change) and the external thermal
        resistance (from the steady-state balance).

        Returns a dict with recommended values and the current configuration.
        """
        if room_name not in self.model.rooms:
            return {"error": f"Room {room_name!r} not found"}

        room = self.model.rooms[room_name]
        delta_t = final_temp - initial_temp
        avg_temp = (initial_temp + final_temp) / 2.0

        # Estimate R_external from power balance at average temperature
        # Q_heater ≈ (T_avg - T_outdoor) / R_ext  →  R_ext = ΔT / Q
        temp_diff = avg_temp - outdoor_temp
        if heating_power > 0 and temp_diff > 0:
            estimated_r = temp_diff / heating_power
        else:
            estimated_r = room.r_external

        # Estimate thermal mass from rate of temperature change
        # Q_net = Q_heater - Q_loss ≈ C × ΔT / Δt
        avg_loss = temp_diff / estimated_r if estimated_r > 0 else 0
        q_net = heating_power - avg_loss
        if duration_seconds > 0 and abs(delta_t) > 0.01:
            estimated_mass = abs(q_net * duration_seconds / delta_t)
        else:
            estimated_mass = room.thermal_mass

        return {
            "estimated_thermal_mass": round(estimated_mass, 0),
            "estimated_r_external": round(estimated_r, 4),
            "current_thermal_mass": room.thermal_mass,
            "current_r_external": room.r_external,
            "notes": (
                "These are rough estimates. For thermal_mass, a typical room "
                "is 2–15 × 10⁶ J/K. For r_external, typical values are "
                "0.02–0.15 K/W. Run multiple experiments and average results."
            ),
        }

    # ------------------------------------------------------------------
    # ML parameter estimation (Kalman filter / maximum likelihood)
    # ------------------------------------------------------------------

    async def async_estimate_parameters_ml(
        self,
        apply_params: bool = True,
        horizon_hours: Optional[float] = None,
        locked_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Estimate thermal parameters using Kalman-filter maximum-likelihood.

        Runs the prediction-error decomposition (PED) log-likelihood
        optimisation over the rolling observation history buffer in a
        thread-executor so that the HA event loop is not blocked.

        Parameters
        ----------
        apply_params : bool
            When *True* (default) the estimated parameters are immediately
            applied to the live model and the MPC controller is rebuilt.
            When *False* the result is only reported (dry run).
        horizon_hours : float or None
            When provided, only the most recent ``horizon_hours`` of data
            (wall-clock) are passed to the estimator.  Use the same value
            as the sysid simulation horizon so identification and fit
            evaluation cover identical data.  ``None`` uses the full buffer.

        Returns
        -------
        dict – the result dict from :class:`~.parameter_estimator.KalmanMLEstimator`.
        """
        from .parameter_estimator import KalmanMLEstimator
        from .history_window import select_recent_window

        dt = _coerce_interval_seconds(self._update_interval_s)

        # Resolve the identification window.  When the caller does not pass an
        # explicit horizon (e.g. the Estimate-Parameters button) fall back to
        # the configured identification horizon rather than the entire rolling
        # buffer: identification must only ever see the configured data
        # horizon, never a multi-day backlog that no longer reflects the
        # current operating regime.
        eff_horizon_hours = (
            float(horizon_hours)
            if horizon_hours is not None
            else float(getattr(
                self, "_identification_horizon_hours",
                DEFAULT_IDENTIFICATION_HORIZON_HOURS,
            ))
        )

        history = list(self._history_buffer)
        if eff_horizon_hours > 0 and history:
            history = select_recent_window(
                history, eff_horizon_hours * 3600.0
            )

        # NOTE: the configured horizon controls only *which* data is used (the
        # most recent ``eff_horizon_hours`` of history, sliced above).  It does
        # NOT set the open-loop simulation window length: the estimator splits
        # the data into short fixed-length open-loop windows internally, because
        # a single multi-hour free-run of the thermal model is numerically
        # ill-conditioned and the optimiser lands in degenerate basins (mass
        # and resistance off by orders of magnitude, scales pinned to bounds).
        # The short-window default is therefore left to the estimator.
        estimator = KalmanMLEstimator(
            rooms=list(self.model.rooms.values()),
            sources=self.heat_sources,
            dt=dt,  # must match history buffer sampling interval, not MPC horizon
        )

        # Optimisation may take a few seconds; run in a thread executor.
        _estimate = lambda: estimator.estimate(history, locked_params=locked_params)  # noqa: E731
        result: Dict[str, Any] = await self.hass.async_add_executor_job(_estimate)

        if result["success"] and apply_params:
            self._apply_estimated_parameters(
                result["estimated_params"],
                result.get("estimated_inter_room_r", {}),
                estimated_internal_gains=result.get("estimated_internal_gains", {}),
                estimated_heater_scales=result.get("estimated_heater_scales", {}),
                estimated_solar_scales=result.get("estimated_solar_scales", {}),
                estimated_envelope_splits=result.get(
                    "estimated_envelope_splits", {}
                ),
                log_likelihood=result.get("log_likelihood"),
            )

        # Record the run in the rolling history – dry-runs included – so the
        # Diagnostics dashboard shows every estimation the user kicked off.
        history_buf = getattr(self, "_estimation_history", None)
        if history_buf is not None:
            history_buf.append(
                {
                    "estimated_at": datetime.now(timezone.utc).isoformat(),
                    "success": bool(result.get("success")),
                    "log_likelihood": (
                        float(result.get("log_likelihood"))
                        if isinstance(result.get("log_likelihood"), (int, float))
                        else None
                    ),
                    "applied": bool(result.get("success")) and apply_params,
                    "n_rooms": len(self.model.room_names),
                    "n_sources": len(self.heat_sources),
                }
            )

        return result

    async def async_compute_loglik_slice(
        self,
        room_name: str,
        n_grid: int = 11,
        span_log: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """Run :meth:`KalmanMLEstimator.compute_loglik_slice` off-thread.

        Returns ``None`` when the room is unknown or the history buffer is
        too short. The grid is centred on the current parameter values
        and cached on ``self._loglik_slices[room_name]`` so the matching
        :class:`LoglikSliceSensor` can expose it without recomputing.
        """
        from .parameter_estimator import KalmanMLEstimator

        estimator = KalmanMLEstimator(
            rooms=list(self.model.rooms.values()),
            sources=self.heat_sources,
            dt=_coerce_interval_seconds(self._update_interval_s),
        )

        history = list(self._history_buffer)
        result = await self.hass.async_add_executor_job(
            estimator.compute_loglik_slice,
            history,
            room_name,
            int(n_grid),
            float(span_log),
        )
        if result is not None:
            slices = getattr(self, "_loglik_slices", None)
            if slices is None:
                slices = {}
                self._loglik_slices = slices
            slices[room_name] = {
                "computed_at": datetime.now(timezone.utc).isoformat(),
                **result,
            }
            self.async_update_listeners()
        return result

    def _apply_estimated_parameters(
        self,
        estimated_params: Dict[str, Dict[str, float]],
        estimated_inter_room_r: Optional[Dict[str, float]] = None,
        estimated_internal_gains: Optional[Dict[str, float]] = None,
        estimated_heater_scales: Optional[Dict[str, float]] = None,
        estimated_solar_scales: Optional[Dict[str, float]] = None,
        estimated_envelope_splits: Optional[Dict[str, Dict[str, float]]] = None,
        log_likelihood: Optional[float] = None,
    ) -> None:
        """
        Apply estimated parameters to the house model and rebuild the
        MPC controller so that the new values take effect immediately.

        The existing Kalman filter state is discarded when the controller
        is rebuilt; it will be re-bootstrapped on the next update cycle.
        The full parameter snapshot is also persisted to ``entry.data`` via
        ``async_update_entry`` so that values survive a full HA restart.
        """
        for room_name, params in estimated_params.items():
            if room_name not in self.model.rooms:
                continue
            room = self.model.rooms[room_name]
            room.thermal_mass = float(params["thermal_mass"])
            room.r_external = float(params["r_external"])
            if estimated_internal_gains and room_name in estimated_internal_gains:
                room.internal_gain = float(estimated_internal_gains[room_name])
            if estimated_solar_scales and room_name in estimated_solar_scales:
                room.solar_scale = float(estimated_solar_scales[room_name])
            if estimated_envelope_splits and room_name in estimated_envelope_splits:
                splits = estimated_envelope_splits[room_name]
                if "c_air_fraction" in splits:
                    room.c_air_fraction = float(splits["c_air_fraction"])
                if "r_aw_fraction" in splits:
                    room.r_aw_fraction = float(splits["r_aw_fraction"])

        # Apply per-source heater power-scale factors.
        if estimated_heater_scales:
            for src in self.heat_sources:
                if src.name in estimated_heater_scales:
                    src.power_scale = float(estimated_heater_scales[src.name])

        # Apply inter-room resistances if estimated (Stage 2 result)
        if estimated_inter_room_r:
            for key, r_val in estimated_inter_room_r.items():
                parts = key.split(":")
                if len(parts) != 2:
                    continue
                room_a, room_b = parts
                for name in (room_a, room_b):
                    if name not in self.model.rooms:
                        continue
                    other = room_b if name == room_a else room_a
                    for conn in self.model.rooms[name].connections:
                        if conn.connected_room == other:
                            conn.r_value = float(r_val)
            _LOGGER.info(
                "Applied estimated inter-room resistances: %s", estimated_inter_room_r
            )

        # Rebuild the internal model matrices (A, B_ext, C).
        # Must call rebuild_derived_parameters() first so the cached per-room
        # arrays (_leakage_area, _B_sky_offset) are re-derived from the new
        # room.thermal_mass / room.r_external values before _build_matrices()
        # reads them.
        self.model.rebuild_derived_parameters()
        (
            self.model._C,
            self.model._A,
            self.model._B_ext,
        ) = self.model._build_matrices()

        # Rebuild the MPC controller with the updated model
        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            albedo=getattr(self, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=self._tracking_weight,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            soft_constraint_weight=self._soft_constraint_weight,
            soft_constraint_linear_weight=self._soft_constraint_linear_weight,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            energy_price_weight=self._energy_price_weight,
        )

        _LOGGER.info(
            "Applied estimated thermal parameters: %s",
            {
                name: {
                    "thermal_mass": self.model.rooms[name].thermal_mass,
                    "r_external": self.model.rooms[name].r_external,
                }
                for name in estimated_params
                if name in self.model.rooms
            },
        )

        # --- Persist the snapshot so values survive a full HA restart -------
        now_iso = datetime.now(timezone.utc).isoformat()
        snapshot: Dict[str, Any] = {
            "rooms": {
                name: {
                    "thermal_mass": self.model.rooms[name].thermal_mass,
                    "r_external": self.model.rooms[name].r_external,
                    "internal_gain": float(
                        getattr(self.model.rooms[name], "internal_gain", 0.0)
                    ),
                    "solar_scale": float(
                        getattr(self.model.rooms[name], "solar_scale", 1.0)
                    ),
                    "c_air_fraction": float(
                        getattr(self.model.rooms[name], "c_air_fraction", 0.05)
                    ),
                    "r_aw_fraction": float(
                        getattr(self.model.rooms[name], "r_aw_fraction", 0.05)
                    ),
                }
                for name in self.model.room_names
            },
            "sources": {
                src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
                for src in self.heat_sources
            },
            "connections": dict(estimated_inter_room_r) if estimated_inter_room_r else {},
            "estimated_at": now_iso,
            "log_likelihood": log_likelihood,
        }
        self._estimation_timestamp = now_iso
        self._estimation_log_likelihood = log_likelihood

        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snapshot},
            )
            _LOGGER.debug("Persisted estimated parameter snapshot to entry.data")
