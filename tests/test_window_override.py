"""SWD-298: App window/door override state machine and heater clamp."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from heatingassistant.app import window_override as window_ov
from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def _options(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instance_id": "haos",
        "system_enabled": True,
        "update_interval": 900,
        "window_open_debounce": 0.05,
        "window_open_close_settle": 0.05,
        "window_open_q_inflation": 10.0,
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 22.0,
                "temp_sensors": ["sensor.living_temp"],
                "window_sensors": ["binary_sensor.lr_window"],
                "enabled": True,
            }
        ],
        "heat_sources": [
            {
                "name": "Living Heater",
                "room": "Living Room",
                "type": "electric",
                "heater_entity": "switch.living_heater",
                "output_tag": "living_heater",
                "max_power": 1000.0,
            }
        ],
        "bindings": [
            {
                "tag": "living_heater",
                "entity_id": "switch.living_heater",
                "direction": "out",
            }
        ],
    }
    base.update(overrides)
    return base


def test_override_active_only_for_open_and_pending_closed() -> None:
    states = {"living_room": "pending_open"}
    assert window_ov.is_window_override_active(states, "living_room") is False
    states["living_room"] = "open"
    assert window_ov.is_window_override_active(states, "living_room") is True
    states["living_room"] = "pending_closed"
    assert window_ov.is_window_override_active(states, "living_room") is True
    states["living_room"] = "closed"
    assert window_ov.is_window_override_active(states, "living_room") is False


def test_state_machine_requires_debounce_before_open() -> None:
    tags = {"Living Room": ["lr_window"]}
    tag_values = {"lr_window": True}
    states: dict[str, str] = {"Living Room": "closed"}
    since: dict[str, datetime] = {}
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0,
        debounce_s=60.0,
        settle_s=30.0,
    )
    assert states["Living Room"] == "pending_open"

    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=30),
        debounce_s=60.0,
        settle_s=30.0,
    )
    assert states["Living Room"] == "pending_open"

    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=61),
        debounce_s=60.0,
        settle_s=30.0,
    )
    assert states["Living Room"] == "open"
    assert window_ov.is_window_override_active(states, "Living Room") is True


def test_state_machine_settle_and_bounce() -> None:
    tags = {"Living Room": ["lr_window"]}
    tag_values: dict[str, Any] = {"lr_window": True}
    states: dict[str, str] = {"Living Room": "closed"}
    since: dict[str, datetime] = {}
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0,
        debounce_s=10.0,
        settle_s=20.0,
    )
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=11),
        debounce_s=10.0,
        settle_s=20.0,
    )
    assert states["Living Room"] == "open"

    tag_values["lr_window"] = False
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=12),
        debounce_s=10.0,
        settle_s=20.0,
    )
    assert states["Living Room"] == "pending_closed"

    tag_values["lr_window"] = True
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=18),
        debounce_s=10.0,
        settle_s=20.0,
    )
    assert states["Living Room"] == "open"

    tag_values["lr_window"] = False
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=19),
        debounce_s=10.0,
        settle_s=20.0,
    )
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0 + timedelta(seconds=40),
        debounce_s=10.0,
        settle_s=20.0,
    )
    assert states["Living Room"] == "closed"


def test_state_machine_or_for_multi_sensor() -> None:
    tags = {"Living Room": ["win_1", "win_2"]}
    tag_values = {"win_1": False, "win_2": True}
    states: dict[str, str] = {"Living Room": "closed"}
    since: dict[str, datetime] = {}
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0,
        debounce_s=0.0,
        settle_s=0.0,
    )
    window_ov.update_window_state_machine(
        room_names=["Living Room"],
        window_tags=tags,
        tag_values=tag_values,
        states=states,
        since=since,
        now_utc=t0,
        debounce_s=0.0,
        settle_s=0.0,
    )
    assert states["Living Room"] == "open"


@pytest.mark.asyncio
async def test_runtime_debounce_clamps_heater(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    assert "Living Room" in runtime._window_tags
    window_tag = runtime._window_tags["Living Room"][0]

    runtime.actuator_outputs["living_heater"] = 1.0
    await publish_tag_in(runtime, window_tag, True)
    assert runtime.get_window_state("Living Room") == "pending_open"
    assert runtime.is_window_override_active("Living Room") is False
    assert runtime.actuator_outputs.get("living_heater") == 1.0

    await asyncio.sleep(0.12)
    assert runtime.get_window_state("Living Room") == "open"
    assert runtime.is_window_override_active("Living Room") is True
    assert runtime.actuator_outputs.get("living_heater") == 0.0

    states = runtime.hass_states()
    assert states["sensor.heating_assistant_living_room_window_state"]["state"] == "open"
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_brief_open_does_not_clamp(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    window_tag = runtime._window_tags["Living Room"][0]
    # start() already ran one control cycle; seed a known non-zero command.
    runtime.actuator_outputs["living_heater"] = 0.8

    await publish_tag_in(runtime, window_tag, True)
    assert runtime.get_window_state("Living Room") == "pending_open"
    assert runtime.is_window_override_active("Living Room") is False
    await publish_tag_in(runtime, window_tag, False)
    assert runtime.get_window_state("Living Room") == "closed"
    assert runtime.is_window_override_active("Living Room") is False
    # Brief open must not leave the heater forced off by override.
    assert runtime.actuator_outputs.get("living_heater") != 0.0
    await runtime.stop()


@pytest.mark.asyncio
async def test_control_cycle_disables_sources_under_override(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    runtime._window_state["Living Room"] = "open"
    ctx = runtime._schedule_control_context()
    assert "Living Heater" in ctx["disabled_sources"]
    await runtime.stop()


@pytest.mark.asyncio
async def test_push_override_resumes_shadow_mpc(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    runtime.actuator_outputs["living_heater"] = 0.0
    runtime._window_state["Living Room"] = "closed"
    runtime.control_engine.mpc_actions_by_tag = lambda: {"living_heater": 0.65}  # type: ignore[method-assign]
    await runtime.push_window_override()
    assert runtime.actuator_outputs["living_heater"] == pytest.approx(0.65)
    await runtime.stop()


@pytest.mark.asyncio
async def test_id_sample_flags_window_open_only_when_override_active(
    tmp_path: Path,
) -> None:
    """SWD-322: ID history flags the room only after heater shutoff triggers."""
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    room = "Living Room"
    runtime.room_temperatures[room] = 21.5

    runtime._window_state[room] = "pending_open"
    sample = runtime._take_identification_sample(force=True)
    assert sample is not None
    assert sample["window_open"][room] is False

    runtime._window_state[room] = "open"
    sample = runtime._take_identification_sample(force=True)
    assert sample is not None
    assert sample["window_open"][room] is True

    runtime._window_state[room] = "pending_closed"
    sample = runtime._take_identification_sample(force=True)
    assert sample is not None
    assert sample["window_open"][room] is True

    runtime._window_state[room] = "closed"
    sample = runtime._take_identification_sample(force=True)
    assert sample is not None
    assert sample["window_open"][room] is False
    await runtime.stop()
