"""SWD-385: stale BAD tag quality must not stick when HA measurements are fine."""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload, entities as entities_topic
from heatingassistant.persistence import save_state


pytestmark = pytest.mark.unit


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
    }


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
async def test_catalog_clears_persisted_bad_tag_quality(tmp_path) -> None:
    save_state(
        tmp_path,
        {
            "tag_values": {
                "living_room_temp_1": None,
                "living_room_temp_2": None,
            },
            "tag_statuses": {
                "living_room_temp_1": "BAD",
                "living_room_temp_2": "BAD",
            },
            "tag_timestamps": {
                "living_room_temp_1": 10.0,
                "living_room_temp_2": 10.0,
            },
        },
    )
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    assert runtime.tag_statuses["living_room_temp_1"] == "BAD"

    await _publish_catalog(
        runtime,
        ts=100.0,
        states={
            "sensor.living_room_temperature": "21.4",
            "sensor.living_room_temperature_2": "21.6",
        },
    )

    assert runtime.tag_statuses["living_room_temp_1"] == "GOOD"
    assert runtime.tag_statuses["living_room_temp_2"] == "GOOD"
    assert runtime.tag_values["living_room_temp_1"] == pytest.approx(21.4)
    assert runtime.tag_values["living_room_temp_2"] == pytest.approx(21.6)
    assert runtime.room_temperature("Living Room") == pytest.approx(21.5)
    health = runtime.system_health()
    assert _sensor_module(health)["quality"] == "healthy"
    assert health["quality"] == "healthy"


@pytest.mark.asyncio
async def test_stale_retained_bad_is_ignored_after_catalog(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    await _publish_catalog(
        runtime,
        ts=100.0,
        states={"sensor.living_room_temperature": "20.5"},
    )
    assert runtime.tag_statuses["living_room_temp_1"] == "GOOD"

    await publish_tag_in(
        runtime,
        "living_room_temp_1",
        None,
        status="BAD",
        reason="entity_unavailable",
        ts=50.0,
    )

    assert runtime.tag_statuses["living_room_temp_1"] == "GOOD"
    assert runtime.tag_values["living_room_temp_1"] == pytest.approx(20.5)
    assert _sensor_module(runtime.system_health())["quality"] == "healthy"


@pytest.mark.asyncio
async def test_later_unavailable_bad_still_warns(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    await _publish_catalog(
        runtime,
        ts=100.0,
        states={"sensor.living_room_temperature": "20.5"},
    )

    await publish_tag_in(
        runtime,
        "living_room_temp_1",
        None,
        status="BAD",
        reason="entity_unavailable",
        ts=200.0,
    )

    assert runtime.tag_statuses["living_room_temp_1"] == "BAD"
    assert _sensor_module(runtime.system_health())["quality"] == "warning"
    assert runtime.system_health()["quality"] == "warning"


@pytest.mark.asyncio
async def test_unbound_leftover_bad_does_not_affect_health(tmp_path) -> None:
    save_state(
        tmp_path,
        {
            "tag_statuses": {
                "retired_temp": "BAD",
                "living_room_temp_1": "GOOD",
            }
        },
    )
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    assert "retired_temp" not in runtime.tag_statuses
    await runtime.start()
    await _publish_catalog(
        runtime,
        ts=100.0,
        states={"sensor.living_room_temperature": "21.0"},
    )
    assert runtime.system_health()["quality"] == "healthy"


@pytest.mark.asyncio
async def test_hass_states_uses_catalog_when_binding_stub_unknown(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_room_options())
    await runtime.start()
    stub = runtime.hass_states()["sensor.living_room_temperature"]
    assert stub["state"] == "unknown"

    await _publish_catalog(
        runtime,
        ts=100.0,
        states={"sensor.living_room_temperature": "19.75"},
    )
    live = runtime.hass_states()["sensor.living_room_temperature"]
    assert live["state"] == "19.75"


@pytest.mark.asyncio
async def test_bridge_republishes_inbound_tags_when_ha_started() -> None:
    ha_mqtt = MagicMock()
    published: list[dict[str, Any]] = []

    async def fake_publish(*args: Any, **kwargs: Any) -> None:
        published.append({"args": args, "kwargs": kwargs})

    ha_mqtt.async_publish = AsyncMock(side_effect=fake_publish)

    fake_components = MagicMock()
    fake_components.mqtt = ha_mqtt
    fake_ha = MagicMock()
    fake_ha.components = fake_components
    fake_ha.config_entries = MagicMock()
    fake_ha.const = MagicMock(
        SERVICE_TURN_OFF="turn_off",
        SERVICE_TURN_ON="turn_on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
    )
    fake_ha.core = MagicMock()
    fake_ha.helpers = MagicMock()
    fake_ha.helpers.event = MagicMock(async_track_state_change_event=MagicMock())

    with patch.dict(
        "sys.modules",
        {
            "homeassistant": fake_ha,
            "homeassistant.components": fake_components,
            "homeassistant.config_entries": fake_ha.config_entries,
            "homeassistant.const": fake_ha.const,
            "homeassistant.core": fake_ha.core,
            "homeassistant.helpers": fake_ha.helpers,
            "homeassistant.helpers.event": fake_ha.helpers.event,
        },
    ):
        for name in list(sys.modules):
            if name.startswith("custom_components.heating_assistant"):
                del sys.modules[name]
        thin_init = importlib.import_module("custom_components.heating_assistant.__init__")
        sensor_state = MagicMock()
        sensor_state.state = "21.5"
        sensor_state.domain = "sensor"
        sensor_state.attributes = {"unit_of_measurement": "°C"}
        hass = MagicMock()
        hass.states.get.return_value = sensor_state
        hass.states.async_all.return_value = []
        manager = thin_init._BridgeManager(
            hass, MagicMock(data={"instance_id": "default"})
        )
        manager._inbound_bindings = [
            ("living_room_temp_1", "sensor.living_room_temperature")
        ]
        await manager._async_homeassistant_started(None)

    topics = [str(item["args"][1]) for item in published if len(item["args"]) > 1]
    assert any(topic.endswith("/tag/living_room_temp_1/in") for topic in topics)
    inbound = next(
        item for item in published if str(item["args"][1]).endswith("/tag/living_room_temp_1/in")
    )
    payload = MqttTagPayload.decode(inbound["args"][2])
    assert payload.status == "GOOD"
    assert payload.value == pytest.approx(21.5)
    assert inbound["kwargs"].get("retain") is True
