"""SWD-280: climate heat-pump actuation + thermal measured power."""

from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from heatingassistant.app.actuation import (
    climate_hp_command,
    climate_write_payload,
    number_write_payload,
    resolve_hp_hvac_mode,
    switch_write_payload,
)
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.heat_sources import HeatPump
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload, tag_out


def test_resolve_hp_hvac_mode_prefers_supported_cool() -> None:
    assert resolve_hp_hvac_mode("cool", ["heat", "cool", "off"]) == "cool"
    assert resolve_hp_hvac_mode("cool", ["dry", "fan_only"]) == "dry"
    assert resolve_hp_hvac_mode("heat_cool", ["auto", "heat"]) == "auto"


def test_climate_hp_command_full_cooling_lowers_setpoint() -> None:
    hp = HeatPump(
        name="hp",
        room="Living",
        max_power=5000.0,
        cop_rated=3.5,
        cooling_cop=2.5,
        hvac_mode="heat_cool",
        delta_sat=3.0,
    )
    cmd = climate_hp_command(hp, -1.0, internal_temp=24.0, supported_modes=["heat_cool", "off"])
    assert cmd["hvac_mode"] == "heat_cool"
    assert cmd["temperature"] == pytest.approx(hp.target_temperature(-1.0, 24.0))
    assert cmd["temperature"] < 24.0


def test_climate_write_payload_turns_off_when_disabled() -> None:
    hp = HeatPump(name="hp", room="Living", max_power=5000.0, cop_rated=3.5)
    cmd = climate_write_payload(
        hp,
        -1.0,
        enabled=False,
        internal_temp=24.0,
        outdoor_temp=20.0,
        room_temp=24.0,
        room_setpoint=22.0,
    )
    assert cmd == {"hvac_mode": "off"}


def test_number_and_switch_payloads() -> None:
    assert number_write_payload(0.6, enabled=True) == 60.0
    assert number_write_payload(-0.5, enabled=True) == 0.0
    assert number_write_payload(1.0, enabled=False) == 0.0
    assert switch_write_payload(0.6, enabled=True) is True
    assert switch_write_payload(-1.0, enabled=True) is False
    assert switch_write_payload(1.0, enabled=False) is False


@pytest.mark.asyncio
async def test_runtime_publishes_climate_command_not_raw_fraction(tmp_path) -> None:
    bus = InMemoryMqttBus()
    published: list[tuple[str, str]] = []

    async def capture(topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        published.append((topic, payload if isinstance(payload, str) else payload.decode()))

    bus.publish = capture  # type: ignore[method-assign]

    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
            "system_enabled": True,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "temp_sensors": ["sensor.living_temp"],
                    "enabled": True,
                }
            ],
            "heat_sources": [
                {
                    "name": "living_hp",
                    "room": "Living Room",
                    "type": "heat_pump",
                    "max_power": 5000.0,
                    "cop_rated": 3.5,
                    "cooling_cop": 2.5,
                    "hvac_mode": "heat_cool",
                    "delta_sat": 3.0,
                    "heater_entity": "climate.living_hp",
                    "output_tag": "living_hp_heat",
                }
            ],
        },
    )
    # Seed climate feedback (internal temperature).
    runtime.tag_attributes["living_hp_heat_state"] = {
        "current_temperature": 24.0,
        "hvac_modes": ["heat_cool", "cool", "heat", "off"],
    }
    runtime.room_temperatures["Living Room"] = 24.0
    runtime.actuator_outputs["living_hp_heat"] = -1.0

    await runtime.publish_actuator_outputs()

    topic = tag_out("haos", "living_hp_heat")
    assert published
    assert published[0][0] == topic
    payload = MqttTagPayload.decode(published[0][1])
    assert isinstance(payload.value, dict)
    assert payload.value["hvac_mode"] == "heat_cool"
    expected = runtime.control_engine.heat_sources[0].target_temperature(-1.0, 24.0)
    assert payload.value["temperature"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_runtime_publishes_number_percent(tmp_path) -> None:
    bus = InMemoryMqttBus()
    published: list[str] = []

    async def capture(topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        published.append(payload if isinstance(payload, str) else payload.decode())

    bus.publish = capture  # type: ignore[method-assign]

    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
            "system_enabled": True,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "enabled": True}],
            "heat_sources": [
                {
                    "name": "living_heater",
                    "room": "Living Room",
                    "type": "electric",
                    "max_power": 2000.0,
                    "heater_entity": "number.living_heater",
                    "output_tag": "living_heater",
                }
            ],
        },
    )
    runtime.actuator_outputs["living_heater"] = 0.75
    await runtime.publish_actuator_outputs()
    payload = MqttTagPayload.decode(published[0])
    assert payload.value == pytest.approx(75.0)


