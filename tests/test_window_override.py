"""Tests for Phase 3 W1 open-window override behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.heat_sources import ElectricHeater


def _make_hass(binary_states: dict[str, str]) -> SimpleNamespace:
    hass = SimpleNamespace()
    hass.services = SimpleNamespace(async_call=AsyncMock())

    def _get_state(entity_id: str):
        if entity_id not in binary_states:
            return None
        return SimpleNamespace(state=binary_states[entity_id], attributes={})

    hass.states = SimpleNamespace(get=_get_state)
    return hass


def _make_state_machine_coord(
    *,
    binary_states: dict[str, str],
    sensors: dict[str, list[str]],
    debounce: float = 60.0,
    settle: float = 30.0,
) -> HeatingAssistantCoordinator:
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = _make_hass(binary_states)
    coord.model = SimpleNamespace(room_names=list(sensors))
    coord._window_sensors = sensors
    coord._window_state = {room: "closed" for room in sensors}
    coord._window_state_since = {}
    coord._window_open_debounce = debounce
    coord._window_open_close_settle = settle
    return coord


def test_window_state_machine_requires_debounce_before_open():
    binary = {"binary_sensor.lr_window": "on"}
    coord = _make_state_machine_coord(
        binary_states=binary,
        sensors={"living_room": ["binary_sensor.lr_window"]},
        debounce=60.0,
        settle=30.0,
    )
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    coord._update_window_state_machine(t0)
    assert coord.get_window_state("living_room") == "pending_open"

    coord._update_window_state_machine(t0 + timedelta(seconds=30))
    assert coord.get_window_state("living_room") == "pending_open"

    coord._update_window_state_machine(t0 + timedelta(seconds=61))
    assert coord.get_window_state("living_room") == "open"
    assert coord.is_window_override_active("living_room") is True


def test_window_state_machine_settle_and_bounce_behavior():
    binary = {"binary_sensor.lr_window": "on"}
    coord = _make_state_machine_coord(
        binary_states=binary,
        sensors={"living_room": ["binary_sensor.lr_window"]},
        debounce=10.0,
        settle=20.0,
    )
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    coord._update_window_state_machine(t0)
    coord._update_window_state_machine(t0 + timedelta(seconds=11))
    assert coord.get_window_state("living_room") == "open"

    # Start closing: should enter pending_closed first.
    binary["binary_sensor.lr_window"] = "off"
    coord._update_window_state_machine(t0 + timedelta(seconds=12))
    assert coord.get_window_state("living_room") == "pending_closed"

    # Bounce back to on while settling -> return to open.
    binary["binary_sensor.lr_window"] = "on"
    coord._update_window_state_machine(t0 + timedelta(seconds=18))
    assert coord.get_window_state("living_room") == "open"

    # Close for full settle duration -> closed.
    binary["binary_sensor.lr_window"] = "off"
    coord._update_window_state_machine(t0 + timedelta(seconds=19))
    coord._update_window_state_machine(t0 + timedelta(seconds=40))
    assert coord.get_window_state("living_room") == "closed"
    assert coord.is_window_override_active("living_room") is False


def test_window_state_machine_uses_or_for_multi_sensor_room():
    binary = {
        "binary_sensor.win_1": "off",
        "binary_sensor.win_2": "on",
    }
    coord = _make_state_machine_coord(
        binary_states=binary,
        sensors={"living_room": ["binary_sensor.win_1", "binary_sensor.win_2"]},
        debounce=0.0,
        settle=0.0,
    )
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    coord._update_window_state_machine(t0)
    coord._update_window_state_machine(t0)
    assert coord.get_window_state("living_room") == "open"


@pytest.mark.asyncio
async def test_apply_actions_forces_switch_off_when_window_override_open():
    coord = object.__new__(HeatingAssistantCoordinator)
    source = ElectricHeater(
        "heater_1", "living_room", max_power=2000.0, heater_entity="switch.lr_heater",
    )
    coord.heat_sources = [source]
    coord.actions = {"heater_1": 1.0}
    coord._room_enabled = {"living_room": True}
    coord._schedule_disabled = {"living_room": False}
    coord._window_state = {"living_room": "open"}
    coord.model = SimpleNamespace(
        rooms={"living_room": SimpleNamespace(temperature=20.0, setpoint=21.0)},
    )
    coord.hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock()),
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(state="off", attributes={})
            if entity_id == "switch.lr_heater"
            else None
        ),
    )
    coord.controller = MagicMock()

    await coord._apply_actions(outdoor_temp=5.0)

    calls = coord.hass.services.async_call.call_args_list
    assert len(calls) == 1
    assert calls[0].args[:2] == ("switch", "turn_off")
    coord.controller.notify_applied_u.assert_called_once_with("heater_1", 0.0)
