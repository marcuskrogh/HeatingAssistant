"""
Heating Assistant – Climate platform.

One ``climate`` entity is created for each configured room.  The entity
exposes:
- current temperature  (from the measured or model temperature)
- target temperature   (setpoint)
- HVAC mode            (``heat_cool`` for rooms served by a heat pump that
                        can cool, ``heat`` for heat-only rooms, ``off``
                        when the user has turned the room off)
- HVAC action          (``heating`` when heaters are actively producing
                        heat, ``cooling`` when a heat pump is actively
                        removing heat, ``idle`` otherwise)
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
from .heat_sources import HeatPump

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

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
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
        # Expose HEAT_COOL for rooms that have at least one heat pump
        # (heat pumps can cool via dry / fan-only); other rooms remain
        # heat-only.
        if any(
            isinstance(s, HeatPump) and s.room == room_name
            for s in coordinator.heat_sources
        ):
            self._attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.OFF]
        else:
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

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
        """Return the current HVAC mode based on the user-controlled room state.

        ``HEAT_COOL`` is reported for rooms with at least one heat-pump
        source — those rooms are auto-cooled when above setpoint and
        auto-heated when below.  Heat-only rooms report ``HEAT``.
        """
        if not self._coordinator.is_room_enabled(self._room_name):
            return HVACMode.OFF
        if HVACMode.HEAT_COOL in self._attr_hvac_modes:
            return HVACMode.HEAT_COOL
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current action based on the active heater / cooler power.

        - ``OFF`` when the room is disabled.
        - ``HEATING`` when at least one source is producing positive
          thermal power.
        - ``COOLING`` when at least one source is producing negative
          thermal power (heat removal in dry / fan-only / cool mode) or
          the coordinator's cooling-active flag is set for any source in
          the room.
        - ``IDLE`` otherwise.
        """
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        sources = self._coordinator.heat_sources
        room_sources = [s for s in sources if s.room == self._room_name]
        if any(s.current_power > 0 for s in room_sources):
            return HVACAction.HEATING
        cooling_flags = getattr(self._coordinator, "_cooling_active", {})
        if any(s.current_power < 0 for s in room_sources) or any(
            cooling_flags.get(s.name, False) for s in room_sources
        ):
            return HVACAction.COOLING
        return HVACAction.IDLE

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            self._coordinator.set_room_setpoint(self._room_name, float(temp))
            # Do not force an out-of-band control update; let the new setpoint
            # be consumed on the next regular coordinator interval.
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """
        Toggle the room on or off.

        Switching to ``off`` disables heating control without touching the
        configured setpoint, so the user's chosen value is preserved across
        the toggle.  Switching to ``heat`` or ``heat_cool`` re-enables the
        room and only restores DEFAULT_SETPOINT when the stored value is
        below the frost-protection floor (legacy behaviour from earlier
        versions that overwrote the setpoint on off).
        """
        if hvac_mode == HVACMode.OFF:
            self._coordinator.set_room_enabled(self._room_name, False)
        elif hvac_mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
            self._coordinator.set_room_enabled(self._room_name, True)
            if self._coordinator.get_room_setpoint(self._room_name) <= MIN_TEMP:
                self._coordinator.set_room_setpoint(self._room_name, DEFAULT_SETPOINT)
        await self._coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Re-enable the room (required by ClimateEntityFeature.TURN_ON)."""
        target = (
            HVACMode.HEAT_COOL
            if HVACMode.HEAT_COOL in self._attr_hvac_modes
            else HVACMode.HEAT
        )
        await self.async_set_hvac_mode(target)

    async def async_turn_off(self) -> None:
        """Disable the room (required by ClimateEntityFeature.TURN_OFF)."""
        await self.async_set_hvac_mode(HVACMode.OFF)
