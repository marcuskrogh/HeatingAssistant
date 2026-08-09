"""SWD-279: JSON-safe attrs, weather.get_forecasts, linearised from estimated output."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from heatingassistant.app.disturbance_forecasts import build_mpc_disturbance_inputs
from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def _load_forecast_publish():
    """Load thin-bridge helper module without importing HA-bound package __init__."""

    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "heating_assistant"
        / "forecast_publish.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ha_forecast_publish_swd279", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_forecast_publish = _load_forecast_publish()
json_safe = _forecast_publish.json_safe
forecast_attributes_for_publish = _forecast_publish.forecast_attributes_for_publish
clear_weather_forecast_cache = _forecast_publish.clear_weather_forecast_cache


@pytest.fixture(autouse=True)
def _clear_weather_cache() -> None:
    clear_weather_forecast_cache()
    yield
    clear_weather_forecast_cache()


def test_mqtt_tag_payload_encodes_datetime_attributes() -> None:
    payload = MqttTagPayload(
        value=0.25,
        status="GOOD",
        reason=None,
        ts=1.0,
        attributes={
            "raw_today": [
                {
                    "start": datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
                    "value": 0.25,
                }
            ]
        },
    )
    encoded = payload.encode()
    decoded = MqttTagPayload.decode(encoded)
    assert decoded.attributes["raw_today"][0]["start"] == "2026-08-09T08:00:00+00:00"
    assert decoded.attributes["raw_today"][0]["value"] == pytest.approx(0.25)


def test_json_safe_converts_nested_datetimes() -> None:
    raw = {
        "raw_today": [
            {"start": datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc), "value": 0.3}
        ]
    }
    safe = json_safe(raw)
    assert safe["raw_today"][0]["start"] == "2026-08-09T09:00:00+00:00"


def test_json_safe_naive_datetime_becomes_utc() -> None:
    safe = json_safe({"start": datetime(2026, 8, 9, 10, 0)})
    assert safe["start"] == "2026-08-09T10:00:00+00:00"


def test_datetime_raw_today_builds_varying_price_forecast() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    attrs = json_safe(
        {
            "raw_today": [
                {"start": datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc), "value": 0.10},
                {"start": datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc), "value": 0.25},
                {"start": datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc), "value": 0.40},
            ]
        }
    )
    out = build_mpc_disturbance_inputs(
        outdoor_temp=5.0,
        weather_attrs=None,
        price_value=0.25,
        price_attrs=attrs,
        solar_value=None,
        solar_attrs=None,
        horizon=8,
        dt_s=900.0,
        now=now,
    )
    assert out["price_forecast"]
    # Steps at 08:00..08:45 hold 0.25; from 09:00 the series jumps to 0.40.
    assert out["price_forecast"][0] == pytest.approx(0.25)
    assert out["price_forecast"][4] == pytest.approx(0.40)
    assert len(set(out["price_forecast"])) > 1


@pytest.mark.asyncio
async def test_forecast_attributes_call_weather_get_forecasts() -> None:
    forecast = [
        {"datetime": "2026-08-09T10:00:00+00:00", "temperature": 7.0},
        {"datetime": "2026-08-09T11:00:00+00:00", "temperature": 8.0},
    ]
    hass = MagicMock()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock(
        return_value={"weather.home": {"forecast": forecast}}
    )
    state = SimpleNamespace(
        domain="weather",
        entity_id="weather.home",
        attributes={"temperature": 5.0, "cloud_coverage": 40},
    )
    attrs = await forecast_attributes_for_publish(hass, state)
    assert attrs is not None
    assert attrs["forecast"] == forecast
    assert attrs["temperature"] == pytest.approx(5.0)
    hass.services.async_call.assert_awaited()


@pytest.mark.asyncio
async def test_weather_forecast_cache_survives_service_failure() -> None:
    forecast = [
        {"datetime": "2026-08-09T10:00:00+00:00", "temperature": 7.0},
        {"datetime": "2026-08-09T11:00:00+00:00", "temperature": 8.5},
    ]
    hass = MagicMock()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock(
        return_value={"weather.home": {"forecast": forecast}}
    )
    state = SimpleNamespace(
        domain="weather",
        entity_id="weather.home",
        attributes={"temperature": 5.0, "cloud_coverage": 40},
    )
    first = await forecast_attributes_for_publish(hass, state)
    assert first is not None and first.get("forecast") == forecast

    hass.services.async_call = AsyncMock(side_effect=RuntimeError("upstream"))
    second = await forecast_attributes_for_publish(hass, state)
    assert second is not None
    assert second["forecast"] == forecast
    assert second["temperature"] == pytest.approx(5.0)


def test_forecast_bridge_uses_filtered_estimated_output() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = build_app_forecast_payload(
        rooms=[{"name": "Living Room", "setpoint": 22.0}],
        room_temperatures={"Living Room": 21.5},
        outdoor_temp=5.0,
        energy_price=0.2,
        snapshot={
            "mode": "mpc",
            "dt": 900.0,
            "predictions": [{"Living Room": 21.7}],
            "linearised_predictions": [{"Living Room": 21.8}],
            "heating_schedule": [{"Living Room": 400.0}],
            "outdoor_forecast": [5.0],
            "solar_forecast": [
                {"Living Room": 10.0},
                {"Living Room": 50.0},
            ],
            "filtered_temperatures": {"Living Room": 21.62},
            "price_forecast": [0.11, 0.22],
        },
        plot_forecast_hours=0.25,
        now=now,
    )
    room = payload["rooms"]["living_room"]
    bridge = room["forecast"][0]
    assert bridge["temperature"] == pytest.approx(21.62)
    assert bridge["linearised_temperature"] == pytest.approx(21.62)
    assert bridge["solar_gain"] == pytest.approx(10.0)
    # Future step uses solar_forecast[i+1] for the N+1 series.
    assert room["forecast"][1]["solar_gain"] == pytest.approx(50.0)
    assert room["forecast"][1]["linearised_temperature"] == pytest.approx(21.8)


def test_control_engine_caches_filtered_temperatures() -> None:
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 2,
            "rooms": [{"name": "Living Room", "setpoint": 22.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1000.0,
                }
            ],
        }
    )

    class FakeController:
        predictions = [{"Living Room": 21.0}]
        linearised_predictions = [{"Living Room": 21.1}]
        heating_schedule = [{"Living Room": 100.0}]
        outdoor_forecast = [4.0, 5.0]
        solar_forecast = [
            {"Living Room": 10.0},
            {"Living Room": 20.0},
            {"Living Room": 30.0},
        ]
        price_forecast = [0.1, 0.2]
        filtered_temperatures = {"Living Room": 21.55}

        def compute(self, outdoor, solar_gains=None, now=None, **kwargs):
            return {"heater": 0.0}

    engine._controller = FakeController()
    engine.heat_sources = [SimpleNamespace(name="heater", room="Living Room")]
    engine._source_output_tags = {"heater": "heater_out"}
    engine.compute_actions({"Living Room": 21.0}, 4.0, {"Living Room": 22.0})
    snap = engine.forecast_snapshot()
    assert snap["filtered_temperatures"]["Living Room"] == pytest.approx(21.55)
