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

import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import (
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
    CONF_PERSISTED_ROOM_ENABLED,
    CONF_PERSISTED_SYSTEM_ENABLED,
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
    EXPERIMENT_RELAXED_COMFORT_OFFSET,
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
    CONF_IDENTIFICATION_HISTORY_DAYS,
    DEFAULT_IDENTIFICATION_HISTORY_DAYS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PLOT_FORECAST_HOURS,
    DEFAULT_PLOT_HISTORY_HOURS,
    DEFAULT_PLOT_FORECAST_HOURS,
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
    SOURCE_TYPE_GENERIC_THERMOSTAT,
    SOURCE_TYPE_OIL_RADIATOR,
    SOURCE_TYPE_ELECTRIC_FLOOR,
    SOURCE_TYPE_GAS_HEATER,
    SOURCE_TYPE_HYDRONIC_RADIATOR,
    SOURCE_TYPE_HYDRONIC_FLOOR,
    SOURCE_TYPE_OIL_BOILER,
    SOURCE_TYPE_GROUND_SOURCE_HP,
    SOURCE_TYPE_PELLET_STOVE,
    SOURCE_TYPE_ELECTRIC_STORAGE,
    DEFAULT_GAS_EFFICIENCY,
    DEFAULT_OIL_BOILER_EFFICIENCY,
    DEFAULT_GROUND_SOURCE_COP,
    DEFAULT_PELLET_EFFICIENCY,
    DEFAULT_PELLET_MIN_POWER_FRACTION,
    DEFAULT_STORAGE_CHARGE_POWER,
    DEFAULT_STORAGE_CAPACITY_KWH,
    DEFAULT_STORAGE_DISCHARGE_RATE,
    CONF_SOURCE_DELTA_SAT,
    CONF_SOURCE_MIN_POWER_FRACTION,
    CONF_SOURCE_CHARGE_POWER,
    CONF_SOURCE_STORAGE_CAPACITY_KWH,
    CONF_SOURCE_PASSIVE_DISCHARGE_RATE,
    DEFAULT_DELTA_SAT,
    SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU,
    UPDATE_INTERVAL,
)
from ..heat_sources import (
    ElectricHeater,
    ElectricStorageHeater,
    GasHeater,
    GenericThermostat,
    GroundSourceHeatPump,
    HeatPump,
    HeatSource,
    HydronicRadiator,
    PelletStove,
)
from ..thermal_model import HouseModel, Room, RoomConnection, Window
from ..controller import HeatingMPCController
from ..controller.factory import ControllerBuildConfig, build_mpc_controller
from . import (
    actuation,
    disturbances,
    enablement,
    forecast_payload,
    schedule_control,
    window,
)
from .model_builders import build_heat_sources, build_house_model
from .types import (
    ControlTrajectory,
    ControllerConfigSnapshot,
    _coerce_interval_seconds,
    _coerce_opt_float,
)
from ..ground_temp import ground_temperature
from ..solar_model import (
    horizontal_irradiance,
)
from ..schedule import (
    EffectiveControlParams,
    EffectiveSetpoint,
    RoomSchedule,
    build_schedule,
    control_params_at,
    resolve_effective_setpoint,
)
from .. import weather as _weather
from ..experiments import (
    ExperimentManager,
    apply_safety_bounds,
    ceil_to_grid,
    excitation_fraction,
)
from ..datasets import build_dataset

_LOGGER = logging.getLogger(__name__)

# Number of consecutive coordinator cycles with no valid outdoor temperature
# before an UpdateFailed is raised (only applies when no prior reading was ever
# obtained — i.e., the entity was never reachable since startup).
# With the default 15-minute update interval this gives ~45 minutes of grace.
_OUTDOOR_TEMP_MAX_STARTUP_FAILURES = 3