def test_room_power_uses_thermal_watts_not_fraction(tmp_path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "system_enabled": True,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "enabled": True}],
            "heat_sources": [
                {
                    "name": "living_hp",
                    "room": "Living Room",
                    "type": "heat_pump",
                    "max_power": 5000.0,
                    "cop_rated": 3.5,
                    "cooling_cop": 2.5,
                    "cooling_efficiency": 1.0,
                    "hvac_mode": "heat_cool",
                    "heater_entity": "climate.living_hp",
                    "output_tag": "living_hp_heat",
                }
            ],
            "outdoor_temp_entity": "sensor.outdoor",
        },
    )
    runtime.tag_values["outdoor_temp"] = 25.0
    runtime.tag_statuses["outdoor_temp"] = "GOOD"
    runtime.actuator_outputs["living_hp_heat"] = -1.0

    power = runtime._room_power("Living Room")
    hp = runtime.control_engine.heat_sources[0]
    expected = hp.display_smooth_thermal_power(-1.0, 25.0)
    assert power == pytest.approx(expected)
    # Max cooling should be kilowatts of thermal removal, not -1 "W".
    assert power < -1000.0


def test_entity_wiring_adds_climate_state_feedback_tag(tmp_path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [{"name": "Living Room", "setpoint": 22.0}],
            "heat_sources": [
                {
                    "name": "living_hp",
                    "room": "Living Room",
                    "type": "heat_pump",
                    "heater_entity": "climate.living_hp",
                    "output_tag": "living_hp_heat",
                }
            ],
        },
    )
    source = runtime.options["heat_sources"][0]
    assert source["output_tag"] == "living_hp_heat"
    assert source["state_tag"] == "living_hp_heat_state"
    directions = {
        (b["tag"], b["direction"]) for b in runtime.options["bindings"] if isinstance(b, dict)
    }
    assert ("living_hp_heat", "out") in directions
    assert ("living_hp_heat_state", "in") in directions


@pytest.mark.asyncio
async def test_thin_bridge_writes_climate_mode_and_temperature() -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake_call(domain: str, service: str, data: dict, blocking: bool = False) -> None:
        calls.append((domain, service, data))

    hass = MagicMock()
    hass.services.async_call = AsyncMock(side_effect=fake_call)

    fake_components = MagicMock()
    fake_components.mqtt = MagicMock()
    fake_ha = MagicMock()
    fake_ha.components = fake_components
    fake_ha.config_entries = MagicMock()
    fake_ha.const = MagicMock(
        SERVICE_TURN_OFF="turn_off",
        SERVICE_TURN_ON="turn_on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
    )
    fake_ha.core = MagicMock()
    fake_ha.helpers = MagicMock()
    fake_ha.helpers.event = MagicMock(async_track_state_change_event=MagicMock())

    with patch.dict(
        "sys.modules",
        {
            "homeassistant": fake_ha,
            "homeassistant.components": fake_components,
            "homeassistant.config_entries": fake_ha.config_entries,
            "homeassistant.const": fake_ha.const,
            "homeassistant.core": fake_ha.core,
            "homeassistant.helpers": fake_ha.helpers,
            "homeassistant.helpers.event": fake_ha.helpers.event,
        },
    ):
        for name in list(sys.modules):
            if name.startswith("custom_components.heating_assistant"):
                del sys.modules[name]
        thin_init = importlib.import_module("custom_components.heating_assistant.__init__")
        manager = thin_init._BridgeManager(
            hass, MagicMock(data={"instance_id": "default"})
        )
        await manager._write_entity(
            "climate.living_hp",
            {"hvac_mode": "heat_cool", "temperature": 21.0},
        )
        await manager._write_entity("climate.living_hp", {"hvac_mode": "off"})
        # Defense: signed fraction must not turn switch on.
        assert thin_init._truthy(-1.0) is False
        assert thin_init._truthy(0.6) is True

    assert calls[0][:2] == ("climate", "set_hvac_mode")
    assert calls[0][2]["hvac_mode"] == "heat_cool"
    assert calls[1][:2] == ("climate", "set_temperature")
    assert calls[1][2]["temperature"] == pytest.approx(21.0)
    assert calls[2][:2] == ("climate", "set_hvac_mode")
    assert calls[2][2]["hvac_mode"] == "off"


