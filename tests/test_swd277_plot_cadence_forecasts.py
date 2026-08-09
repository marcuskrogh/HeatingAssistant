"""SWD-277: history gated to update_interval + MPC forecast payload."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.naming import room_slug, slugify
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def test_history_tick_interval_follows_update_interval(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos", "update_interval": 900},
    )
    assert runtime._history_tick_interval_s() == 900.0


def test_tag_spam_does_not_append_history_within_update_interval(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
        },
    )
    entity = "sensor.heating_assistant_living_room_temperature_measured"
    runtime.update_tag("living_temp", MqttTagPayload(value=21.0, status="GOOD"))
    runtime._record_history_samples(force=True)
    before = len(runtime.history(entity_ids=[entity]).get(entity, []))
    runtime.update_tag("living_temp", MqttTagPayload(value=21.1, status="GOOD"))
    runtime.update_tag("living_temp", MqttTagPayload(value=21.2, status="GOOD"))
    after = len(runtime.history(entity_ids=[entity]).get(entity, []))
    assert after == before


def test_build_app_forecast_payload_timestamps_use_update_interval() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = build_app_forecast_payload(
        rooms=[{"name": "Living Room", "setpoint": 22.0, "comfort_offset": 2.0}],
        room_temperatures={"Living Room": 21.5},
        outdoor_temp=5.0,
        energy_price=0.12,
        snapshot={
            "mode": "mpc",
            "dt": 900.0,
            "predictions": [
                {"Living Room": 21.6},
                {"Living Room": 21.7},
            ],
            "linearised_predictions": [
                {"Living Room": 21.55},
                {"Living Room": 21.65},
            ],
            "heating_schedule": [
                {"Living Room": 800.0},
                {"Living Room": 600.0},
            ],
            "outdoor_forecast": [5.0, 4.5],
            "solar_forecast": [
                {"Living Room": 10.0},
                {"Living Room": 20.0},
            ],
        },
        plot_forecast_hours=0.5,
        now=now,
        room_power_meta={
            "Living Room": {
                "max_power": 2000.0,
                "current_rated_max_power": 1800.0,
                "current_max_power": 1800.0,
            }
        },
    )
    room = payload["rooms"]["living_room"]
    assert room["step_seconds"] == 900.0
    assert len(room["forecast"]) == 3  # bridge + 2 steps
    assert room["forecast"][0]["time"].startswith("2026-08-09T08:00:00")
    assert room["forecast"][1]["time"].startswith("2026-08-09T08:15:00")
    assert room["forecast"][2]["time"].startswith("2026-08-09T08:30:00")
    assert room["forecast"][1]["temperature"] == pytest.approx(21.6)
    assert room["forecast"][1]["heating_power"] == pytest.approx(800.0)
    assert room["max_power"] == pytest.approx(2000.0)
    assert room["forecast"][0]["heating_capacity"] == pytest.approx(1800.0)
    assert len(payload["price_forecast"]) == 2
    assert payload["price_forecast"][0]["price"] == pytest.approx(0.12)


def test_forecast_room_keys_match_app_room_slug_not_ha_slugify() -> None:
    name = "Erik's Room"
    assert room_slug(name) == "erik_s_room"
    assert slugify(name) == "eriks_room"
    payload = build_app_forecast_payload(
        rooms=[{"name": name, "setpoint": 21.0}],
        room_temperatures={name: 20.5},
        outdoor_temp=0.0,
        energy_price=None,
        snapshot={
            "mode": "mpc",
            "dt": 900.0,
            "predictions": [{name: 20.6}],
            "heating_schedule": [{name: 400.0}],
            "outdoor_forecast": [0.0],
            "solar_forecast": [],
            "linearised_predictions": [],
        },
        plot_forecast_hours=0.25,
        now=datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
    )
    assert "erik_s_room" in payload["rooms"]
    assert "eriks_room" not in payload["rooms"]


@pytest.mark.asyncio
async def test_runtime_forecasts_expose_cached_mpc_snapshot(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
            "heat_sources": [
                {
                    "name": "living_heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        },
    )
    runtime.update_tag("living_temp", MqttTagPayload(value=21.0, status="GOOD"))
    runtime.update_tag("energy_price", MqttTagPayload(value=0.2, status="GOOD"))
    runtime.control_engine._last_predictions = [{"Living Room": 21.2}]
    runtime.control_engine._last_heating_schedule = [{"Living Room": 500.0}]
    runtime.control_engine._last_outdoor_forecast = [3.0]
    runtime.control_engine._last_solar_forecast = [{"Living Room": 0.0}]
    runtime.control_engine.mode = "mpc"

    payload = runtime.forecasts(plot_forecast_hours=0.25)
    assert "living_room" in payload["rooms"]
    assert payload["rooms"]["living_room"]["forecast"][1]["heating_power"] == pytest.approx(
        500.0
    )
    assert payload["rooms"]["living_room"]["max_power"] == pytest.approx(1500.0)
    assert payload["price_forecast"]
    assert payload["step_seconds"] == 900.0


@pytest.mark.asyncio
async def test_runtime_forecasts_honour_configured_price_tag(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
        },
    )
    # Entity wiring may clear price_tag when no price entity is configured;
    # set the derived tag the same way a successful bind would.
    runtime.options["price_tag"] = "energy_price_2"
    runtime.update_tag("energy_price_2", MqttTagPayload(value=0.33, status="GOOD"))
    runtime.control_engine._last_predictions = [{"Living Room": 21.0}]
    runtime.control_engine._last_heating_schedule = [{"Living Room": 100.0}]
    runtime.control_engine.mode = "mpc"
    payload = runtime.forecasts(plot_forecast_hours=0.25)
    assert payload["price_forecast"][0]["price"] == pytest.approx(0.33)
