"""SWD-570: room plots and HA states drop the wall series."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def test_forecast_payload_omits_wall_temperature() -> None:
    now = datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)
    payload = build_app_forecast_payload(
        rooms=[{"name": "Living Room", "setpoint": 21.0, "comfort_offset": 0.5}],
        room_temperatures={"Living Room": 16.0},
        outdoor_temp=0.0,
        energy_price=None,
        snapshot={
            "mode": "mpc",
            "dt": 900.0,
            "predictions": [{"Living Room": 16.4}, {"Living Room": 16.8}],
            "wall_temperatures": {"Living Room": 20.5},
            "wall_predictions": [
                {"Living Room": 20.4},
                {"Living Room": 20.3},
            ],
            "heating_schedule": [{"Living Room": 0.0}, {"Living Room": 400.0}],
            "outdoor_forecast": [0.0, 0.0],
            "solar_forecast": [{"Living Room": 0.0}, {"Living Room": 0.0}],
        },
        plot_forecast_hours=0.5,
        now=now,
    )
    steps = payload["rooms"]["living_room"]["forecast"]
    for step in steps:
        assert "wall_temperature" not in step or step.get("wall_temperature") in (
            None,
            step.get("temperature"),
        )


def test_runtime_does_not_publish_wall_temperature_entity(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
        },
    )
    runtime.control_engine._last_wall_temperatures = {"Living Room": 19.25}
    states = runtime.hass_states()
    entity = "sensor.heating_assistant_living_room_temperature_wall"
    assert entity not in states
