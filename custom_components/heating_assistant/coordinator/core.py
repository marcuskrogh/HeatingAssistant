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
    experiments_mpc,
    forecast_payload,
    live_refresh,
    mpc_cycle,
    parameter_lifecycle,
    runtime_state,
    schedule_control,
    tuning_preview,
    window,
)
from .model_builders import build_heat_sources, build_house_model
from .types import (
    ControlTrajectory,
    ControllerConfigSnapshot,
    _coerce_interval_seconds,
    _coerce_opt_float,
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
from ..experiments import ExperimentManager

_LOGGER = logging.getLogger(__name__)


class HeatingAssistantCoordinator(DataUpdateCoordinator):
    """
    Central coordinator that runs the MPC controller periodically and
    distributes results to the climate and sensor platforms.
    """

    #: Class-level default so partially-constructed instances (tests,
    #: legacy persistence paths) always have a ground albedo.
    _ground_albedo: float = DEFAULT_GROUND_ALBEDO

    def apply_runtime_reconfiguration(self, config: Dict[str, Any]) -> bool:
        from . import runtime_reconfig

        return runtime_reconfig.apply_runtime_reconfiguration(self, config)

    def _apply_pending_runtime_reconfiguration(self) -> None:
        from . import runtime_reconfig

        runtime_reconfig.apply_pending_runtime_reconfiguration(self)

    def apply_tuning_updates(self, updates: Dict[str, Any]) -> None:
        from . import runtime_reconfig

        runtime_reconfig.apply_tuning_updates(self, updates)

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

        self._init_room_state(rooms_cfg)

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
        # Prefer the authoritative config-entry store (disk) over the
        # in-memory MergedEntry overlay, which can lag behind service writes.
        persisted_schedules: Dict[str, Any] = {}
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            persisted_schedules = dict(
                real_entry.data.get(CONF_PERSISTED_SCHEDULES) or {}
            )
        if not persisted_schedules:
            persisted_schedules = dict(
                self._entry.data.get(CONF_PERSISTED_SCHEDULES) or {}
            )
        schedule_control.apply_persisted_schedules(self, persisted_schedules)

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

        # Monotonic timestamp of the last actuation-watchdog re-apply.  Used to
        # rate-limit corrective ``apply_actions`` calls when commanded and
        # delivered heater states diverge.
        self._actuation_watchdog_last_ts: float = 0.0

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
        await runtime_state.ensure_runtime_state_loaded(self)

    def _smooth_cloud_cover(self, cc_obs: Optional[float]) -> Optional[float]:
        return runtime_state.smooth_cloud_cover(self, cc_obs)

    def _save_runtime_state(self) -> None:
        runtime_state.save_runtime_state(self)

    def _propagate_ekf_gap(
        self,
        save_ts: float,
        u_prev: Optional[np.ndarray],
        d_prev: Optional[np.ndarray],
    ) -> None:
        runtime_state.propagate_ekf_gap(self, save_ts, u_prev, d_prev)

    def _system_nd(self) -> int:
        return runtime_state.system_nd(self)

    def _build_gap_u_sequence(
        self,
        start_ts: float,
        n_steps: int,
        dt: float,
        u_default: np.ndarray,
    ) -> np.ndarray:
        return runtime_state.build_gap_u_sequence(
            self, start_ts, n_steps, dt, u_default
        )

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
        parameter_lifecycle.restore_estimated_parameters(self, snapshot)

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
        return tuning_preview.preview_tuning_forecast(
            self, tuning_overrides, plot_forecast_steps, weather
        )

    def reset_estimated_parameters(self) -> None:
        parameter_lifecycle.reset_estimated_parameters(self)

    def apply_manual_parameters(
        self,
        room_name: str,
        thermal_mass: float,
        r_external: float,
    ) -> None:
        parameter_lifecycle.apply_manual_parameters(
            self, room_name, thermal_mass, r_external
        )

    def apply_heater_scales(
        self,
        heater_scales: Dict[str, float],
    ) -> None:
        parameter_lifecycle.apply_heater_scales(self, heater_scales)

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
        parameter_lifecycle.store_identified_parameters(
            self,
            room_name,
            thermal_mass,
            r_external,
            source=source,
            rmse=rmse,
            internal_gain=internal_gain,
            solar_scale=solar_scale,
            c_air_fraction=c_air_fraction,
            r_aw_fraction=r_aw_fraction,
            heater_scales=heater_scales,
        )

    def revert_parameters(self, room_name: str, history_index: int) -> None:
        parameter_lifecycle.revert_parameters(self, room_name, history_index)

    def delete_parameter_history(self, history_index: int) -> None:
        parameter_lifecycle.delete_parameter_history(self, history_index)

    def reload_room_schedule(self, room_name: str, periods_raw: list) -> None:
        schedule_control.reload_room_schedule(self, room_name, periods_raw)

    async def _async_update_data(self) -> Dict[str, Any]:
        """
        Called by HA periodically.  Reads sensors, runs the controller,
        and returns a snapshot of the system state.
        """
        try:
            self.now_utc = datetime.now(tz=timezone.utc)
            self._apply_pending_runtime_reconfiguration()

            rooms_not_ready = mpc_cycle.read_measurements(self)

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

            outdoor_temp, early = await mpc_cycle.resolve_outdoor_temperature(self)
            if early is not None:
                return early

            ctx = await mpc_cycle.gather_disturbances(self)
            mpc_cycle.publish_cycle_solar_gains(self, ctx)

            kalman_innovation = mpc_cycle.run_controller_compute(
                self, outdoor_temp, control_traj, rooms_not_ready, ctx
            )
            mpc_cycle.finalize_actions(self, outdoor_temp)

            await mpc_cycle.record_observation_history(
                self, outdoor_temp, ctx, kalman_innovation
            )
            await self._async_refresh_time_in_range_kpis_if_due()

            if self._system_enabled:
                try:
                    await self._apply_actions(outdoor_temp)
                except Exception:
                    _LOGGER.warning(
                        "Failed to apply computed heater actions; keeping forecast "
                        "and prediction entities available",
                        exc_info=True,
                    )

            return mpc_cycle.build_cycle_result(self, outdoor_temp)

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
        return live_refresh.refresh_live_state(self)

    async def async_refresh_ui(self) -> None:
        await live_refresh.async_refresh_ui(self)

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

    async def _async_verify_heater_entities(self, outdoor_temp: float) -> bool:
        return await actuation.async_verify_heater_entities(self, outdoor_temp)

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
        return experiments_mpc.is_experiment_active(self, room_name)

    def _build_experiment_clamps(
        self, now: datetime
    ) -> Dict[str, "np.ndarray"]:
        return experiments_mpc.build_experiment_clamps(self, now)

    def _relax_experiment_comfort(self, control_traj: Any) -> None:
        experiments_mpc.relax_experiment_comfort(self, control_traj)

    def _on_experiment_completed(self, exp: Any) -> None:
        experiments_mpc.on_experiment_completed(self, exp)

    async def _async_autosave_experiment(self, exp: Any) -> None:
        await experiments_mpc.async_autosave_experiment(self, exp)

    async def _async_collect_window_records(
        self, start_ts: float, end_ts: float
    ) -> List[Dict[str, Any]]:
        return await experiments_mpc.async_collect_window_records(
            self, start_ts, end_ts
        )

    def schedule_experiment(self, exp: Any) -> Any:
        return experiments_mpc.schedule_experiment(self, exp)

    def cancel_experiment(self, experiment_id: str) -> bool:
        return experiments_mpc.cancel_experiment(self, experiment_id)

    def delete_experiment(self, experiment_id: str) -> bool:
        return experiments_mpc.delete_experiment(self, experiment_id)

    def _persist_experiments(self) -> None:
        experiments_mpc.persist_experiments(self)

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
        from . import setup_simulation

        return setup_simulation.simulate_thermal_response(
            self, room_name, initial_temp, outdoor_temp, heating_power, duration_hours
        )

    def estimate_parameters(
        self,
        room_name: str,
        heating_power: float,
        outdoor_temp: float,
        initial_temp: float,
        final_temp: float,
        duration_seconds: float,
    ) -> Dict[str, Any]:
        from . import setup_simulation

        return setup_simulation.estimate_parameters(
            self,
            room_name,
            heating_power,
            outdoor_temp,
            initial_temp,
            final_temp,
            duration_seconds,
        )

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
        return await parameter_lifecycle.async_estimate_parameters_ml(
            self,
            apply_params=apply_params,
            horizon_hours=horizon_hours,
            locked_params=locked_params,
            history_override=history_override,
            dataset_start_timestamps=dataset_start_timestamps,
        )

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
        parameter_lifecycle.apply_estimated_parameters(
            self,
            estimated_params,
            estimated_inter_room_r,
            estimated_internal_gains=estimated_internal_gains,
            estimated_heater_scales=estimated_heater_scales,
            estimated_solar_scales=estimated_solar_scales,
            estimated_envelope_splits=estimated_envelope_splits,
            log_likelihood=log_likelihood,
        )
