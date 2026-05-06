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
from typing import Any, Dict, List, Optional

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

_LOGGER = logging.getLogger(__name__)


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
            options.get(CONF_UPDATE_INTERVAL)
            or data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
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
            dt=float(self._update_interval),
            measurement_dt=float(self._update_interval),
            latitude=self._latitude,
            longitude=self._longitude,
            energy_weight=self._energy_weight,
            smoothing_weight=self._smoothing_weight,
            constraint_offset=self._constraint_offset,
            terminal_weight=self._terminal_weight,
        )

        # Per-room enabled state (True = active, False = off).
        # Defaults to True so the system is active after (re)start.
        self._room_enabled: Dict[str, bool] = {
            name: True for name in self.model.room_names
        }

        # Latest control actions (source_name → fraction 0‑1)
        self.actions: Dict[str, float] = {}

        # Track which heat sources are in cooling mode (source_name → bool)
        self._cooling_active: Dict[str, bool] = {}

        # Visualization data
        self.solar_gains: Dict[str, float] = {}
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
        return float(self._update_interval)

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

            # 2. Read outdoor temperature
            outdoor_temp = self._read_outdoor_temp()
            self.outdoor_temp = outdoor_temp

            # 2b. Read weather forecast for outdoor temperature prediction
            outdoor_forecast = await self._async_read_weather_forecast()

            # 3. Compute current solar gains for visualization
            now = datetime.now(tz=timezone.utc)
            self.solar_gains = {
                name: room_solar_gains(
                    self.model.rooms[name].windows,
                    now,
                    self._latitude,
                    self._longitude,
                )
                for name in self.model.room_names
            }

            # 4. Run MPC controller
            self.actions = self.controller.compute(
                outdoor_temp=outdoor_temp,
                solar_gains=self.solar_gains,
                now=now,
                outdoor_forecast=outdoor_forecast,
            )

            # 5. Store prediction trajectory and heat-flow breakdown
            self.predictions = self.controller.predictions
            self.heat_flows = self.model.compute_heat_flows(outdoor_temp)
            self.outdoor_forecast = self.controller.outdoor_forecast
            self.solar_forecast = self.controller.solar_forecast
            self.heating_schedule = self.controller.heating_schedule

            # Capture Kalman innovation for diagnostics (may be None on first step)
            try:
                kalman_innovation: Optional[List[float]] = (
                    self.controller._mpc._estimator.last_innovation
                )
            except AttributeError:
                kalman_innovation = None

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
                "timestamp": now.timestamp(),
                # Kalman innovation ν = y − C x̂⁻  (None on the very first step)
                "kalman_innovation": kalman_innovation,
            })

            # 7. Write set-points to heater entities
            await self._apply_actions(outdoor_temp)

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
                        return self._parse_weather_forecast(
                            forecast_data, self._horizon, float(self._update_interval)
                        )
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

        return self._parse_weather_forecast(forecast_data, self._horizon, float(self._update_interval))

    @staticmethod
    def _parse_weather_forecast(
        forecast_data: list,
        horizon: int,
        dt: float,
        now: Optional[datetime] = None,
    ) -> Optional[List[float]]:
        """Parse raw weather forecast entries into interpolated MPC horizon temperatures.

        Parameters
        ----------
        forecast_data : list of dict
            Raw forecast entries from either the ``weather.get_forecasts``
            service or the deprecated ``forecast`` state attribute.  Each
            entry must have a ``datetime`` key (ISO-8601 string or datetime
            object) and a ``temperature`` key (float, °C).
        horizon : int
            Number of MPC prediction steps.
        dt : float
            MPC time step in seconds.
        now : datetime, optional
            Reference time for interpolation.  Defaults to the current UTC
            time when not provided.

        Returns
        -------
        list of float or None
            List of ``horizon`` interpolated outdoor temperatures (one per MPC
            step), or None if the data cannot be parsed into any valid entries.
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)

        entries: List[tuple] = []
        for entry in forecast_data:
            dt_str = entry.get("datetime")
            temp = entry.get("temperature")
            if dt_str is None or temp is None:
                continue
            try:
                if isinstance(dt_str, str):
                    # Handle ISO-8601 strings (with or without timezone)
                    if dt_str.endswith("Z"):
                        dt_str = dt_str[:-1] + "+00:00"
                    fc_time = datetime.fromisoformat(dt_str)
                elif isinstance(dt_str, datetime):
                    fc_time = dt_str
                else:
                    continue
                if fc_time.tzinfo is None:
                    fc_time = fc_time.replace(tzinfo=timezone.utc)
                entries.append((fc_time.timestamp(), float(temp)))
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

    @staticmethod
    def _interpolate_forecast(
        entries: List[tuple], target_ts: float,
    ) -> float:
        """Linearly interpolate forecast temperature at a target timestamp.

        If the target is before all entries, returns the first entry's temp.
        If after all entries, returns the last entry's temp.
        """
        if not entries:
            return 5.0  # fallback

        # Before first entry
        if target_ts <= entries[0][0]:
            return entries[0][1]

        # After last entry
        if target_ts >= entries[-1][0]:
            return entries[-1][1]

        # Find the two surrounding entries
        for i in range(len(entries) - 1):
            t0, temp0 = entries[i]
            t1, temp1 = entries[i + 1]
            if t0 <= target_ts <= t1:
                # Linear interpolation
                if t1 == t0:
                    return temp0
                frac = (target_ts - t0) / (t1 - t0)
                return temp0 + frac * (temp1 - temp0)

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

                    if fraction < 0.0:
                        # MPC-requested active cooling (fraction ∈ [-1, 0)).
                        # Use "cool" mode when available; fall back to "dry"
                        # then "fan_only".  The temperature setpoint is
                        # placed below the HP's internal sensor by an amount
                        # proportional to the requested cooling intensity.
                        cooling_fraction = abs(fraction)
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

                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": cooling_mode},
                            blocking=False,
                        )

                        if hp_internal_temp is not None:
                            target_temp = src.target_temperature_cooling(
                                cooling_fraction, hp_internal_temp,
                            )
                        else:
                            target_temp = src.target_temperature_cooling(
                                cooling_fraction, room_temp,
                            )

                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": target_temp},
                            blocking=False,
                        )

                        # _current_power is already set by controller.compute()
                        # via smooth_thermal_power — no override needed here.
                        if not hasattr(self, '_cooling_active'):
                            self._cooling_active = {}
                        self._cooling_active[src.name] = True
                    elif room_temp > room_setpoint:
                        # Room is above setpoint but the MPC did not request
                        # active cooling (e.g. cooling-capable source already
                        # at u=0, or heating-only source).  Activate a
                        # cooling mode.  "cool" (compressor cooling) is
                        # preferred; fall back to "dry" (dehumidify) if
                        # "cool" is not listed, then "fan_only".
                        #
                        # The temperature setpoint is also placed below the
                        # HP's own sensor reading to prevent any residual
                        # heating.  The offset grows with the degree to which
                        # the room exceeds the desired setpoint.
                        #
                        # We also notify the controller so the EKF uses the
                        # correct previous input on the next compute() call,
                        # preventing state-estimate drift from the mismatch
                        # between the OCP output (u=0) and the actually
                        # applied cooling.
                        overshoot = max(0.0, room_temp - room_setpoint)
                        idle_offset = DEFAULT_IDLE_OFFSET + overshoot

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

                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": cooling_mode},
                            blocking=False,
                        )

                        if hp_internal_temp is not None:
                            target_temp = hp_internal_temp - idle_offset
                        else:
                            target_temp = room_temp - idle_offset

                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": target_temp},
                            blocking=False,
                        )

                        # Apply cooling power to the heat source so the
                        # thermal model accounts for heat removal.
                        cooling_power = src.cooling_power(outdoor_temp)
                        src.set_power(0.0, outdoor_temp)  # Clear heating power
                        src._current_power = cooling_power  # Set to negative (cooling)

                        # Notify the controller that full cooling was applied
                        # so the EKF uses the correct u_prev on the next step.
                        if hasattr(self, 'controller'):
                            self.controller.notify_applied_u(src.name, -1.0)

                        if not hasattr(self, '_cooling_active'):
                            self._cooling_active = {}
                        self._cooling_active[src.name] = True
                    elif fraction > 0.0:
                        # Active heating: keep on with offset-based setpoint
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

                        # Not in cooling mode - clear flag
                        if not hasattr(self, '_cooling_active'):
                            self._cooling_active = {}
                        self._cooling_active[src.name] = False
                    else:
                        # Idle: room at or below setpoint, keep HP on but
                        # set target below internal temp so the device
                        # stops heating.  The offset is re-applied every
                        # update cycle to track any drift in the internal
                        # sensor.
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

                        # Not in cooling mode - clear flag
                        if not hasattr(self, '_cooling_active'):
                            self._cooling_active = {}
                        self._cooling_active[src.name] = False
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
        """Update the temperature setpoint for a room."""
        if room_name in self.model.rooms:
            self.model.rooms[room_name].setpoint = setpoint

    def get_room_setpoint(self, room_name: str) -> float:
        """Return the current setpoint for a room."""
        if room_name in self.model.rooms:
            return self.model.rooms[room_name].setpoint
        return DEFAULT_SETPOINT

    # ------------------------------------------------------------------
    # Room enable/disable helpers (called by climate platform)
    # ------------------------------------------------------------------

    def set_room_enabled(self, room_name: str, enabled: bool) -> None:
        """Enable or disable heating control for a room."""
        self._room_enabled[room_name] = enabled

    def is_room_enabled(self, room_name: str) -> bool:
        """Return whether heating control is active for a room."""
        return self._room_enabled.get(room_name, True)

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
            dt=self._update_interval,  # must match history buffer sampling interval, not MPC horizon
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
            dt=float(self._update_interval),
            measurement_dt=float(self._update_interval),
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
