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
    redact_mqtt_secrets,
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
        "mqtt_port": 1883,
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


def test_partial_credentials_not_half_filled_from_supervisor() -> None:
    """Username/password are a pair — do not fill only the blank half."""
    options = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "manual-user",
        "mqtt_password": "",
    }
    discovered = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "addons",
        "mqtt_password": "secret-token",
    }
    merged = apply_supervisor_mqtt_discovery(options, discovered=discovered)
    assert merged["mqtt_username"] == "manual-user"
    assert merged["mqtt_password"] == ""
    assert merged["mqtt_source"] == "options"


def test_fallback_restores_durable_secrets_when_discovery_fails() -> None:
    options = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
    }
    fallback = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "addons",
        "mqtt_password": "persisted-secret",
    }
    merged = apply_supervisor_mqtt_discovery(
        options, discovered={}, fallback=fallback
    )
    assert merged["mqtt_username"] == "addons"
    assert merged["mqtt_password"] == "persisted-secret"
    assert merged["mqtt_source"] == "options"


def test_mqtt_source_supervisor_only_when_discovery_fills() -> None:
    options = {
        "mqtt_broker": "mqtt://10.0.0.5:1883",
        "mqtt_port": 1884,
        "mqtt_username": "manual",
        "mqtt_password": "keep-me",
    }
    discovered = {
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "addons",
        "mqtt_password": "secret-token",
    }
    merged = apply_supervisor_mqtt_discovery(options, discovered=discovered)
    assert merged["mqtt_broker"] == "10.0.0.5"
    assert merged["mqtt_port"] == 1884
    assert merged["mqtt_source"] == "options"


def test_redact_mqtt_secrets() -> None:
    payload = {
        "mqtt_password": "s3cret",
        "nested": {"mqtt_password": "also-secret", "ok": 1},
        "list": [{"mqtt_password": "x"}, {"other": "y"}],
    }
    redacted = redact_mqtt_secrets(payload)
    assert redacted["mqtt_password"] == "***"
    assert redacted["nested"]["mqtt_password"] == "***"
    assert redacted["nested"]["ok"] == 1
    assert redacted["list"][0]["mqtt_password"] == "***"
    assert redacted["list"][1]["other"] == "y"
    assert payload["mqtt_password"] == "s3cret"


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


def test_merge_blank_options_keeps_durable_creds_when_discovery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_config(
        tmp_path,
        {
            "mqtt_broker": "core-mosquitto",
            "mqtt_username": "addons",
            "mqtt_password": "persisted-secret",
            "rooms": [{"name": "Studio"}],
        },
    )
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "mqtt_broker": "core-mosquitto",
                "mqtt_port": 1883,
                "mqtt_username": "",
                "mqtt_password": "",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "heatingassistant.mqtt.supervisor.fetch_supervisor_mqtt_service",
        lambda **kwargs: None,
    )
    merged = merge_supervisor_options(tmp_path, options_path)
    assert merged["mqtt_username"] == "addons"
    assert merged["mqtt_password"] == "persisted-secret"
    assert merged["mqtt_source"] == "options"
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
