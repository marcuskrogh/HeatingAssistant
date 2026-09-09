"""Overview health must rebuild from currently configured sensors each cycle."""

from __future__ import annotations

import json
from typing import Any

import pytest

from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.supervisor import set_last_discovery_error
from heatingassistant.mqtt.topics import entities as entities_topic
from heatingassistant.persistence import save_state


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_mqtt_discovery_error() -> None:
    set_last_discovery_error(None)
    yield
    set_last_discovery_error(None)


def _room_options() -> dict[str, Any]:
    return {
        "instance_id": "haos",
        "rooms": [
            {
                "name": "Living Room",
                "temp_sensors": [
                    "sensor.living_room_temperature",
                    "sensor.living_room_temperature_2",
                ],
            }
        ],
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


def _heat_sources() -> list[dict[str, Any]]:
    return [
        {
            "name": "living_panel",
            "type": "electric_heater",
            "room": "Living Room",
            "max_power": 1500,
            "heater_entity": "switch.living_heater",
        }
    ]


async def _publish_catalog(
    runtime: HeatingRuntime,
    *,
    ts: float,
    states: dict[str, str],
) -> None:
    entities = [
        {
            "entity_id": entity_id,
            "name": entity_id,
            "state": state,
            "unit": "°C",
        }
        for entity_id, state in states.items()
    ]
    await runtime.bus.publish(
        entities_topic(runtime.instance_id),
        json.dumps({"ts": ts, "entities": entities}),
        qos=1,
        retain=True,
    )


def _sensor_module(health: dict[str, Any]) -> dict[str, Any]:
    return next(mod for mod in health["modules"] if mod["id"] == "sensors")


@pytest.mark.asyncio
async def test_leftover_inbound_bad_is_ignored_when_not_configured(tmp_path) -> None:
    save_state(
        tmp_path,
        {
            "tag_statuses": {
                "retired_temp": "BAD",
                "living_room_temp_1": "BAD",
                "living_room_temp_2": "BAD",
            },
            "tag_values": {
                "retired_temp": None,
                "living_room_temp_1": None,
                "living_room_temp_2": None,
            },
        },
    )
    options = _room_options()
    options["bindings"] = [
        {
            "tag": "living_room_temp_1",
            "entity_id": "sensor.living_room_temperature",
            "direction": "in",
        },
        {
            "tag": "retired_temp",
            "entity_id": "sensor.retired_temperature",
            "direction": "in",
        },
    ]
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=options)
    await runtime.start()
    await _publish_catalog(
        runtime,
        ts=100.0,
        states={
            "sensor.living_room_temperature": "21.0",
            "sensor.living_room_temperature_2": "21.2",
        },
    )

    inbound_tags = {item["tag"] for item in runtime.binding_dicts() if item["direction"] == "in"}
    health = runtime.system_health()
    sensors = _sensor_module(health)
    summary = health.get("issue_summary") or ""

    assert "retired_temp" not in inbound_tags
    assert "retired_temp" not in runtime.tag_statuses
    assert "retired_temp" not in sensors["detail"]
    assert "retired_temp" not in summary
    assert sensors["quality"] == "healthy"


@pytest.mark.asyncio
async def test_removed_room_sensor_drops_from_health_without_restart(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    await _publish_catalog(
        runtime,
        ts=100.0,
        states={
            "sensor.living_room_temperature": "21.0",
            "sensor.living_room_temperature_2": "21.2",
        },
    )
    await publish_tag_in(
        runtime,
        "living_room_temp_2",
        None,
        status="BAD",
        reason="entity_unavailable",
        ts=200.0,
    )
    assert "living_room_temp_2" in _sensor_module(runtime.system_health())["detail"]

    await runtime.update_config(
        {
            "rooms": [
                {
                    "name": "Living Room",
                    "temp_sensors": ["sensor.living_room_temperature"],
                    "setpoint": 21.0,
                }
            ],
            "heat_sources": _heat_sources(),
        }
    )

    health = runtime.system_health()
    sensors = _sensor_module(health)
    summary = health.get("issue_summary") or ""
    assert "living_room_temp_2" not in runtime.tag_statuses
    assert "living_room_temp_2" not in sensors["detail"]
    assert "living_room_temp_2" not in summary
    assert sensors["quality"] == "healthy"


@pytest.mark.asyncio
async def test_persisted_bad_clears_when_configured_tag_has_live_value(tmp_path) -> None:
    save_state(
        tmp_path,
        {
            "tag_statuses": {"living_room_temp_1": "BAD", "living_room_temp_2": "BAD"},
            "tag_values": {"living_room_temp_1": None, "living_room_temp_2": None},
            "tag_timestamps": {"living_room_temp_1": 10.0, "living_room_temp_2": 10.0},
        },
    )
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    await _publish_catalog(
        runtime,
        ts=100.0,
        states={
            "sensor.living_room_temperature": "19.5",
            "sensor.living_room_temperature_2": "19.7",
        },
    )

    health = runtime.system_health()
    assert runtime.tag_statuses["living_room_temp_1"] == "GOOD"
    assert runtime.tag_statuses["living_room_temp_2"] == "GOOD"
    assert _sensor_module(health)["quality"] == "healthy"
    assert "BAD" not in (health.get("issue_summary") or "")


@pytest.mark.asyncio
async def test_live_bad_numeric_stays_bad_through_health_refresh(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    await publish_tag_in(runtime, "living_room_temp_1", 19.0)
    await publish_tag_in(
        runtime,
        "living_room_temp_2",
        99.0,
        status="BAD",
        reason="stale",
    )

    health = runtime.system_health()
    assert runtime.tag_statuses["living_room_temp_1"] == "GOOD"
    assert runtime.tag_statuses["living_room_temp_2"] == "BAD"
    assert runtime.room_temperature("Living Room") == pytest.approx(19.0)
    assert _sensor_module(health)["quality"] == "warning"
    assert "living_room_temp_2" in (_sensor_module(health)["detail"] or "")


@pytest.mark.asyncio
async def test_configured_tag_without_live_value_still_warns(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()

    health = runtime.system_health()
    sensors = _sensor_module(health)
    assert sensors["quality"] == "warning"
    assert "living_room_temp_1" in sensors["detail"]
