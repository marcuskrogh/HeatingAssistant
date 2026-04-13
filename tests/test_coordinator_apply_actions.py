"""Unit tests for HeatingAssistantCoordinator._apply_actions."""

import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.heat_sources import (
    ElectricHeater,
    HeatPump,
)


# ---------------------------------------------------------------------------
# Helpers – lightweight stand-ins for the coordinator
# ---------------------------------------------------------------------------


def _make_fake_hass(entity_states: dict):
    """
    Build a mock *hass* object that knows about the given entity states
    and records every ``services.async_call``.

    ``entity_states`` values may be:
      - a plain string (the entity's ``state``), or
      - a dict ``{"state": ..., "attributes": {...}}`` to also set
        attributes like ``current_temperature``.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    def _get_state(entity_id):
        if entity_id in entity_states:
            raw = entity_states[entity_id]
            s = MagicMock()
            if isinstance(raw, dict):
                s.state = raw.get("state", "unknown")
                s.attributes = raw.get("attributes", {})
            else:
                s.state = raw
                s.attributes = {}
            return s
        return None

    hass.states.get = _get_state
    return hass


async def _run_apply_actions(heat_sources, actions, entity_states, room_setpoints,
                            room_temperatures=None):
    """
    Directly exercise the ``_apply_actions`` logic without spinning up
    a full coordinator.  We import the module and patch just enough to
    call the method.
    """
    from custom_components.heating_assistant.coordinator import (
        HeatingAssistantCoordinator,
    )

    hass = _make_fake_hass(entity_states)

    # Build a bare-minimum coordinator-like object (skip __init__)
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = hass
    coord.heat_sources = heat_sources
    coord.actions = actions

    # Provide the room-setpoint lookup used by the new code path
    model_rooms = {}
    for name, sp in room_setpoints.items():
        room = MagicMock()
        room.setpoint = sp
        room.temperature = (room_temperatures or {}).get(name, 20.0)
        model_rooms[name] = room
    model = MagicMock()
    model.rooms = model_rooms
    coord.model = model

    # All rooms enabled by default (matches coordinator __init__)
    coord._room_enabled = {name: True for name in room_setpoints}

    await coord._apply_actions(outdoor_temp=5.0)
    return hass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyActionsClimate:
    """climate.* entity handling with offset-based heat pump control."""

    @pytest.mark.asyncio
    async def test_heat_pump_uses_internal_temp_plus_offset(self):
        """When fraction > 0 the heat pump setpoint = T_hp_internal + fraction × max_offset."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            max_temp_offset=5.0,
            heater_entity="climate.heat_pump",
        )
        # Heat pump's own sensor reads 23 °C  (distinct from HA room sensor)
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.6},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {"current_temperature": 23.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2

        # First call: set_hvac_mode → heat
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "heat"

        # Second call: set_temperature = 23 + 0.6 × 5 = 26.0
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(26.0)

    @pytest.mark.asyncio
    async def test_heat_pump_full_power_offset(self):
        """At fraction=1.0 the full max_temp_offset is applied."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            max_temp_offset=4.0,
            heater_entity="climate.heat_pump",
        )
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 1.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {"current_temperature": 21.0},
                },
            },
            room_setpoints={"living_room": 25.0},
        )

        temp_call = hass.services.async_call.call_args_list[1]
        # 21 + 1.0 × 4 = 25.0
        assert temp_call.args[2]["temperature"] == pytest.approx(25.0)

    @pytest.mark.asyncio
    async def test_heat_pump_fallback_to_room_temp(self):
        """When the heat pump entity has no current_temperature attribute,
        fall back to the HA room temperature + offset."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            max_temp_offset=5.0,
            heater_entity="climate.heat_pump",
        )
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.5},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {},  # no current_temperature
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 22.0},
        )

        temp_call = hass.services.async_call.call_args_list[1]
        # fallback: 22 + 0.5 × 5 = 24.5
        assert temp_call.args[2]["temperature"] == pytest.approx(24.5)

    @pytest.mark.asyncio
    async def test_heat_pump_idles_within_deadband(self):
        """When fraction == 0 and room temp is within deadband of setpoint,
        the HP stays on with target = internal temp (idle, no offset)."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 23.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 24.5},
        )

        calls = hass.services.async_call.call_args_list
        # Should stay in heat mode and set temperature to internal temp
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "heat"
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(23.0)

    @pytest.mark.asyncio
    async def test_heat_pump_turns_off_above_deadband(self):
        """When fraction == 0 and room temp > setpoint + deadband,
        the HP actually turns off."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 27.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 1
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "off"

    @pytest.mark.asyncio
    async def test_heat_pump_idle_fallback_to_room_temp(self):
        """When idling and internal temp is unavailable, fall back to room temp."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {},  # no current_temperature
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # Falls back to room temperature (22.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_heat_pump_custom_deadband(self):
        """Custom turn_off_deadband is respected."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=2.0,
                       heater_entity="climate.heat_pump")
        # Room at 26.5 with setpoint 25.0 → 26.5 < 25.0 + 2.0 → idles (doesn't turn off)
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 26.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        assert calls[1].args[2]["temperature"] == pytest.approx(26.0)

    @pytest.mark.asyncio
    async def test_non_heat_pump_climate_uses_room_setpoint(self):
        """An ElectricHeater on a climate entity falls back to the room setpoint."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.7},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "off",
                    "attributes": {"current_temperature": 20.0},
                },
            },
            room_setpoints={"bedroom": 22.5},
        )

        temp_call = hass.services.async_call.call_args_list[1]
        assert temp_call.args[2]["temperature"] == 22.5


class TestApplyActionsSwitch:
    """switch.* entity handling."""

    @pytest.mark.asyncio
    async def test_switch_on(self):
        heater = ElectricHeater("e1", "kitchen", max_power=2000, heater_entity="switch.heater")
        hass = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.8},
            entity_states={"switch.heater": "off"},
            room_setpoints={"kitchen": 21.0},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("switch", "turn_on")

    @pytest.mark.asyncio
    async def test_switch_off(self):
        heater = ElectricHeater("e1", "kitchen", max_power=2000, heater_entity="switch.heater")
        hass = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.3},
            entity_states={"switch.heater": "on"},
            room_setpoints={"kitchen": 21.0},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("switch", "turn_off")


class TestApplyActionsNumber:
    """number.* entity handling."""

    @pytest.mark.asyncio
    async def test_number_value(self):
        heater = ElectricHeater("e1", "office", max_power=1500, heater_entity="number.heater_power")
        hass = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.75},
            entity_states={"number.heater_power": "0"},
            room_setpoints={"office": 21.0},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("number", "set_value")
        assert call.args[2]["value"] == 75


class TestApplyActionsEdgeCases:
    """Edge-case handling."""

    @pytest.mark.asyncio
    async def test_no_entity_configured(self):
        """Heat source with no heater_entity should be silently skipped."""
        hp = HeatPump("hp1", "living_room", max_power=5000)
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 1.0},
            entity_states={},
            room_setpoints={"living_room": 25.0},
        )
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_entity(self):
        """If the entity doesn't exist in HA, skip it."""
        hp = HeatPump("hp1", "living_room", max_power=5000, heater_entity="climate.gone")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 1.0},
            entity_states={},  # entity not registered
            room_setpoints={"living_room": 25.0},
        )
        hass.services.async_call.assert_not_called()
