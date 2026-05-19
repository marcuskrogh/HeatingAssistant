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
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONSTRAINT_OFFSET,
    CONF_ENERGY_WEIGHT,
    CONF_ESTIMATED_PARAMS,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MPC_ANALYTIC_DERIVATIVES,
    CONF_MPC_SOLVER,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_ROOMS,
    CONF_SMOOTHING_WEIGHT,
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
    CONF_SOURCE_TURN_OFF_DEADBAND,
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
    DEFAULT_CONSTRAINT_OFFSET,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_EFFICIENCY,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_HORIZON,
    DEFAULT_MIN_POWER,
    DEFAULT_MPC_ANALYTIC_DERIVATIVES,
    DEFAULT_MPC_SOLVER,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_TURN_OFF_DEADBAND,
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
    SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU,
    UPDATE_INTERVAL,
)
from .heat_sources import ElectricHeater, HeatPump, HeatSource
from .thermal_model import HouseModel, Room, RoomConnection, Window
from .controller import HeatingMPCController
from .ground_temp import ground_temperature
from .solar_model import room_solar_gains
from .schedule import (
    EffectiveSetpoint,
    RoomSchedule,
    build_schedule,
    next_transition,
    resolve_effective_setpoint,
)
from . import weather as _weather

_LOGGER = logging.getLogger(__name__)


def _coerce_interval_seconds(value: Any) -> float:
    """Return a numeric interval in seconds from int/float/str/timedelta values."""
    if isinstance(value, timedelta):
        return float(value.total_seconds())
    return float(value)


