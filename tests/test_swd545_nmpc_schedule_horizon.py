"""SWD-545: scheduled comfort/off bounds on the NMPC prediction horizon."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import (
    DEFAULT_FROST_PROTECTION,
    OFF_PERIOD_TMAX,
)
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.naming import room_slug
from heatingassistant.engine.schedule_control import compute_control_trajectory
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit

_OFF_THEN_COMFORT = {
    "enabled": True,
    "periods": [
        {
            "name": "night_off",
            "schedule_type": "weekly_recurring",
            "time_mode": "window",
            "start": "22:00",
            "end": "06:00",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "mode": "off",
            "frost_protection": 12.0,
            "enabled": True,
        },
        {
            "name": "morning",
            "schedule_type": "weekly_recurring",
            "time_mode": "window",
            "start": "06:00",
            "end": "09:00",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "mode": "comfort",
            "setpoint": 21.0,
            "comfort_offset": 0.5,
            "enabled": True,
        },
    ],
}


def _room_ctrl():
    model = HouseModel(
        [
            Room(
                name="Living Room",
                thermal_mass=5_000_000.0,
                r_external=0.05,
                setpoint=21.0,
                comfort_offset=2.0,
            )
        ]
    )
    heater = ElectricHeater("heater", "Living Room", max_power=2000.0)
    return HeatingMPCController(
        model, [heater], horizon=8, dt=1800.0, mpc_mode="nmpc"
    )


def test_trajectory_marks_off_and_upcoming_comfort() -> None:
    now = datetime(2026, 9, 11, 4, 0)  # still in night_off; comfort at 06:00
    rooms = [{"name": "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    traj = compute_control_trajectory(
        rooms=rooms,
        schedules_by_slug={"living_room": _OFF_THEN_COMFORT},
        room_slug_fn=room_slug,
        base_setpoints={"Living Room": 21.0},
        default_comfort_offsets={"Living Room": 2.0},
        room_enabled={"Living Room": True},
        now_local=now,
        n_steps=8,
        dt_seconds=1800.0,
    )
    enabled = traj.enabled_steps["Living Room"].tolist()
    # k=0..3 → 04:00, 04:30, 05:00, 05:30 off; k=4 → 06:00 comfort
    assert enabled[:4] == [False, False, False, False]
    assert enabled[4] is True
    assert traj.frost_floors["Living Room"][0] == pytest.approx(12.0)
    assert traj.setpoints["Living Room"][4] == pytest.approx(21.0)
    assert traj.comfort_offsets["Living Room"][4] == pytest.approx(0.5)


def test_nmpc_bounds_relax_off_and_tighten_at_comfort() -> None:
    now = datetime(2026, 9, 11, 4, 0)
    rooms = [{"name": "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    traj = compute_control_trajectory(
        rooms=rooms,
        schedules_by_slug={"living_room": _OFF_THEN_COMFORT},
        room_slug_fn=room_slug,
        base_setpoints={"Living Room": 21.0},
        default_comfort_offsets={"Living Room": 2.0},
        room_enabled={"Living Room": True},
        now_local=now,
        n_steps=8,
        dt_seconds=1800.0,
    )
    ctrl = _room_ctrl()
    t_min, t_max = ctrl._comfort_bounds_fast(traj, 8)
    assert t_min[0, 0] == pytest.approx(DEFAULT_FROST_PROTECTION)
    assert t_max[0, 0] == pytest.approx(OFF_PERIOD_TMAX)
    # Preheat must not sit inside frost ± 2 °C.
    assert t_max[0, 0] > 21.0
    assert t_min[4, 0] == pytest.approx(20.5)
    assert t_max[4, 0] == pytest.approx(21.5)


def test_schedule_off_does_not_disable_heaters(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "horizon": 16,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "comfort_offset": 2.0,
                    "temp_tags": ["living_temp"],
                    "enabled": True,
                }
            ],
            "heat_sources": [
                {
                    "name": "Living Heater",
                    "room": "Living Room",
                    "type": "electric",
                    "max_power": 2000.0,
                    "output_tag": "living_heater",
                }
            ],
            "schedules": {"living_room": _OFF_THEN_COMFORT},
        },
    )
    runtime._schedule_now_local = lambda: datetime(2026, 9, 11, 4, 0).astimezone()
    ctx = runtime._schedule_control_context()
    assert "Living Heater" not in ctx["disabled_sources"]
    n_steps, dt = runtime._mpc_horizon_grid()
    assert n_steps == ctx["trajectory"].enabled_steps["Living Room"].shape[0]
    assert dt == pytest.approx(900.0)
    enabled = ctx["trajectory"].enabled_steps["Living Room"]
    assert bool(enabled[0]) is False
    # 06:00 is 2 h later; 16 × 900 s covers it.
    assert bool(np.any(enabled)) is True
