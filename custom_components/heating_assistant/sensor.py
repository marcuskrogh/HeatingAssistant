"""
Heating Assistant – Sensor platform.

For each room the following sensor entities are created:
- Predicted temperature  (model's 1-step-ahead prediction) [°C]
- Heating power          (sum of active heater outputs for the room) [W]
- Solar gain             (current solar heat gain through windows) [W]
- Temperature forecast   (MPC prediction trajectory) [°C]
- Heat loss              (instantaneous heat loss breakdown) [W]
- Energy balance         (net energy flow in the room) [W]
- Heating plan           (planned heating power over MPC horizon) [W]
- Solar forecast         (predicted solar gain over MPC horizon) [W]

For each heat source:
- Control action         (MPC controller output fraction) [%]

For each heat pump source:
- COP                    (current coefficient of performance)

System-wide:
- Outdoor temperature    (as read by the integration) [°C]
- System efficiency      (aggregate system metrics)
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

    # Per-source sensors
    for src in coordinator.heat_sources:
        entities.append(ControlActionSensor(coordinator, src.name))
        if isinstance(src, HeatPump):
            entities.append(HeatPumpCOPSensor(coordinator, src.name))

    # System-wide sensors
    entities.append(OutdoorTemperatureSensor(coordinator))
    entities.append(SystemEfficiencySensor(coordinator))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Predicted temperature sensor
# ---------------------------------------------------------------------------

class PredictedTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the model-predicted temperature for a room."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
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
    """Sensor reporting the total active heating power for a room."""

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

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
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
            return None
        last = predictions[-1]
        temp = last.get(self._room_name)
        return round(temp, 2) if temp is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        predictions = self._coordinator.predictions
        room = self._coordinator.model.rooms[self._room_name]
        dt = self._coordinator._dt

        trajectory = []
        for i, pred in enumerate(predictions):
            temp = pred.get(self._room_name)
            if temp is not None:
                trajectory.append(round(temp, 2))

        # Build timestamped forecast entries for dashboard visualisation.
        # Each entry combines temperature, heating power, solar gain, and
        # outdoor temperature so cards (e.g. apexcharts-card) can plot
        # them all from a single attribute.
        now = datetime.now(tz=timezone.utc)
        forecast = []
        outdoor_forecast = self._coordinator.outdoor_forecast
        solar_forecast = self._coordinator.solar_forecast
        heating_schedule = self._coordinator.heating_schedule
        for i, pred in enumerate(predictions):
            temp = pred.get(self._room_name)
            if temp is None:
                continue
            step_time = now + timedelta(seconds=dt * (i + 1))
            entry: Dict[str, Any] = {
                "time": step_time.isoformat(),
                "temperature": round(temp, 2),
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

        attrs: Dict[str, Any] = {
            "trajectory": trajectory,
            "forecast": forecast,
            "setpoint": room.setpoint,
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
    Sensor reporting the planned heating power over the MPC horizon for a room.

    The state is the current planned heating power [W].  The full schedule
    is exposed as a timestamped ``forecast`` attribute so it can be plotted
    in dashboard cards like ``apexcharts-card``.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
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
        return 0.0

    @property
    def extra_state_attributes(self) -> dict:
        schedule = self._coordinator.heating_schedule
        dt = self._coordinator._dt
        now = datetime.now(tz=timezone.utc)

        forecast = []
        for i, step in enumerate(schedule):
            step_time = now + timedelta(seconds=dt * (i + 1))
            forecast.append({
                "time": step_time.isoformat(),
                "heating_power": round(step.get(self._room_name, 0.0), 1),
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

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
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
        return 0.0

    @property
    def extra_state_attributes(self) -> dict:
        solar_forecast = self._coordinator.solar_forecast
        dt = self._coordinator._dt
        now = datetime.now(tz=timezone.utc)

        forecast = []
        for i, step in enumerate(solar_forecast):
            step_time = now + timedelta(seconds=dt * (i + 1))
            forecast.append({
                "time": step_time.isoformat(),
                "solar_gain": round(step.get(self._room_name, 0.0), 1),
            })

        room = self._coordinator.model.rooms[self._room_name]
        return {
            "forecast": forecast,
            "horizon_steps": len(solar_forecast),
            "step_seconds": dt,
            "window_count": len(room.windows),
            "total_window_area": round(sum(w.area for w in room.windows), 2),
        }
