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
                            room_temperatures=None, room_enabled=None,
                            initial_cooling_state=None):
    """
    Directly exercise the ``_apply_actions`` logic without spinning up
    a full coordinator.  We import the module and patch just enough to
    call the method.

    ``initial_cooling_state`` is an optional dict mapping source name → bool
    that pre-seeds the Schmitt-trigger state (simulates a previous cycle).
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

    # All rooms enabled by default (matches coordinator __init__).
    # Caller may override individual rooms via room_enabled dict.
    coord._room_enabled = {name: True for name in room_setpoints}
    if room_enabled:
        coord._room_enabled.update(room_enabled)
    # Comfort-schedule state: no schedules in the bare _apply_actions tests.
    coord._schedule_disabled = {name: False for name in room_setpoints}

    # Initialise the Schmitt-trigger state dict (required by _apply_actions).
    # Caller may pre-seed individual sources to simulate a prior cycle.
    coord._cooling_active = dict(initial_cooling_state or {})

    await coord._apply_actions(outdoor_temp=5.0)
    return hass, coord


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
        # fallback: 22 + 0.5 × 5 = 24.5
        assert temp_call.args[2]["temperature"] == pytest.approx(24.5)

    @pytest.mark.asyncio
    async def test_heat_pump_idles_within_deadband(self):
        """When fraction == 0 and room temp is within deadband of setpoint,
        the HP stays on with target = internal temp (idle, no offset)."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
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
        # Should stay in heat mode and set temperature to internal temp - idle offset
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "heat"
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)  # 23.0 - 1.0

    @pytest.mark.asyncio
    async def test_heat_pump_turns_off_above_deadband(self):
        """When fraction == 0 and room temp > setpoint, the HP switches to
        fan_only mode (no dry mode listed) and sets the temperature below the
        internal sensor by DEFAULT_IDLE_OFFSET + overshoot."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    # No "hvac_modes" attr → fallback to fan_only
                    "attributes": {"current_temperature": 27.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # overshoot = 26.5 - 25.0 = 1.5 → offset = 1.0 + 1.5 = 2.5
        # target = 27.0 - 2.5 = 24.5
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(24.5)

    @pytest.mark.asyncio
    async def test_heat_pump_idle_fallback_to_room_temp(self):
        """When idling and internal temp is unavailable, fall back to room temp."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
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
        # Falls back to room temperature minus idle offset (22.0 - 1.0)
        assert calls[1].args[2]["temperature"] == pytest.approx(21.0)

    @pytest.mark.asyncio
    async def test_heat_pump_custom_deadband_holds_idle_within_band(self):
        """A wider deadband keeps the HP in idle mode when room_temp is above
        the setpoint but still inside the dead band (below setpoint + deadband).
        room=26.5, setpoint=25.0, deadband=2.0 → upper threshold=27.0 → idle."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=2.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
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
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        # 26.5 < 25.0 + 2.0 = 27.0 → deadband prevents cooling → stays in heat/idle
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # idle: target = internal_temp - DEFAULT_IDLE_OFFSET = 26.0 - 1.0 = 25.0
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(25.0)

    @pytest.mark.asyncio
    async def test_heat_pump_custom_deadband_enters_cooling_above_threshold(self):
        """With deadband=2.0, cooling starts once room_temp exceeds setpoint + 2.0.
        room=27.5, setpoint=25.0 → upper threshold=27.0 → passive cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=2.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 27.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 27.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # overshoot = 2.5; offset = 1.0 + 2.5 = 3.5; target = 27.0 - 3.5 = 23.5
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(23.5)

    # ── Bidirectional dead-band / jitter tests ─────────────────────────────

    @pytest.mark.asyncio
    async def test_deadband_blocks_active_cooling_request_within_band(self):
        """When MPC outputs fraction < 0 (active cooling) but room_temp has not
        crossed the upper dead-band threshold, the HP must NOT switch to cooling.
        It should stay in idle/heat mode to prevent heating→cooling jitter."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        # room=25.3, setpoint=25.0, deadband=1.0 → upper threshold=26.0
        # room_temp (25.3) < threshold (26.0) → deadband blocks cooling
        hass, coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.3},   # MPC says cool
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 24.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 25.3},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        # Dead band prevents cooling → stays in heat/idle mode
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # idle: target = internal_temp − DEFAULT_IDLE_OFFSET = 24.0 − 1.0 = 23.0
        assert calls[1].args[2]["temperature"] == pytest.approx(23.0)
        # Schmitt-trigger state must still be False
        assert coord._cooling_active.get("hp1") is False

    @pytest.mark.asyncio
    async def test_deadband_allows_active_cooling_above_upper_threshold(self):
        """When fraction < 0 AND room_temp > setpoint + deadband, active cooling
        should proceed (deadband cleared, HP allowed to cool)."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0, max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        # room=26.5, setpoint=25.0, deadband=1.0 → upper threshold=26.0 → enters cooling
        hass, coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.4},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 25.0,
                        "hvac_modes": ["heat", "cool"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        # Dead band crossed → active cooling
        assert calls[0].args[2]["hvac_mode"] == "cool"
        assert coord._cooling_active.get("hp1") is True

    @pytest.mark.asyncio
    async def test_deadband_holds_cooling_until_lower_threshold_when_heating_requested(self):
        """While in cooling mode (previous cycle set _cooling_active=True), a
        positive MPC fraction must NOT immediately switch to heating if room_temp
        has not yet dropped below setpoint − deadband.  The HP stays in passive
        cooling mode to prevent cooling→heating jitter."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0,
                       heater_entity="climate.heat_pump")
        # room=24.8, setpoint=25.0, deadband=1.0 → lower threshold=24.0
        # room_temp (24.8) > threshold (24.0) → deadband holds cooling
        hass, coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.4},   # MPC says heat
            entity_states={
                "climate.heat_pump": {
                    "state": "cool",
                    "attributes": {"current_temperature": 23.5},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 24.8},
            initial_cooling_state={"hp1": True},  # was cooling last cycle
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        # Dead band holds cooling despite positive fraction
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # Schmitt-trigger state must still be True
        assert coord._cooling_active.get("hp1") is True

    @pytest.mark.asyncio
    async def test_deadband_exits_cooling_once_below_lower_threshold(self):
        """When cooling was active and room_temp drops below setpoint − deadband,
        the Schmitt trigger clears and the HP switches to heating."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       turn_off_deadband=1.0, max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        # room=23.5, setpoint=25.0, deadband=1.0 → lower threshold=24.0
        # room_temp (23.5) < threshold (24.0) → exits cooling → heats
        hass, coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.5},   # MPC says heat
            entity_states={
                "climate.heat_pump": {
                    "state": "cool",
                    "attributes": {"current_temperature": 22.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 23.5},
            initial_cooling_state={"hp1": True},  # was cooling last cycle
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        # Dropped below lower threshold → switches to heating
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # target = 22.0 + 0.5 × 5.0 = 24.5
        assert calls[1].args[2]["temperature"] == pytest.approx(24.5)
        assert coord._cooling_active.get("hp1") is False

    @pytest.mark.asyncio
    async def test_non_heat_pump_climate_uses_room_setpoint(self):
        """An ElectricHeater on a climate entity falls back to the room setpoint."""
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
        assert temp_call.args[2]["temperature"] == 22.5

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


class TestHeatPumpFanMode:
    """Heat pump fan_only mode when room is above setpoint."""

    @pytest.mark.asyncio
    async def test_fan_mode_slightly_above_setpoint_stays_idle_within_deadband(self):
        """When room is just above setpoint but still inside the dead band
        (room < setpoint + deadband), the HP stays in idle/heat mode rather than
        switching to a cooling mode.  This prevents jitter near the setpoint."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        # room=25.1, setpoint=25.0, deadband=1.0 → upper threshold=26.0
        # 25.1 < 26.0 → Schmitt-trigger stays False → idle
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 25.5},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 25.1},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        # Within deadband → HP stays in heat/idle mode, no cooling
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # idle: target = internal_temp − DEFAULT_IDLE_OFFSET = 25.5 − 1.0 = 24.5
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(24.5)

    @pytest.mark.asyncio
    async def test_fan_mode_not_applied_at_setpoint(self):
        """When room is exactly at setpoint, HP idles in heat mode."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 25.0},
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 25.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "heat"
        # Idle: 25.0 - 1.0 = 24.0
        assert calls[1].args[2]["temperature"] == pytest.approx(24.0)

    @pytest.mark.asyncio
    async def test_fan_mode_not_applied_to_electric_heater(self):
        """Fan/dry mode is only for heat pumps; electric heaters stay in heat mode
        with a scaled idle offset when room is above setpoint."""
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
    async def test_heat_pump_disabled_room_turns_off_not_fan(self):
        """When room is disabled, fraction is forced to 0.  If room_temp exceeds
        the upper dead-band threshold (setpoint + deadband) the HP uses passive
        cooling mode and sets a temperature below its internal sensor."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        # room=26.5, setpoint=25.0, deadband=1.0 → upper threshold=26.0
        # 26.5 > 26.0 → enters passive cooling
        hass, _coord = await _run_apply_actions(
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
            room_enabled={"living_room": False},
        )

        # room_temp (26.5) > upper threshold (26.0) → cooling mode
        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # overshoot = 1.5 → offset = 2.5; target = 27.0 - 2.5 = 24.5
        assert calls[1].args[2]["temperature"] == pytest.approx(24.5)


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
        # Room at setpoint, fraction > 0 → use room setpoint
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)


class TestHeatPumpCoolingModeSelection:
    """Heat pump cool/dry/fan mode selection and scaled temperature offset."""

    @pytest.mark.asyncio
    async def test_cool_mode_preferred_over_dry_when_available(self):
        """When the HP entity supports 'cool' mode, it is chosen over 'dry'.
        room=26.5 > setpoint(25.0) + deadband(1.0) = 26.0 → enters cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "cool", "dry", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "cool"
        # overshoot = 1.5 → offset = 2.5; target = 26.0 - 2.5 = 23.5
        assert calls[1].args[2]["temperature"] == pytest.approx(23.5)

    @pytest.mark.asyncio
    async def test_dry_mode_preferred_when_cool_unavailable(self):
        """When 'cool' is not listed but 'dry' is, 'dry' is chosen over 'fan_only'.
        room=26.5 > setpoint(25.0) + deadband(1.0) = 26.0 → enters cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "dry", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "dry"
        # overshoot = 1.5 → offset = 2.5; target = 26.0 - 2.5 = 23.5
        assert calls[1].args[2]["temperature"] == pytest.approx(23.5)

    @pytest.mark.asyncio
    async def test_fan_only_fallback_when_no_dry_or_cool(self):
        """When neither 'cool' nor 'dry' is listed, fall back to 'fan_only'.
        room=26.5 > setpoint(25.0) + deadband(1.0) = 26.0 → enters cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # overshoot = 1.5 → offset = 2.5; target = 26.0 - 2.5 = 23.5
        assert calls[1].args[2]["temperature"] == pytest.approx(23.5)

    @pytest.mark.asyncio
    async def test_hp_cooling_protection_overrides_positive_fraction(self):
        """Even when MPC fraction > 0, if room is above the upper dead-band
        threshold the HP switches to passive cooling instead of heating.
        room=26.5 > setpoint(25.0) + deadband(1.0) = 26.0 → enters cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.8},  # MPC wants heating but deadband forces cooling
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 27.0,
                        "hvac_modes": ["heat", "dry", "fan_only"],
                    },
                },
            },
            room_setpoints={"living_room": 25.0},
            room_temperatures={"living_room": 26.5},
        )

        calls = hass.services.async_call.call_args_list
        # Should switch to dry mode (no 'cool' available), not heat
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "dry"
        # overshoot = 1.5 → offset = 2.5; target = 27.0 - 2.5 = 24.5
        assert calls[1].args[2]["temperature"] == pytest.approx(24.5)

    @pytest.mark.asyncio
    async def test_hp_cooling_scaled_offset_large_overshoot(self):
        """A larger room overshoot produces a proportionally lower HP setpoint."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {"current_temperature": 25.0},
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 23.0},  # 2°C above setpoint
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        # overshoot = 2.0 → offset = 3.0; target = 25.0 - 3.0 = 22.0
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_hp_cooling_fallback_to_room_temp(self):
        """When HP has no current_temperature, fall back to room temp for the
        cooling setpoint calculation."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": 0.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {},  # no current_temperature
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 23.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # fallback: room_temp=23.0; overshoot=2.0 → offset=3.0; 23.0-3.0=20.0
        assert calls[1].args[2]["temperature"] == pytest.approx(20.0)



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


class TestMPCDrivenCooling:
    """MPC-driven active cooling via negative control fractions."""

    @pytest.mark.asyncio
    async def test_negative_fraction_activates_cool_mode(self):
        """When MPC outputs fraction < 0, the coordinator switches to 'cool' mode."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.5},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "cool", "dry", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 26.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[:2] == ("climate", "set_hvac_mode")
        assert calls[0].args[2]["hvac_mode"] == "cool"
        # target = 26.0 − 0.5 × 5.0 = 23.5
        assert calls[1].args[:2] == ("climate", "set_temperature")
        assert calls[1].args[2]["temperature"] == pytest.approx(23.5)

    @pytest.mark.asyncio
    async def test_negative_fraction_falls_back_to_dry_when_no_cool(self):
        """When 'cool' is not available but 'dry' is, fall back to 'dry'."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -1.0},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 28.0,
                        "hvac_modes": ["heat", "dry", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 28.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "dry"
        # target = 28.0 − 1.0 × 5.0 = 23.0
        assert calls[1].args[2]["temperature"] == pytest.approx(23.0)

    @pytest.mark.asyncio
    async def test_negative_fraction_falls_back_to_fan_only(self):
        """When neither 'cool' nor 'dry' is available, use 'fan_only'."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.8},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 27.0,
                        "hvac_modes": ["heat", "fan_only", "off"],
                    },
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 27.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0].args[2]["hvac_mode"] == "fan_only"
        # target = 27.0 − 0.8 × 5.0 = 23.0
        assert calls[1].args[2]["temperature"] == pytest.approx(23.0)

    @pytest.mark.asyncio
    async def test_negative_fraction_uses_room_temp_fallback(self):
        """When HP has no current_temperature, fall back to room_temp."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.6},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {},  # no current_temperature
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 25.0},
        )

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        # Fallback to room_temp: 25.0 − 0.6 × 5.0 = 22.0
        assert calls[1].args[2]["temperature"] == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_negative_fraction_takes_priority_over_passive_cooling(self):
        """When MPC requests fraction < 0, the active cooling path is taken,
        not the passive room_temp > setpoint path."""
        hp = HeatPump("hp1", "living_room", max_power=5000,
                       max_temp_offset=5.0,
                       heater_entity="climate.heat_pump")
        hass, _coord = await _run_apply_actions(
            heat_sources=[hp],
            actions={"hp1": -0.4},
            entity_states={
                "climate.heat_pump": {
                    "state": "heat",
                    "attributes": {
                        "current_temperature": 26.0,
                        "hvac_modes": ["heat", "cool", "fan_only"],
                    },
                },
            },
            room_setpoints={"living_room": 21.0},
            room_temperatures={"living_room": 26.0},
        )

        calls = hass.services.async_call.call_args_list
        # Must enter MPC-driven cool path ("cool" is preferred over "dry"/"fan_only")
        assert calls[0].args[2]["hvac_mode"] == "cool"
        # target = 26.0 − 0.4 × 5.0 = 24.0 (not the passive-cooling overshoot formula)
        assert calls[1].args[2]["temperature"] == pytest.approx(24.0)


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
