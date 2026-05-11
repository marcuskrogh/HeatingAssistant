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
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONSTRAINT_OFFSET,
    CONF_ENERGY_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_ROOMS,
    CONF_SMOOTHING_WEIGHT,
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
    CONF_SOURCE_TYPE,
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_R_VALUE,
    CONF_R_EXTERNAL,
    CONF_ROOM_NAME,
    CONF_SCHEDULE,
    CONF_SETPOINT,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
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
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_TURN_OFF_DEADBAND,
    DEFAULT_IDLE_OFFSET,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_THERMAL_MASS,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DOMAIN,
    HISTORY_BUFFER_SIZE,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
    UPDATE_INTERVAL,
)
from .heat_sources import ElectricHeater, HeatPump, HeatSource
from .thermal_model import HouseModel, Room, RoomConnection, Window
from .controller import HeatingMPCController
from .solar_model import room_solar_gains
from .schedule import (
    EffectiveSetpoint,
    RoomSchedule,
    build_schedule,
    next_transition,
    resolve_effective_setpoint,
)

_LOGGER = logging.getLogger(__name__)


def _coerce_interval_seconds(value: Any) -> float:
    """Return a numeric interval in seconds from int/float/str/timedelta values."""
    if isinstance(value, timedelta):
        return float(value.total_seconds())
    return float(value)


