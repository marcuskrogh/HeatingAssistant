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
    # App synthetics are preserved.
    assert "sensor.heating_assistant_system_summary" in states


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
    assert "limitedCatalog" in source
    assert "isValidEntityId" in source
    assert "Use entity ID" in source


def test_entities_topic_helper() -> None:
    assert entities_topic("haos") == "heatingassistant/haos/entities"
