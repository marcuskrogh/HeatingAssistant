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
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    def _get_state(entity_id):
        if entity_id in entity_states:
            s = MagicMock()
            s.state = entity_states[entity_id]
            return s
        return None

    hass.states.get = _get_state
    return hass


async def _run_apply_actions(heat_sources, actions, entity_states, room_setpoints):
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
        model_rooms[name] = room
    model = MagicMock()
    model.rooms = model_rooms
    coord.model = model

    await coord._apply_actions(outdoor_temp=5.0)
    return hass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyActionsClimate:
    """climate.* entity handling."""

    @pytest.mark.asyncio
    async def test_climate_heat_sets_hvac_mode_and_temperature(self):
        """When fraction > 0, both set_hvac_mode and set_temperature must be called."""
        hp = HeatPump("hp1", "living_room", max_power=5000, heater_entity="climate.heat_pump")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.8},
            entity_states={"climate.heat_pump": "off"},
            room_setpoints={"living_room": 25.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2

        # First call: set_hvac_mode → heat
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["entity_id"] == "climate.heat_pump"
        assert calls[0].args[2]["hvac_mode"] == "heat"

        # Second call: set_temperature → room setpoint
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["entity_id"] == "climate.heat_pump"
        assert calls[1].args[2]["temperature"] == 25.0

    @pytest.mark.asyncio
    async def test_climate_off_only_sets_hvac_mode(self):
        """When fraction == 0, only set_hvac_mode → off is called (no set_temperature)."""
        hp = HeatPump("hp1", "living_room", max_power=5000, heater_entity="climate.heat_pump")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={"climate.heat_pump": "heat"},
            room_setpoints={"living_room": 25.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 1
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "off"

    @pytest.mark.asyncio
    async def test_climate_setpoint_matches_room(self):
        """The temperature sent to the climate entity equals the HA room setpoint."""
        hp = HeatPump("hp1", "bedroom", max_power=3000, heater_entity="climate.bedroom_hp")
        hass = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.5},
            entity_states={"climate.bedroom_hp": "off"},
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
