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


def test_window_override_active_during_open_and_settle_only():
    coord = _make_state_machine_coord(
        binary_states={},
        sensors={"living_room": ["binary_sensor.lr_window"]},
    )
    # Opening starts the debounce timer but the heater keeps running.
    coord._window_state["living_room"] = "pending_open"
    assert coord.is_window_override_active("living_room") is False
    # Debounce elapsed: heater forced off.
    coord._window_state["living_room"] = "open"
    assert coord.is_window_override_active("living_room") is True
    # Window closed but settle timer still running: heater must STAY off.
    coord._window_state["living_room"] = "pending_closed"
    assert coord.is_window_override_active("living_room") is True
    # Settle elapsed: override clears, heater may run again.
    coord._window_state["living_room"] = "closed"
    assert coord.is_window_override_active("living_room") is False


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
    coord._system_enabled = True

    await coord._apply_actions(outdoor_temp=5.0)

    calls = coord.hass.services.async_call.call_args_list
    assert len(calls) == 1
    assert calls[0].args[:2] == ("switch", "turn_off")
    coord.controller.notify_applied_u.assert_called_once_with("heater_1", 0.0)


@pytest.mark.asyncio
async def test_push_override_resumes_closed_room_at_mpc_value_keeps_open_off():
    """When a window-close settle timer expires between scheduled MPC solves,
    the heater must come back on at the MPC's shadow optimum, while a room whose
    window is still open stays clamped to 0."""
    coord = object.__new__(HeatingAssistantCoordinator)
    s_open = ElectricHeater(
        "h_open", "open_room", max_power=2000.0, heater_entity="switch.h_open",
    )
    s_closed = ElectricHeater(
        "h_closed", "closed_room", max_power=2000.0, heater_entity="switch.h_closed",
    )
    coord.heat_sources = [s_open, s_closed]
    # Last scheduled solve clamped both override rooms to 0 in self.actions...
    coord.actions = {"h_open": 0.0, "h_closed": 0.0}
    # ...but the shadow optimum holds what the MPC kept planning meanwhile.
    coord._mpc_shadow_actions = {"h_open": 0.6, "h_closed": 0.7}
    coord._system_enabled = True
    coord._room_enabled = {"open_room": True, "closed_room": True}
    coord._schedule_disabled = {"open_room": False, "closed_room": False}
    # open_room is still under override; closed_room's settle just expired.
    coord._window_state = {"open_room": "open", "closed_room": "closed"}

    coord._refresh_live_state = lambda: 5.0
    applied: dict[str, float] = {}

    async def _fake_apply(outdoor_temp):
        applied.update(coord.actions)

    coord._apply_actions = _fake_apply
    coord.async_update_listeners = MagicMock()

    await coord._async_push_window_override()

    # Window still open -> heater stays off.
    assert coord.actions["h_open"] == 0.0
    # Window just closed -> resume at the MPC's planned actuation.
    assert coord.actions["h_closed"] == pytest.approx(0.7)
    assert applied["h_closed"] == pytest.approx(0.7)
    coord.async_update_listeners.assert_called_once()


class _FakeHass:
    """Minimal hass exposing binary-sensor states and a no-op task scheduler."""

    def __init__(self, states: dict[str, str]):
        self._states = states

    @property
    def states(self):
        def _get(eid):
            if eid not in self._states:
                return None
            return SimpleNamespace(state=self._states[eid], attributes={})

        return SimpleNamespace(get=_get)

    def async_create_task(self, coro):
        # We only assert on state transitions here; never run the push.
        coro.close()


def test_event_handler_reopen_during_settle_cancels_timer_and_reverts(monkeypatch):
    """A window re-opening during the close-settle window must cancel the
    settle timer and return the room to ``open`` (heater stays off), without
    starting a fresh debounce timer."""
    import custom_components.heating_assistant.coordinator as coord_mod

    timers: list = []

    def fake_call_later(hass, delay, cb):
        cancel = MagicMock()
        timers.append((delay, cb, cancel))
        return cancel

    captured: dict = {}

    def fake_track(hass, ids, listener):
        captured["listener"] = listener
        return MagicMock()

    monkeypatch.setattr(coord_mod, "async_call_later", fake_call_later)
    monkeypatch.setattr(coord_mod, "async_track_state_change_event", fake_track)

    states = {"binary_sensor.lr": "off"}
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = _FakeHass(states)
    coord._window_sensors = {"living_room": ["binary_sensor.lr"]}
    coord._window_state = {"living_room": "closed"}
    coord._window_state_since = {}
    coord._window_open_debounce = 60.0
    coord._window_open_close_settle = 30.0

    coord.setup_window_listeners()
    listener = captured["listener"]

    def fire(new_open: bool, old_open: bool):
        states["binary_sensor.lr"] = "on" if new_open else "off"
        listener(SimpleNamespace(data={
            "entity_id": "binary_sensor.lr",
            "new_state": SimpleNamespace(state="on" if new_open else "off"),
            "old_state": SimpleNamespace(state="on" if old_open else "off"),
        }))

    # 1) Open -> pending_open with a debounce timer.
    fire(new_open=True, old_open=False)
    assert coord.get_window_state("living_room") == "pending_open"
    assert len(timers) == 1 and timers[0][0] == 60.0

    # 2) Debounce elapses -> open.
    timers[0][1]()  # invoke _advance_open callback
    assert coord.get_window_state("living_room") == "open"

    # 3) Close -> pending_closed with a settle timer.
    fire(new_open=False, old_open=True)
    assert coord.get_window_state("living_room") == "pending_closed"
    assert len(timers) == 2 and timers[1][0] == 30.0
    settle_cancel = timers[1][2]

    # 4) Re-open during settle -> back to open, settle timer cancelled, no new timer.
    fire(new_open=True, old_open=False)
    assert coord.get_window_state("living_room") == "open"
    settle_cancel.assert_called_once()
    assert len(timers) == 2  # no fresh debounce timer was armed
