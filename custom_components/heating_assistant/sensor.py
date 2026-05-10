"""
Heating Assistant – Sensor platform.

For each room the following sensor entities are created:
- Predicted temperature  (model's 1-step-ahead prediction) [°C]
- Heating power          (sum of active heater outputs for the room; negative for cooling) [W]
- Solar gain             (current solar heat gain through windows) [W]
- Temperature forecast   (MPC prediction trajectory) [°C]
- Heat loss              (instantaneous heat loss breakdown) [W]
- Energy balance         (net energy flow in the room) [W]
- Heating plan           (planned heating/cooling power over MPC horizon; negative for cooling) [W]
- Solar forecast         (predicted solar gain over MPC horizon) [W]
- Temperature prediction (same data as Temperature forecast, stable availability) [°C]
- Heating plan prediction (same data as Heating plan, stable availability) [W]
- Solar power prediction (same data as Solar forecast, stable availability) [W]

For each heat source:
- Control action         (MPC controller output fraction) [%]

For each heat pump source:
- COP                    (current coefficient of performance)

System-wide:
- Outdoor temperature    (as read by the integration) [°C]
- Outdoor temp forecast  (timestamped forecast over MPC horizon) [°C]
- Outdoor temperature prediction (same data as Outdoor temp forecast, stable availability) [°C]
- System efficiency      (aggregate system metrics)

The "prediction" variants are the ones the advanced visualisation dashboards
in the README reference.  They expose identical data to the matching forecast
/ plan sensors but override ``available`` to True so dashboards keep
rendering the trajectory across transient coordinator update failures.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator
from .heat_sources import HeatPump

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant sensor entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: List[SensorEntity] = []

    # Per-room sensors
    for room_name in coordinator.model.room_names:
        entities.append(PredictedTemperatureSensor(coordinator, room_name))
        entities.append(HeatingPowerSensor(coordinator, room_name))
        entities.append(SolarGainSensor(coordinator, room_name))
        entities.append(TemperatureForecastSensor(coordinator, room_name))
        entities.append(HeatLossSensor(coordinator, room_name))
        entities.append(EnergyBalanceSensor(coordinator, room_name))
        entities.append(HeatingPlanSensor(coordinator, room_name))
        entities.append(SolarForecastSensor(coordinator, room_name))
        # Always-available prediction entities (used by the advanced
        # visualisation dashboards).  These mirror the data of the forecast/
        # plan sensors above but stay available across coordinator update
        # failures so dashboards never lose the trajectory.
        entities.append(TemperaturePredictionSensor(coordinator, room_name))
        entities.append(HeatingPlanPredictionSensor(coordinator, room_name))
        entities.append(SolarPowerPredictionSensor(coordinator, room_name))
        # Model fit diagnostics sensors
        entities.append(PredictionErrorSensor(coordinator, room_name))
        entities.append(ModelFitQualitySensor(coordinator, room_name))
        entities.append(ParameterConfidenceSensor(coordinator, room_name))
        # Advanced model quality sensors
        entities.append(OpenLoopRMSESensor(coordinator, room_name))
        entities.append(KalmanInnovationSensor(coordinator, room_name))
        entities.append(ResidualACFSensor(coordinator, room_name))

    # Per-source sensors
    for src in coordinator.heat_sources:
        entities.append(ControlActionSensor(coordinator, src.name))
        if isinstance(src, HeatPump):
            entities.append(HeatPumpCOPSensor(coordinator, src.name))

    # System-wide sensors
    entities.append(OutdoorTemperatureSensor(coordinator))
    entities.append(OutdoorForecastSensor(coordinator))
    entities.append(OutdoorTemperaturePredictionSensor(coordinator))
    entities.append(SystemEfficiencySensor(coordinator))
    entities.append(MPCPerformanceSensor(coordinator))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Predicted temperature sensor
# ---------------------------------------------------------------------------

class PredictedTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the model-predicted temperature for a room."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Predicted Temperature"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_predicted_temperature"

    @property
    def native_value(self) -> Optional[float]:
        """Return the current model temperature (updated each coordinator cycle)."""
        temp = self._coordinator.model.rooms[self._room_name].temperature
        return round(temp, 2)

    @property
    def extra_state_attributes(self) -> dict:
        room = self._coordinator.model.rooms[self._room_name]
        return {
            "setpoint": room.setpoint,
            "thermal_mass": room.thermal_mass,
            "r_external": room.r_external,
        }


# ---------------------------------------------------------------------------
# Heating power sensor
# ---------------------------------------------------------------------------

class HeatingPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the total active heating/cooling power for a room.

    Positive values indicate heating, negative values indicate cooling
    (heat removal when heat pumps operate in dry/dehumidify mode).
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Heating Power"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heating_power"

    @property
    def native_value(self) -> float:
        """Return the sum of current heater powers for the room [W]."""
        sources = self._coordinator.heat_sources
        return round(
            sum(s.current_power for s in sources if s.room == self._room_name),
            1,
        )

    @property
    def extra_state_attributes(self) -> dict:
        sources = [s for s in self._coordinator.heat_sources if s.room == self._room_name]
        return {
            src.name: round(src.current_power, 1)
            for src in sources
        }


# ---------------------------------------------------------------------------
# Solar gain sensor
# ---------------------------------------------------------------------------

class SolarGainSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the current solar heat gain for a room [W]."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:white-balance-sunny"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Solar Gain"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_solar_gain"

    @property
    def native_value(self) -> float:
        gain = self._coordinator.solar_gains.get(self._room_name, None)
        if gain is None:
            _LOGGER.debug(
                "No solar gain data for room %s; defaulting to 0", self._room_name
            )
            return 0.0
        return round(gain, 1)

    @property
    def extra_state_attributes(self) -> dict:
        room = self._coordinator.model.rooms[self._room_name]
        return {
            "window_count": len(room.windows),
            "total_window_area": round(sum(w.area for w in room.windows), 2),
        }


# ---------------------------------------------------------------------------
# Control action sensor (per heat source)
# ---------------------------------------------------------------------------

class ControlActionSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the MPC control action for a heat source [%]."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:tune-vertical"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – Control Action"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_control_action"

    @property
    def native_value(self) -> float:
        fraction = self._coordinator.actions.get(self._source_name, 0.0)
        return round(fraction * 100.0, 1)

    @property
    def extra_state_attributes(self) -> dict:
        src = next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )
        if src is None:
            return {}
        return {
            "room": src.room,
            "max_power": src.max_power,
            "current_power": round(src.current_power, 1),
        }


# ---------------------------------------------------------------------------
# Heat pump COP sensor
# ---------------------------------------------------------------------------

class HeatPumpCOPSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the current COP of a heat pump source."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – COP"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_cop"

    @property
    def native_value(self) -> Optional[float]:
        src = next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )
        if src is None or not isinstance(src, HeatPump):
            return None
        return round(src.cop(self._coordinator.outdoor_temp), 2)

    @property
    def extra_state_attributes(self) -> dict:
        src = next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )
        if src is None or not isinstance(src, HeatPump):
            return {}
        return {
            "cop_rated": src.cop_rated,
            "cop_temp_ref": src.cop_temp_ref,
            "min_power": src.min_power,
            "outdoor_temp": self._coordinator.outdoor_temp,
        }


# ---------------------------------------------------------------------------
# Outdoor temperature sensor
# ---------------------------------------------------------------------------

class OutdoorTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the outdoor temperature as read by the integration."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Outdoor Temperature"
        self._attr_unique_id = f"{DOMAIN}_outdoor_temperature"

    @property
    def native_value(self) -> float:
        return round(self._coordinator.outdoor_temp, 2)


# ---------------------------------------------------------------------------
# Outdoor temperature forecast sensor (system-wide)
# ---------------------------------------------------------------------------

class OutdoorForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the outdoor temperature forecast over the MPC horizon.

    The state is the current outdoor temperature [°C].  The full forecast is
    exposed as a timestamped ``forecast`` attribute for dashboard visualisation.
    When a weather entity is configured, the forecast reflects the interpolated
    weather predictions; otherwise a persistence forecast (current value held
    constant) is used.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Outdoor Temperature Forecast"
        self._attr_unique_id = f"{DOMAIN}_outdoor_temperature_forecast"

    @property
    def native_value(self) -> float:
        return round(self._coordinator.outdoor_temp, 2)

    @property
    def extra_state_attributes(self) -> dict:
        outdoor_forecast = self._coordinator.outdoor_forecast
        dt = self._coordinator.dt
        now = datetime.now(tz=timezone.utc)

        # Entry at t=now: bridge between history and prediction
        forecast: List[Dict[str, Any]] = [{
            "time": now.isoformat(),
            "outdoor_temp": round(self._coordinator.outdoor_temp, 2),
        }]
        for i, temp in enumerate(outdoor_forecast):
            step_time = now + timedelta(seconds=dt * (i + 1))
            forecast.append({
                "time": step_time.isoformat(),
                "outdoor_temp": round(temp, 2),
            })

        return {
            "forecast": forecast,
            "horizon_steps": len(outdoor_forecast),
            "step_seconds": dt,
            "horizon_minutes": round(len(outdoor_forecast) * dt / 60, 1),
        }


# ---------------------------------------------------------------------------
# Temperature forecast sensor (per room)
# ---------------------------------------------------------------------------

class TemperatureForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the MPC-predicted temperature trajectory for a room.

    The state is the predicted temperature at the end of the prediction
    horizon.  The full trajectory (one value per time step) is exposed as
    state attributes so users can plot it in Lovelace or use it in
    automations.
    """

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:chart-line"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Temperature Forecast"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_temperature_forecast"

    @property
    def native_value(self) -> Optional[float]:
        predictions = self._coordinator.predictions
        if not predictions:
            room = self._coordinator.model.rooms.get(self._room_name)
            if room is None:
                return None
            return round(room.temperature, 2)
        last = predictions[-1]
        temp = last.get(self._room_name)
        return round(temp, 2) if temp is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        predictions = self._coordinator.predictions
        room = self._coordinator.model.rooms[self._room_name]
        dt = self._coordinator.dt

        trajectory = []
        for i, pred in enumerate(predictions):
            temp = pred.get(self._room_name)
            if temp is not None:
                trajectory.append(round(temp, 2))

        # Build timestamped forecast entries for dashboard visualisation.
        # Each entry combines temperature, heating power, solar gain,
        # outdoor temperature, and setpoint so cards (e.g. apexcharts-card)
        # can plot them all from a single attribute.
        #
        # The first entry is at t=now with the *current* measured values so
        # that the predicted trace connects seamlessly to the historical
        # recorder trace (no gap between history and forecast).
        now = datetime.now(tz=timezone.utc)
        forecast = []
        outdoor_forecast = self._coordinator.outdoor_forecast
        solar_forecast = self._coordinator.solar_forecast
        heating_schedule = self._coordinator.heating_schedule

        # Current heating power for this room (actual, not planned)
        current_heating = sum(
            getattr(s, "current_power", 0.0)
            for s in self._coordinator.heat_sources
            if s.room == self._room_name
        )
        current_solar = self._coordinator.solar_gains.get(self._room_name, 0.0)

        # Entry at t=now: bridge between history and prediction
        now_entry: Dict[str, Any] = {
            "time": now.isoformat(),
            "temperature": round(room.temperature, 2),
            "heating_power": round(current_heating, 1),
            "solar_gain": round(current_solar, 1),
            "outdoor_temp": round(self._coordinator.outdoor_temp, 2),
            "setpoint": room.setpoint,
        }
        forecast.append(now_entry)

        for i, pred in enumerate(predictions):
            temp = pred.get(self._room_name)
            if temp is None:
                continue
            step_time = now + timedelta(seconds=dt * (i + 1))
            entry: Dict[str, Any] = {
                "time": step_time.isoformat(),
                "temperature": round(temp, 2),
                "setpoint": room.setpoint,
            }
            if i < len(heating_schedule):
                entry["heating_power"] = round(
                    heating_schedule[i].get(self._room_name, 0.0), 1
                )
            if i < len(solar_forecast):
                entry["solar_gain"] = round(
                    solar_forecast[i].get(self._room_name, 0.0), 1
                )
            if i < len(outdoor_forecast):
                entry["outdoor_temp"] = round(outdoor_forecast[i], 2)
            forecast.append(entry)

        # Expose the MPC constraint offset for dashboard constraint-band
        # visualisation (setpoint ± constraint_offset).
        constraint_offset = 2.0  # default
        if hasattr(self._coordinator, "controller"):
            constraint_offset = self._coordinator.controller.constraint_offset

        attrs: Dict[str, Any] = {
            "trajectory": trajectory,
            "forecast": forecast,
            "setpoint": room.setpoint,
            "constraint_offset": constraint_offset,
            "current_temperature": round(room.temperature, 2),
            "horizon_steps": len(predictions),
            "step_seconds": dt,
            "horizon_minutes": round(len(predictions) * dt / 60, 1),
        }
        return attrs