def _normalize_solver_name(value: Any) -> str:
    """Normalize solver names to canonical values used by the controller."""
    if value is None:
        return DEFAULT_MPC_SOLVER
    key = str(value).strip().lower()
    if key in {"ipopt", "cyipopt"}:
        return key
    if key in {"slsqp", "scipy", "scipy-minimize"}:
        return "SLSQP"
    return DEFAULT_MPC_SOLVER


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
                    turn_off_deadband=sc.get(CONF_SOURCE_TURN_OFF_DEADBAND, DEFAULT_TURN_OFF_DEADBAND),
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

    _RELOAD_REQUIRED_CONFIG_KEYS: Set[str] = {
        CONF_ROOMS,
        CONF_HEAT_SOURCES,
        CONF_HORIZON,
        CONF_UPDATE_INTERVAL,
    }
    _RUNTIME_RECONFIG_KEYS: Set[str] = {
        CONF_OUTDOOR_TEMP_ENTITY,
        CONF_WEATHER_ENTITY,
        CONF_LATITUDE,
        CONF_LONGITUDE,
        CONF_CONSTRAINT_OFFSET,
        CONF_ENERGY_WEIGHT,
        CONF_SMOOTHING_WEIGHT,
        CONF_TERMINAL_WEIGHT,
        CONF_MPC_SOLVER,
        CONF_MPC_ANALYTIC_DERIVATIVES,
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
        # The update interval drives how often the coordinator ticks, the EKF
        # measurement step, and the OCP ZOH step — all three must be equal for
        # the MPC predictions to match physical reality.  Options take precedence
        # over initial data so that the user can reconfigure via the UI without
        # re-creating the entry.  Falls back to DEFAULT_UPDATE_INTERVAL when absent.
        # Old config entries that stored a separate "dt" key are silently ignored;
        # the update_interval is the single source of truth.
        self._update_interval: int = int(
            _coerce_interval_seconds(
                options.get(CONF_UPDATE_INTERVAL)
                or data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            )
        )
        self._horizon: int = int(
            options.get(CONF_HORIZON)
            or data.get(CONF_HORIZON, DEFAULT_HORIZON)
        )
        self._energy_weight: float = data.get(CONF_ENERGY_WEIGHT, DEFAULT_ENERGY_WEIGHT)
        self._smoothing_weight: float = data.get(CONF_SMOOTHING_WEIGHT, DEFAULT_SMOOTHING_WEIGHT)
        self._constraint_offset: float = data.get(CONF_CONSTRAINT_OFFSET, DEFAULT_CONSTRAINT_OFFSET)
        self._terminal_weight: float = data.get(CONF_TERMINAL_WEIGHT, DEFAULT_TERMINAL_WEIGHT)
        self._sigma_w: float = float(
            options.get(CONF_SIGMA_W, data.get(CONF_SIGMA_W, DEFAULT_SIGMA_W))
        )
        self._sigma_v: float = float(
            options.get(CONF_SIGMA_V, data.get(CONF_SIGMA_V, DEFAULT_SIGMA_V))
        )
        self._sigma_b: float = float(
            options.get(CONF_SIGMA_B, data.get(CONF_SIGMA_B, DEFAULT_SIGMA_B))
        )
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
        self._mpc_solver: str = _normalize_solver_name(
            options.get(CONF_MPC_SOLVER)
            or data.get(CONF_MPC_SOLVER, DEFAULT_MPC_SOLVER)
        )
        self._mpc_analytic_derivatives: bool = bool(
            options.get(CONF_MPC_ANALYTIC_DERIVATIVES)
            if CONF_MPC_ANALYTIC_DERIVATIVES in options
            else data.get(
                CONF_MPC_ANALYTIC_DERIVATIVES,
                DEFAULT_MPC_ANALYTIC_DERIVATIVES,
            )
        )
        self._last_runtime_config: Dict[str, Any] = {**dict(data), **dict(options)}
        self._pending_runtime_reconfiguration: Dict[str, Any] = {}

        rooms_cfg: List[Dict[str, Any]] = data.get(CONF_ROOMS, [])
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
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            constraint_offset=self._constraint_offset,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            solver=self._mpc_solver,
            use_analytic_derivatives=self._mpc_analytic_derivatives,
        )

        self._init_room_state(rooms_cfg)
        self._init_runtime_buffers()

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._update_interval),
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
        self._room_enabled: Dict[str, bool] = {name: True for name in room_names}
        self._schedule_disabled: Dict[str, bool] = {name: False for name in room_names}

        self._room_schedule: Dict[str, RoomSchedule] = {}
        self._base_setpoint: Dict[str, float] = {}
        self._schedule_enabled: Dict[str, bool] = {}
        self._window_sensors: Dict[str, List[str]] = self._build_window_sensor_map(rooms_cfg)
        self._window_state: Dict[str, str] = {name: "closed" for name in room_names}
        self._window_state_since: Dict[str, datetime] = {}
        # Last applied effective setpoint per room — exposed for diagnostics.
        self._effective_setpoint: Dict[str, EffectiveSetpoint] = {}
        for rc in rooms_cfg:
            room_name = rc[CONF_ROOM_NAME]
            self._room_schedule[room_name] = build_schedule(rc.get(CONF_SCHEDULE))
            self._base_setpoint[room_name] = float(
                rc.get(CONF_SETPOINT, DEFAULT_SETPOINT)
            )
            self._schedule_enabled[room_name] = True

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
        """Return True while the room is in the ``open`` window state."""
        return self.get_window_state(room_name) == "open"

    def _init_runtime_buffers(self) -> None:
        """Initialise per-cycle and visualisation state.

        Everything here is updated each ``_async_update_data`` tick and read
        by sensor entities between ticks; the empty initial values are what
        the sensors see before the first cycle completes.
        """
        # Latest control actions (source_name → fraction 0‑1)
        self.actions: Dict[str, float] = {}
        # Track which heat sources are in cooling mode (source_name → bool)
        self._cooling_active: Dict[str, bool] = {}

        # Visualization data
        self.solar_gains: Dict[str, float] = {}
        # Current cloud-cover fraction in [0, 1], or None when unavailable;
        # used to attenuate the clear-sky solar model.
        self.cloud_cover: Optional[float] = None
        self.outdoor_temp: float = 5.0
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

        # Rolling observation history for ML parameter estimation.
        # Each entry is a dict: {y, u, d_outdoor, d_solar, timestamp}.
        self._history_buffer: deque = deque(maxlen=HISTORY_BUFFER_SIZE)

        # Cache populated by run_open_loop_simulation service.
        # Keyed by room_name; each value is the per-room dict returned by
        # compute_open_loop_predictions (rmse, mae, simulation list).
        # OpenLoopRMSESensor reads from here instead of computing on the
        # event loop.
        self.open_loop_results: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def dt(self) -> float:
        """Return the OCP/EKF time step (= update interval) in seconds."""
        return _coerce_interval_seconds(self._update_interval)

    @property
    def update_interval_seconds(self) -> int:
        """Return the coordinator / EKF update period in seconds."""
        return self._update_interval

    @property
    def history_buffer(self) -> deque:
        """Return a view of the rolling observation history buffer."""
        return self._history_buffer

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
        """
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
        self.model._C, self.model._A, self.model._B_ext = self.model._build_matrices()

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
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            constraint_offset=self._constraint_offset,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            solver=self._mpc_solver,
            use_analytic_derivatives=self._mpc_analytic_derivatives,
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
        if CONF_LATITUDE in pending:
            self._latitude = float(pending.get(CONF_LATITUDE, self._latitude))
        if CONF_LONGITUDE in pending:
            self._longitude = float(pending.get(CONF_LONGITUDE, self._longitude))
        if CONF_CONSTRAINT_OFFSET in pending:
            self._constraint_offset = float(
                pending.get(CONF_CONSTRAINT_OFFSET, self._constraint_offset)
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
        if CONF_MPC_SOLVER in pending:
            self._mpc_solver = _normalize_solver_name(
                pending.get(CONF_MPC_SOLVER, self._mpc_solver)
            )
            rebuild_controller = True
        if CONF_MPC_ANALYTIC_DERIVATIVES in pending:
            self._mpc_analytic_derivatives = bool(
                pending.get(
                    CONF_MPC_ANALYTIC_DERIVATIVES, self._mpc_analytic_derivatives
                )
            )
            rebuild_controller = True

        if rebuild_controller:
            self._build_controller()

    def sources_for_room(self, room_name: str) -> List[HeatSource]:
        """Return the cached list of heat sources for ``room_name`` (empty if
        none), without copying.  Sensors should not mutate the returned list.
        """
        return self._sources_by_room.get(room_name, [])

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
        self._rebuild_sources_by_room()

        self.controller = HeatingMPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self.dt,
            measurement_dt=self.dt,
            latitude=self._latitude,
            longitude=self._longitude,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            constraint_offset=self._constraint_offset,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            solver=self._mpc_solver,
            use_analytic_derivatives=self._mpc_analytic_derivatives,
        )

        self._estimation_timestamp = None
        self._estimation_log_likelihood = None
        _LOGGER.info("Estimated parameters reset to configured defaults")

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
                        except ValueError:
                            _LOGGER.warning(
                                "Cannot parse temperature from entity %s: %r",
                                entity_id,
                                state.state,
                            )
                if readings:
                    averaged = sum(readings) / len(readings)
                    self.model.rooms[room_name].temperature = averaged
                    self.measured_temperatures[room_name] = averaged

            # 1b. Apply comfort schedules: resolve the active period for each
            #     room and update the live setpoint / enabled flag accordingly.
            #     Done after measurements are read so frost-protection logic
            #     sees the current temperature.
            now_local = datetime.now()
            self._apply_schedule(now_local)
            self._update_window_state_machine(self.now_utc)

            # 2. Read outdoor temperature
            outdoor_temp = self._read_outdoor_temp()
            self.outdoor_temp = outdoor_temp

            # 2b. Read weather forecast for outdoor temperature prediction
            #     and cloud-cover (used to attenuate the clear-sky solar model)
            outdoor_forecast = await self._async_read_weather_forecast()
            cloud_cover_now = self._read_cloud_cover_now()
            cloud_forecast = await self._async_read_cloud_forecast()

            # 2c. Read current wind speed for the Sherman–Grimsrud
            #     infiltration overlay (Phase 1 C1).  When the weather
            #     entity does not expose ``wind_speed`` this returns
            #     ``None`` and the controller's external conductance
            #     falls back to its typical-conditions baseline.  Held
            #     constant over the OCP horizon and the EKF sub-steps in
            #     this cycle (a horizon-time-varying wind forecast is a
            #     Phase 5 follow-up).
            wind_speed_now = self._read_wind_speed_now()
            self.wind_speed = wind_speed_now
            if hasattr(self.controller, "set_wind_speed"):
                self.controller.set_wind_speed(wind_speed_now)
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

            # 2d. Compute the current ground temperature from the
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

            # 3. Compute current solar gains for visualization
            now = self.now_utc
            self.cloud_cover = cloud_cover_now
            self.solar_gains = {
                name: room_solar_gains(
                    self.model.rooms[name].windows,
                    now,
                    self._latitude,
                    self._longitude,
                    cloud_cover=cloud_cover_now,
                )
                for name in self.model.room_names
            }

            # 4. Run MPC controller
            try:
                self.actions = self.controller.compute(
                    outdoor_temp=outdoor_temp,
                    solar_gains=self.solar_gains,
                    now=now,
                    outdoor_forecast=outdoor_forecast,
                    cloud_forecast=cloud_forecast,
                    cloud_cover_now=cloud_cover_now,
                )
                self.predictions = self.controller.predictions
                self.outdoor_forecast = self.controller.outdoor_forecast
                self.solar_forecast = self.controller.solar_forecast
                self.heating_schedule = self.controller.heating_schedule
                self.filtered_temperatures = self.controller.filtered_temperatures

                # Capture Kalman innovation for diagnostics (may be None on first step)
                # controller.last_innovation is populated by compute() after splitting
                # the EKF predict/update steps to record ν = y − hm(x̂⁻).
                kalman_innovation: Optional[List[float]] = self.controller.last_innovation
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
                self.heating_schedule = []
                self.solar_forecast = []
                self.filtered_temperatures = {}
                kalman_innovation = None

            # Dispatch-layer W1 override: clamp all sources in open-window
            # rooms to u=0 before history write and actuator commands.
            for src in self.heat_sources:
                if self.is_window_override_active(src.room):
                    self.actions[src.name] = 0.0

            # 5. Store heat-flow breakdown (independent of MPC solve success)
            self.heat_flows = self.model.compute_heat_flows(outdoor_temp)

            # 6. Record observation in the rolling history buffer for ML
            #    parameter estimation and model fit analysis.
            #
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
                "y": [
                    self.model.rooms[name].temperature
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
                "timestamp": now.timestamp(),
                # Kalman innovation ν = y − C x̂⁻  (None on the very first step)
                "kalman_innovation": kalman_innovation,
            })

            # 7. Write set-points to heater entities. Keep the latest
            # forecast/prediction entities available even if HA service calls
            # fail for a specific heater entity.
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

    def _read_outdoor_temp(self) -> float:
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

    async def _async_read_cloud_forecast(self) -> Optional[List[float]]:
        """Read cloud-cover forecast (fraction in [0, 1]) from the weather entity."""
        forecast_data = await self._async_get_forecast_entries()
        if not forecast_data:
            return None
        return _weather.parse_cloud_forecast(forecast_data, self._horizon, self.dt)

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

    # Backwards-compatible aliases for any caller / test that imported the
    # static helpers from the coordinator before the U3 weather extraction.
    _parse_weather_forecast = staticmethod(_weather.parse_temperature_forecast)
    _parse_cloud_forecast = staticmethod(_weather.parse_cloud_forecast)
    _interpolate_forecast = staticmethod(_weather.interpolate_forecast)

    async def _apply_actions(self, outdoor_temp: float) -> None:
        """Write the computed set-point fractions to heater entities via HA services."""
        for src in self.heat_sources:
            fraction = self.actions.get(src.name, 0.0)
            room_enabled = self.is_room_enabled(src.room)
            window_override_active = self.is_window_override_active(src.room)
            effective_room_enabled = room_enabled and not window_override_active
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
                        # Room explicitly disabled (user toggle or active "off"
                        # schedule period without frost-protection demand) —
                        # force the heat pump fully off.
                        self._cooling_active[src.name] = False
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

                    room_temp = self.model.rooms[src.room].temperature
                    room_setpoint = self.get_room_setpoint(src.room)

                    # Read the heat pump's own internal temperature
                    hp_internal_temp: Optional[float] = None
                    attrs = getattr(state, "attributes", {})
                    raw = attrs.get("current_temperature")
                    if raw is not None:
                        try:
                            hp_internal_temp = float(raw)
                        except (ValueError, TypeError):
                            pass

                    # ── Schmitt-trigger dead band (applies to BOTH directions) ──
                    # Evaluate the two-threshold hysteresis gate first, before
                    # inspecting the MPC fraction.  This ensures the dead band
                    # prevents jitter in both transitions:
                    #
                    #   heating → cooling : only enter when
                    #       room_temp > setpoint + deadband
                    #   cooling → heating : only exit when
                    #       room_temp < setpoint − deadband
                    #
                    # A negative MPC fraction (active cooling request) is honoured
                    # only once the upper threshold has been crossed.  This stops
                    # the heat pump toggling mode when the temperature is close to
                    # the setpoint from either side.
                    currently_cooling = getattr(
                        self, '_cooling_active', {}
                    ).get(src.name, False)
                    if currently_cooling:
                        if room_temp < room_setpoint - src.turn_off_deadband:
                            currently_cooling = False
                    else:
                        if room_temp > room_setpoint + src.turn_off_deadband:
                            currently_cooling = True
                    self._cooling_active[src.name] = currently_cooling

                    # Resolve the best available cooling mode once (used below).
                    supported_modes = attrs.get("hvac_modes", [])
                    if "cool" in supported_modes:
                        cooling_mode = "cool"
                    elif "dry" in supported_modes:
                        cooling_mode = "dry"
                    elif "fan_only" in supported_modes or not supported_modes:
                        cooling_mode = "fan_only"
                    else:
                        _LOGGER.warning(
                            "Heat pump %r supports neither 'cool', 'dry' nor "
                            "'fan_only' modes (%r); defaulting to 'fan_only'",
                            entity_id, supported_modes,
                        )
                        cooling_mode = "fan_only"

                    if currently_cooling:
                        # Dead band permits cooling.  Decide between active
                        # (MPC-requested, fraction < 0) and passive (temperature
                        # above upper threshold but MPC fraction ≥ 0).
                        if fraction < 0.0:
                            # Active cooling: use the MPC-computed fraction to
                            # set the HP setpoint proportionally below its own
                            # internal sensor.  _current_power is already set by
                            # controller.compute() — no override needed here.
                            cooling_fraction = abs(fraction)
                            if hp_internal_temp is not None:
                                target_temp = src.target_temperature_cooling(
                                    cooling_fraction, hp_internal_temp,
                                )
                            else:
                                target_temp = src.target_temperature_cooling(
                                    cooling_fraction, room_temp,
                                )
                        else:
                            # Passive cooling: room is above the upper dead-band
                            # threshold but MPC is not requesting active cooling.
                            # Drive the HP setpoint below its internal sensor by
                            # an offset that grows with overshoot.  Notify the
                            # controller so the CD-EKF uses the correct u_prev.
                            overshoot = max(0.0, room_temp - room_setpoint)
                            idle_offset = DEFAULT_IDLE_OFFSET + overshoot
                            if hp_internal_temp is not None:
                                target_temp = hp_internal_temp - idle_offset
                            else:
                                target_temp = room_temp - idle_offset

                            # Override power so the thermal model accounts for
                            # heat removal.
                            cooling_power = src.cooling_power(outdoor_temp)
                            src.set_power(0.0, outdoor_temp)
                            src._current_power = cooling_power  # negative = heat removal
                            if controller is not None:
                                controller.notify_applied_u(src.name, -1.0)

                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": cooling_mode},
                            blocking=False,
                        )
                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": target_temp},
                            blocking=False,
                        )

                    elif fraction > 0.0:
                        # Active heating: dead band cleared, MPC requests heat.
                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": "heat"},
                            blocking=False,
                        )

                        if hp_internal_temp is not None:
                            target_temp = src.target_temperature(
                                fraction, hp_internal_temp,
                            )
                        else:
                            target_temp = src.target_temperature(
                                fraction, room_temp,
                            )

                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": target_temp},
                            blocking=False,
                        )

                    else:
                        # Idle: either fraction == 0, or fraction < 0 but the
                        # dead band is preventing a switch to cooling because the
                        # temperature has not yet crossed the upper threshold.
                        # Keep the HP in heat mode with the setpoint below its
                        # own reading so it produces near-zero output while
                        # keeping the refrigerant circuit warm for fast response.
                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": "heat"},
                            blocking=False,
                        )

                        if hp_internal_temp is not None:
                            target_temp = hp_internal_temp - DEFAULT_IDLE_OFFSET
                        else:
                            target_temp = self.model.rooms[src.room].temperature - DEFAULT_IDLE_OFFSET

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

            if schedule is None or schedule.is_empty or not self._schedule_enabled.get(
                room_name, True
            ):
                effective = EffectiveSetpoint(
                    setpoint=base, enabled=True, period_name=None, mode=None,
                )
            else:
                effective = resolve_effective_setpoint(
                    schedule=schedule,
                    base_setpoint=base,
                    measured_temp=measured,
                    now=now,
                )

            self.model.rooms[room_name].setpoint = effective.setpoint
            self._schedule_disabled[room_name] = not effective.enabled
            self._effective_setpoint[room_name] = effective

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

    def active_schedule_period(self, room_name: str) -> Optional[EffectiveSetpoint]:
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

        Returns
        -------
        dict – the result dict from :class:`~.parameter_estimator.KalmanMLEstimator`.
        """
        from .parameter_estimator import KalmanMLEstimator

        estimator = KalmanMLEstimator(
            rooms=list(self.model.rooms.values()),
            sources=self.heat_sources,
            dt=_coerce_interval_seconds(self._update_interval),  # must match history buffer sampling interval, not MPC horizon
        )

        history = list(self._history_buffer)

        # Optimisation may take a few seconds; run in a thread executor.
        result: Dict[str, Any] = await self.hass.async_add_executor_job(
            estimator.estimate, history
        )

        if result["success"] and apply_params:
            self._apply_estimated_parameters(
                result["estimated_params"],
                result.get("estimated_inter_room_r", {}),
                estimated_internal_gains=result.get("estimated_internal_gains", {}),
                estimated_heater_scales=result.get("estimated_heater_scales", {}),
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
            dt=_coerce_interval_seconds(self._update_interval),
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

        # Rebuild the internal model matrices (A, B_ext, C_cap)
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
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            constraint_offset=self._constraint_offset,
            terminal_weight=self._terminal_weight,
            sigma_w=self._sigma_w,
            sigma_v=self._sigma_v,
            sigma_b=self._sigma_b,
            solver=self._mpc_solver,
            use_analytic_derivatives=self._mpc_analytic_derivatives,
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
