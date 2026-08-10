"""SWD-300: system health quality enum aggregation."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.app.system_health import SystemQuality, evaluate_system_health
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def test_evaluate_system_health_error_when_mqtt_down() -> None:
    health = evaluate_system_health(
        mqtt_connected=False,
        mqtt_last_error="rc=5",
        mqtt_discovery_error=None,
        tag_statuses={},
        control_mode="mpc",
        fallback_reason=None,
        bindings_count=2,
        entity_catalog_count=10,
        started=True,
        uptime_s=12.0,
    )
    assert health["quality"] == SystemQuality.ERROR.value
    assert health["issue_summary"]
    assert "MQTT" in health["issue_summary"]


def test_evaluate_system_health_warning_for_bad_tags_and_fallback() -> None:
    health = evaluate_system_health(
        mqtt_connected=True,
        mqtt_last_error=None,
        mqtt_discovery_error=None,
        tag_statuses={"living_room_temp": "BAD"},
        control_mode="fallback",
        fallback_reason="controller compute failed",
        bindings_count=3,
        entity_catalog_count=50,
        started=True,
        uptime_s=60.0,
    )
    assert health["quality"] == SystemQuality.WARNING.value
    assert any(m["id"] == "sensors" and m["quality"] == "warning" for m in health["modules"])
    assert any(m["id"] == "control" and m["quality"] == "warning" for m in health["modules"])


def test_evaluate_system_health_healthy_baseline() -> None:
    health = evaluate_system_health(
        mqtt_connected=True,
        mqtt_last_error=None,
        mqtt_discovery_error=None,
        tag_statuses={"living_room_temp": "GOOD"},
        control_mode="mpc",
        fallback_reason=None,
        bindings_count=4,
        entity_catalog_count=100,
        started=True,
        uptime_s=120.0,
    )
    assert health["quality"] == SystemQuality.HEALTHY.value
    assert health["issue_summary"] is None


@pytest.mark.asyncio
async def test_runtime_status_exposes_system_health(tmp_path: Path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options={"instance_id": "haos"})
    await runtime.start()
    status = runtime.status()
    assert status["quality"] in {"healthy", "warning", "error"}
    assert "system_health" in status
    assert "modules" in status["system_health"]
    summary = runtime.hass_states()["sensor.heating_assistant_system_summary"]
    assert summary["attributes"]["system_quality"] == status["quality"]
    assert "uptime_s" in summary["attributes"]
    assert isinstance(summary["attributes"].get("modules"), list)
    assert summary["attributes"]["modules"]