# ---------------------------------------------------------------------------
# Heat loss sensor (per room)
# ---------------------------------------------------------------------------

class HeatLossSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the instantaneous heat-loss breakdown for a room.

    The state is the total heat loss [W] (positive = losing heat).
    Individual components (external loss, flow to/from each connected room)
    are exposed as state attributes.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:thermometer-minus"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Heat Loss"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heat_loss"

    @property
    def native_value(self) -> float:
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        return round(flows.get("total_loss", 0.0), 1)

    @property
    def extra_state_attributes(self) -> dict:
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        room = self._coordinator.model.rooms[self._room_name]
        attrs: Dict[str, Any] = dict(flows)
        attrs["outdoor_temp"] = self._coordinator.outdoor_temp
        attrs["room_temp"] = round(room.temperature, 2)
        return attrs


# ---------------------------------------------------------------------------
# Energy balance sensor (per room)
# ---------------------------------------------------------------------------

class EnergyBalanceSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the net energy balance for a room [W].

    Positive = room is gaining energy (heating up).
    Negative = room is losing energy (cooling down).

    The attributes give a detailed breakdown of all energy flows.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:scale-balance"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Energy Balance"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_energy_balance"

    @property
    def native_value(self) -> float:
        heating = sum(
            s.current_power
            for s in self._coordinator.heat_sources
            if s.room == self._room_name
        )
        solar = self._coordinator.solar_gains.get(self._room_name, 0.0)
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        total_loss = flows.get("total_loss", 0.0)
        net = heating + solar - total_loss
        return round(net, 1)

    @property
    def extra_state_attributes(self) -> dict:
        sources = [
            s for s in self._coordinator.heat_sources if s.room == self._room_name
        ]
        heating = sum(s.current_power for s in sources)
        solar = self._coordinator.solar_gains.get(self._room_name, 0.0)
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        total_loss = flows.get("total_loss", 0.0)
        external_loss = flows.get("external_loss", 0.0)
        room = self._coordinator.model.rooms[self._room_name]

        inter_room_exchange = total_loss - external_loss

        return {
            "heating_power": round(heating, 1),
            "solar_gain": round(solar, 1),
            "external_heat_loss": round(external_loss, 1),
            "inter_room_heat_exchange": round(inter_room_exchange, 1),
            "total_heat_loss": round(total_loss, 1),
            "net_energy_flow": round(heating + solar - total_loss, 1),
            "room_temperature": round(room.temperature, 2),
            "setpoint": room.setpoint,
        }