def _coerce_float(value: Any) -> Optional[float]:
    """Return ``float(value)`` or None when the value is missing or unparsable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_cloud_cover_percent(value: Any) -> Optional[float]:
    """Convert a cloud-cover percentage (0–100) to a fraction in [0, 1].

    Returns ``None`` for missing or unparsable input.  Values outside the
    [0, 100] range are clamped.
    """
    f = _coerce_float(value)
    if f is None:
        return None
    return max(0.0, min(1.0, f / 100.0))


# Representative cloud-cover fractions for the standard HA weather conditions.
# Used when ``cloud_coverage`` is not available on the entity / forecast entry.
# Values picked to be conservative: clearly clear vs clearly overcast, with
# partlycloudy in the middle.  Conditions implying heavy precipitation map
# to fully overcast.  See https://www.home-assistant.io/integrations/weather/
# for the standardised condition strings.
_CONDITION_CLOUD_COVER: Dict[str, float] = {
    "sunny": 0.0,
    "clear-night": 0.0,
    "windy": 0.0,
    "windy-variant": 0.3,
    "partlycloudy": 0.3,
    "cloudy": 0.85,
    "fog": 1.0,
    "hail": 1.0,
    "lightning": 0.9,
    "lightning-rainy": 1.0,
    "pouring": 1.0,
    "rainy": 0.95,
    "snowy": 1.0,
    "snowy-rainy": 1.0,
    "exceptional": 0.85,
}


def _parse_forecast_field(
    forecast_data: list,
    horizon: int,
    dt: float,
    field: str,
    coerce: Any,
    fallback_field: Optional[str] = None,
    fallback_coerce: Any = None,
    now: Optional[datetime] = None,
) -> Optional[List[float]]:
    """Parse a forecast field into ``horizon`` values interpolated to MPC steps.

    Parameters
    ----------
    forecast_data : list of dict
        Raw forecast entries.  Each entry must have a ``datetime`` key
        (ISO-8601 string or datetime object) plus the requested ``field``.
    horizon : int
        Number of MPC prediction steps.
    dt : float
        MPC time step in seconds.
    field : str
        Primary field name to read from each entry (e.g. ``"temperature"``,
        ``"cloud_coverage"``).
    coerce : callable
        Function mapping the raw field value to a float in the desired unit,
        or returning None when the value is missing / unparsable.
    fallback_field, fallback_coerce : optional
        When provided and the primary field is missing/unparsable for an
        entry, the parser tries this fallback field with this coerce function
        before discarding the entry.  Used to back ``cloud_coverage`` with
        the per-entry ``condition`` string.
    now : datetime, optional
        Reference time for interpolation.  Defaults to current UTC.

    Returns
    -------
    list of float or None
        ``horizon`` values aligned to MPC steps, or None when no usable
        entries are found.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    entries: List[tuple] = []
    for entry in forecast_data:
        dt_str = entry.get("datetime")
        if dt_str is None:
            continue
        raw = entry.get(field)
        value = coerce(raw)
        if value is None and fallback_field is not None and fallback_coerce is not None:
            value = fallback_coerce(entry.get(fallback_field))
        if value is None:
            continue
        try:
            if isinstance(dt_str, str):
                if dt_str.endswith("Z"):
                    dt_str = dt_str[:-1] + "+00:00"
                fc_time = datetime.fromisoformat(dt_str)
            elif isinstance(dt_str, datetime):
                fc_time = dt_str
            else:
                continue
            if fc_time.tzinfo is None:
                fc_time = fc_time.replace(tzinfo=timezone.utc)
            entries.append((fc_time.timestamp(), float(value)))
        except (ValueError, TypeError):
            continue

    if not entries:
        return None

    entries.sort(key=lambda e: e[0])

    now_ts = now.timestamp()
    result: List[float] = []
    for k in range(horizon):
        target_ts = now_ts + dt * (k + 1)
        result.append(HeatingAssistantCoordinator._interpolate_forecast(entries, target_ts))

    return result


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

        if src_type == SOURCE_TYPE_ELECTRIC:
            sources.append(
                ElectricHeater(
                    name=name,
                    room=room,
                    max_power=max_power,
                    efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_EFFICIENCY),
                    heater_entity=entity,
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
        self._horizon: int = data.get(CONF_HORIZON, DEFAULT_HORIZON)
        self._energy_weight: float = data.get(CONF_ENERGY_WEIGHT, DEFAULT_ENERGY_WEIGHT)
        self._smoothing_weight: float = data.get(CONF_SMOOTHING_WEIGHT, DEFAULT_SMOOTHING_WEIGHT)
        self._constraint_offset: float = data.get(CONF_CONSTRAINT_OFFSET, DEFAULT_CONSTRAINT_OFFSET)
        self._terminal_weight: float = data.get(CONF_TERMINAL_WEIGHT, DEFAULT_TERMINAL_WEIGHT)

        rooms_cfg: List[Dict[str, Any]] = data.get(CONF_ROOMS, [])
        sources_cfg: List[Dict[str, Any]] = data.get(CONF_HEAT_SOURCES, [])

        self.model: HouseModel = build_house_model(rooms_cfg)
        self.heat_sources: List[HeatSource] = build_heat_sources(sources_cfg)

        # Map room_name → list of temp_sensor entity_ids (for state updates)
        self._temp_sensors: Dict[str, List[str]] = {}
        for rc in rooms_cfg:
            room_name = rc[CONF_ROOM_NAME]
            sensors: List[str] = []
            # Support both singular 'temp_sensor' and plural 'temp_sensors'
            if CONF_TEMP_SENSORS in rc:
                sensors.extend(rc[CONF_TEMP_SENSORS])
            if CONF_TEMP_SENSOR in rc:
                single = rc[CONF_TEMP_SENSOR]
                if single not in sensors:
                    sensors.append(single)
            if sensors:
                self._temp_sensors[room_name] = sensors

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
        )

        # Per-room user toggle (True = on, False = manually disabled by the
        # climate UI / automations).  ``is_room_enabled`` combines this with
        # the schedule-imposed disable so the two concerns never clobber
        # each other.  Defaults to True so the system is active after
        # (re)start.
        self._room_enabled: Dict[str, bool] = {
            name: True for name in self.model.room_names
        }
        # True while an "off" schedule period is currently disabling the room.
        # Maintained by ``_apply_schedule`` and read by ``is_room_enabled``.
        self._schedule_disabled: Dict[str, bool] = {
            name: False for name in self.model.room_names
        }

        # Comfort schedules: per-room time-of-day setpoint / setback rules.
        # ``_base_setpoint`` is the user's chosen setpoint when no schedule
        # period is active (i.e. the value the climate UI restores to);
        # ``_room_schedule`` holds the parsed schedule;
        # ``_schedule_enabled`` lets the user suspend the schedule per room
        # at runtime (e.g. "stay up late tonight") without losing the
        # configured rules.
        self._room_schedule: Dict[str, RoomSchedule] = {}
        self._base_setpoint: Dict[str, float] = {}
        self._schedule_enabled: Dict[str, bool] = {}
        # Last applied effective setpoint per room — exposed for diagnostics.
        self._effective_setpoint: Dict[str, EffectiveSetpoint] = {}
        for rc in rooms_cfg:
            room_name = rc[CONF_ROOM_NAME]
            self._room_schedule[room_name] = build_schedule(rc.get(CONF_SCHEDULE))
            self._base_setpoint[room_name] = float(
                rc.get(CONF_SETPOINT, DEFAULT_SETPOINT)
            )
            self._schedule_enabled[room_name] = True

        # Latest control actions (source_name → fraction 0‑1)
        self.actions: Dict[str, float] = {}

        # Track which heat sources are in cooling mode (source_name → bool)
        self._cooling_active: Dict[str, bool] = {}

        # Visualization data
        self.solar_gains: Dict[str, float] = {}
        # Current cloud-cover fraction in [0, 1], or None when unavailable.
        # Used to attenuate the clear-sky solar model — see solar_model.cloud_attenuation_factor.
        self.cloud_cover: Optional[float] = None
        self.outdoor_temp: float = 5.0
        self.heat_flows: Dict[str, Dict[str, float]] = {}
        self.predictions: list = []
        self.outdoor_forecast: List[float] = []
        self.solar_forecast: list = []
        self.heating_schedule: list = []

        # Rolling observation history for ML parameter estimation.
        # Each entry is a dict: {y, u, d_outdoor, d_solar, timestamp}.
        self._history_buffer: deque = deque(maxlen=HISTORY_BUFFER_SIZE)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._update_interval),
        )

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

    # ------------------------------------------------------------------
    # DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> Dict[str, Any]:
        """
        Called by HA periodically.  Reads sensors, runs the controller,
        and returns a snapshot of the system state.
        """
        try:
            # 1. Update measured room temperatures from HA sensor states.
            #    When multiple sensors are configured for a room, use the
            #    average of all valid readings.
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
                    self.model.rooms[room_name].temperature = (
                        sum(readings) / len(readings)
                    )

            # 1b. Apply comfort schedules: resolve the active period for each
            #     room and update the live setpoint / enabled flag accordingly.
            #     Done after measurements are read so frost-protection logic
            #     sees the current temperature.
            now_local = datetime.now()
            self._apply_schedule(now_local)

            # 2. Read outdoor temperature
            outdoor_temp = self._read_outdoor_temp()
            self.outdoor_temp = outdoor_temp

            # 2b. Read weather forecast for outdoor temperature prediction
            #     and cloud-cover (used to attenuate the clear-sky solar model)
            outdoor_forecast = await self._async_read_weather_forecast()
            cloud_cover_now = self._read_cloud_cover_now()
            cloud_forecast = await self._async_read_cloud_forecast()

            # 3. Compute current solar gains for visualization
            now = datetime.now(tz=timezone.utc)
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

                # Capture Kalman innovation for diagnostics (may be None on first step)
                # controller.last_innovation is populated by compute() after splitting
                # the EKF predict/update steps to record ν = y − hm(x̂⁻).
                kalman_innovation: Optional[List[float]] = self.controller.last_innovation
            except Exception:
                _LOGGER.warning(
                    "Failed to compute MPC actions; using thermal-model fallback "
                    "for visualisation data",
                    exc_info=True,
                )
                # Keep previous actions if available; otherwise default to all-off.
                if not self.actions:
                    self.actions = {src.name: 0.0 for src in self.heat_sources}

                # Build outdoor / solar forecasts so they are available for the
                # thermal-model prediction below.
                if outdoor_forecast:
                    self.outdoor_forecast = list(outdoor_forecast[:self._horizon])
                    if len(self.outdoor_forecast) < self._horizon:
                        self.outdoor_forecast.extend(
                            [outdoor_temp] * (self._horizon - len(self.outdoor_forecast))
                        )
                else:
                    self.outdoor_forecast = [outdoor_temp] * self._horizon
                self.solar_forecast = [
                    dict(self.solar_gains) for _ in range(self._horizon + 1)
                ]

                # Build a per-room heating schedule from the current (or fallback)
                # actions so HeatingPlanSensor has meaningful data.
                fallback_step: Dict[str, float] = {
                    name: 0.0 for name in self.model.room_names
                }
                for src in self.heat_sources:
                    frac = float(self.actions.get(src.name, 0.0))
                    fallback_step[src.room] += src.thermal_power(max(0.0, frac), outdoor_temp)
                self.heating_schedule = [
                    dict(fallback_step) for _ in range(self._horizon)
                ]

                # Simulate a temperature trajectory with the simple RC thermal model
                # so TemperatureForecastSensor shows a real trend instead of nothing.
                solar_seq = [dict(self.solar_gains) for _ in range(self._horizon)]
                try:
                    self.predictions = self.model.predict(
                        horizon=self._horizon,
                        dt=self.dt,
                        heat_schedule=self.heating_schedule,
                        outdoor_temps=self.outdoor_forecast,
                        solar_gain_schedule=solar_seq,
                    )
                except Exception:
                    _LOGGER.debug(
                        "Thermal-model fallback prediction failed; "
                        "forecast entities will show no future trajectory",
                        exc_info=True,
                    )
                    self.predictions = []

                kalman_innovation = None

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
        if self._outdoor_entity:
            state = self.hass.states.get(self._outdoor_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    return float(state.state)
                except ValueError:
                    pass
        # No dedicated outdoor-temp entity — try the weather entity's current
        # temperature attribute (weather entities expose the current observation
        # via attributes["temperature"]).
        if self._weather_entity:
            state = self.hass.states.get(self._weather_entity)
            if state and state.state not in ("unknown", "unavailable"):
                temp = state.attributes.get("temperature")
                if temp is not None:
                    try:
                        return float(temp)
                    except (ValueError, TypeError):
                        pass
        # Fall back to a benign default
        return 5.0

    async def _async_read_weather_forecast(self) -> Optional[List[float]]:
        """Read outdoor temperature forecast from a weather entity.

        Tries the modern ``weather.get_forecasts`` service first (HA 2023.9+),
        then falls back to reading the deprecated ``forecast`` state attribute
        for backward compatibility with older HA versions.

        Returns a list of N outdoor temperature values aligned to the MPC
        horizon steps, or None if no weather entity is configured or
        no forecast data is available.
        """
        forecast_data = await self._async_get_forecast_entries()
        if not forecast_data:
            return None
        return self._parse_weather_forecast(forecast_data, self._horizon, self.dt)

    async def _async_read_cloud_forecast(self) -> Optional[List[float]]:
        """Read cloud-cover forecast (fraction in [0, 1]) from the weather entity.

        Uses ``cloud_coverage`` percent values when present on the forecast
        entries (populated by most modern HA weather integrations), and falls
        back to mapping the per-entry ``condition`` string when the percentage
        is missing.  Returns a list of N values aligned to the MPC horizon
        steps, or None if no usable data is available.
        """
        forecast_data = await self._async_get_forecast_entries()
        if not forecast_data:
            return None
        return self._parse_cloud_forecast(forecast_data, self._horizon, self.dt)

    async def _async_get_forecast_entries(self) -> Optional[list]:
        """Fetch raw forecast entries from the configured weather entity.

        Shared by the temperature and cloud-cover readers so both signals come
        from a single service call per cycle.  Tries ``weather.get_forecasts``
        first, then falls back to the deprecated ``forecast`` state attribute.
        """
        if not self._weather_entity:
            return None

        # ── Modern approach: weather.get_forecasts service (HA 2023.9+) ──────
        if self.hass.services.has_service("weather", "get_forecasts"):
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    service_data={
                        "entity_id": self._weather_entity,
                        "type": "hourly",
                    },
                    blocking=True,
                    return_response=True,
                )
                if response and self._weather_entity in response:
                    forecast_data = response[self._weather_entity].get("forecast", [])
                    if forecast_data:
                        return forecast_data
            except Exception as exc:
                _LOGGER.debug(
                    "weather.get_forecasts service call failed for %s, "
                    "falling back to state attribute: %s",
                    self._weather_entity,
                    exc,
                )

        # ── Fallback: read from the deprecated state attribute ────────────────
        state = self.hass.states.get(self._weather_entity)
        if state is None:
            return None

        forecast_data = state.attributes.get("forecast")
        if not forecast_data:
            return None
        return forecast_data

    def _read_cloud_cover_now(self) -> Optional[float]:
        """Read the current cloud-cover fraction in [0, 1] from the weather entity.

        Prefers the numeric ``cloud_coverage`` attribute (percent) when
        present, and falls back to mapping the entity's ``condition`` /
        state string (``sunny``, ``partlycloudy``, ``cloudy``, ``rainy``, …)
        to a representative fraction.  Returns ``None`` when no weather
        entity is configured or neither signal is available, in which case
        the solar model stays on its clear-sky default.
        """
        if not self._weather_entity:
            return None
        state = self.hass.states.get(self._weather_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None

        cc = state.attributes.get("cloud_coverage")
        frac = _coerce_cloud_cover_percent(cc)
        if frac is not None:
            return frac

        # Weather entities expose the condition as the entity state.
        return _CONDITION_CLOUD_COVER.get(state.state)

    @staticmethod
    def _parse_weather_forecast(
        forecast_data: list,
        horizon: int,
        dt: float,
        now: Optional[datetime] = None,
    ) -> Optional[List[float]]:
        """Parse raw weather forecast entries into interpolated horizon temperatures."""
        return _parse_forecast_field(
            forecast_data,
            horizon,
            dt,
            field="temperature",
            coerce=_coerce_float,
            now=now,
        )

    @staticmethod
    def _parse_cloud_forecast(
        forecast_data: list,
        horizon: int,
        dt: float,
        now: Optional[datetime] = None,
    ) -> Optional[List[float]]:
        """Parse raw weather forecast entries into interpolated horizon cloud-cover.

        Returns fractions in [0, 1].  Prefers the numeric ``cloud_coverage``
        field; falls back to mapping the per-entry ``condition`` string when
        the percentage is missing on individual entries.
        """
        return _parse_forecast_field(
            forecast_data,
            horizon,
            dt,
            field="cloud_coverage",
            coerce=_coerce_cloud_cover_percent,
            fallback_field="condition",
            fallback_coerce=lambda v: _CONDITION_CLOUD_COVER.get(v) if isinstance(v, str) else None,
            now=now,
        )

    @staticmethod
    def _interpolate_forecast(
        entries: List[tuple], target_ts: float,
    ) -> float:
        """Linearly interpolate a forecast field at a target timestamp.

        If the target is before all entries, returns the first entry's value.
        If after all entries, returns the last entry's value.
        """
        if not entries:
            return 5.0  # fallback (only reached via the temperature path)

        if target_ts <= entries[0][0]:
            return entries[0][1]
        if target_ts >= entries[-1][0]:
            return entries[-1][1]

        for i in range(len(entries) - 1):
            t0, v0 = entries[i]
            t1, v1 = entries[i + 1]
            if t0 <= target_ts <= t1:
                if t1 == t0:
                    return v0
                frac = (target_ts - t0) / (t1 - t0)
                return v0 + frac * (v1 - v0)

        return entries[-1][1]

    async def _apply_actions(self, outdoor_temp: float) -> None:
        """Write the computed set-point fractions to heater entities via HA services."""
        for src in self.heat_sources:
            fraction = self.actions.get(src.name, 0.0)

            # If the room is disabled, force fraction to 0 (turn off).
            if not self.is_room_enabled(src.room):
                fraction = 0.0

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
                            if hasattr(self, 'controller'):
                                self.controller.notify_applied_u(src.name, -1.0)

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

                    if not self.is_room_enabled(src.room):
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
                        target_temp = room_setpoint
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
                    {"entity_id": entity_id, "value": round(fraction * 100)},
                    blocking=False,
                )
            elif domain == "switch":
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
            )

        return result

    def _apply_estimated_parameters(
        self,
        estimated_params: Dict[str, Dict[str, float]],
        estimated_inter_room_r: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        Apply estimated parameters to the house model and rebuild the
        MPC controller so that the new values take effect immediately.

        The existing Kalman filter state is discarded when the controller
        is rebuilt; it will be re-bootstrapped on the next update cycle.
        """
        for room_name, params in estimated_params.items():
            if room_name not in self.model.rooms:
                continue
            room = self.model.rooms[room_name]
            room.thermal_mass = float(params["thermal_mass"])
            room.r_external = float(params["r_external"])

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
        )

        _LOGGER.info(
            "Applied estimated thermal parameters: %s",
            {
                name: {
                    "thermal_mass": params["thermal_mass"],
                    "r_external": params["r_external"],
                }
                for name, params in estimated_params.items()
            },
        )
