"""SWD-270: Supervisor MQTT service discovery for Mosquitto credentials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heatingassistant.app.__main__ import merge_supervisor_options
from heatingassistant.mqtt.bridge import create_mqtt_bus
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.supervisor import (
    apply_supervisor_mqtt_discovery,
    normalize_mqtt_broker,
)
from heatingassistant.persistence import save_config


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("core-mosquitto", "core-mosquitto"),
        (" mqtt://core-mosquitto:1883 ", "core-mosquitto"),
        ("mqtts://user:pass@broker.local:8883/path", "broker.local"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_mqtt_broker(raw: Any, expected: str | None) -> None:
    assert normalize_mqtt_broker(raw) == expected


def test_discovery_fills_blank_credentials_only() -> None:
    options = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "rooms": [{"name": "Living Room"}],
    }
    discovered = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "addons",
        "mqtt_password": "secret-token",
        "mqtt_source": "supervisor",
    }
    merged = apply_supervisor_mqtt_discovery(options, discovered=discovered)
    assert merged["mqtt_username"] == "addons"
    assert merged["mqtt_password"] == "secret-token"
    assert merged["mqtt_source"] == "supervisor"
    assert merged["rooms"] == [{"name": "Living Room"}]


def test_explicit_credentials_override_discovery() -> None:
    options = {
        "mqtt_broker": "my-broker",
        "mqtt_username": "custom",
        "mqtt_password": "custom-pass",
    }
    discovered = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "addons",
        "mqtt_password": "secret-token",
    }
    merged = apply_supervisor_mqtt_discovery(options, discovered=discovered)
    assert merged["mqtt_broker"] == "my-broker"
    assert merged["mqtt_username"] == "custom"
    assert merged["mqtt_password"] == "custom-pass"
    assert merged["mqtt_source"] == "options"


def test_merge_supervisor_options_discovers_mqtt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_config(tmp_path, {"rooms": [{"name": "Studio"}]})
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "instance_id": "haos",
                "mqtt_broker": "core-mosquitto",
                "mqtt_port": 1883,
                "mqtt_username": "",
                "mqtt_password": "",
            }
        ),
        encoding="utf-8",
    )

    def fake_fetch(**kwargs: Any) -> dict[str, Any]:
        return {
            "mqtt_broker": "core-mosquitto",
            "mqtt_port": 1883,
            "mqtt_username": "addons",
            "mqtt_password": "from-supervisor",
            "mqtt_source": "supervisor",
        }

    monkeypatch.setattr(
        "heatingassistant.mqtt.supervisor.fetch_supervisor_mqtt_service",
        fake_fetch,
    )
    merged = merge_supervisor_options(tmp_path, options_path)
    assert merged["mqtt_username"] == "addons"
    assert merged["mqtt_password"] == "from-supervisor"
    assert merged["mqtt_source"] == "supervisor"
    assert merged["rooms"] == [{"name": "Studio"}]


def test_create_mqtt_bus_strips_mqtt_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakePahoBus:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setattr("heatingassistant.mqtt.paho_bus.PahoMqttBus", FakePahoBus)
    bus = create_mqtt_bus(
        {
            "mqtt_broker": "mqtt://core-mosquitto:1883",
            "mqtt_port": 1883,
            "mqtt_username": "addons",
            "mqtt_password": "x",
        }
    )
    assert not isinstance(bus, InMemoryMqttBus)
    assert created[0]["host"] == "core-mosquitto"
    assert created[0]["username"] == "addons"
