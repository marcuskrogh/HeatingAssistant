"""SWD-286: schedule-aware comfort constraints for App sensors + forecasts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.naming import room_slug
from heatingassistant.engine.schedule_control import (
    compute_control_trajectory,
    resolve_room_effective_params,
)
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


_NIGHT_SCHEDULE = {
    "enabled": True,
    "periods": [
        {
            "name": "Night Mode",
            "schedule_type": "weekly_recurring",
            "time_mode": "window",
            "start": "22:00",
            "end": "05:00",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "mode": "comfort",
            "comfort_offset": 3.0,
            "enabled": True,
        }
    ],
}


def test_resolve_room_effective_uses_period_comfort_offset() -> None:
    now = datetime(2026, 8, 9, 22, 30)  # inside Night Mode
    effective = resolve_room_effective_params(
        schedule_payload=_NIGHT_SCHEDULE,
        base_setpoint=22.0,
        measured_temp=23.0,
        now=now,
        default_comfort_offset=1.0,
    )
    assert effective.comfort_offset == pytest.approx(3.0)
    assert effective.setpoint == pytest.approx(22.0)
    assert effective.enabled is True
    assert effective.period_name == "Night Mode"


def test_resolve_room_effective_falls_back_outside_period() -> None:
    now = datetime(2026, 8, 9, 12, 0)  # outside Night Mode
    effective = resolve_room_effective_params(
        schedule_payload=_NIGHT_SCHEDULE,
        base_setpoint=22.0,
        measured_temp=23.0,
        now=now,
        default_comfort_offset=1.0,
    )
    assert effective.comfort_offset == pytest.approx(1.0)
    assert effective.period_name is None


def test_control_trajectory_projects_night_comfort_offset() -> None:
    now = datetime(2026, 8, 9, 21, 0)  # one hour before night
    rooms = [{"name": "Living Room", "setpoint": 22.0, "comfort_offset": 1.0}]
    traj = compute_control_trajectory(
        rooms=rooms,
        schedules_by_slug={"living_room": _NIGHT_SCHEDULE},
        room_slug_fn=room_slug,
        base_setpoints={"Living Room": 22.0},
        default_comfort_offsets={"Living Room": 1.0},
        room_enabled={"Living Room": True},
        now_local=now,
        n_steps=8,
        dt_seconds=1800.0,  # 30 min → night starts at k=2 (22:00)
    )
    offsets = traj.comfort_offsets["Living Room"].tolist()
    assert offsets[0] == pytest.approx(1.0)
    assert offsets[1] == pytest.approx(1.0)
    assert offsets[2] == pytest.approx(3.0)
    assert offsets[3] == pytest.approx(3.0)


def test_forecast_payload_uses_schedule_trajectory_for_constraints() -> None:
    now = datetime(2026, 8, 9, 21, 0, tzinfo=timezone.utc)
    rooms = [{"name": "Living Room", "setpoint": 22.0, "comfort_offset": 1.0}]
    traj = compute_control_trajectory(
        rooms=rooms,
        schedules_by_slug={"living_room": _NIGHT_SCHEDULE},
        room_slug_fn=room_slug,
        base_setpoints={"Living Room": 22.0},
        default_comfort_offsets={"Living Room": 1.0},
        room_enabled={"Living Room": True},
        now_local=datetime(2026, 8, 9, 21, 0),
        n_steps=5,  # now + 4 future steps
        dt_seconds=1800.0,
    )
    payload = build_app_forecast_payload(
        rooms=rooms,
        room_temperatures={"Living Room": 23.0},
        outdoor_temp=5.0,
        energy_price=0.1,
        snapshot={
            "mode": "mpc",
            "dt": 1800.0,
            "predictions": [
                {"Living Room": 23.0},
                {"Living Room": 23.0},
                {"Living Room": 23.0},
                {"Living Room": 23.0},
            ],
            "heating_schedule": [
                {"Living Room": 100.0},
                {"Living Room": 100.0},
                {"Living Room": 100.0},
                {"Living Room": 100.0},
            ],
            "outdoor_forecast": [5.0, 5.0, 5.0, 5.0],
            "solar_forecast": [],
            "linearised_predictions": [],
        },
        plot_forecast_hours=2.0,
        now=now,
        control_trajectory=traj,
    )
    fc = payload["rooms"]["living_room"]["forecast"]
    # now 21:00 ±1; 21:30 ±1; 22:00 ±3; 22:30 ±3
    assert fc[0]["constraint_upper"] == pytest.approx(23.0)
    assert fc[0]["constraint_lower"] == pytest.approx(21.0)
    assert fc[1]["constraint_upper"] == pytest.approx(23.0)
    assert fc[2]["constraint_upper"] == pytest.approx(25.0)
    assert fc[2]["constraint_lower"] == pytest.approx(19.0)
    assert fc[3]["constraint_upper"] == pytest.approx(25.0)


def test_runtime_hass_states_publish_schedule_comfort_offset(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "comfort_offset": 1.0,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "comfort_offset": 1.0,
                    "temp_tags": ["living_temp"],
                }
            ],
            "schedules": {"living_room": _NIGHT_SCHEDULE},
        },
    )
    runtime.update_tag("living_temp", MqttTagPayload(value=23.0, status="GOOD"))
    # Force schedule clock into the night window.
    runtime._schedule_now_local = lambda: datetime(2026, 8, 9, 22, 30).astimezone()
    states = runtime.hass_states()
    upper = states["sensor.heating_assistant_living_room_constraint_upper"]["state"]
    lower = states["sensor.heating_assistant_living_room_constraint_lower"]["state"]
    assert float(upper) == pytest.approx(25.0)
    assert float(lower) == pytest.approx(19.0)
