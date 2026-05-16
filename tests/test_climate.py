"""Unit tests for the RoomClimateEntity HVAC action / mode reporting.

The integration's climate entity must surface ``COOLING`` (not ``IDLE``)
whenever a heat pump in the room is actively removing heat — either
because it is in dry/fan-only mode (current_power < 0) or because the
coordinator's ``_cooling_active`` flag is set for any source in the
room.  Heat-pump-equipped rooms are also expected to advertise
``HEAT_COOL`` as a supported HVAC mode so the HA frontend renders the
cooling state correctly.
"""

import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.climate import RoomClimateEntity
from custom_components.heating_assistant.heat_sources import (
    ElectricHeater,
    HeatPump,
)

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE


class _FakeRoom:
    def __init__(self, temperature: float = 21.0):
        self.temperature = temperature


class _FakeModel:
    def __init__(self, names):
        self.room_names = list(names)
        self.rooms = {n: _FakeRoom() for n in names}


class _FakeCoordinator:
    """Minimal coordinator stand-in matching the climate entity's
    public surface."""

    def __init__(self, room_names, heat_sources, enabled=True):
        self.model = _FakeModel(room_names)
        self.heat_sources = heat_sources
        self._enabled = {n: enabled for n in room_names}
        self._setpoints = {n: 21.0 for n in room_names}
        self._cooling_active = {}

    # ── methods that climate.py calls ────────────────────────────────
    def is_room_enabled(self, name: str) -> bool:
        return self._enabled[name]

    def set_room_enabled(self, name: str, value: bool) -> None:
        self._enabled[name] = value

    def get_room_setpoint(self, name: str) -> float:
        return self._setpoints[name]

    def set_room_setpoint(self, name: str, value: float) -> None:
        self._setpoints[name] = value


def _make_entity(heat_sources, enabled=True, room="living_room"):
    coord = _FakeCoordinator([room], heat_sources, enabled=enabled)
    # Bypass CoordinatorEntity.__init__ which expects a real coordinator
    ent = object.__new__(RoomClimateEntity)
    ent._room_name = room
    ent._coordinator = coord
    ent._attr_name = f"Heating Assistant – {room}"
    ent._attr_unique_id = f"heating_assistant_{room}_climate"
    if any(isinstance(s, HeatPump) and s.room == room for s in heat_sources):
        ent._attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.OFF]
    else:
        ent._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    return ent, coord


class TestHvacActionHeating:
    def test_heating_when_power_positive(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        heater._current_power = 1500.0
        ent, _ = _make_entity([heater])
        assert ent.hvac_action == HVACAction.HEATING

    def test_idle_when_power_zero(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        heater._current_power = 0.0
        ent, _ = _make_entity([heater])
        assert ent.hvac_action == HVACAction.IDLE

    def test_off_when_disabled(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        heater._current_power = 1500.0
        ent, _ = _make_entity([heater], enabled=False)
        assert ent.hvac_action == HVACAction.OFF


class TestHvacActionCooling:
    def test_cooling_when_heat_pump_negative_power(self):
        """A heat pump producing negative thermal power = cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        hp._current_power = -1500.0  # heat removal
        ent, _ = _make_entity([hp])
        assert ent.hvac_action == HVACAction.COOLING

    def test_cooling_when_coordinator_flag_set(self):
        """Even before set_power runs, the cooling flag forces COOLING."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        hp._current_power = 0.0
        ent, coord = _make_entity([hp])
        coord._cooling_active = {"hp1": True}
        assert ent.hvac_action == HVACAction.COOLING

    def test_idle_when_cooling_inactive_and_power_zero(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        hp._current_power = 0.0
        ent, coord = _make_entity([hp])
        coord._cooling_active = {"hp1": False}
        assert ent.hvac_action == HVACAction.IDLE


class TestHvacModes:
    def test_heat_only_room_advertises_heat_and_off(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        ent, _ = _make_entity([heater])
        assert HVACMode.HEAT in ent._attr_hvac_modes
        assert HVACMode.HEAT_COOL not in ent._attr_hvac_modes
        assert ent.hvac_mode == HVACMode.HEAT

    def test_heat_pump_room_advertises_heat_cool(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        ent, _ = _make_entity([hp])
        assert HVACMode.HEAT_COOL in ent._attr_hvac_modes
        assert ent.hvac_mode == HVACMode.HEAT_COOL

    def test_off_when_disabled(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        ent, _ = _make_entity([hp], enabled=False)
        assert ent.hvac_mode == HVACMode.OFF


@pytest.mark.asyncio
async def test_set_temperature_does_not_force_immediate_refresh():
    heater = ElectricHeater("h1", "living_room", max_power=2000.0)
    ent, coord = _make_entity([heater])
    coord.async_request_refresh = AsyncMock()
    ent.async_write_ha_state = MagicMock()

    await ent.async_set_temperature(**{ATTR_TEMPERATURE: 23.5})

    assert coord.get_room_setpoint("living_room") == 23.5
    coord.async_request_refresh.assert_not_awaited()
    ent.async_write_ha_state.assert_called_once()
