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
from heatingassistant.engine.schedule_control import (
    compute_control_trajectory,
    off_step_holds_heater_off,
    schedule_off_zeros_live_actuation,
)
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

_ECO_THEN_COMFORT = {
    "enabled": True,
    "periods": [
        {
            "name": "day_eco",
            "schedule_type": "weekly_recurring",
            "time_mode": "window",
            "start": "08:00",
            "end": "16:00",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "mode": "comfort",
            "setpoint": 18.0,
            "comfort_offset": 1.0,
            "enabled": True,
        },
        {
            "name": "evening",
            "schedule_type": "weekly_recurring",
            "time_mode": "window",
            "start": "16:00",
            "end": "22:00",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "mode": "comfort",
            "setpoint": 21.0,
            "comfort_offset": 0.5,
            "enabled": True,
        },
    ],
}

_OFF_ONLY = {
    "enabled": True,
    "periods": [
        {
            "name": "away",
            "schedule_type": "weekly_recurring",
            "time_mode": "window",
            "start": "00:00",
            "end": "23:59",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "mode": "off",
            "frost_protection": 12.0,
            "enabled": True,
        }
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


def test_off_step_holds_heater_until_upcoming_comfort() -> None:
    en = np.array([False, False, False, True, True])
    assert off_step_holds_heater_off(en, 0) is False
    assert off_step_holds_heater_off(en, 2) is False
    assert off_step_holds_heater_off(en, 3) is False
    all_off = np.array([False, False, False])
    assert off_step_holds_heater_off(all_off, 0) is True
    assert schedule_off_zeros_live_actuation(en, currently_on=False) is False
    assert schedule_off_zeros_live_actuation(all_off, currently_on=False) is True
    assert schedule_off_zeros_live_actuation(all_off, currently_on=True) is False


def test_comfort_to_comfort_bounds_shift_before_the_change() -> None:
    now = datetime(2026, 9, 11, 15, 0)
    rooms = [{"name": "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    traj = compute_control_trajectory(
        rooms=rooms,
        schedules_by_slug={"living_room": _ECO_THEN_COMFORT},
        room_slug_fn=room_slug,
        base_setpoints={"Living Room": 21.0},
        default_comfort_offsets={"Living Room": 2.0},
        room_enabled={"Living Room": True},
        now_local=now,
        n_steps=6,
        dt_seconds=1800.0,
    )
    ctrl = _room_ctrl()
    t_min, t_max = ctrl._comfort_bounds_fast(traj, 6)
    assert bool(traj.enabled_steps["Living Room"][0]) is True
    assert t_min[0, 0] == pytest.approx(17.0)
    assert t_max[0, 0] == pytest.approx(19.0)
    assert t_min[2, 0] == pytest.approx(20.5)
    assert t_max[2, 0] == pytest.approx(21.5)


def _runtime(tmp_path: Path, schedule: dict, *, horizon: int = 16) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "horizon": horizon,
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
            "schedules": {"living_room": schedule},
        },
    )


def test_schedule_off_does_not_disable_heaters(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _OFF_THEN_COMFORT)
    runtime._schedule_now_local = lambda: datetime(2026, 9, 11, 4, 0).astimezone()
    ctx = runtime._schedule_control_context()
    assert "Living Heater" not in ctx["disabled_sources"]
    n_steps, dt = runtime._mpc_horizon_grid()
    assert n_steps == ctx["trajectory"].enabled_steps["Living Room"].shape[0]
    assert dt == pytest.approx(900.0)
    enabled = ctx["trajectory"].enabled_steps["Living Room"]
    assert bool(enabled[0]) is False
    assert bool(np.any(enabled)) is True


def test_off_without_upcoming_comfort_zeros_heaters(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _OFF_ONLY, horizon=8)
    runtime._schedule_now_local = lambda: datetime(2026, 9, 11, 12, 0).astimezone()
    ctx = runtime._schedule_control_context()
    assert "Living Heater" in ctx["disabled_sources"]
    assert not bool(np.any(ctx["trajectory"].enabled_steps["Living Room"]))


def test_off_period_u_hold_pins_zero_when_no_later_comfort() -> None:
    now = datetime(2026, 9, 11, 12, 0)
    rooms = [{"name": "Living Room", "setpoint": 21.0, "comfort_offset": 2.0}]
    traj = compute_control_trajectory(
        rooms=rooms,
        schedules_by_slug={"living_room": _OFF_ONLY},
        room_slug_fn=room_slug,
        base_setpoints={"Living Room": 21.0},
        default_comfort_offsets={"Living Room": 2.0},
        room_enabled={"Living Room": True},
        now_local=now,
        n_steps=4,
        dt_seconds=1800.0,
    )
    ctrl = _room_ctrl()
    u_min, u_max, mask = ctrl._apply_off_period_u_hold(traj, None, None, None, 4)
    assert u_min is not None and u_max is not None and mask is not None
    assert np.allclose(u_min[:, 0], 0.0)
    assert np.allclose(u_max[:, 0], 0.0)
    assert np.all(mask[:, 0])


def test_off_period_u_hold_leaves_preheat_steps_free() -> None:
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
    u_min, u_max, mask = ctrl._apply_off_period_u_hold(traj, None, None, None, 8)
    assert u_min is None and u_max is None and mask is None


def test_disabled_room_stays_disabled_with_upcoming_comfort(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _OFF_THEN_COMFORT)
    runtime.options["rooms"][0]["enabled"] = False
    runtime._schedule_now_local = lambda: datetime(2026, 9, 11, 4, 0).astimezone()
    ctx = runtime._schedule_control_context()
    assert "Living Heater" in ctx["disabled_sources"]

