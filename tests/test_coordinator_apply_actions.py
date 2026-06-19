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
                            room_temperatures=None, room_enabled=None):
    """Directly exercise ``_apply_actions`` without a full coordinator."""
    from custom_components.heating_assistant.coordinator import (
        HeatingAssistantCoordinator,
    )

    hass = _make_fake_hass(entity_states)

    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = hass
    coord.heat_sources = heat_sources
    coord.actions = actions

    model_rooms = {}
    for name, sp in room_setpoints.items():
        room = MagicMock()
        room.setpoint = sp
        room.temperature = (room_temperatures or {}).get(name, 20.0)
        model_rooms[name] = room
    model = MagicMock()
    model.rooms = model_rooms
    coord.model = model

    coord._system_enabled = True
    coord._room_enabled = {name: True for name in room_setpoints}
    if room_enabled:
        coord._room_enabled.update(room_enabled)
    coord._schedule_disabled = {name: False for name in room_setpoints}
    coord._window_state = {name: "closed" for name in room_setpoints}

    await coord._apply_actions(outdoor_temp=5.0)
    return hass, coord


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyActionsClimate:
    """climate.* entity handling with offset-based heat pump control."""

    @pytest.mark.asyncio
    async def test_heat_pump_heat_mode_logit_offset_from_internal_temp(self):
        """hvac_mode='heat': logit-based offset applied to HP's internal temperature."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
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
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # internal=23, fraction=0.6 (heat: u_range=1), delta_sat=3
        # f=0.6, logit(0.6)=0.4055, offset=1.5*(1+0.4055/5)=1.622 → 24.622
        expected = hp.target_temperature(0.6, 23.0)
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_heat_pump_heat_cool_mode_uses_heat_cool_string(self):
        """hvac_mode='heat_cool': HA mode string 'heat_cool' when supported."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat_cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.6},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {
                        "current_temperature": 23.0,
                        "hvac_modes": ["heat", "cool", "heat_cool", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "heat_cool"
        expected = hp.target_temperature(0.6, 23.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_heat_pump_heat_cool_falls_back_to_auto(self):
        """hvac_mode='heat_cool' but entity only supports 'auto' → use 'auto'."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat_cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.4},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {
                        "current_temperature": 22.0,
                        "hvac_modes": ["heat", "cool", "auto", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "auto"
        expected = hp.target_temperature(0.4, 22.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_heat_pump_heat_cool_negative_fraction_cools(self):
        """hvac_mode='heat_cool', fraction=-0.5: logit offset below internal → HP cools."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat_cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.5},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat_cool",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "cool", "heat_cool", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "heat_cool"
        # internal=26, f=-0.5 (hc: u_range=1), logit midpoint → offset=1.5 → 26−1.5=24.5
        expected = hp.target_temperature(-0.5, 26.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_heat_pump_heat_cool_zero_fraction_idles(self):
        """fraction=0 → setpoint equals internal temp → HP idles naturally."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat_cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat_cool",
                    "attributes": {
                        "current_temperature": 24.0,
                        "hvac_modes": ["heat", "cool", "heat_cool", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "heat_cool"
        # fraction=0 → no offset → setpoint = internal_temp = 24.0
        assert calls[1].args[2]["temperature"] == pytest.approx(24.0)

    @pytest.mark.asyncio
    async def test_heat_pump_cool_mode_uses_cool_string(self):
        """hvac_mode='cool': HA mode is 'cool', fraction=-0.8 drives logit offset down."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.8},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {
                        "current_temperature": 28.0,
                        "hvac_modes": ["heat", "cool", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "cool"
        # internal=28, cool (u_range=1), f=0.8 → logit(0.8)=1.386, offset=1.5*(1+0.277)=1.916 → 26.08
        expected = hp.target_temperature(-0.8, 28.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_heat_pump_cool_mode_falls_back_to_dry(self):
        """hvac_mode='cool' but 'cool' not in supported → falls back to 'dry'."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.4},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "dry", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "dry"

    @pytest.mark.asyncio
    async def test_heat_pump_cool_mode_falls_back_to_fan_only(self):
        """hvac_mode='cool', neither 'cool' nor 'dry' → falls back to 'fan_only'."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="cool",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.4},
            entity_states={
                "climate.heat_pump": {
                    "state": "off",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert calls[0].args[2]["hvac_mode"] == "fan_only"

    @pytest.mark.asyncio
    async def test_heat_pump_full_power_offset(self):
        """At fraction=1.0 the offset saturates at delta_sat."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
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
        # internal=21, f=1.0 → offset saturates at delta_sat=3.0 → 24.0
        assert temp_call.args[2]["temperature"] == pytest.approx(hp.target_temperature(1.0, 21.0))

    @pytest.mark.asyncio
    async def test_heat_pump_fallback_to_room_temp(self):
        """When HP entity has no current_temperature, fall back to room temperature."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            delta_sat=3.0, hvac_mode="heat",
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
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
        # falls back to room_temp=22; f=0.5 → logit midpoint → offset=1.5 → 23.5
        assert temp_call.args[2]["temperature"] == pytest.approx(hp.target_temperature(0.5, 22.0))

    @pytest.mark.asyncio
    async def test_non_heat_pump_climate_uses_internal_temp_plus_offset(self):
        """An ElectricHeater on a climate entity modulates from internal temp."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
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
        # max(22.5, 20.0 + 0.7 * 5.0) = 23.5
        assert temp_call.args[2]["temperature"] == pytest.approx(23.5)

    @pytest.mark.asyncio
    async def test_non_heat_pump_climate_idle_uses_internal_temp(self):
        """When fraction == 0 and room is enabled (system idle), a non-HP climate
        entity stays in heat mode with the setpoint equal to its own internal
        temperature so it does not produce heat but commands keep being sent."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 21.5},
                },
            },
            room_setpoints={"bedroom": 22.5},
            room_temperatures={"bedroom": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        # heat mode + setpoint at internal temperature minus idle offset
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "heat"
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(20.5)  # 21.5 - 1.0

    @pytest.mark.asyncio
    async def test_non_heat_pump_climate_idle_fallback_to_room_temp(self):
        """When idling and the entity has no current_temperature attribute,
        the setpoint falls back to the HA room temperature."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {},  # no current_temperature
                },
            },
            room_setpoints={"bedroom": 22.5},
            room_temperatures={"bedroom": 21.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # fallback: HA room temperature minus idle offset (21.0 - 1.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_non_heat_pump_climate_disabled_room_turns_off(self):
        """When the room is disabled the climate entity is turned off,
        regardless of what fraction the MPC may have computed."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 21.5},
                },
            },
            room_setpoints={"bedroom": 22.5},
            room_temperatures={"bedroom": 22.0},
            room_enabled={"bedroom": False},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 1
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "off"

    @pytest.mark.asyncio
    async def test_heat_pump_disabled_room_turns_off(self):
        """When a heat-pump room is disabled, force hvac_mode=off."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000,
            heater_entity="climate.heat_pump",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.7},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 25.0,
                        "hvac_modes": ["heat", "cool", "heat_cool", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 25.0},
            room_enabled={"living_room": False},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 1
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "off"


class TestApplyActionsSwitch:
    """switch.* entity handling."""

    @pytest.mark.asyncio
    async def test_switch_on(self):
        heater = ElectricHeater("e1", "kitchen", max_power=2000, heater_entity="switch.heater")
        hass, _coord = await _run_apply_actions(
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
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.3},
            entity_states={"switch.heater": "on"},
            room_setpoints={"kitchen": 21.0},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("switch", "turn_off")

    @pytest.mark.asyncio
    async def test_switch_disabled_room_forces_off(self):
        heater = ElectricHeater("e1", "kitchen", max_power=2000, heater_entity="switch.heater")
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 1.0},
            entity_states={"switch.heater": "on"},
            room_setpoints={"kitchen": 21.0},
            room_enabled={"kitchen": False},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("switch", "turn_off")


class TestApplyActionsNumber:
    """number.* entity handling."""

    @pytest.mark.asyncio
    async def test_number_value(self):
        heater = ElectricHeater("e1", "office", max_power=1500, heater_entity="number.heater_power")
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.75},
            entity_states={"number.heater_power": "0"},
            room_setpoints={"office": 21.0},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("number", "set_value")
        assert call.args[2]["value"] == 75

    @pytest.mark.asyncio
    async def test_number_disabled_room_forces_zero(self):
        heater = ElectricHeater("e1", "office", max_power=1500, heater_entity="number.heater_power")
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.75},
            entity_states={"number.heater_power": "75"},
            room_setpoints={"office": 21.0},
            room_enabled={"office": False},
        )
        call = hass.services.async_call.call_args_list[0]
        assert call.args[:2] == ("number", "set_value")
        assert call.args[2]["value"] == 0


class TestApplyActionsEdgeCases:
    """Edge-case handling."""

    @pytest.mark.asyncio
    async def test_no_entity_configured(self):
        """Heat source with no heater_entity should be silently skipped."""
        hp = HeatPump("hp1", "living_room", max_power=5000)
        hass, _coord = await _run_apply_actions(
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
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 1.0},
            entity_states={},  # entity not registered
            room_setpoints={"living_room": 25.0},
        )
        hass.services.async_call.assert_not_called()


class TestElectricHeaterCoolingProtection:
    """Electric heater cooling protection when room is above setpoint."""

    @pytest.mark.asyncio
    async def test_above_setpoint_no_heating(self):
        """When room is above setpoint with fraction=0, the entity setpoint is
        placed below the entity's internal temperature by DEFAULT_IDLE_OFFSET
        plus the room overshoot."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 24.0},
                },
            },
            room_setpoints={"bedroom": 22.0},
            room_temperatures={"bedroom": 23.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # overshoot = 1.0 → offset = 2.0; entity_temp 24.0 - 2.0 = 22.0
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_above_setpoint_overrides_positive_fraction(self):
        """Even when MPC says fraction > 0, if room is above setpoint the
        electric heater setpoint must be below the entity's internal temp
        by DEFAULT_IDLE_OFFSET plus the room overshoot."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.5},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 24.0},
                },
            },
            room_setpoints={"bedroom": 22.0},
            room_temperatures={"bedroom": 23.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # Despite fraction=0.5, room is above setpoint → use cooling protection
        # overshoot = 1.0 → offset = 2.0; entity_temp 24.0 - 2.0 = 22.0
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_above_setpoint_fallback_to_room_temp(self):
        """When entity has no current_temperature, fall back to HA room temp
        for the offset calculation."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {},  # no current_temperature
                },
            },
            room_setpoints={"bedroom": 22.0},
            room_temperatures={"bedroom": 23.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # Fallback to room temp: entity_temp = 23.0
        # overshoot = 1.0 → offset = 2.0; 23.0 - 2.0 = 21.0
        assert calls[1].args[2]["temperature"] == pytest.approx(21.0)

    @pytest.mark.asyncio
    async def test_at_setpoint_with_fraction_heats_normally(self):
        """When room is at setpoint and MPC says heat, normal heating applies."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.7},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 21.0},
                },
            },
            room_setpoints={"bedroom": 22.0},
            room_temperatures={"bedroom": 22.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # Room at setpoint, fraction > 0 → internal_temp + fraction * offset
        assert calls[1].args[2]["temperature"] == pytest.approx(24.5)


class TestScaledIdleOffsetNonHP:
    """Non-HP electric heater scaled idle offset when room exceeds setpoint."""

    @pytest.mark.asyncio
    async def test_two_degrees_above_setpoint(self):
        """When room is 2°C above setpoint the offset is DEFAULT_IDLE_OFFSET + 2."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 25.0},
                },
            },
            room_setpoints={"bedroom": 21.0},
            room_temperatures={"bedroom": 23.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # overshoot = 2.0 → offset = 3.0; entity_temp 25.0 - 3.0 = 22.0
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_at_setpoint_uses_default_offset(self):
        """When room is exactly at setpoint, only DEFAULT_IDLE_OFFSET (1°C) is used."""
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000,
            heater_entity="climate.bedroom_heater",
        )
        hass, _coord = await _run_apply_actions(
            heat_sources=[heater],
            actions={"e1": 0.0},
            entity_states={
                "climate.bedroom_heater": {
                    "state": "heat",
                    "attributes": {"current_temperature": 22.0},
                },
            },
            room_setpoints={"bedroom": 21.0},
            room_temperatures={"bedroom": 21.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # overshoot = 0 → offset = 1.0; 22.0 - 1.0 = 21.0
        assert calls[1].args[2]["temperature"] == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# Tests: delivered-power read-back (system stopped)
# ---------------------------------------------------------------------------


def _make_readback_coord(heat_sources, entity_states, controller=None, room_setpoints=None):
    """Bare coordinator wired only for the delivered-power read-back helpers."""
    from custom_components.heating_assistant.coordinator import (
        HeatingAssistantCoordinator,
    )

    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = _make_fake_hass(entity_states)
    coord.heat_sources = heat_sources
    coord.controller = controller
    if room_setpoints:
        class _Room:
            def __init__(self, setpoint: float) -> None:
                self.setpoint = setpoint

        class _Model:
            def __init__(self, rooms: dict) -> None:
                self.rooms = rooms

        coord.model = _Model(
            {name: _Room(sp) for name, sp in room_setpoints.items()}
        )
    return coord


class TestReadDeliveredFraction:
    """switch.* / number.* heaters report an exact delivered fraction."""

    def test_switch_on(self):
        h = ElectricHeater("e1", "k", max_power=2000, heater_entity="switch.h")
        coord = _make_readback_coord([h], {"switch.h": "on"})
        assert coord._read_delivered_fraction(h) == 1.0

    def test_switch_off(self):
        h = ElectricHeater("e1", "k", max_power=2000, heater_entity="switch.h")
        coord = _make_readback_coord([h], {"switch.h": "off"})
        assert coord._read_delivered_fraction(h) == 0.0

    def test_number_value(self):
        h = ElectricHeater("e1", "o", max_power=1500, heater_entity="number.p")
        coord = _make_readback_coord([h], {"number.p": "60"})
        assert coord._read_delivered_fraction(h) == pytest.approx(0.6)

    def test_number_clamped_above_100(self):
        h = ElectricHeater("e1", "o", max_power=1500, heater_entity="number.p")
        coord = _make_readback_coord([h], {"number.p": "150"})
        assert coord._read_delivered_fraction(h) == 1.0

    def test_number_unparsable(self):
        h = ElectricHeater("e1", "o", max_power=1500, heater_entity="number.p")
        coord = _make_readback_coord([h], {"number.p": "n/a"})
        assert coord._read_delivered_fraction(h) == 0.0

    def test_no_entity_configured(self):
        h = ElectricHeater("e1", "o", max_power=1500)
        coord = _make_readback_coord([h], {})
        assert coord._read_delivered_fraction(h) == 0.0

    def test_missing_entity(self):
        h = ElectricHeater("e1", "o", max_power=1500, heater_entity="switch.gone")
        coord = _make_readback_coord([h], {})
        assert coord._read_delivered_fraction(h) == 0.0

    def test_unavailable_entity(self):
        h = ElectricHeater("e1", "o", max_power=1500, heater_entity="switch.h")
        coord = _make_readback_coord([h], {"switch.h": "unavailable"})
        assert coord._read_delivered_fraction(h) == 0.0


class TestReadDeliveredFractionClimate:
    """climate.* delivered fraction is estimated from hvac_action + setpoint gap."""

    def _hp(self, mode="heat"):
        return HeatPump(
            "hp1", "living_room", max_power=5000, delta_sat=3.0,
            hvac_mode=mode, heater_entity="climate.hp",
        )

    def test_off_state_reads_zero(self):
        hp = self._hp()
        coord = _make_readback_coord(
            [hp], {"climate.hp": {"state": "off", "attributes": {}}}
        )
        assert coord._read_delivered_fraction(hp) == 0.0

    def test_idle_action_reads_zero(self):
        hp = self._hp()
        coord = _make_readback_coord(
            [hp],
            {"climate.hp": {"state": "heat", "attributes": {"hvac_action": "idle"}}},
        )
        assert coord._read_delivered_fraction(hp) == 0.0

    def test_heating_infers_from_setpoint_gap(self):
        hp = self._hp()
        coord = _make_readback_coord(
            [hp],
            {"climate.hp": {"state": "heat", "attributes": {
                "hvac_action": "heating",
                "temperature": 24.0,
                "current_temperature": 22.0,
            }}},
            room_setpoints={"living_room": 22.0},
        )
        # Offset from comfort setpoint: 24 − 22 = 2 °C → logit inverse.
        expected = hp.fraction_from_setpoint_offset(2.0)
        assert coord._read_delivered_fraction(hp) == pytest.approx(expected)

    def test_heating_full_offset_reads_one(self):
        """Saturating offset (delta_sat) must read back as fraction ≈ 1."""
        hp = self._hp()
        coord = _make_readback_coord(
            [hp],
            {"climate.hp": {"state": "heat", "attributes": {
                "hvac_action": "heating",
                "temperature": 25.0,
                "current_temperature": 23.0,
            }}},
            room_setpoints={"living_room": 22.0},
        )
        # target − setpoint = +3 °C = delta_sat → full heating.
        assert coord._read_delivered_fraction(hp) == pytest.approx(1.0, abs=0.02)

    def test_heating_no_telemetry_assumes_full(self):
        hp = self._hp()
        coord = _make_readback_coord(
            [hp],
            {"climate.hp": {"state": "heat", "attributes": {"hvac_action": "heating"}}},
        )
        assert coord._read_delivered_fraction(hp) == 1.0

    def test_heating_no_action_no_telemetry_reads_zero(self):
        hp = self._hp()
        coord = _make_readback_coord(
            [hp], {"climate.hp": {"state": "heat", "attributes": {}}}
        )
        assert coord._read_delivered_fraction(hp) == 0.0

    def test_cooling_on_can_cool_source_is_negative(self):
        hp = self._hp(mode="heat_cool")  # can_cool == True
        coord = _make_readback_coord(
            [hp],
            {"climate.hp": {"state": "cool", "attributes": {
                "hvac_action": "cooling",
                "temperature": 20.0,
                "current_temperature": 24.0,
            }}},
            room_setpoints={"living_room": 22.0},
        )
        # Offset from comfort setpoint: 20 − 22 = −2 °C → negative logit inverse.
        expected = hp.fraction_from_setpoint_offset(-2.0)
        assert coord._read_delivered_fraction(hp) == pytest.approx(expected)

    def test_cooling_full_offset_reads_minus_one(self):
        hp = self._hp(mode="heat_cool")
        coord = _make_readback_coord(
            [hp],
            {"climate.hp": {"state": "cool", "attributes": {
                "hvac_action": "cooling",
                "temperature": 19.0,
                "current_temperature": 24.0,
            }}},
            room_setpoints={"living_room": 22.0},
        )
        assert coord._read_delivered_fraction(hp) == pytest.approx(-1.0, abs=0.02)

    def test_cooling_on_heating_only_source_reads_zero(self):
        heater = ElectricHeater(
            "e1", "bedroom", max_power=2000, heater_entity="climate.h"
        )  # can_cool == False
        coord = _make_readback_coord(
            [heater],
            {"climate.h": {"state": "cool", "attributes": {"hvac_action": "cooling"}}},
        )
        assert coord._read_delivered_fraction(heater) == 0.0


class TestSetSourcePower:
    """_set_source_power maps a delivered fraction onto current_power."""

    def test_heating_sets_thermal_power(self):
        h = ElectricHeater("e1", "k", max_power=2000, heater_entity="switch.h")
        coord = _make_readback_coord([h], {})
        coord._set_source_power(h, 0.5, outdoor_temp=5.0)
        assert h.current_power == pytest.approx(1000.0)  # 2000 * 0.5

    def test_cooling_sets_negative_power(self):
        hp = HeatPump(
            "hp1", "lr", max_power=5000, hvac_mode="heat_cool",
            heater_entity="climate.hp",
        )
        coord = _make_readback_coord([hp], {})
        coord._set_source_power(hp, -0.5, outdoor_temp=5.0)
        assert hp.current_power < 0.0


class TestReadDeliveredActions:
    """The aggregate read-back updates power, notifies the EKF, returns fractions."""

    def test_updates_power_and_notifies_controller(self):
        h1 = ElectricHeater("e1", "k", max_power=2000, heater_entity="switch.h1")
        h2 = ElectricHeater("e2", "o", max_power=1000, heater_entity="number.h2")
        controller = MagicMock()
        coord = _make_readback_coord(
            [h1, h2],
            {"switch.h1": "on", "number.h2": "50"},
            controller=controller,
        )

        applied = coord._read_delivered_actions(outdoor_temp=5.0)

        assert applied == {"e1": pytest.approx(1.0), "e2": pytest.approx(0.5)}
        assert h1.current_power == pytest.approx(2000.0)
        assert h2.current_power == pytest.approx(500.0)
        notified = {
            c.args[0]: c.args[1]
            for c in controller.notify_applied_u.call_args_list
        }
        assert notified == {"e1": pytest.approx(1.0), "e2": pytest.approx(0.5)}

    def test_no_controller_is_tolerated(self):
        h = ElectricHeater("e1", "k", max_power=2000, heater_entity="switch.h")
        coord = _make_readback_coord([h], {"switch.h": "off"}, controller=None)
        applied = coord._read_delivered_actions(outdoor_temp=5.0)
        assert applied == {"e1": 0.0}
        assert h.current_power == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: _read_outdoor_temp
# ---------------------------------------------------------------------------


def _make_coordinator_for_outdoor_temp(outdoor_entity, weather_entity, entity_states):
    """Build a bare-minimum coordinator object for _read_outdoor_temp tests."""
    from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator

    coord = object.__new__(HeatingAssistantCoordinator)
    coord._outdoor_entity = outdoor_entity
    coord._weather_entity = weather_entity

    hass = MagicMock()

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
    coord.hass = hass
    return coord


class TestReadOutdoorTemp:
    """Tests for HeatingAssistantCoordinator._read_outdoor_temp."""

    def test_reads_from_outdoor_entity_when_configured(self):
        """Reads temperature from the dedicated outdoor-temp sensor."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity="sensor.outdoor_temp",
            weather_entity=None,
            entity_states={"sensor.outdoor_temp": "-3.5"},
        )
        assert coord._read_outdoor_temp() == pytest.approx(-3.5)

    def test_falls_back_to_weather_entity_temperature_attribute(self):
        """When no outdoor entity is set, reads from weather entity's temperature attribute."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity=None,
            weather_entity="weather.home",
            entity_states={
                "weather.home": {
                    "state": "sunny",
                    "attributes": {"temperature": 7.3},
                }
            },
        )
        assert coord._read_outdoor_temp() == pytest.approx(7.3)

    def test_prefers_outdoor_entity_over_weather_entity(self):
        """Outdoor-temp sensor takes priority over weather entity attribute."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity="sensor.outdoor_temp",
            weather_entity="weather.home",
            entity_states={
                "sensor.outdoor_temp": "2.0",
                "weather.home": {
                    "state": "cloudy",
                    "attributes": {"temperature": 99.0},
                },
            },
        )
        assert coord._read_outdoor_temp() == pytest.approx(2.0)

    def test_falls_back_to_weather_when_outdoor_entity_unavailable(self):
        """Falls back to weather entity when the outdoor sensor is unavailable."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity="sensor.outdoor_temp",
            weather_entity="weather.home",
            entity_states={
                "sensor.outdoor_temp": "unavailable",
                "weather.home": {
                    "state": "cloudy",
                    "attributes": {"temperature": 4.5},
                },
            },
        )
        assert coord._read_outdoor_temp() == pytest.approx(4.5)

    def test_falls_back_to_weather_when_outdoor_entity_unknown(self):
        """Falls back to weather entity when the outdoor sensor is unknown."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity="sensor.outdoor_temp",
            weather_entity="weather.home",
            entity_states={
                "sensor.outdoor_temp": "unknown",
                "weather.home": {
                    "state": "rainy",
                    "attributes": {"temperature": 11.0},
                },
            },
        )
        assert coord._read_outdoor_temp() == pytest.approx(11.0)

    def test_falls_back_to_default_when_no_entities_configured(self):
        """Returns 5.0 when neither outdoor entity nor weather entity is configured."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity=None,
            weather_entity=None,
            entity_states={},
        )
        assert coord._read_outdoor_temp() == pytest.approx(5.0)

    def test_falls_back_to_default_when_weather_entity_unavailable(self):
        """Returns 5.0 when weather entity is unavailable and no outdoor entity exists."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity=None,
            weather_entity="weather.home",
            entity_states={
                "weather.home": {
                    "state": "unavailable",
                    "attributes": {},
                }
            },
        )
        assert coord._read_outdoor_temp() == pytest.approx(5.0)

    def test_falls_back_to_default_when_weather_entity_missing(self):
        """Returns 5.0 when weather entity is configured but does not exist in HA states."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity=None,
            weather_entity="weather.home",
            entity_states={},  # entity not present
        )
        assert coord._read_outdoor_temp() == pytest.approx(5.0)

    def test_falls_back_to_default_when_weather_temperature_attribute_missing(self):
        """Returns 5.0 when weather entity has no temperature attribute."""
        coord = _make_coordinator_for_outdoor_temp(
            outdoor_entity=None,
            weather_entity="weather.home",
            entity_states={
                "weather.home": {
                    "state": "sunny",
                    "attributes": {},  # no temperature key
                }
            },
        )
        assert coord._read_outdoor_temp() == pytest.approx(5.0)
