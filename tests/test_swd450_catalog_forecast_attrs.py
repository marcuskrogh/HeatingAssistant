"""SWD-450: catalog overlay must not wipe weather/price forecast attributes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from heatingassistant.app.disturbance_forecasts import build_mpc_disturbance_inputs
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload, entities as entities_topic


pytestmark = pytest.mark.unit

WEATHER_FORECAST = [
    {"datetime": "2026-08-30T10:00:00+00:00", "temperature": 12.0},
    {"datetime": "2026-08-30T13:00:00+00:00", "temperature": 14.0},
]
PRICE_TODAY = [
    {"start": "2026-08-30T08:00:00+00:00", "value": 0.15},
    {"start": "2026-08-30T09:00:00+00:00", "value": 0.35},
    {"start": "2026-08-30T10:00:00+00:00", "value": 0.55},
]


def _runtime_options() -> dict[str, Any]:
    return {
        "instance_id": "haos",
        "update_interval": 900,
        "horizon": 4,
        "weather_entity": "weather.home",
        "price_entity": "sensor.nordpool",
        "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
    }


async def _publish_catalog(
    runtime: HeatingRuntime,
    *,
    ts: float,
    states: dict[str, str],
) -> None:
    entities = [
        {"entity_id": entity_id, "name": entity_id, "state": state}
        for entity_id, state in states.items()
    ]
    await runtime.bus.publish(
        entities_topic(runtime.instance_id),
        json.dumps({"ts": ts, "entities": entities}),
        qos=1,
        retain=True,
    )


def _seed_forecast_tags(runtime: HeatingRuntime) -> None:
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(
            value=8.0,
            status="GOOD",
            reason="cloudy",
            ts=1.0,
            attributes={
                "forecast": list(WEATHER_FORECAST),
                "temperature": 8.0,
                "cloud_coverage": 40,
            },
        ),
    )
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(
            value=0.20,
            status="GOOD",
            ts=1.0,
            attributes={"raw_today": list(PRICE_TODAY)},
        ),
    )


@pytest.mark.asyncio
async def test_numeric_catalog_overlay_keeps_forecast_attrs(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options=_runtime_options()
    )
    await runtime.start()
    _seed_forecast_tags(runtime)
    assert "forecast" in runtime.tag_attributes["weather_forecast"]
    assert "raw_today" in runtime.tag_attributes["energy_price"]

    await _publish_catalog(
        runtime,
        ts=100.0,
        states={"weather.home": "9.5", "sensor.nordpool": "0.22"},
    )

    assert runtime.tag_values["weather_forecast"] == pytest.approx(9.5)
    assert runtime.tag_values["energy_price"] == pytest.approx(0.22)
    assert runtime.tag_attributes["weather_forecast"]["forecast"] == WEATHER_FORECAST
    assert runtime.tag_attributes["energy_price"]["raw_today"] == PRICE_TODAY

    outdoor = runtime._outdoor_temperature()
    assert outdoor == pytest.approx(9.5)
    inputs = build_mpc_disturbance_inputs(
        outdoor_temp=outdoor,
        weather_attrs=runtime.tag_attributes.get("weather_forecast"),
        price_value=runtime._coerce_number(runtime.tag_values.get("energy_price")),
        price_attrs=runtime.tag_attributes.get("energy_price"),
        solar_value=None,
        solar_attrs=None,
        horizon=4,
        dt_s=900.0,
        now=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )
    assert inputs["outdoor_forecast"]
    # Live outdoor is 9.5; forecast attrs start at 12 °C — not a persist hold.
    assert all(abs(value - 9.5) > 0.5 for value in inputs["outdoor_forecast"])
    assert inputs["price_forecast"]
    # Live price is 0.22; day-ahead raw_today starts at 0.15.
    assert any(abs(value - 0.22) > 0.05 for value in inputs["price_forecast"])


@pytest.mark.asyncio
async def test_condition_catalog_does_not_overwrite_weather_temp(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options=_runtime_options()
    )
    await runtime.start()
    _seed_forecast_tags(runtime)

    await _publish_catalog(
        runtime,
        ts=100.0,
        states={"weather.home": "cloudy", "sensor.nordpool": "0.22"},
    )

    assert runtime.tag_values["weather_forecast"] == pytest.approx(8.0)
    assert runtime.tag_attributes["weather_forecast"]["forecast"] == WEATHER_FORECAST
    assert runtime.tag_values["energy_price"] == pytest.approx(0.22)
    assert runtime.tag_attributes["energy_price"]["raw_today"] == PRICE_TODAY
    assert runtime._outdoor_temperature() == pytest.approx(8.0)


def test_outdoor_falls_back_to_weather_temperature_attr(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, options=_runtime_options())
    runtime.tag_values["weather_forecast"] = "cloudy"
    runtime.tag_attributes["weather_forecast"] = {"temperature": 6.4}
    assert runtime._outdoor_temperature() == pytest.approx(6.4)


def test_coerce_number_parses_numeric_strings() -> None:
    assert HeatingRuntime._coerce_number("7.5") == pytest.approx(7.5)
    assert HeatingRuntime._coerce_number("cloudy") is None
    assert HeatingRuntime._usable_catalog_value("cloudy") is None
    assert HeatingRuntime._usable_catalog_value("on") is True
    assert HeatingRuntime._usable_catalog_value("0.22") == pytest.approx(0.22)


@pytest.mark.asyncio
async def test_mqtt_scalar_only_still_clears_price_attrs(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options=_runtime_options()
    )
    await runtime.start()
    _seed_forecast_tags(runtime)
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(value=0.21, status="GOOD", ts=2.0),
    )
    assert "energy_price" not in runtime.tag_attributes
