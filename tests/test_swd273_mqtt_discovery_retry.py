"""SWD-273: retry Supervisor MQTT discovery + SSL/endpoint apply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import create_mqtt_bus
from heatingassistant.mqtt.supervisor import (
    apply_supervisor_mqtt_discovery,
    fetch_supervisor_mqtt_service,
    get_last_discovery_error,
    set_last_discovery_error,
)


pytestmark = pytest.mark.unit


def test_blank_creds_take_full_supervisor_endpoint_including_ssl() -> None:
    options = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
    }
    discovered = {
        "mqtt_broker": "172.30.33.0",
        "mqtt_port": 8883,
        "mqtt_username": "addons",
        "mqtt_password": "secret-token",
        "mqtt_ssl": True,
        "mqtt_source": "supervisor",
    }
    merged = apply_supervisor_mqtt_discovery(options, discovered=discovered)
    assert merged["mqtt_broker"] == "172.30.33.0"
    assert merged["mqtt_port"] == 8883
    assert merged["mqtt_ssl"] is True
    assert merged["mqtt_username"] == "addons"
    assert merged["mqtt_password"] == "secret-token"
    assert merged["mqtt_source"] == "supervisor"


def test_explicit_creds_keep_user_broker_and_ignore_ssl_from_supervisor() -> None:
    options = {
        "mqtt_broker": "my-broker",
        "mqtt_port": 1883,
        "mqtt_username": "custom",
        "mqtt_password": "custom-pass",
        "mqtt_ssl": False,
    }
    discovered = {
        "mqtt_broker": "172.30.33.0",
        "mqtt_port": 8883,
        "mqtt_username": "addons",
        "mqtt_password": "secret-token",
        "mqtt_ssl": True,
    }
    merged = apply_supervisor_mqtt_discovery(options, discovered=discovered)
    assert merged["mqtt_broker"] == "my-broker"
    assert merged["mqtt_port"] == 1883
    assert merged["mqtt_ssl"] is False
    assert merged["mqtt_username"] == "custom"
    assert merged["mqtt_source"] == "options"


def test_fetch_supervisor_records_service_not_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_last_discovery_error(None)

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps({"result": "error", "message": "Service not enabled"}).encode()

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(*args: object, **kwargs: object) -> FakeResponse:
        from urllib.error import HTTPError

        raise HTTPError(
            "http://supervisor/services/mqtt",
            400,
            "Bad Request",
            hdrs=None,  # type: ignore[arg-type]
            fp=FakeResponse(),  # type: ignore[arg-type]
        )

    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    monkeypatch.setattr("heatingassistant.mqtt.supervisor.urlopen", fake_urlopen)
    assert fetch_supervisor_mqtt_service() is None
    assert "Service not enabled" in (get_last_discovery_error() or "")


def test_create_mqtt_bus_passes_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakePahoBus:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.PahoMqttBus", FakePahoBus)
    create_mqtt_bus(
        {
            "mqtt_broker": "core-mosquitto",
            "mqtt_port": 8883,
            "mqtt_username": "addons",
            "mqtt_password": "x",
            "mqtt_ssl": True,
        }
    )
    assert created[0]["ssl"] is True
    assert created[0]["port"] == 8883


def test_paho_reconfigure_updates_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[Any] = []

    class FakeInfo:
        def wait_for_publish(self, timeout: float = 10) -> None:
            return None

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.username = None
            self.password = None
            self.tls = False
            self.connected_to: tuple[str, int] | None = None
            self._on_connect = None
            clients.append(self)

        def username_pw_set(self, username: str, password: str = "") -> None:
            self.username = username
            self.password = password

        def tls_set(self, *args: object, **kwargs: object) -> None:
            self.tls = True

        def tls_insecure_set(self, value: bool) -> None:
            self.tls_insecure = value

        def reconnect_delay_set(self, **kwargs: object) -> None:
            return None

        def connect_async(self, host: str, port: int, keepalive: int = 60) -> None:
            self.connected_to = (host, port)

        def loop_start(self) -> None:
            return None

        def loop_stop(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

        def subscribe(self, *args: object, **kwargs: object) -> None:
            return None

        def publish(self, *args: object, **kwargs: object) -> FakeInfo:
            return FakeInfo()

        def __setattr__(self, name: str, value: object) -> None:
            object.__setattr__(self, name, value)

    class FakeMqttModule:
        CallbackAPIVersion = MagicMock(VERSION1=1)
        MQTTv311 = 4

        @staticmethod
        def Client(**kwargs: object) -> FakeClient:
            return FakeClient(**kwargs)

    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.mqtt", FakeMqttModule)
    from heatingassistant.mqtt.paho_bus import PahoMqttBus

    bus = PahoMqttBus(host="core-mosquitto", port=1883, username=None, password=None, ssl=False)
    assert clients[0].connected_to == ("core-mosquitto", 1883)
    assert clients[0].username is None

    bus.reconfigure(
        host="172.30.33.0",
        port=8883,
        username="addons",
        password="secret",
        ssl=True,
    )
    assert len(clients) == 2
    assert clients[1].connected_to == ("172.30.33.0", 8883)
    assert clients[1].username == "addons"
    assert clients[1].password == "secret"
    assert clients[1].tls is True
    bus.close()


@pytest.mark.asyncio
async def test_runtime_discovery_retry_reconfigures_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconfigured: list[dict[str, object]] = []

    class FakeBus:
        def __init__(self) -> None:
            self.connected = False
            self.last_error = "not authorised (rc=5)"
            self._subs: list[Any] = []

        def subscribe(self, topic_filter: str, handler: Any) -> Any:
            self._subs.append((topic_filter, handler))
            return lambda: None

        def add_connect_handler(self, handler: Any) -> None:
            return None

        async def publish(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("MQTT client is not connected")

        def reconfigure(self, **kwargs: object) -> None:
            reconfigured.append(kwargs)
            self.connected = True
            self.last_error = None

    monkeypatch.setattr(
        "heatingassistant.app.runtime.apply_supervisor_mqtt_discovery",
        lambda options, fallback=None: {
            **dict(options),
            "mqtt_broker": "172.30.33.0",
            "mqtt_port": 1883,
            "mqtt_username": "addons",
            "mqtt_password": "from-supervisor",
            "mqtt_ssl": False,
            "mqtt_source": "supervisor",
        },
    )

    bus = FakeBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,  # type: ignore[arg-type]
        options={
            "instance_id": "default",
            "mqtt_broker": "core-mosquitto",
            "mqtt_port": 1883,
            "mqtt_username": "",
            "mqtt_password": "",
            "mqtt_source": "options",
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
        },
    )
    # Avoid starting the background thread; call the apply helper directly.
    runtime._mqtt_discovery_retry = True
    await runtime.start()
    assert runtime.status()["mqtt_connected"] is False
    assert runtime.status()["mqtt_last_error"] == "not authorised (rc=5)"

    assert runtime._try_apply_supervisor_mqtt_discovery() is True
    assert reconfigured
    assert reconfigured[0]["host"] == "172.30.33.0"
    assert reconfigured[0]["username"] == "addons"
    assert runtime.options["mqtt_username"] == "addons"
    assert runtime.options["mqtt_source"] == "supervisor"
    assert runtime.status()["mqtt_connected"] is True
    await runtime.stop()