# Maximum number of EKF prediction steps to propagate when filling a stop/start
# gap.  Beyond this limit the persisted state is still restored (better than a
# cold start) but no forward propagation is attempted.  At a 15-minute update
# interval this caps the gap at 4 days.
_MAX_EKF_GAP_STEPS = 384


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
        CONF_PERSISTED_COMFORT_OFFSETS,
        CONF_PERSISTED_ROOM_ENABLED,
        CONF_PERSISTED_SYSTEM_ENABLED,
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
        CONF_PRICE_ENTITY,
        CONF_PLOT_HISTORY_HOURS,
        CONF_PLOT_FORECAST_HOURS,
        CONF_IDENTIFICATION_HISTORY_DAYS,
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
        self._identification_history_days: int = int(
            options.get(
                CONF_IDENTIFICATION_HISTORY_DAYS,
                data.get(CONF_IDENTIFICATION_HISTORY_DAYS, DEFAULT_IDENTIFICATION_HISTORY_DAYS),
            )
        )
        # Industrial-panel plot display settings (Configuration → Display).
        # These never affect the controller — only how the custom dashboard
        # renders history / forecast windows.  ``plot_forecast_hours == 0``
        # means "use the full controller horizon" (the historical behaviour).
        self._plot_history_hours: float = float(
            options.get(
                CONF_PLOT_HISTORY_HOURS,
                data.get(CONF_PLOT_HISTORY_HOURS, DEFAULT_PLOT_HISTORY_HOURS),
            )
        )
        self._plot_forecast_hours: float = float(
            options.get(
                CONF_PLOT_FORECAST_HOURS,
                data.get(CONF_PLOT_FORECAST_HOURS, DEFAULT_PLOT_FORECAST_HOURS),
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
        self.heat_sources: List[HeatSource] = self._drop_orphaned_sources(
            build_heat_sources(sources_cfg)
        )
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

        self.controller = build_mpc_controller(
            ControllerBuildConfig.from_coordinator(self)
        )
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

    _build_window_sensor_map = staticmethod(window.build_window_sensor_map)

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
        # Restore the global START/STOP toggle from the config entry so a full
        # HA restart resumes in the same running or stopped state.  State
        # estimation and logging still run while stopped; only the MPC
        # optimisation and actuator commands are gated on this flag.
        self._system_enabled: bool = bool(
            self._entry.data.get(CONF_PERSISTED_SYSTEM_ENABLED, False)
        )
        self._room_enabled: Dict[str, bool] = {name: True for name in room_names}
        self._schedule_disabled: Dict[str, bool] = {name: False for name in room_names}

        self._room_schedule: Dict[str, RoomSchedule] = {}
        self._base_setpoint: Dict[str, float] = {}
        self._schedule_enabled: Dict[str, bool] = {}
        # Per-room default comfort_offset, used as fallback when no schedule
        # period overrides it and to seed the control trajectory builder.
        self._room_comfort_offset: Dict[str, float] = {}
        self._window_sensors: Dict[str, List[str]] = window.build_window_sensor_map(rooms_cfg)
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

        # Overlay with user-controlled room-enabled state persisted across restarts.
        persisted_enabled: Dict[str, Any] = self._entry.data.get(CONF_PERSISTED_ROOM_ENABLED, {})
        for room_name, value in persisted_enabled.items():
            if room_name in self._room_enabled:
                self._room_enabled[room_name] = bool(value)

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
        return window.read_binary_sensor_on(self, entity_id)

    def _set_window_state(
        self,
        room_name: str,
        state: str,
        now_utc: datetime,
    ) -> None:
        window.set_window_state(self, room_name, state, now_utc)

    def _update_window_state_machine(self, now_utc: datetime) -> None:
        window.update_window_state_machine(self, now_utc)

    def get_window_state(self, room_name: str) -> str:
        return window.get_window_state(self, room_name)

    def is_window_override_active(self, room_name: str) -> bool:
        return window.is_window_override_active(self, room_name)

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
        # EKF state loaded from the previous session; injected into the
        # controller on the first compute cycle then cleared.
        # Tuple: (x_hat, P, save_timestamp, u_prev, d_prev)
        self._pending_ekf_state: Optional[
            Tuple[np.ndarray, np.ndarray, float, np.ndarray, np.ndarray]
        ] = None
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

        # Per-room rolling 24 h time-in-range [%] from recorder history; refreshed
        # periodically by ``kpi_history.async_refresh_time_in_range_kpis``.
        self._time_in_range_pct_24h: Dict[str, Optional[float]] = {}
        self._time_in_range_last_refresh_ts: float = 0.0

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

        # Integration-managed identification history store (set by async_setup_entry
        # after the coordinator is created; None until then).
        self.id_history_store: Optional[Any] = None

        # System-identification experiments and stored datasets.
        # ``experiment_manager`` holds the live experiment state machine and is
        # populated from ``experiment_store`` (a persistent Store) during setup.
        # ``dataset_store`` persists named, snapshotted identification datasets.
        # Both stores are attached by async_setup_entry; until then the manager
        # is empty and the stores are None.
        self.experiment_manager: ExperimentManager = ExperimentManager()
        self.experiment_store: Optional[Any] = None
        self.dataset_store: Optional[Any] = None
        # Room slugs whose heaters the active experiment is exciting *this*
        # cycle; consumed by _apply_actions so the excitation bypasses the
        # normal schedule-off / room-disabled gating.
        self._experiment_active_rooms: Set[str] = set()
        # Per-canonical-room boolean list (length = horizon) marking which MPC
        # steps an experiment governs this cycle.  Drives the comfort relaxation
        # over those steps and the per-step ``experiment`` flag in the forecast.
        self._experiment_horizon_steps: Dict[str, List[bool]] = {}


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
        if isinstance(data, dict):
            if self._cloud_cover_filtered is None:
                cc = data.get("cloud_cover_filtered")
                if cc is not None:
                    try:
                        self._cloud_cover_filtered = max(0.0, min(1.0, float(cc)))
                    except (TypeError, ValueError):
                        pass
            ekf_x_hat = data.get("ekf_x_hat")
            ekf_P = data.get("ekf_P")
            ekf_save_ts = data.get("ekf_save_ts")
            ekf_u_prev = data.get("ekf_u_prev")
            ekf_d_prev = data.get("ekf_d_prev")
            if ekf_x_hat is not None and ekf_P is not None:
                try:
                    x_hat = np.array(ekf_x_hat, dtype=float)
                    P_mat = np.array(ekf_P, dtype=float)
                    save_ts = float(ekf_save_ts) if ekf_save_ts is not None else 0.0
                    u_prev = (
                        np.array(ekf_u_prev, dtype=float)
                        if ekf_u_prev is not None else None
                    )
                    d_prev = (
                        np.array(ekf_d_prev, dtype=float)
                        if ekf_d_prev is not None else None
                    )
                    self._pending_ekf_state = (x_hat, P_mat, save_ts, u_prev, d_prev)
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
        """Persist cloud cover and EKF state (throttled) so they survive restarts."""
        try:
            def _snapshot() -> Dict[str, Any]:
                payload: Dict[str, Any] = {
                    "cloud_cover_filtered": self._cloud_cover_filtered,
                }
                try:
                    import time as _time
                    x_hat, P = self.controller.ekf_state
                    u_prev, d_prev = self.controller.ekf_inputs
                    payload["ekf_x_hat"] = x_hat.tolist()
                    payload["ekf_P"] = P.tolist()
                    payload["ekf_save_ts"] = _time.time()
                    payload["ekf_u_prev"] = u_prev.tolist()
                    payload["ekf_d_prev"] = d_prev.tolist()
                except Exception:  # pragma: no cover - defensive
                    pass
                return payload

            self._runtime_store.async_delay_save(_snapshot, RUNTIME_STATE_SAVE_DELAY_S)
        except Exception:  # pragma: no cover - defensive; never block a cycle
            pass

    def _propagate_ekf_gap(
        self,
        save_ts: float,
        u_prev: Optional[np.ndarray],
        d_prev: Optional[np.ndarray],
    ) -> None:
        """Propagate the EKF forward from save_ts to now without measurement updates.

        Fills the time gap that accumulates during a system stop or HA restart
        so the restored state is consistent with the current wall-clock time.
        The actuator signal for each gap step is chosen by priority:

        1. Experiment excitation — if an identification experiment was scheduled
           to run at that step it prescribes the exact actuator fraction.
        2. Schedule off-period — rooms in an off period contribute u=0 for those
           sources (heating was logically off, so propagating as if it were on
           would bias the wall-temperature estimate).
        3. Fallback — the last commanded actuator (u_prev) is used, which is the
           best available proxy for what was actually delivered during the gap.

        The disturbance (outdoor temp + solar gains) is held constant at the
        last saved value (d_prev) since we have no measurements for the gap.
        """
        if save_ts <= 0.0:
            return
        import time as _time
        now_ts = _time.time()
        gap_s = now_ts - save_ts
        if gap_s <= 0.0:
            return
        dt = float(self.dt)
        n_gap = min(round(gap_s / dt), _MAX_EKF_GAP_STEPS)
        if n_gap <= 0:
            return

        u_default = (
            np.asarray(u_prev, dtype=float)
            if u_prev is not None
            else np.zeros(len(self.heat_sources), dtype=float)
        )
        d_arr = (
            np.asarray(d_prev, dtype=float)
            if d_prev is not None
            else np.zeros(self._system_nd(), dtype=float)
        )

        try:
            u_seq = self._build_gap_u_sequence(save_ts, n_gap, dt, u_default)
            self.controller.propagate_ekf(u_seq, d_arr)
            _LOGGER.debug(
                "EKF gap propagation: %.0f s gap → %d steps (max %d)",
                gap_s, n_gap, _MAX_EKF_GAP_STEPS,
            )
        except Exception:  # pragma: no cover - defensive; never block startup
            _LOGGER.debug(
                "EKF gap propagation failed; state restored without propagation",
                exc_info=True,
            )

    def _system_nd(self) -> int:
        """Return the disturbance dimension of the current controller model."""
        try:
            return self.controller._mpc._model.nd
        except Exception:
            return 1 + 2 * len(self.model.room_names)

    def _build_gap_u_sequence(
        self,
        start_ts: float,
        n_steps: int,
        dt: float,
        u_default: np.ndarray,
    ) -> np.ndarray:
        """Build per-step actuator commands for EKF gap propagation.

        For each step k at time t_k = start_ts + k*dt:
        - Uses experiment excitation fractions where an experiment was active.
        - Uses u=0 for sources whose room was in an off-period (comfort schedule).
        - Falls back to u_default everywhere else.
        """
        from ..dashboard import slugify as _slugify

        n_sources = len(self.heat_sources)
        u_seq = np.empty((n_steps, n_sources), dtype=float)
        u_seq[:] = u_default  # broadcast default over all steps

        manager = getattr(self, "experiment_manager", None)

        for k in range(n_steps):
            t_k = start_ts + k * dt

            for i, src in enumerate(self.heat_sources):
                room_slug = _slugify(src.room)
                can_cool = bool(getattr(src, "can_cool", False))

                # Priority 1: active experiment prescribes the exact fraction
                if manager is not None:
                    exp = manager.active_for_room(room_slug, t_k)
                    if exp is not None:
                        u_seq[k, i] = excitation_fraction(exp, t_k, can_cool=can_cool)
                        continue

                # Priority 2: comfort schedule off-period → zero input
                room_name = src.room
                schedule = self._room_schedule.get(room_name)
                if schedule is not None and self._schedule_enabled.get(room_name, True):
                    base_sp = self._base_setpoint.get(room_name, DEFAULT_SETPOINT)
                    default_off = self._room_comfort_offset.get(room_name, DEFAULT_COMFORT_OFFSET)
                    t_local = datetime.fromtimestamp(t_k)
                    params = control_params_at(schedule, base_sp, t_local, default_off)
                    if params is None:  # off period
                        u_seq[k, i] = 0.0

        return u_seq

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

    def _drop_orphaned_sources(
        self, sources: List[HeatSource]
    ) -> List[HeatSource]:
        """Drop heat sources whose room is not in the current model.

        A source references its room by name; deleting or renaming a room can
        leave a source pointing at a room that no longer exists.  The controller
        looks that name up in its room index, so an orphaned source raises
        ``KeyError`` and prevents the whole integration from starting.  Dropping
        it (with a warning) keeps setup resilient to stale configuration.

        If the source's room name is a slug form of a known canonical room name
        (e.g. ``"living_room"`` vs ``"Living Room"``), the reference is corrected
        in-memory so the controller sees the right name.  This handles setups
        where heat sources were originally configured with slug-style room names.
        """
        from ..dashboard import slugify as _slugify

        known_rooms = set(self.model.rooms)
        slug_to_canonical: Dict[str, str] = {
            _slugify(name): name for name in known_rooms
        }
        valid: List[HeatSource] = []
        for s in sources:
            if s.room in known_rooms:
                valid.append(s)
                continue
            # Fallback: check if the room name is a slug of a known canonical name.
            canonical = slug_to_canonical.get(_slugify(s.room))
            if canonical is not None:
                _LOGGER.info(
                    "Heating Assistant: heat source %r references room %r by "
                    "slug form; correcting to canonical name %r.",
                    getattr(s, "name", s),
                    s.room,
                    canonical,
                )
                s.room = canonical
                valid.append(s)
            else:
                _LOGGER.warning(
                    "Heating Assistant: dropping heat source %r — its room %r "
                    "is not configured",
                    getattr(s, "name", s),
                    s.room,
                )
        return valid

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
        # Snapshot EKF state before rebuilding so it is not lost on hot reconfig.
        _prior_ekf: Optional[Tuple[np.ndarray, np.ndarray]] = None
        if hasattr(self, "controller"):
            try:
                _prior_ekf = self.controller.ekf_state
            except Exception:
                pass

        self.controller = build_mpc_controller(
            ControllerBuildConfig.from_coordinator(self)
        )

        if _prior_ekf is not None:
            x_hat, P = _prior_ekf
            self.controller.restore_ekf_state(x_hat, P)

    def get_controller_config_snapshot(self) -> ControllerConfigSnapshot:
        """Return JSON-serialisable MPC tuning parameters for UI / WebSocket.

        Single source of truth for controller config exposed via WebSocket and
        mirrored in :class:`ControllerConfigSensor` attributes.
        """
        ui = self.update_interval
        return {
            "comfort_offset": float(
                next(iter(getattr(self, "_room_comfort_offset", {}).values()), 2.0)
            ),
            "tracking_weight": float(self._tracking_weight),
            "energy_weight": float(self._energy_weight),
            "energy_price_weight": float(self._energy_price_weight),
            "smoothing_weight": float(self._smoothing_weight),
            "soft_constraint_weight": float(self._soft_constraint_weight),
            "soft_constraint_linear_weight": float(
                self._soft_constraint_linear_weight
            ),
            "terminal_weight": float(self._terminal_weight),
            "horizon": int(self._horizon),
            "update_interval": int(
                ui.total_seconds() if hasattr(ui, "total_seconds") else ui
            ),
            "window_open_debounce": int(self._window_open_debounce),
            "window_open_close_settle": int(self._window_open_close_settle),
            "window_open_q_inflation": float(self._window_open_q_inflation),
        }

    def serialize_room_schedules(self) -> Dict[str, Any]:
        return schedule_control.serialize_room_schedules(self)

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

        if CONF_PRICE_ENTITY in pending:
            self._price_entity = (str(pending.get(CONF_PRICE_ENTITY) or "") or None)
        if CONF_PLOT_HISTORY_HOURS in pending:
            self._plot_history_hours = float(
                pending.get(CONF_PLOT_HISTORY_HOURS, self._plot_history_hours)
            )
        if CONF_PLOT_FORECAST_HOURS in pending:
            self._plot_forecast_hours = float(
                pending.get(CONF_PLOT_FORECAST_HOURS, self._plot_forecast_hours)
            )
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
        if CONF_IDENTIFICATION_HISTORY_DAYS in pending:
            self._identification_history_days = int(
                pending.get(CONF_IDENTIFICATION_HISTORY_DAYS, self._identification_history_days)
            )
            if self.id_history_store is not None:
                self.id_history_store.update_retention_days(self._identification_history_days)
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
        self,
        room_names: Optional[List[str]] = None,
        plot_forecast_steps: Optional[int] = None,
        *,
        predictions: Optional[list] = None,
        linearised_predictions: Optional[list] = None,
        heating_schedule: Optional[list] = None,
        outdoor_forecast: Optional[List[float]] = None,
        solar_forecast: Optional[list] = None,
        price_forecast: Optional[List[float]] = None,
        control_trajectory: Optional["ControlTrajectory"] = None,
        step_dt: Optional[float] = None,
    ) -> dict:
        return forecast_payload.build_forecast_payload(
            self,
            room_names,
            plot_forecast_steps,
            predictions=predictions,
            linearised_predictions=linearised_predictions,
            heating_schedule=heating_schedule,
            outdoor_forecast=outdoor_forecast,
            solar_forecast=solar_forecast,
            price_forecast=price_forecast,
            control_trajectory=control_trajectory,
            step_dt=step_dt,
        )

    def preview_tuning_forecast(
        self,
        tuning_overrides: Dict[str, Any],
        plot_forecast_steps: Optional[int] = None,
        weather: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Run a one-off MPC solve with proposed tuning parameters.

        Does not persist or apply the overrides.  Returns the same forecast
        payload shape as :meth:`build_forecast_payload` so the dashboard can
        render room-view plots for every room from a single solve.
        """
        from ..controller import HeatingMPCController
        from ..ground_temp import ground_temperature

        overrides = dict(tuning_overrides or {})
        weather = dict(weather or {})

        outdoor_temp = self.outdoor_temp
        if outdoor_temp is None:
            outdoor_temp = self._last_valid_outdoor_temp
        if outdoor_temp is None:
            return {"error": "outdoor_temperature_unavailable"}

        preview_horizon = int(overrides.get(CONF_HORIZON, self._horizon))
        preview_dt = float(overrides.get(CONF_UPDATE_INTERVAL, self._update_interval_s))
        comfort_override = overrides.get(CONF_COMFORT_OFFSET)

        preview_ctrl = build_mpc_controller(
            ControllerBuildConfig.from_coordinator(self, overrides=overrides)
        )

        if hasattr(self, "controller"):
            try:
                x_hat, P = self.controller.ekf_state
                preview_ctrl.restore_ekf_state(x_hat, P)
            except Exception:
                _LOGGER.debug(
                    "preview_tuning_forecast: could not copy EKF state",
                    exc_info=True,
                )

        now = getattr(self, "now_utc", None) or datetime.now(tz=timezone.utc)
        now_local = now.astimezone()

        if comfort_override is not None:
            saved_offsets = dict(self._room_comfort_offset)
            try:
                preview_comfort = float(comfort_override)
                for room_name in self._room_comfort_offset:
                    self._room_comfort_offset[room_name] = preview_comfort
                preview_traj = self._compute_control_trajectory(
                    now_local, preview_horizon, preview_dt
                )
            finally:
                self._room_comfort_offset = saved_offsets
        else:
            preview_traj = self._compute_control_trajectory(
                now_local, preview_horizon, preview_dt
            )

        disabled_src_names = {
            src.name
            for src in self.heat_sources
            if not self.is_room_enabled(src.room)
            or self.is_window_override_active(src.room)
        }
        experiment_clamps = (
            self._build_experiment_clamps(now) if self._system_enabled else {}
        )

        cloud_cover_now = weather.get("cloud_cover_now")
        cloud_forecast = weather.get("cloud_forecast")
        ghi_now = weather.get("ghi_now")
        ghi_forecast = weather.get("ghi_forecast")
        wind_forecast = weather.get("wind_forecast")

        if hasattr(preview_ctrl, "set_wind_speed"):
            preview_ctrl.set_wind_speed(self._read_wind_speed_now())
        if hasattr(preview_ctrl, "set_cloud_cover"):
            preview_ctrl.set_cloud_cover(cloud_cover_now)
        if hasattr(preview_ctrl, "set_ground_temp"):
            preview_ctrl.set_ground_temp(ground_temperature(now))
        if hasattr(preview_ctrl, "set_room_process_noise_covariance_scales"):
            q_scale = {
                room_name: (
                    self._window_open_q_inflation
                    if self.is_window_override_active(room_name)
                    else 1.0
                )
                for room_name in self.model.room_names
            }
            preview_ctrl.set_room_process_noise_covariance_scales(q_scale)

        outdoor_fc = list(self.outdoor_forecast or [])
        price_fc = list(self.price_forecast or [])

        preview_ctrl.compute(
            outdoor_temp=outdoor_temp,
            solar_gains=self.solar_gains,
            now=now,
            outdoor_forecast=outdoor_fc if outdoor_fc else None,
            cloud_forecast=cloud_forecast,
            cloud_cover_now=cloud_cover_now,
            ghi_forecast=ghi_forecast,
            ghi_now=ghi_now,
            wind_forecast=wind_forecast,
            disabled_sources=disabled_src_names or None,
            control_trajectory=preview_traj,
            price_forecast=price_fc if price_fc else None,
            input_clamps=experiment_clamps or None,
            run_optimization=True,
        )

        return self.build_forecast_payload(
            plot_forecast_steps=plot_forecast_steps,
            predictions=preview_ctrl.predictions,
            linearised_predictions=preview_ctrl.linearised_predictions,
            heating_schedule=preview_ctrl.heating_schedule,
            outdoor_forecast=preview_ctrl.outdoor_forecast,
            solar_forecast=preview_ctrl.solar_forecast,
            price_forecast=preview_ctrl.price_forecast or price_fc,
            control_trajectory=preview_traj,
            step_dt=preview_dt,
        )

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
        self.heat_sources = self._drop_orphaned_sources(
            build_heat_sources(sources_cfg)
        )

        # Restore user-controlled setpoints so that "Reset to Defaults"
        # only affects estimated thermal parameters (thermal_mass,
        # r_external) and not the user's chosen comfort setpoints.
        for _room_name, _sp in self._base_setpoint.items():
            if _room_name in self.model.rooms:
                self.model.rooms[_room_name].setpoint = _sp
        self._rebuild_sources_by_room()

        self._build_controller()

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
        self._build_controller()

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
        self._build_controller()

        # --- Build new active snapshot ---
        # Persist the full per-room parameter set (not just C / R_ext) so the
        # restart-time restore re-applies the identified envelope split, solar
        # scale and internal gain rather than reverting them to defaults.
        prev_rooms = old_active.get("rooms", {}) or existing_snap.get("rooms", {})

        def _room_snapshot_entry(r_name: str, r_obj: Any) -> Dict[str, Any]:
            prev = prev_rooms.get(r_name, {}) if isinstance(prev_rooms.get(r_name), dict) else {}
            just_estimated = r_name == room_name
            entry: Dict[str, Any] = {
                "thermal_mass": r_obj.thermal_mass,
                "r_external": r_obj.r_external,
                "internal_gain": float(getattr(r_obj, "internal_gain", 0.0)),
                "solar_scale": float(getattr(r_obj, "solar_scale", 1.0)),
                "c_air_fraction": float(getattr(r_obj, "c_air_fraction", 0.05)),
                "r_aw_fraction": float(getattr(r_obj, "r_aw_fraction", 0.05)),
                "is_estimated": bool(just_estimated or prev.get("is_estimated", False)),
            }
            if just_estimated:
                entry["estimated_at"] = now_iso
                entry["estimation_source"] = source
            elif prev.get("estimated_at"):
                entry["estimated_at"] = prev.get("estimated_at")
                if prev.get("estimation_source"):
                    entry["estimation_source"] = prev.get("estimation_source")
            return entry

        new_active: Dict[str, Any] = {
            "rooms": {
                name: _room_snapshot_entry(name, r)
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
        self._build_controller()

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

    def delete_parameter_history(self, history_index: int) -> None:
        """Delete a single entry from the persisted parameter history.

        The parameter history is a system-wide list of past parameter snapshots
        (most recent first), shared by every room.  ``history_index`` is the
        zero-based position in that list.  The active parameter set is never
        touched — only a stored historical entry is removed — so this is purely a
        housekeeping operation and does not rebuild the model or controller.
        """
        existing_snap = {}
        try:
            existing_snap = self.estimated_params_snapshot or {}
        except Exception:
            pass

        history = list(existing_snap.get("history", []))
        if history_index < 0 or history_index >= len(history):
            raise ValueError(
                f"history_index {history_index} out of range "
                f"(0..{len(history) - 1})"
            )

        history.pop(history_index)

        new_snap = dict(existing_snap)
        new_snap["history"] = history

        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            self.hass.config_entries.async_update_entry(
                real_entry,
                data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: new_snap},
            )
        _LOGGER.info("Deleted parameter history entry at index %d", history_index)

    def reload_room_schedule(self, room_name: str, periods_raw: list) -> None:
        schedule_control.reload_room_schedule(self, room_name, periods_raw)

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
            now_local = self.now_utc.astimezone()
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
            # Restore persisted EKF state (x̂, P) on the first cycle after a
            # stop/start so the wall/envelope temperature estimate is not lost.
            # If there is a gap between the save time and now, propagate the
            # EKF forward through it so the state is consistent with the
            # current wall-clock time.  The actuator sequence used for the
            # gap respects experiment prescriptions and schedule off-periods
            # (timing unification) rather than blindly using the last command.
            if self._pending_ekf_state is not None:
                x_hat, P, save_ts, u_prev, d_prev = self._pending_ekf_state
                self._pending_ekf_state = None
                if self.controller.restore_ekf_state(x_hat, P):
                    self._propagate_ekf_gap(save_ts, u_prev, d_prev)
                else:
                    _LOGGER.debug(
                        "Persisted EKF state has incompatible dimensions — "
                        "starting from cold initial conditions"
                    )
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
            # System-identification experiments: advance their lifecycle and build
            # per-room input clamps over the horizon.  Passing these into the MPC
            # makes it plan *around* the prescribed excitation (so the actuator
            # forecast already shows the experiment) instead of overriding the
            # chosen action afterwards.  Only while the controller is engaged.
            if self._system_enabled:
                experiment_clamps = self._build_experiment_clamps(self.now_utc)
                self._relax_experiment_comfort(control_traj)
            else:
                experiment_clamps = {}
                self._experiment_active_rooms = set()
                self._experiment_horizon_steps = {}
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
                    input_clamps=experiment_clamps or None,
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

            # 4c. System-identification experiments are now applied *inside* the
            # MPC via per-room input clamps (built above and passed to
            # ``compute``), so the chosen action, recorded input ``u`` and the
            # planned trajectory all already reflect the excitation — no
            # post-solve override is needed.

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
                    # Per-room data-quality label: True while a window override is
                    # active (state ``open``/``pending_closed``).  These samples
                    # carry unmodelled air-exchange losses, so system
                    # identification excludes the affected room at those steps and
                    # the reconstruction/open-loop diagnostics render them as gaps.
                    # Uses the *same* predicate as the real-time EKF Q-inflation so
                    # "don't trust the model here" has a single definition.
                    "window_open": {
                        name: self.is_window_override_active(name)
                        for name in self.model.room_names
                    },
                })

                if self.id_history_store is not None:
                    await self.id_history_store.async_append(
                        self._history_buffer[-1]
                    )
                    await self.id_history_store.async_purge_old()

            await self._async_refresh_time_in_range_kpis_if_due()

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

    async def _async_refresh_time_in_range_kpis_if_due(self) -> None:
        """Rate-limited refresh of per-room 24 h time-in-range KPI cache."""
        from ..kpi_history import (  # noqa: PLC0415 — HA import boundary
            TIME_IN_RANGE_REFRESH_S,
            async_refresh_time_in_range_kpis,
        )

        now_ts = self.now_utc.timestamp()
        if (
            self._time_in_range_last_refresh_ts > 0.0
            and now_ts - self._time_in_range_last_refresh_ts < TIME_IN_RANGE_REFRESH_S
        ):
            return

        self._time_in_range_pct_24h = await async_refresh_time_in_range_kpis(
            self.hass, self, now_ts,
        )
        self._time_in_range_last_refresh_ts = now_ts

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

        cancel = async_track_state_change_event(self.hass, entity_ids, _on_entity_ready)

        # After a HA restart, watched entities are often already reporting valid
        # states when this listener is registered, so no unknown→valid transition
        # ever fires and the coordinator would otherwise sit idle until the next
        # scheduled interval.  Request a refresh when any watched entity is
        # already readable.
        if any(
            (state := self.hass.states.get(eid)) is not None
            and state.state not in _UNAVAILABLE
            for eid in entity_ids
        ):
            _LOGGER.debug(
                "Startup: watched entities already available — requesting "
                "coordinator refresh",
            )
            self.hass.async_create_task(self.async_request_refresh())

        return cancel

    def setup_window_listeners(self) -> Optional[Callable]:
        return window.setup_window_listeners(self)

    def _read_outdoor_temp(self) -> Optional[float]:
        return disturbances.read_outdoor_temp(self)

    async def _async_read_weather_forecast(self) -> Optional[List[float]]:
        return await disturbances.async_read_weather_forecast(self)

    async def _async_read_cloud_forecast(
        self, cloud_cover_now: Optional[float] = None
    ) -> Optional[List[float]]:
        return await disturbances.async_read_cloud_forecast(self, cloud_cover_now)

    async def _async_read_wind_forecast(
        self, wind_speed_now: Optional[float] = None
    ) -> Optional[List[float]]:
        return await disturbances.async_read_wind_forecast(self, wind_speed_now)

    async def _async_get_forecast_entries(self) -> Optional[list]:
        return await disturbances.async_get_forecast_entries(self)

    async def _async_read_price_forecast(
        self, now: datetime
    ) -> Optional[List[float]]:
        return await disturbances.async_read_price_forecast(self, now)

    def _record_weather_success(self) -> None:
        disturbances.record_weather_success(self)

    def _record_weather_failure(self, reason: str) -> None:
        disturbances.record_weather_failure(self, reason)

    def _read_cloud_cover_now(self) -> Optional[float]:
        return disturbances.read_cloud_cover_now(self)

    def _read_wind_speed_now(self) -> Optional[float]:
        return disturbances.read_wind_speed_now(self)

    def _record_solar_fc_success(self) -> None:
        disturbances.record_solar_fc_success(self)

    def _record_solar_fc_failure(self, reason: str) -> None:
        disturbances.record_solar_fc_failure(self, reason)

    def _read_ghi(
        self, now: datetime,
    ) -> Tuple[Optional[float], Optional[List[Optional[float]]]]:
        return disturbances.read_ghi(self, now)

    def _room_solar_gain(
        self,
        name: str,
        now: datetime,
        cloud_cover: Optional[float],
        ghi: Optional[float],
    ) -> float:
        return disturbances.room_solar_gain(self, name, now, cloud_cover, ghi)

    def _resolve_display_cloud_cover(self) -> Optional[float]:
        return disturbances.resolve_display_cloud_cover(self)

    def _publish_current_solar_gains(self) -> None:
        disturbances.publish_current_solar_gains(self)


    # Backwards-compatible aliases for any caller / test that imported the
    # static helpers from the coordinator before the U3 weather extraction.
    _parse_weather_forecast = staticmethod(_weather.parse_temperature_forecast)
    _parse_cloud_forecast = staticmethod(_weather.parse_cloud_forecast)
    _interpolate_forecast = staticmethod(_weather.interpolate_forecast)

    # ------------------------------------------------------------------
    # Delivered-power read-back (used while the system is stopped)
    # ------------------------------------------------------------------

    def _read_delivered_actions(self, outdoor_temp: float) -> Dict[str, float]:
        return actuation.read_delivered_actions(self, outdoor_temp)

    def _read_delivered_fraction(self, src: HeatSource) -> float:
        return actuation.read_delivered_fraction(self, src)

    def _read_delivered_fraction_climate(self, src: HeatSource, state: Any) -> float:
        return actuation.read_delivered_fraction_climate(self, src, state)

    def _set_source_power(
        self, src: HeatSource, frac: float, outdoor_temp: float
    ) -> None:
        actuation.set_source_power(self, src, frac, outdoor_temp)

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
            self._apply_schedule(self.now_utc.astimezone())
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
            outdoor_temp = self._refresh_live_state()
        except Exception:
            _LOGGER.warning("Fast UI refresh failed", exc_info=True)
            return
        # Re-anchor climate setpoints to the units' current internal
        # temperatures so the delivered power tracks the MPC's constant-input
        # assumption between scheduled ticks (see _reapply_climate_setpoints).
        if outdoor_temp is not None:
            try:
                await self._reapply_climate_setpoints(outdoor_temp)
            except Exception:
                _LOGGER.debug(
                    "Fast UI refresh: setpoint re-apply failed", exc_info=True,
                )
        self.async_update_listeners()

    async def _async_push_window_override(self) -> None:
        await window.async_push_window_override(self)

    def _climate_internal_temp(self, src: HeatSource, state: Any) -> float:
        return actuation.climate_internal_temp(self, src, state)

    def _climate_hp_command(
        self,
        src: "HeatPump",
        fraction: float,
        internal_temp: float,
        outdoor_temp: float,
        state: Any,
    ) -> Tuple[str, float]:
        return actuation.climate_hp_command(
            self, src, fraction, internal_temp, outdoor_temp, state
        )

    def _climate_thermostat_command(
        self,
        src: HeatSource,
        fraction: float,
        internal_temp: float,
        room_temp: float,
        room_setpoint: float,
    ) -> Tuple[str, float]:
        return actuation.climate_thermostat_command(
            self, src, fraction, internal_temp, room_temp, room_setpoint
        )

    async def _reapply_climate_setpoints(self, outdoor_temp: float) -> None:
        await actuation.reapply_climate_setpoints(self, outdoor_temp)

    async def _apply_actions(self, outdoor_temp: float) -> None:
        await actuation.apply_actions(self, outdoor_temp)

    # ------------------------------------------------------------------
    # Setpoint helpers (called by climate platform)
    # ------------------------------------------------------------------

    def set_room_setpoint(self, room_name: str, setpoint: float) -> None:
        enablement.set_room_setpoint(self, room_name, setpoint)

    def get_room_setpoint(self, room_name: str) -> float:
        return enablement.get_room_setpoint(self, room_name)

    def get_base_setpoint(self, room_name: str) -> float:
        return enablement.get_base_setpoint(self, room_name)

    def set_room_comfort_offset(self, room_name: str, comfort_offset: float) -> None:
        enablement.set_room_comfort_offset(self, room_name, comfort_offset)

    def get_room_comfort_offset(self, room_name: str) -> float:
        return enablement.get_room_comfort_offset(self, room_name)

    @property
    def system_enabled(self) -> bool:
        return enablement.system_enabled(self)

    def set_system_enabled(self, enabled: bool) -> None:
        enablement.set_system_enabled(self, enabled)

    def set_room_enabled(self, room_name: str, enabled: bool) -> None:
        enablement.set_room_enabled(self, room_name, enabled)

    def is_room_enabled(self, room_name: str) -> bool:
        return enablement.is_room_enabled(self, room_name)


    # ------------------------------------------------------------------
    # System-identification experiments
    # ------------------------------------------------------------------

    def is_experiment_active(self, room_name: str) -> bool:
        """Return whether an identification experiment is exciting ``room_name``.

        Reflects the set computed for the current cycle by
        ``_build_experiment_clamps`` (keyed by room slug).
        """
        from ..dashboard import slugify as _slugify

        active = getattr(self, "_experiment_active_rooms", None)
        if not active:
            return False
        return _slugify(room_name) in active

    def _build_experiment_clamps(
        self, now: datetime
    ) -> Dict[str, "np.ndarray"]:
        """Advance experiments and build per-source horizon input clamps for the MPC.

        Rather than overriding the MPC's chosen actions after the solve, an active
        experiment pins its room's heater inputs over the whole prediction horizon
        by handing the controller a ``{source_name: ndarray(N,)}`` clamp: each
        entry is the *signed power* fraction (``+`` heat / ``-`` cool, of capacity)
        the excitation signal requests at ``now + k·dt`` (``NaN`` where no
        experiment is active at that step); the controller converts each to the
        control input that delivers it, so the step is linear in delivered power.
        The clamp is built per source so a reversible (heat/cool) unit gets the
        cool phases while a heat-only unit gets the heat-only pattern.  The MPC then
        treats the signal as a hard input constraint, planning the rest of the
        house around it, and the resulting plan — and thus the actuator forecast
        plot — already reflects the experiment.

        The applied (k=0) entry is additionally passed through the experiment's
        safety temperature band (frost floor / overheat ceiling) using the current
        measurement, so the realised action is safe; future steps use the raw
        signal (their temperatures are only known to the MPC's own rollout).  A
        room whose window is open is left unclamped — the window override forces it
        off and its air-exchange is unmodelled.
        """
        from ..dashboard import slugify as _slugify

        active_rooms: Set[str] = set()
        manager = self.experiment_manager
        now_ts = now.timestamp()

        # Advance lifecycle states; auto-save any experiment that just finished.
        transitions = manager.advance(now_ts)
        for finished in transitions.get("completed", []):
            self._on_experiment_completed(finished)
        if transitions.get("started") or transitions.get("completed"):
            self._persist_experiments()

        clamps: Dict[str, "np.ndarray"] = {}
        horizon_steps: Dict[str, List[bool]] = {}
        N = int(self._horizon)
        dt = float(self.dt)
        horizon_end = now_ts + N * dt
        # Nothing to do unless an experiment overlaps the horizon window.
        if not any(
            e.start_ts < horizon_end and now_ts < e.end_ts and not e.is_terminal()
            for e in manager.experiments
        ):
            self._experiment_active_rooms = active_rooms
            self._experiment_horizon_steps = horizon_steps
            return clamps

        slug_to_room = {_slugify(rn): rn for rn in self.model.room_names}

        for src in self.heat_sources:
            room_slug = _slugify(src.room)
            # An open window makes the room's air-exchange unmodelled, so the
            # excitation is suppressed there; the window override forces u=0.
            if self.is_window_override_active(src.room):
                continue

            room_name = slug_to_room.get(room_slug, src.room)
            can_cool = bool(getattr(src, "can_cool", False))
            arr = np.full(N, np.nan, dtype=float)
            clamped_any = False
            measured = self.measured_temperatures.get(room_name)
            for k in range(N):
                t_k = now_ts + k * dt
                exp = manager.active_for_room(room_slug, t_k)
                if exp is None:
                    continue
                frac = excitation_fraction(exp, t_k, can_cool=can_cool)
                if k == 0:
                    frac = apply_safety_bounds(
                        frac, measured, exp.min_temp, exp.max_temp,
                        exp.step_pct,
                    )
                    active_rooms.add(room_slug)
                arr[k] = frac
                clamped_any = True
            if clamped_any:
                clamps[src.name] = arr
                # Per-room mask of horizon steps the experiment governs (any of
                # the room's sources), keyed by canonical room name — drives the
                # comfort-relaxation below and the forecast ``experiment`` flag.
                mask = horizon_steps.setdefault(room_name, [False] * N)
                for k in range(N):
                    if not np.isnan(arr[k]):
                        mask[k] = True

        self._experiment_active_rooms = active_rooms
        self._experiment_horizon_steps = horizon_steps
        return clamps

    def _relax_experiment_comfort(self, control_traj: Any) -> None:
        """Neutralise comfort penalties on the steps an experiment governs.

        Over those steps the room's input is pinned to the excitation signal, so
        penalising its comfort is meaningless — and leaving it on makes the MPC
        *anticipate* the experiment (aggressively pre-heating just before it),
        which both produces odd actions at the border and contaminates the
        baseline.  Both comfort penalties are removed per governed step: the
        setpoint-tracking weight is zeroed and the soft comfort corridor is opened
        wide so leaving it costs nothing.  The widened corridor is kept out of the
        displayed forecast (see :meth:`build_forecast_payload`) so the temperature
        plot does not zoom out.
        """
        if control_traj is None or not self._experiment_horizon_steps:
            return
        for room_name, mask in self._experiment_horizon_steps.items():
            q_seq = control_traj.q_scales.get(room_name)
            off_seq = control_traj.comfort_offsets.get(room_name)
            for k, governed in enumerate(mask):
                if not governed:
                    continue
                if q_seq is not None and k < len(q_seq):
                    q_seq[k] = 0.0
                if off_seq is not None and k < len(off_seq):
                    off_seq[k] = EXPERIMENT_RELAXED_COMFORT_OFFSET

    def _on_experiment_completed(self, exp: Any) -> None:
        """React to an experiment finishing: auto-save its window as a dataset.

        The snapshot is taken asynchronously (it queries the JSONL history store)
        so the control cycle is never blocked.
        """
        if not getattr(exp, "auto_save", False) or exp.dataset_id is not None:
            return
        if self.dataset_store is None:
            return
        try:
            self.hass.async_create_task(self._async_autosave_experiment(exp))
        except Exception:  # pragma: no cover - defensive
            _LOGGER.warning(
                "Experiment %s: failed to schedule dataset auto-save", exp.id,
                exc_info=True,
            )

    async def _async_autosave_experiment(self, exp: Any) -> None:
        """Snapshot a completed experiment's window into a stored dataset."""
        records = await self._async_collect_window_records(exp.start_ts, exp.end_ts)
        if not records:
            _LOGGER.info(
                "Experiment %s finished with no captured records; no dataset saved",
                exp.id,
            )
            return
        name = exp.name or f"Experiment {exp.room_name}"
        dataset = build_dataset(
            name,
            records,
            room_name=exp.room_name,
            room_slug=exp.room_slug,
            source="experiment",
            notes=f"Auto-saved {exp.signal_type} experiment",
            window_start=exp.start_ts,
            window_end=exp.end_ts,
            experiment=exp.to_dict(),
        )
        await self.dataset_store.async_add(dataset)
        exp.dataset_id = dataset["id"]
        self._persist_experiments()
        self.async_update_listeners()
        _LOGGER.info(
            "Experiment %s auto-saved as dataset '%s' (%d records)",
            exp.id, name, dataset["record_count"],
        )

    async def _async_collect_window_records(
        self, start_ts: float, end_ts: float
    ) -> List[Dict[str, Any]]:
        """Return observation records covering ``[start_ts, end_ts]``.

        Prefers the integration-managed JSONL store (durable across restarts),
        falling back to the in-memory buffer filtered to the window.
        """
        records: List[Dict[str, Any]] = []
        if self.id_history_store is not None:
            try:
                records = await self.id_history_store.async_query_range(
                    start_ts, end_ts
                )
            except Exception:
                _LOGGER.warning(
                    "Dataset snapshot: JSONL query failed for [%s, %s]",
                    start_ts, end_ts, exc_info=True,
                )
        if not records:
            records = [
                dict(r)
                for r in self._history_buffer
                if start_ts <= float(r.get("timestamp", 0.0)) < end_ts
            ]
        return records

    def schedule_experiment(self, exp: Any) -> Any:
        """Register a new experiment and persist the experiment list."""
        interval_s = int(self._update_interval_s)
        # Use the most recent coordinator run-time as the grid reference so
        # snapped timestamps land on actual ZOH boundaries (actuator steps).
        ref_ts = (
            self.now_utc.timestamp()
            if getattr(self, "now_utc", None) is not None
            else time.time()
        )
        # Snap start to the first actuator step at or after the configured time.
        # Snap end to the first actuator step at or after the configured end,
        # so the last full step before the configured end is included.
        exp.start_ts = ceil_to_grid(exp.start_ts, ref_ts, interval_s)
        exp.end_ts = ceil_to_grid(exp.end_ts, ref_ts, interval_s)
        self.experiment_manager.add(exp)
        self.experiment_manager.prune_terminal()
        self._persist_experiments()
        return exp

    def cancel_experiment(self, experiment_id: str) -> bool:
        """Cancel a scheduled/running experiment.  Returns True if it changed."""
        cancelled = self.experiment_manager.cancel(experiment_id)
        if cancelled is None:
            # Allow removing terminal entries from the list entirely.
            removed = self.experiment_manager.remove(experiment_id)
            if removed:
                self._persist_experiments()
            return removed
        self._experiment_active_rooms.discard(cancelled.room_slug)
        self._persist_experiments()
        return True

    def delete_experiment(self, experiment_id: str) -> bool:
        """Remove an experiment from the list outright, regardless of status.

        A running/scheduled experiment is first cancelled so it stops driving the
        room, then dropped entirely (no lingering ``cancelled`` record to remove
        separately).  Returns True if an experiment was removed.
        """
        exp = self.experiment_manager.get(experiment_id)
        if exp is not None and not exp.is_terminal():
            self.experiment_manager.cancel(experiment_id)
            self._experiment_active_rooms.discard(exp.room_slug)
        removed = self.experiment_manager.remove(experiment_id)
        if removed:
            self._persist_experiments()
        return removed

    def _persist_experiments(self) -> None:
        """Persist the experiment list (best-effort, never blocks a cycle)."""
        if self.experiment_store is None:
            return
        try:
            self.hass.async_create_task(
                self.experiment_store.async_save(self.experiment_manager)
            )
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("Failed to persist experiments", exc_info=True)

    # ------------------------------------------------------------------
    # Comfort schedule helpers
    # ------------------------------------------------------------------

    def _apply_schedule(self, now: datetime) -> None:
        schedule_control.apply_schedule(self, now)

    def _compute_control_trajectory(
        self,
        now_local: datetime,
        N: int,
        dt_seconds: float,
    ) -> ControlTrajectory:
        return schedule_control.compute_control_trajectory(
            self, now_local, N, dt_seconds
        )

    def has_schedule(self, room_name: str) -> bool:
        return schedule_control.has_schedule(self, room_name)

    def is_schedule_enabled(self, room_name: str) -> bool:
        return schedule_control.is_schedule_enabled(self, room_name)

    def set_schedule_enabled(self, room_name: str, enabled: bool) -> None:
        schedule_control.set_schedule_enabled(self, room_name, enabled)

    def active_schedule_period(self, room_name: str) -> Optional[EffectiveControlParams]:
        return schedule_control.active_schedule_period(self, room_name)

    def next_schedule_transition(self, room_name: str) -> Optional[datetime]:
        return schedule_control.next_schedule_transition(self, room_name)

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
    # Parameter estimation (open-loop simulation MSE + IPOPT)
    # ------------------------------------------------------------------

    async def async_estimate_parameters_ml(
        self,
        apply_params: bool = True,
        horizon_hours: Optional[float] = None,
        locked_params: Optional[Dict[str, Any]] = None,
        history_override: Optional[List[Dict[str, Any]]] = None,
        dataset_start_timestamps: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """
        Estimate thermal parameters via open-loop simulation MSE minimisation.

        Runs :class:`~.parameter_estimator.KalmanMLEstimator` joint IPOPT
        optimisation over the rolling observation history buffer in a
        thread-executor so that the HA event loop is not blocked.  The
        objective is multi-step free-run simulation error (not CD-EKF PED
        log-likelihood).

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
        from ..parameter_estimator import KalmanMLEstimator
        from ..history_window import select_recent_window

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

        if history_override is not None:
            # Caller pre-fetched the data (e.g. from HA Recorder for a custom
            # window that extends beyond the in-memory buffer).
            history = list(history_override)
        else:
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
        _estimate = lambda: estimator.estimate(  # noqa: E731
            history,
            locked_params=locked_params,
            dataset_start_timestamps=dataset_start_timestamps,
        )
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
        from ..parameter_estimator import KalmanMLEstimator

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
        self._build_controller()

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
        prev_rooms: Dict[str, Any] = {}
        try:
            prev_rooms = (self.estimated_params_snapshot or {}).get("rooms", {})
        except Exception:
            pass

        snapshot_rooms: Dict[str, Any] = {}
        for name in self.model.room_names:
            prev = prev_rooms.get(name, {}) if isinstance(prev_rooms.get(name), dict) else {}
            just_estimated = name in estimated_params
            entry: Dict[str, Any] = {
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
                "is_estimated": bool(just_estimated or prev.get("is_estimated", False)),
            }
            if just_estimated:
                entry["estimated_at"] = now_iso
                entry["estimation_source"] = "ml"
            elif prev.get("estimated_at"):
                entry["estimated_at"] = prev.get("estimated_at")
                if prev.get("estimation_source"):
                    entry["estimation_source"] = prev.get("estimation_source")
            snapshot_rooms[name] = entry

        snapshot: Dict[str, Any] = {
            "rooms": snapshot_rooms,
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
