"""
Heating Assistant – Sensor platform.

For each room the following sensor entities are created:
- Predicted temperature  (model's 1-step-ahead prediction) [°C]
- Heating power          (sum of active heater outputs for the room) [W]
"""

from __future__ import annotations

import logging
from typing import List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant sensor entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: List[SensorEntity] = []
    for room_name in coordinator.model.room_names:
        entities.append(PredictedTemperatureSensor(coordinator, room_name))
        entities.append(HeatingPowerSensor(coordinator, room_name))

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
