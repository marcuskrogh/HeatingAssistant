"""
Heating Assistant – Climate platform.

One ``climate`` entity is created for each configured room.  The entity
exposes:
- current temperature  (from the measured or model temperature)
- target temperature   (setpoint)
- HVAC mode            (``heat`` when any heater in the room is active,
                        ``off`` otherwise)
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEFAULT_SETPOINT
from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

# Min / max allowed setpoints
MIN_TEMP = 5.0
MAX_TEMP = 30.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant climate entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        RoomClimateEntity(coordinator, room_name)
        for room_name in coordinator.model.room_names
    ]
    async_add_entities(entities)


class RoomClimateEntity(CoordinatorEntity, ClimateEntity):
    """Climate entity representing a single room."""

    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name}"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_climate"

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def current_temperature(self) -> Optional[float]:
        return self._coordinator.model.rooms[self._room_name].temperature

    @property
    def target_temperature(self) -> float:
        return self._coordinator.get_room_setpoint(self._room_name)

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode based on active heater power."""
        sources = self._coordinator.heat_sources
        room_sources = [s for s in sources if s.room == self._room_name]
        if any(s.current_power > 0 for s in room_sources):
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if self.hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        return HVACAction.IDLE

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            self._coordinator.set_room_setpoint(self._room_name, float(temp))
            await self._coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """
        Switching to ``off`` sets the room setpoint to MIN_TEMP (frost protection).
        Switching to ``heat`` restores DEFAULT_SETPOINT if the current setpoint
        is at the frost-protection level.
        """
        if hvac_mode == HVACMode.OFF:
            self._coordinator.set_room_setpoint(self._room_name, MIN_TEMP)
        elif hvac_mode == HVACMode.HEAT:
            if self._coordinator.get_room_setpoint(self._room_name) <= MIN_TEMP:
                self._coordinator.set_room_setpoint(self._room_name, DEFAULT_SETPOINT)
        await self._coordinator.async_request_refresh()
