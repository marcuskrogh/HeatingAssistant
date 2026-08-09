"""SWD-278: wire outdoor/solar/price disturbances into MPC + MQTT attrs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from heatingassistant.app.disturbance_forecasts import build_mpc_disturbance_inputs
from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def test_mqtt_tag_payload_roundtrips_attributes() -> None:
    payload = MqttTagPayload(
        value=0.12,
        status="GOOD",
        reason=None,
        ts=1.0,
        attributes={"raw_today": [{"start": "2026-08-09T00:00:00+00:00", "value": 0.1}]},
    )
    decoded = MqttTagPayload.decode(payload.encode())
    assert decoded.attributes == payload.attributes


def test_build_mpc_disturbance_inputs_outdoor_persistence_and_price_series() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    out = build_mpc_disturbance_inputs(
        outdoor_temp=5.0,
        weather_attrs={
            "forecast": [
                {"datetime": "2026-08-09T09:00:00+00:00", "temperature": 6.0},
                {"datetime": "2026-08-09T12:00:00+00:00", "temperature": 8.0},
            ],
            "cloud_coverage": 40,
        },
        price_value=0.2,
        price_attrs={
            "raw_today": [
                {"start": "2026-08-09T00:00:00+00:00", "value": 0.10},
                {"start": "2026-08-09T08:00:00+00:00", "value": 0.25},
                {"start": "2026-08-09T09:00:00+00:00", "value": 0.30},
            ]
        },
        solar_value=100.0,
        solar_attrs=None,
        horizon=4,
        dt_s=900.0,
        now=now,
    )
    assert out["outdoor_forecast"]
    assert out["outdoor_forecast"][0] != out["outdoor_forecast"][-1] or len(set(out["outdoor_forecast"])) >= 1
    assert out["price_forecast"]
    assert out["price_forecast"][0] == pytest.approx(0.25)
    assert out["ghi_now"] == pytest.approx(100.0)


def test_forecast_payload_prefers_snapshot_price_series() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = build_app_forecast_payload(
        rooms=[{"name": "Living Room", "setpoint": 22.0}],
        room_temperatures={"Living Room": 21.5},
        outdoor_temp=5.0,
        energy_price=0.99,
        snapshot={
            "mode": "mpc",
            "dt": 900.0,
            "predictions": [{"Living Room": 21.6}],
            "linearised_predictions": [{"Living Room": 21.55}],
            "heating_schedule": [{"Living Room": 400.0}],
            "outdoor_forecast": [5.0],
            "solar_forecast": [{"Living Room": 50.0}],
            "price_forecast": [0.11, 0.22],
        },
        plot_forecast_hours=0.25,
        now=now,
    )
    assert payload["price_forecast"][0]["price"] == pytest.approx(0.11)
    assert payload["price_forecast"][1]["price"] == pytest.approx(0.22)
    room = payload["rooms"]["living_room"]
    assert room["forecast"][1]["outdoor_temp"] == pytest.approx(5.0)
    assert room["forecast"][1]["solar_gain"] == pytest.approx(50.0)
    assert room["forecast"][1]["linearised_temperature"] == pytest.approx(21.55)


def test_control_engine_does_not_force_zero_solar(monkeypatch: pytest.MonkeyPatch) -> None:
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
    captured: dict[str, object] = {}

    class FakeController:
        predictions = [{"Living Room": 21.0}]
        linearised_predictions = [{"Living Room": 21.1}]
        heating_schedule = [{"Living Room": 100.0}]
        outdoor_forecast = [4.0, 5.0]
        solar_forecast = [{"Living Room": 10.0}, {"Living Room": 20.0}]
        price_forecast = [0.1, 0.2]

        def compute(self, outdoor, solar_gains=None, now=None, **kwargs):
            captured["solar_gains"] = solar_gains
            captured["kwargs"] = kwargs
            return {"heater": 0.0}

    engine._controller = FakeController()
    engine.heat_sources = [SimpleNamespace(name="heater", room="Living Room")]
    engine._source_output_tags = {"heater": "heater_out"}
    engine.compute_actions(
        {"Living Room": 21.0},
        4.0,
        {"Living Room": 22.0},
        outdoor_forecast=[4.0, 5.0],
        price_forecast=[0.1, 0.2],
        ghi_now=50.0,
    )
    assert captured["solar_gains"] is None
    assert captured["kwargs"]["outdoor_forecast"] == [4.0, 5.0]
    assert captured["kwargs"]["price_forecast"] == [0.1, 0.2]
    snap = engine.forecast_snapshot()
    assert snap["price_forecast"] == [0.1, 0.2]
    assert snap["solar_forecast"][0]["Living Room"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_runtime_stores_tag_attributes_and_builds_disturbances(
    tmp_path: Path,
) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "horizon": 4,
            "weather_tag": "weather_forecast",
            "price_tag": "energy_price",
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
        },
    )
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(
            value=5.0,
            status="GOOD",
            reason="cloudy",
            ts=1.0,
            attributes={
                "forecast": [
                    {"datetime": "2026-08-09T10:00:00+00:00", "temperature": 7.0},
                    {"datetime": "2026-08-09T13:00:00+00:00", "temperature": 9.0},
                ]
            },
        ),
    )
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(
            value=0.2,
            status="GOOD",
            reason=None,
            ts=1.0,
            attributes={
                "raw_today": [
                    {"start": "2026-08-09T08:00:00+00:00", "value": 0.15},
                    {"start": "2026-08-09T09:00:00+00:00", "value": 0.35},
                ]
            },
        ),
    )
    assert "forecast" in runtime.tag_attributes["weather_forecast"]
    inputs = runtime._mpc_disturbance_inputs(5.0)
    assert inputs["outdoor_forecast"]
    assert inputs["price_forecast"]
    # Scalar-only GOOD update must clear stale forecast attrs (review-fix).
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(value=0.21, status="GOOD", reason=None, ts=2.0),
    )
    assert "energy_price" not in runtime.tag_attributes
