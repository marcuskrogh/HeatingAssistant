from __future__ import annotations

import json

import pytest

from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.thermal_model import HouseModel
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def test_control_engine_imports_thermal_model() -> None:
    engine = ControlEngine({"rooms": [{"name": "living"}]})

    assert isinstance(engine.model, HouseModel)
    assert engine.mode == "proportional"


@pytest.mark.asyncio
async def test_runtime_averages_room_temperature_and_publishes_control(tmp_path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
            "rooms": [
                {
                    "name": "living",
                    "temp_tags": ["living_temp_1", "living_temp_2"],
                    "setpoint": 21.5,
                    "output_tags": ["living_heat"],
                }
            ],
        },
    )

    await runtime.start()
    await publish_tag_in(runtime, "living_temp_1", 19.0)
    await publish_tag_in(runtime, "living_temp_2", 21.0)

    assert runtime.room_temperature("living") == pytest.approx(20.0)

    control_messages = [
        payload
        for topic, payload, _qos, retain in bus.published
        if topic == "heatingassistant/haos/tag/living_heat/out" and retain
    ]
    assert control_messages
    assert json.loads(control_messages[-1])["value"] == pytest.approx(0.5)

    status_messages = [
        payload
        for topic, payload, _qos, retain in bus.published
        if topic == "heatingassistant/haos/status" and retain
    ]
    assert status_messages
    status = json.loads(status_messages[-1])
    assert status["rooms"][0]["temperature"] == pytest.approx(20.0)
    assert status["control"]["mode"] == "proportional"
