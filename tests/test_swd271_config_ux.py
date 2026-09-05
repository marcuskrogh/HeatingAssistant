"""SWD-271: HA entity catalog over MQTT for Ingress entity pickers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import entities as entities_topic


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_entity_catalog_merges_into_hass_states_for_pickers(tmp_path: Path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options={"instance_id": "haos"})

    catalog = {
        "ts": 1.0,
        "entities": [
            {
                "entity_id": "sensor.living_room_temperature",
                "name": "Living room temperature",
                "state": "21.4",
                "unit": "°C",
            },
            {
                "entity_id": "weather.home",
                "name": "Home",
                "state": "cloudy",
            },
            {
                "entity_id": "sensor.nordpool_kwh",
                "name": "Nord Pool",
                "state": "0.42",
                "unit": "EUR/kWh",
            },
        ],
    }
    # Retained before App start — replayed on subscribe (SWD-271).
    await bus.publish(
        entities_topic("haos"),
        json.dumps(catalog),
        qos=1,
        retain=True,
    )
    await runtime.start()

    states = runtime.hass_states()
    assert "sensor.living_room_temperature" in states
    assert states["sensor.living_room_temperature"]["attributes"]["friendly_name"] == (
        "Living room temperature"
    )
    assert states["sensor.living_room_temperature"]["attributes"]["unit_of_measurement"] == "°C"
    assert "weather.home" in states
    assert "sensor.nordpool_kwh" in states
    # Catalog-backed rows are flagged so the Ingress picker can tell them apart
    # from binding stubs alone.
    assert states["sensor.living_room_temperature"]["attributes"][
        "heating_assistant_catalog"
    ] is True
    # App synthetics are preserved.
    assert "sensor.heating_assistant_system_summary" in states


@pytest.mark.asyncio
async def test_binding_stub_alone_is_not_marked_as_catalog(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "bindings": [
                {
                    "tag": "living_temp",
                    "entity_id": "sensor.living_room_temperature",
                    "direction": "in",
                }
            ],
        },
    )
    states = runtime.hass_states()
    stub = states["sensor.living_room_temperature"]
    assert stub["attributes"].get("heating_assistant_catalog") is not True


@pytest.mark.asyncio
async def test_outdoor_temperature_falls_back_to_weather_tag(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "weather_entity": "weather.home",
            "weather_tag": "weather_forecast",
            "bindings": [
                {
                    "tag": "weather_forecast",
                    "entity_id": "weather.home",
                    "direction": "in",
                }
            ],
        },
    )
    runtime.tag_values["weather_forecast"] = 4.2
    assert runtime._outdoor_temperature() == pytest.approx(4.2)


def test_outdoor_prefers_dedicated_sensor_over_weather(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "outdoor_temp_entity": "sensor.outdoor",
            "weather_entity": "weather.home",
            "bindings": [
                {
                    "tag": "outdoor_temp",
                    "entity_id": "sensor.outdoor",
                    "direction": "in",
                },
                {
                    "tag": "weather_forecast",
                    "entity_id": "weather.home",
                    "direction": "in",
                },
            ],
        },
    )
    runtime.tag_values["outdoor_temp"] = 1.5
    runtime.tag_values["weather_forecast"] = 9.9
    assert runtime.options.get("outdoor_temp_tag") == "outdoor_temp"
    assert runtime._outdoor_temperature() == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_thin_bridge_publishes_weather_temperature_attribute() -> None:
    """Weather tag/in value is attributes.temperature, not condition string."""

    import importlib
    import sys
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock, patch

    from heatingassistant.mqtt.topics import MqttTagPayload

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
        weather_state = MagicMock()
        weather_state.state = "cloudy"
        weather_state.domain = "weather"
        weather_state.attributes = {"temperature": 3.7}
        hass = MagicMock()
        manager = thin_init._BridgeManager(
            hass, MagicMock(data={"instance_id": "default"})
        )
        await manager._publish_entity_state("weather_forecast", weather_state)

    assert published
    payload = MqttTagPayload.decode(published[0]["args"][2])
    assert payload.value == pytest.approx(3.7)
    assert payload.reason == "cloudy"
    assert payload.status == "GOOD"


@pytest.mark.asyncio
async def test_clearing_solar_radiation_drops_binding(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "solar_radiation_entity": "sensor.ghi",
            "bindings": [
                {
                    "tag": "solar_radiation",
                    "entity_id": "sensor.ghi",
                    "direction": "in",
                }
            ],
        },
    )
    assert runtime.options.get("solar_radiation_tag") == "solar_radiation"

    await runtime.update_config({"solar_radiation_entity": ""})

    assert runtime.options.get("solar_radiation_entity") == ""
    assert "solar_radiation_tag" not in runtime.options
    assert all(item["tag"] != "solar_radiation" for item in runtime.binding_dicts())


def test_environment_ui_recommends_price_and_weather_collapses_outdoor() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "config"
        / "config-system.js"
    ).read_text(encoding="utf-8")
    assert "Electricity price" in source
    assert "Weather forecast" in source
    assert "Optional: outdoor temperature sensor" in source
    assert "solar_radiation_entity" in source  # cleared on save
    assert "Solar irradiance" not in source
    # Price appears before weather in the recommended section.
    price_idx = source.index("'price_entity'")
    weather_idx = source.index("'weather_entity'")
    outdoor_idx = source.index("'outdoor_temp_entity'")
    assert price_idx < weather_idx < outdoor_idx
    assert "advancedSubsection" in source
    assert "Solar model" in source
    assert "solar_gain_smoothing_tau_s" in source
    assert "solar_gain_smoothing_tau_min" in source


def test_entity_picker_prefers_searchable_catalog_copy() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "config"
        / "config-ui.js"
    ).read_text(encoding="utf-8")
    assert "Search by name or entity ID" in source
    assert "heating_assistant_catalog" in source
    assert "catalogReady" in source
    assert "limitedCatalog" in source
    assert "isValidEntityId" in source
    assert "Use entity ID" in source
    # Old synthetic-prefix heuristic must not decide catalog readiness.
    assert "entities.every" not in source


def test_entities_topic_helper() -> None:
    assert entities_topic("haos") == "heatingassistant/haos/entities"