@pytest.mark.asyncio
async def test_thin_bridge_publishes_climate_feedback_attrs() -> None:
    ha_mqtt = MagicMock()
    published: list[dict[str, Any]] = []

    async def fake_publish(*args: Any, **kwargs: Any) -> None:
        published.append({"args": args, "kwargs": kwargs})

    ha_mqtt.async_publish = AsyncMock(side_effect=fake_publish)

    fake_components = MagicMock()
    fake_components.mqtt = ha_mqtt
    fake_ha = MagicMock()
    fake_ha.components = fake_components
    fake_ha.config_entries = MagicMock()
    fake_ha.const = MagicMock(
        SERVICE_TURN_OFF="turn_off",
        SERVICE_TURN_ON="turn_on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
    )
    fake_ha.core = MagicMock()
    fake_ha.helpers = MagicMock()
    fake_ha.helpers.event = MagicMock(async_track_state_change_event=MagicMock())

    with patch.dict(
        "sys.modules",
        {
            "homeassistant": fake_ha,
            "homeassistant.components": fake_components,
            "homeassistant.config_entries": fake_ha.config_entries,
            "homeassistant.const": fake_ha.const,
            "homeassistant.core": fake_ha.core,
            "homeassistant.helpers": fake_ha.helpers,
            "homeassistant.helpers.event": fake_ha.helpers.event,
        },
    ):
        for name in list(sys.modules):
            if name.startswith("custom_components.heating_assistant"):
                del sys.modules[name]
        thin_init = importlib.import_module("custom_components.heating_assistant.__init__")
        state = MagicMock()
        state.state = "cool"
        state.domain = "climate"
        state.entity_id = "climate.living_hp"
        state.attributes = {
            "current_temperature": 24.2,
            "temperature": 22.0,
            "hvac_modes": ["heat_cool", "cool", "off"],
            "hvac_action": "cooling",
        }
        hass = MagicMock()
        manager = thin_init._BridgeManager(
            hass, MagicMock(data={"instance_id": "default"})
        )
        await manager._publish_entity_state("living_hp_heat_state", state)

    payload = MqttTagPayload.decode(published[0]["args"][2])
    assert payload.value == "cool"
    assert payload.attributes is not None
    assert payload.attributes["current_temperature"] == pytest.approx(24.2)
    assert payload.attributes["hvac_modes"] == ["heat_cool", "cool", "off"]

@pytest.mark.asyncio
async def test_climate_state_feedback_does_not_retrigger_control_cycle(tmp_path) -> None:
    """Climate tag/in must update attrs without re-running MPC (thrash guard)."""

    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
            "system_enabled": True,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "enabled": True}],
            "heat_sources": [
                {
                    "name": "living_hp",
                    "room": "Living Room",
                    "type": "heat_pump",
                    "max_power": 5000.0,
                    "heater_entity": "climate.living_hp",
                    "output_tag": "living_hp_heat",
                }
            ],
        },
    )
    runtime.actuator_outputs["living_hp_heat"] = -1.0
    cycles = {"n": 0}

    async def counted() -> dict[str, float]:
        cycles["n"] += 1
        return dict(runtime.actuator_outputs)

    runtime.run_control_cycle = counted  # type: ignore[method-assign]

    await runtime._handle_tag_message(
        "heatingassistant/haos/tag/living_hp_heat_state/in",
        MqttTagPayload(
            value="cool",
            status="GOOD",
            attributes={"current_temperature": 24.5, "hvac_modes": ["cool", "off"]},
        ).encode(),
        qos=0,
        retain=False,
    )
    assert cycles["n"] == 0
    assert runtime.tag_attributes["living_hp_heat_state"]["current_temperature"] == pytest.approx(
        24.5
    )

