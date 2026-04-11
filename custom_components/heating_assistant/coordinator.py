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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOMS,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MIN_POWER,
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
from .heat_sources import ElectricHeater, HeatPump, HeatSource
from .thermal_model import HouseModel, Room, RoomConnection, Window
from .controller import MPCController
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
        self._dt: float = data.get(CONF_DT, DEFAULT_DT)
        self._horizon: int = data.get(CONF_HORIZON, DEFAULT_HORIZON)

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

        self.controller = MPCController(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=self._horizon,
            dt=self._dt,
            latitude=self._latitude,
            longitude=self._longitude,
        )

        # Latest control actions (source_name → fraction 0‑1)
        self.actions: Dict[str, float] = {}

        # Visualization data
        self.solar_gains: Dict[str, float] = {}
        self.outdoor_temp: float = 5.0
        self.heat_flows: Dict[str, Dict[str, float]] = {}
        self.predictions: list = []

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

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
            )

            # 5. Store prediction trajectory and heat-flow breakdown
            self.predictions = self.controller.predictions
            self.heat_flows = self.model.compute_heat_flows(outdoor_temp)

            # 6. Write set-points to heater entities
            await self._apply_actions(outdoor_temp)

            return {
                "temperatures": dict(self.model.temperatures),
                "outdoor_temp": outdoor_temp,
                "actions": dict(self.actions),
                "solar_gains": dict(self.solar_gains),
                "predictions": list(self.predictions),
                "heat_flows": dict(self.heat_flows),
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
        # Fall back to a benign default
        return 5.0

    async def _apply_actions(self, outdoor_temp: float) -> None:
        """Write the computed set-point fractions to heater entities via HA services."""
        for src in self.heat_sources:
            fraction = self.actions.get(src.name, 0.0)
            entity_id = src.heater_entity
            if not entity_id:
                continue
            # Map fraction to 0–100 % and call climate/number/switch service
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            domain = entity_id.split(".")[0]
            if domain == "climate":
                # For climate entities, map fraction to a heat mode
                if fraction > 0.0:
                    await self.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": "heat"},
                        blocking=False,
                    )
                else:
                    await self.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": "off"},
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
