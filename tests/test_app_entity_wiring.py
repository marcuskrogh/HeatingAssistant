"""SWD-267: derive MQTT bindings/tags from configured HA entity IDs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_update_rooms_derives_temp_tags_and_inbound_bindings(tmp_path: Path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options={"instance_id": "haos"})
    await runtime.start()

    await runtime.update_config(
        {
            "rooms": [
                {
                    "name": "Living Room",
                    "temp_sensors": [
                        "sensor.living_room_temperature",
                        "sensor.living_room_temperature_2",
                    ],
                    "setpoint": 21.0,
                }
            ],
            "outdoor_temp_entity": "sensor.outdoor_temperature",
            "heat_sources": [
                {
                    "name": "living_panel",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500,
                    "heater_entity": "switch.living_heater",
                }
            ],
        }
    )

    room = runtime.options["rooms"][0]
    assert room["temp_tags"] == ["living_room_temp_1", "living_room_temp_2"]
    assert runtime.options["outdoor_temp_tag"] == "outdoor_temp"
    assert runtime.options["heat_sources"][0]["output_tag"] == "living_panel_heat"

    bindings = {item["entity_id"]: item for item in runtime.binding_dicts()}
    assert bindings["sensor.living_room_temperature"] == {
        "tag": "living_room_temp_1",
        "entity_id": "sensor.living_room_temperature",
        "direction": "in",
    }
    assert bindings["sensor.living_room_temperature_2"]["tag"] == "living_room_temp_2"
    assert bindings["sensor.outdoor_temperature"]["tag"] == "outdoor_temp"
    assert bindings["switch.living_heater"] == {
        "tag": "living_panel_heat",
        "entity_id": "switch.living_heater",
        "direction": "out",
    }

    retained = [
        json.loads(payload)
        for topic, payload, _qos, retain in bus.published
        if topic == "heatingassistant/haos/bindings" and retain
    ]
    assert retained
    assert {item["entity_id"] for item in retained[-1]["bindings"]} >= {
        "sensor.living_room_temperature",
        "switch.living_heater",
        "sensor.outdoor_temperature",
    }

    await publish_tag_in(runtime, "living_room_temp_1", 20.0)
    await publish_tag_in(runtime, "living_room_temp_2", 22.0)
    assert runtime.room_temperature("Living Room") == pytest.approx(21.0)

    states = runtime.hass_states()
    assert "sensor.living_room_temperature" in states
    assert states["sensor.living_room_temperature"]["attributes"]["heating_assistant_tag"] == (
        "living_room_temp_1"
    )


@pytest.mark.asyncio
async def test_tag_only_rooms_remain_compatible(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "rooms": [{"name": "living", "temp_tags": ["living_temp_1"]}],
            "bindings": [
                {
                    "tag": "living_temp_1",
                    "entity_id": "sensor.living",
                    "direction": "in",
                }
            ],
        },
    )
    assert runtime.options["rooms"][0]["temp_tags"] == ["living_temp_1"]
    assert runtime.binding_dicts() == [
        {
            "tag": "living_temp_1",
            "entity_id": "sensor.living",
            "direction": "in",
        }
    ]


def test_entity_picker_supports_manual_entity_id_entry() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "config"
        / "config-ui.js"
    ).read_text(encoding="utf-8")
    assert "isValidEntityId" in source
    assert "Use entity ID" in source
    assert "type a full entity ID" in source.lower() or "Type a full entity ID" in source
    assert "limitedCatalog" in source