# ---------------------------------------------------------------------------
# System efficiency sensor
# ---------------------------------------------------------------------------

class SystemEfficiencySensor(CoordinatorEntity, SensorEntity):
    """
    System-wide sensor reporting aggregate heating metrics.

    The state is the total heating power across all sources [W].
    Attributes include per-room breakdowns, total heat loss, total solar
    gain, and an effective system COP (for heat pump systems).
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:home-thermometer"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – System Summary"
        self._attr_unique_id = f"{DOMAIN}_system_summary"

    @property
    def native_value(self) -> float:
        return round(
            sum(s.current_power for s in self._coordinator.heat_sources), 1
        )

    @property
    def extra_state_attributes(self) -> dict:
        sources = self._coordinator.heat_sources
        total_heating = sum(s.current_power for s in sources)
        total_solar = sum(self._coordinator.solar_gains.values())
        total_loss = sum(
            f.get("total_loss", 0.0)
            for f in self._coordinator.heat_flows.values()
        )

        # Per-room heating power
        room_heating: Dict[str, float] = {}
        for name in self._coordinator.model.room_names:
            room_heating[name] = round(
                sum(s.current_power for s in sources if s.room == name), 1,
            )

        # Effective system COP (thermal output / electrical input)
        electrical_input = 0.0
        for src in sources:
            if isinstance(src, HeatPump):
                cop = src.cop(self._coordinator.outdoor_temp)
                if cop > 0:
                    electrical_input += src.current_power / cop
                # If COP is 0, heat pump is off, no electrical input
            else:
                electrical_input += src.current_power

        effective_cop = (
            round(total_heating / electrical_input, 2)
            if electrical_input > 0
            else 0.0
        )

        # Count active sources
        active_sources = sum(1 for s in sources if s.current_power > 0)

        return {
            "total_heating_power": round(total_heating, 1),
            "total_solar_gain": round(total_solar, 1),
            "total_heat_loss": round(total_loss, 1),
            "net_energy_flow": round(total_heating + total_solar - total_loss, 1),
            "effective_system_cop": effective_cop,
            "electrical_input_estimate": round(electrical_input, 1),
            "active_sources": active_sources,
            "total_sources": len(sources),
            "room_heating_power": room_heating,
            "outdoor_temperature": self._coordinator.outdoor_temp,
        }


# ---------------------------------------------------------------------------
# Heating plan sensor (per room)
# ---------------------------------------------------------------------------

class HeatingPlanSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the planned heating/cooling power over the MPC horizon for a room.

    The state is the current planned heating power [W]. Negative values indicate
    cooling (heat removal) when heat pumps operate in dry/dehumidify mode.
    The full schedule is exposed as a timestamped ``forecast`` attribute so it
    can be plotted in dashboard cards like ``apexcharts-card``.
    """

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:radiator"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Heating Plan"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heating_plan"

    @property
    def native_value(self) -> float:
        schedule = self._coordinator.heating_schedule
        if schedule:
            return round(schedule[0].get(self._room_name, 0.0), 1)
        current_heating = sum(
            getattr(s, "current_power", 0.0)
            for s in self._coordinator.heat_sources
            if s.room == self._room_name
        )
        return round(current_heating, 1)

    @property
    def extra_state_attributes(self) -> dict:
        schedule = self._coordinator.heating_schedule
        dt = self._coordinator.dt
        now = datetime.now(tz=timezone.utc)

        forecast = []
        if schedule:
            # heating_schedule[i] = planned power for [now + i*dt, now + (i+1)*dt]
            # Label at start of interval (now + i*dt); i=0 bridges history to plan
            for i, step in enumerate(schedule):
                step_time = now + timedelta(seconds=dt * i)
                forecast.append({
                    "time": step_time.isoformat(),
                    "heating_power": round(step.get(self._room_name, 0.0), 1),
                })
        else:
            # Fallback: bridge from current actual power when no schedule is available
            current_heating = sum(
                getattr(s, "current_power", 0.0)
                for s in self._coordinator.heat_sources
                if s.room == self._room_name
            )
            forecast.append({
                "time": now.isoformat(),
                "heating_power": round(current_heating, 1),
            })

        return {
            "forecast": forecast,
            "horizon_steps": len(schedule),
            "step_seconds": dt,
        }


