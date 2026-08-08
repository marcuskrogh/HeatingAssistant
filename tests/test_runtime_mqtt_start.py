"""SWD-268: runtime must start even when MQTT publishes fail."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


class FlakyMqttBus(InMemoryMqttBus):
    """Bus that rejects publishes until armed."""

    def __init__(self) -> None:
        super().__init__()
        self.allow_publish = False
        self.connect_handlers: list[Any] = []
        self.connected = False

    def add_connect_handler(self, handler: Any) -> None:
        self.connect_handlers.append(handler)

    async def publish(
        self,
        topic: str,
        payload: str | bytes,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        if not self.allow_publish:
            raise RuntimeError("MQTT client is not connected")
        await super().publish(topic, payload, qos=qos, retain=retain)


@pytest.mark.asyncio
async def test_runtime_start_survives_mqtt_publish_failure(tmp_path) -> None:
    bus = FlakyMqttBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={"instance_id": "haos", "mqtt_broker": "core-mosquitto"},
    )

    await runtime.start()

    assert runtime._started is True
    status = runtime.status()
    assert status["status"] == "ok"
    assert status["mqtt_connected"] is False
    assert status["started"] is True

    bus.allow_publish = True
    bus.connected = True
    for handler in bus.connect_handlers:
        result = handler()
        if asyncio.iscoroutine(result):
            await result

    assert any(topic.endswith("/bindings") for topic, *_ in bus.published)
    assert any(topic.endswith("/status") for topic, *_ in bus.published)
