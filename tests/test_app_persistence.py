from __future__ import annotations

import json

import pytest

from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.persistence import load_config, load_state, save_config, save_state


pytestmark = pytest.mark.unit


def test_config_and_state_round_trip(tmp_path) -> None:
    save_config(tmp_path, {"instance_id": "haos", "mqtt_broker": "mqtt://broker"})
    save_state(tmp_path, {"tag_values": {"living": 21.0}})

    assert load_config(tmp_path) == {
        "instance_id": "haos",
        "mqtt_broker": "mqtt://broker",
    }
    assert load_state(tmp_path) == {"tag_values": {"living": 21.0}}


def test_missing_config_and_state_default_to_empty_dict(tmp_path) -> None:
    assert load_config(tmp_path) == {}
    assert load_state(tmp_path) == {}


def test_runtime_loads_options_and_exposes_status(tmp_path) -> None:
    save_config(
        tmp_path,
        {
            "instance_id": "haos",
            "mqtt_broker": "mqtt://broker",
            "bindings": [
                {"tag": "living_temp_1", "entity_id": "sensor.living_1", "direction": "in"}
            ],
            "rooms": [{"name": "living", "temp_tags": ["living_temp_1"]}],
        },
    )
    runtime = HeatingRuntime(tmp_path)

    status = runtime.status()

    assert status["instance_id"] == "haos"
    assert status["mqtt_broker"] == "mqtt://broker"
    assert status["bindings_count"] == 1
    assert status["rooms"][0]["name"] == "living"


@pytest.mark.asyncio
async def test_runtime_publishes_bindings_and_applies_multi_sensor_average(tmp_path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
            "bindings": [
                {"tag": "living_temp_1", "entity_id": "sensor.living_1", "direction": "in"},
                {"tag": "living_temp_2", "entity_id": "sensor.living_2", "direction": "in"},
                {"tag": "living_temp_3", "entity_id": "sensor.living_3", "direction": "in"},
            ],
            "rooms": [
                {
                    "name": "living",
                    "temp_tags": ["living_temp_1", "living_temp_2", "living_temp_3"],
                }
            ],
        },
    )

    await runtime.start()
    await publish_tag_in(runtime, "living_temp_1", 19.5)
    await publish_tag_in(runtime, "living_temp_2", 20.0)
    await publish_tag_in(runtime, "living_temp_3", 20.5)

    assert runtime.room_temperature("living") == pytest.approx(20.0)
    retained_bindings = [
        (topic, payload)
        for topic, payload, _qos, retain in bus.published
        if topic == "heatingassistant/haos/bindings" and retain
    ]
    assert retained_bindings
    assert json.loads(retained_bindings[-1][1])["bindings"][0]["tag"] == "living_temp_1"
    assert load_state(tmp_path)["room_temperatures"]["living"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_runtime_skips_bad_sensor_in_multi_sensor_average(tmp_path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [
                {
                    "name": "living",
                    "temp_tags": ["living_temp_1", "living_temp_2", "living_temp_3"],
                }
            ],
        },
    )

    await runtime.start()
    await publish_tag_in(runtime, "living_temp_1", 19.0)
    await publish_tag_in(runtime, "living_temp_2", 99.0, status="BAD", reason="stale")
    await publish_tag_in(runtime, "living_temp_3", 21.0)

    assert runtime.room_temperature("living") == pytest.approx(20.0)