# ---------------------------------------------------------------------------
# Solar forecast sensor (per room)
# ---------------------------------------------------------------------------

class SolarForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the predicted solar gain over the MPC horizon for a room.

    The state is the current solar gain [W].  The full forecast is exposed as
    a timestamped ``forecast`` attribute for dashboard visualisation.
    """

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:weather-sunny-alert"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Solar Forecast"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_solar_forecast"

    @property
    def native_value(self) -> float:
        solar_forecast = self._coordinator.solar_forecast
        if solar_forecast:
            return round(solar_forecast[0].get(self._room_name, 0.0), 1)
        return round(self._coordinator.solar_gains.get(self._room_name, 0.0), 1)

    @property
    def extra_state_attributes(self) -> dict:
        solar_forecast = self._coordinator.solar_forecast
        dt = self._coordinator.dt
        now = datetime.now(tz=timezone.utc)

        # solar_forecast has N+1 entries: solar_forecast[k] = solar at now + k*dt
        # for k = 0, …, N.  Entry k=0 is at "now" and acts as the bridge point
        # that connects the forecast trace to the HA recorder history.
        forecast = []
        for i, step in enumerate(solar_forecast):
            step_time = now + timedelta(seconds=dt * i)
            forecast.append({
                "time": step_time.isoformat(),
                "solar_gain": round(step.get(self._room_name, 0.0), 1),
            })

        # Fallback: if solar_forecast is empty (before first compute), provide
        # a single bridge point using the coordinator's current solar_gains.
        if not forecast:
            current_solar = self._coordinator.solar_gains.get(self._room_name, 0.0)
            forecast.append({
                "time": now.isoformat(),
                "solar_gain": round(current_solar, 1),
            })

        room = self._coordinator.model.rooms[self._room_name]
        # horizon_steps is N (the OCP horizon), which is len(solar_forecast) - 1
        # when solar_forecast is populated (N+1 entries), or 0 when empty.
        horizon_steps = max(0, len(solar_forecast) - 1)
        return {
            "forecast": forecast,
            "horizon_steps": horizon_steps,
            "step_seconds": dt,
            "window_count": len(room.windows),
            "total_window_area": round(sum(w.area for w in room.windows), 2),
        }


# ---------------------------------------------------------------------------
# Always-available prediction sensors
# ---------------------------------------------------------------------------
#
# These entities expose the MPC prediction trajectory under stable entity
# IDs that the advanced visualisation dashboards reference.  Two issues
# stopped earlier "forecast" / "plan" sensors from rendering reliably in
# Home Assistant:
#
# 1. Availability gating.  CoordinatorEntity.available returns
#    coordinator.last_update_success, so every prediction entity flipped to
#    "unavailable" whenever the coordinator raised UpdateFailed — even
#    though the cached prediction data on the coordinator was still valid
#    and the controller kept applying set-points from it.
#
# 2. Sensor validation.  Recent Home Assistant releases tightened the
#    "device_class implies state_class" validation.  A SensorEntity that
#    declares device_class=POWER or TEMPERATURE without a state_class is
#    rejected by the validator and the integration is reported as "no
#    longer delivering" the entity.  We cannot set state_class=MEASUREMENT
#    because predictions are not measurements: doing so would feed
#    forward-looking values into Home Assistant's long-term statistics.
#
# The classes below address both issues:
#   • ``available`` is overridden to return True so dashboards keep
#     rendering across transient coordinator update failures;
#   • ``device_class`` and ``state_class`` are set to None so the
#     validator treats them as plain unit-bearing sensors and accepts the
#     definition at registration time.  The native unit of measurement
#     and icon are preserved so the entities still display nicely in the
#     UI, and the prediction trajectory continues to live on the
#     ``forecast`` state attribute that the dashboards consume.


class TemperaturePredictionSensor(TemperatureForecastSensor):
    """
    Stable-availability variant of :class:`TemperatureForecastSensor`.

    Exposes the same MPC predicted-temperature trajectory under a stable
    entity ID, with sensor metadata that passes Home Assistant's strict
    sensor validator (no ``device_class`` / ``state_class``).
    """

    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator, room_name)
        self._attr_name = (
            f"Heating Assistant – {room_name} – Temperature Prediction"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_temperature_prediction"

    @property
    def available(self) -> bool:
        return True


class HeatingPlanPredictionSensor(HeatingPlanSensor):
    """
    Stable-availability variant of :class:`HeatingPlanSensor`.

    Exposes the same MPC heating-power schedule under a stable entity ID,
    with sensor metadata that passes Home Assistant's strict sensor
    validator (no ``device_class`` / ``state_class``).
    """

    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator, room_name)
        self._attr_name = (
            f"Heating Assistant – {room_name} – Heating Plan Prediction"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heating_plan_prediction"

    @property
    def available(self) -> bool:
        return True


class SolarPowerPredictionSensor(SolarForecastSensor):
    """
    Stable-availability variant of :class:`SolarForecastSensor`.

    Exposes the same predicted solar-gain trajectory under a stable
    entity ID, with sensor metadata that passes Home Assistant's strict
    sensor validator (no ``device_class`` / ``state_class``).
    """

    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator, room_name)
        self._attr_name = (
            f"Heating Assistant – {room_name} – Solar Power Prediction"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_solar_power_prediction"

    @property
    def available(self) -> bool:
        return True


class OutdoorTemperaturePredictionSensor(OutdoorForecastSensor):
    """
    Stable-availability variant of :class:`OutdoorForecastSensor`.

    Exposes the same outdoor-temperature forecast under a stable entity
    ID, with sensor metadata that passes Home Assistant's strict sensor
    validator (no ``device_class`` / ``state_class``).
    """

    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = (
            "Heating Assistant – Outdoor Temperature Prediction"
        )
        self._attr_unique_id = f"{DOMAIN}_outdoor_temperature_prediction"

    @property
    def available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Prediction error sensor (per room) - for model fit visualization
# ---------------------------------------------------------------------------

class PredictionErrorSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the current prediction error (residual) for a room.

    The state is the most recent prediction error [°C]:
        error = predicted_temp - measured_temp

    Positive error = model over-predicts (predicts warmer than actual)
    Negative error = model under-predicts (predicts colder than actual)

    Historical errors over the MPC horizon are exposed as attributes for
    visualization of model fit quality.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Prediction Error"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_prediction_error"

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent aligned prediction error [°C].

        Uses the ``y_pred`` field which is the prediction made at the previous
        cycle *for* the current cycle — so y_pred and y refer to the same
        timestep and their difference is a genuine forecast error.
        """
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        # Walk backwards to find the latest record with a valid aligned y_pred
        for record in reversed(list(self._coordinator.history_buffer)):
            y_pred = record.get("y_pred")
            y = record.get("y", [])
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                return round(float(y_pred[room_idx]) - float(y[room_idx]), 3)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose historical prediction errors and fit metrics."""
        errors = []
        room_idx = self._coordinator.model.room_names.index(self._room_name)

        for record in self._coordinator.history_buffer[-50:]:  # Last 50 samples
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # aligned: prediction made at k-1 for k

            # Skip records without an aligned prediction (first record after start)
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                error = y_pred[room_idx] - y[room_idx]
                errors.append(round(error, 3))

        # Compute basic statistics
        if errors:
            import numpy as np
            errors_arr = np.array(errors)
            rmse = float(np.sqrt(np.mean(errors_arr ** 2)))
            mae = float(np.mean(np.abs(errors_arr)))
            bias = float(np.mean(errors_arr))
            max_error = float(np.max(np.abs(errors_arr)))
        else:
            rmse = mae = bias = max_error = 0.0

        return {
            "recent_errors": errors,
            "rmse": round(rmse, 3),
            "mae": round(mae, 3),
            "bias": round(bias, 3),
            "max_error": round(max_error, 3),
            "n_samples": len(errors),
        }


# ---------------------------------------------------------------------------
# Model fit quality sensor (per room)
# ---------------------------------------------------------------------------

class ModelFitQualitySensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting overall model fit quality for a room.

    The state is the R² (coefficient of determination) score [0-1]:
        1.0 = perfect fit
        0.0 = no better than mean prediction
        < 0 = worse than mean prediction

    Additional fit metrics (RMSE, MAE, autocorrelation) are exposed as
    attributes for detailed diagnostics.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:poll"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Model Fit Quality"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_model_fit_quality"

    @property
    def native_value(self) -> Optional[float]:
        """Return R² score as the main quality metric."""
        from .model_diagnostics import compute_model_fit_metrics

        # Extract predictions and measurements from history
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        predictions = []
        measurements = []

        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            # Skip records where no aligned prediction was stored yet
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                predictions.append(y_pred[room_idx])
                measurements.append(y[room_idx])

        if len(predictions) < 2:
            return None

        try:
            metrics = compute_model_fit_metrics(predictions, measurements, self._room_name)
            return round(metrics.r_squared, 4)
        except Exception as exc:
            _LOGGER.warning("Failed to compute model fit quality for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose detailed fit metrics."""
        from .model_diagnostics import compute_model_fit_metrics

        # Extract predictions and measurements from history
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        predictions = []
        measurements = []

        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            # Skip records where no aligned prediction was stored yet
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                predictions.append(y_pred[room_idx])
                measurements.append(y[room_idx])

        if len(predictions) < 2:
            return {
                "error": "Insufficient data",
                "n_samples": len(predictions),
            }

        try:
            metrics = compute_model_fit_metrics(predictions, measurements, self._room_name)
            return {
                "r_squared": round(metrics.r_squared, 4),
                "rmse": round(metrics.rmse, 3),
                "mae": round(metrics.mae, 3),
                "bias": round(metrics.bias, 3),
                "max_error": round(metrics.max_error, 2),
                "residual_std": round(metrics.residual_std, 3),
                "residual_autocorr_lag1": (
                    round(metrics.residual_autocorr_lag1, 3)
                    if metrics.residual_autocorr_lag1 is not None
                    else None
                ),
                "n_samples": metrics.n_samples,
            }
        except Exception as exc:
            _LOGGER.warning("Failed to compute fit metrics for %s: %s", self._room_name, exc)
            return {
                "error": str(exc),
                "n_samples": len(predictions),
            }


