"""MQTT bus factory and Paho client wiring."""

from __future__ import annotations

import pytest

from heatingassistant.mqtt.bridge import InMemoryMqttBus, create_mqtt_bus


pytestmark = pytest.mark.unit


def test_create_mqtt_bus_without_broker_uses_in_memory() -> None:
    bus = create_mqtt_bus({})
    assert isinstance(bus, InMemoryMqttBus)


def test_create_mqtt_bus_with_blank_broker_uses_in_memory() -> None:
    bus = create_mqtt_bus({"mqtt_broker": "   "})
    assert isinstance(bus, InMemoryMqttBus)


def test_create_mqtt_bus_with_broker_returns_paho_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakePahoBus:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setattr(
        "heatingassistant.mqtt.paho_bus.PahoMqttBus",
        FakePahoBus,
    )

    bus = create_mqtt_bus(
        {
            "mqtt_broker": "core-mosquitto",
            "mqtt_port": 1883,
            "mqtt_username": "user",
            "mqtt_password": "secret",
        }
    )

    assert isinstance(bus, FakePahoBus)
    assert created == [
        {
            "host": "core-mosquitto",
            "port": 1883,
            "username": "user",
            "password": "secret",
        }
    ]
