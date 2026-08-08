"""MQTT bus factory and Paho client wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from heatingassistant.mqtt.bridge import InMemoryMqttBus, create_mqtt_bus
from heatingassistant.mqtt.paho_bus import PahoMqttBus


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


class _FakePublishInfo:
    def wait_for_publish(self, timeout: float = 10) -> None:
        return None


@pytest.fixture
def fake_mqtt_client(monkeypatch: pytest.MonkeyPatch):
    instances: list[SimpleNamespace] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.on_connect = None
            self.on_message = None
            self.subscriptions: list[tuple[str, int]] = []
            self.published: list[tuple[str, str | bytes, int, bool]] = []
            instances.append(self)

        def username_pw_set(self, username: str, password: str) -> None:
            self.username = username
            self.password = password

        def connect_async(self, host: str, port: int, keepalive: int = 60) -> None:
            self.host = host
            self.port = port

        def loop_start(self) -> None:
            if self.on_connect is not None:
                self.on_connect(self, None, {}, 0)

        def subscribe(self, topic: str, qos: int = 1) -> None:
            self.subscriptions.append((topic, qos))

        def publish(self, topic: str, payload: str | bytes, qos: int = 1, retain: bool = False):
            self.published.append((topic, payload, qos, retain))
            return _FakePublishInfo()

        def loop_stop(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

    callback_api_version = SimpleNamespace(VERSION1="v1")
    monkeypatch.setattr(
        "heatingassistant.mqtt.paho_bus.mqtt.CallbackAPIVersion",
        callback_api_version,
    )
    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.mqtt.Client", FakeClient)
    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.mqtt.MQTTv311", "MQTTv311")
    return instances


@pytest.mark.asyncio
async def test_paho_bus_publish_and_subscribe(fake_mqtt_client) -> None:
    seen: list[tuple[str, str | bytes]] = []

    def handler(topic: str, payload: str | bytes, qos: int, retain: bool) -> None:
        seen.append((topic, payload))

    bus = PahoMqttBus(host="core-mosquitto", username="user", password="secret")
    unsub = bus.subscribe("heatingassistant/haos/tag/+/in", handler)

    await bus.publish("heatingassistant/haos/tag/living_temp/in", b'{"value":21.0}')

    client = fake_mqtt_client[0]
    assert client.username == "user"
    assert ("heatingassistant/haos/tag/+/in", 1) in client.subscriptions
    assert client.published[-1][0] == "heatingassistant/haos/tag/living_temp/in"

    client.on_message(
        client,
        None,
        SimpleNamespace(
            topic="heatingassistant/haos/tag/living_temp/in",
            payload=b"22.5",
            qos=1,
            retain=False,
        ),
    )
    assert seen == [("heatingassistant/haos/tag/living_temp/in", b"22.5")]

    unsub()
    bus.close()


@pytest.mark.asyncio
async def test_paho_bus_dispatches_async_handlers(fake_mqtt_client) -> None:
    seen: list[float] = []

    async def handler(topic: str, payload: str | bytes, qos: int, retain: bool) -> None:
        await asyncio.sleep(0)
        seen.append(21.0)

    bus = PahoMqttBus(host="core-mosquitto")
    bus.subscribe("heatingassistant/haos/tag/+/in", handler)

    fake_mqtt_client[0].on_message(
        fake_mqtt_client[0],
        None,
        SimpleNamespace(
            topic="heatingassistant/haos/tag/living_temp/in",
            payload=b"1",
            qos=1,
            retain=True,
        ),
    )
    assert seen == [21.0]
    bus.close()


def test_paho_bus_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class HangingClient:
        def __init__(self, **kwargs: object) -> None:
            self.on_connect = None
            self.on_message = None

        def username_pw_set(self, *args: object) -> None:
            return None

        def connect_async(self, *args: object, **kwargs: object) -> None:
            return None

        def loop_start(self) -> None:
            return None

    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.mqtt.Client", HangingClient)
    monkeypatch.setattr(
        "heatingassistant.mqtt.paho_bus.mqtt.CallbackAPIVersion",
        SimpleNamespace(VERSION1="v1"),
    )
    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.mqtt.MQTTv311", "MQTTv311")

    with pytest.raises(TimeoutError, match="did not connect"):
        PahoMqttBus(host="core-mosquitto", connect_timeout_s=0.05)


def test_paho_bus_publish_requires_connection(fake_mqtt_client) -> None:
    bus = PahoMqttBus(host="core-mosquitto")
    bus._connected.clear()

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(bus.publish("topic", "payload"))

    bus.close()