# ---------------------------------------------------------------------------
# Parameter confidence sensor (per room)
# ---------------------------------------------------------------------------

class ParameterConfidenceSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting confidence/validity of thermal parameters for a room.

    The state is a confidence score [0-100]:
        100 = all parameters in valid range
        0 = parameters outside valid range or no data

    Detailed parameter validation warnings are exposed as attributes.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:shield-check"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Parameter Confidence"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_parameter_confidence"

    @property
    def native_value(self) -> Optional[float]:
        """Return confidence score [0-100]."""
        from .model_diagnostics import validate_parameters

        room = self._coordinator.model.rooms[self._room_name]

        try:
            validation = validate_parameters(
                self._room_name,
                room.thermal_mass,
                room.r_external,
            )

            # Compute confidence score
            score = 0.0
            if validation.mass_valid:
                score += 33.3
            if validation.r_external_valid:
                score += 33.3
            if validation.time_constant_valid:
                score += 33.4

            return round(score, 1)
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose detailed parameter validation."""
        from .model_diagnostics import validate_parameters

        room = self._coordinator.model.rooms[self._room_name]

        try:
            validation = validate_parameters(
                self._room_name,
                room.thermal_mass,
                room.r_external,
            )

            return {
                "thermal_mass": validation.thermal_mass,
                "r_external": validation.r_external,
                "time_constant_hours": round(validation.time_constant_hours, 2),
                "mass_valid": validation.mass_valid,
                "r_external_valid": validation.r_external_valid,
                "time_constant_valid": validation.time_constant_valid,
                "warnings": validation.warnings,
            }
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", self._room_name, exc)
            return {
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# Open-loop RMSE sensor (per room) – direct model quality indicator
# ---------------------------------------------------------------------------

class OpenLoopRMSESensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the open-loop prediction RMSE for a room.

    Runs multi-step simulations (default 30 steps = 30 minutes) over the
    history buffer without Kalman state correction.  The RMSE of these
    open-loop predictions shows how much the thermal model drifts from
    reality, which is the root cause of MPC overshoot.

    Rule of thumb:
        < 0.2 °C: excellent – MPC predictions are reliable
        0.2–0.5 °C: acceptable
        > 0.5 °C: likely contributing to overshoot; re-run parameter estimation
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:chart-timeline-variant"

    SEGMENT_LENGTH = 30  # steps

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Open Loop RMSE"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_open_loop_rmse"

    def _compute(self) -> dict:
        from .model_diagnostics import compute_open_loop_predictions

        history = list(self._coordinator.history_buffer)
        system = self._coordinator.controller._system  # HouseThermalSDE
        room_names = self._coordinator.model.room_names
        n_rooms = len(room_names)

        return compute_open_loop_predictions(
            history=history,
            system=system,
            room_names=room_names,
            n_rooms=n_rooms,
            dt=float(self._coordinator.update_interval_seconds),
            segment_length=self.SEGMENT_LENGTH,
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return open-loop RMSE [°C] for this room."""
        try:
            result = self._compute()
            per_room = result.get("per_room", {})
            room_data = per_room.get(self._room_name, {})
            return room_data.get("rmse")
        except Exception as exc:
            _LOGGER.debug("OpenLoopRMSESensor compute error for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose open-loop simulation data for Apex Charts."""
        try:
            result = self._compute()
            per_room = result.get("per_room", {})
            room_data = per_room.get(self._room_name, {})
            sim = room_data.get("simulation", [])
            # Convert timestamps to ISO strings for Apex Charts
            from datetime import datetime, timezone
            formatted_sim = []
            for entry in sim:
                ts = entry.get("time", 0.0)
                dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                formatted_sim.append({
                    "time": dt_iso,
                    "measured": entry.get("measured"),
                    "predicted": entry.get("predicted"),
                })
            return {
                "open_loop_rmse": room_data.get("rmse"),
                "open_loop_mae": room_data.get("mae"),
                "simulation": formatted_sim,
                "segment_length_steps": result.get("segment_length"),
                "n_segments": result.get("n_segments"),
                "error": result.get("error"),
            }
        except Exception as exc:
            _LOGGER.debug("OpenLoopRMSESensor attributes error for %s: %s", self._room_name, exc)
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Kalman innovation sensor (per room)
# ---------------------------------------------------------------------------

class KalmanInnovationSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the most recent Kalman filter innovation ν = y − C x̂⁻.

    A well-tuned model/filter should have innovations that are:
        - Zero-mean (no systematic bias)
        - White noise (no autocorrelation)
        - Consistent with the innovation covariance

    Persistent non-zero mean or high autocorrelation indicates that the
    thermal model is missing dynamics (e.g. inter-room coupling, solar,
    or heat source dynamics).
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:waveform"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Kalman Innovation"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_kalman_innovation"

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent Kalman innovation [°C] for this room."""
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        for record in reversed(list(self._coordinator.history_buffer)):
            innov = record.get("kalman_innovation")
            if innov is not None and room_idx < len(innov):
                return round(float(innov[room_idx]), 4)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose innovation time series and statistics."""
        from datetime import datetime, timezone
        import numpy as np

        room_idx = self._coordinator.model.room_names.index(self._room_name)
        innovations = []

        for record in self._coordinator.history_buffer:
            innov = record.get("kalman_innovation")
            ts = record.get("timestamp", 0.0)
            if innov is not None and room_idx < len(innov):
                dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                innovations.append({
                    "time": dt_iso,
                    "value": round(float(innov[room_idx]), 4),
                })

        if not innovations:
            return {"innovations": [], "n_samples": 0}

        vals = np.array([e["value"] for e in innovations])
        mean_v = float(np.mean(vals))
        std_v = float(np.std(vals))

        # Lag-1 autocorrelation
        autocorr_lag1: Optional[float] = None
        if len(vals) >= 4:
            n_v = len(vals)
            c0 = float(np.dot(vals - mean_v, vals - mean_v))
            if c0 > 0:
                c1 = float(np.dot((vals - mean_v)[1:], (vals - mean_v)[:-1]))
                autocorr_lag1 = round(c1 / c0, 4)

        # Consistency: |mean| < 2 * std/sqrt(n) (approximate 95% test)
        n_samples = len(vals)
        is_consistent = abs(mean_v) < 2.0 * (std_v / max(1.0, float(np.sqrt(n_samples))))

        return {
            "innovations": innovations[-100:],  # keep last 100 for attribute size
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "autocorr_lag1": autocorr_lag1,
            "is_consistent": is_consistent,
            "n_samples": n_samples,
        }


# ---------------------------------------------------------------------------
# Residual ACF sensor (per room)
# ---------------------------------------------------------------------------

class ResidualACFSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the lag-1 autocorrelation of 1-step prediction residuals.

    A lag-1 autocorrelation close to 0 indicates that the residuals are
    white noise (good model).  High autocorrelation (> 0.3) means the model
    is systematically missing dynamics — a signal to re-run parameter
    estimation or check the model configuration.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bar"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Residual ACF"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_residual_acf"

    @property
    def native_value(self) -> Optional[float]:
        """Return lag-1 residual autocorrelation."""
        try:
            acf_result = self._compute_acf()
            acf = acf_result.get("acf", [])
            return round(acf[1], 4) if len(acf) > 1 else None
        except Exception:
            return None

    def _compute_acf(self) -> dict:
        from .model_diagnostics import compute_autocorrelation_function

        room_idx = self._coordinator.model.room_names.index(self._room_name)
        residuals = []
        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                residuals.append(float(y_pred[room_idx]) - float(y[room_idx]))
        return compute_autocorrelation_function(residuals)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose full ACF, confidence bounds, and Ljung-Box statistic."""
        try:
            return self._compute_acf()
        except Exception as exc:
            _LOGGER.debug("ResidualACFSensor error for %s: %s", self._room_name, exc)
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# MPC performance sensor (system-wide)
# ---------------------------------------------------------------------------

class MPCPerformanceSensor(CoordinatorEntity, SensorEntity):
    """
    System-wide sensor reporting MPC solver performance statistics.

    The state is the total number of completed OCP solves.  It is a
    monotonically-increasing integer so the entity state always advances on
    every coordinator cycle, making it easy to spot if the MPC has stopped
    running.  Detailed statistics (solve times, tracking errors) are exposed
    as state attributes.

    Previously the state was the most-recent solve time [s], but after the
    initial warm-up the L-BFGS-B solver converges in near-constant time, so
    that value appeared frozen even when the controller was running normally.
    """

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = ""
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – MPC Performance"
        self._attr_unique_id = f"{DOMAIN}_mpc_performance"

    @property
    def native_value(self) -> Optional[int]:
        """Return the total number of completed OCP solves."""
        return self._coordinator.controller.total_computes

    @property
    def extra_state_attributes(self) -> dict:
        """Expose rolling solve-time statistics and recent history."""
        import numpy as np

        controller = self._coordinator.controller
        solve_times = list(controller._solve_times)

        last_t = controller.last_solve_time
        mean_t = controller.mean_solve_time
        max_t = controller.max_solve_time
        n = controller.n_solves

        attrs: Dict[str, Any] = {
            "total_computes": controller.total_computes,
            "last_solve_time_s": round(last_t, 4) if last_t is not None else None,
            "mean_solve_time_s": round(mean_t, 4) if mean_t is not None else None,
            "max_solve_time_s": round(max_t, 4) if max_t is not None else None,
            "n_solves": n,
            "horizon": self._coordinator.controller._horizon,
            "dt_s": self._coordinator.dt,
        }

        # Tracking error per room (absolute deviation from setpoint)
        room_names = self._coordinator.model.room_names
        tracking_error_values = [
            abs(
                self._coordinator.model.rooms[name].temperature
                - self._coordinator.model.rooms[name].setpoint
            )
            for name in room_names
        ]
        attrs["current_tracking_errors"] = {
            name: round(v, 3) for name, v in zip(room_names, tracking_error_values)
        }
        attrs["mean_tracking_error"] = (
            round(float(np.mean(tracking_error_values)), 3)
            if tracking_error_values else None
        )
        attrs["max_tracking_error"] = (
            round(float(np.max(tracking_error_values)), 3)
            if tracking_error_values else None
        )

        # Terminal-weight in effect (for reference)
        attrs["terminal_weight"] = (
            controller.terminal_weight
            if hasattr(controller, "terminal_weight")
            else None
        )

        # Rolling solve time history (last 50 samples) for sparkline charts
        attrs["recent_solve_times_s"] = [round(t, 4) for t in solve_times[-50:]]

        return attrs
