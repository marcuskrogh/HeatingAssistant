"""SWD-418: drift-free NMPC / control wall-clock grid."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.nmpc_timing import grid_slot_index, next_grid_ts
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus


def _runtime(tmp_path: Path, **options: object) -> HeatingRuntime:
    base = {
        "instance_id": "haos",
        "system_enabled": False,
        "nmpc_period": 1800,
        "nmpc_fast_substeps": 2,
        "nmpc_horizon_h": 0.5,
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 21.0,
                "temp_tags": ["living_temp"],
            }
        ],
        "heat_sources": [
            {
                "name": "heater",
                "type": "electric_heater",
                "room": "Living Room",
                "max_power": 1500.0,
            }
        ],
    }
    base.update(options)
    return HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=base)


def test_grid_slot_index_and_next_grid_ts() -> None:
    epoch = 1_000.0
    period = 100.0
    assert grid_slot_index(epoch, period, epoch) == 0
    assert grid_slot_index(epoch, period, epoch + 99.9) == 0
    assert grid_slot_index(epoch, period, epoch + 100.0) == 1
    assert next_grid_ts(epoch, period, epoch) == pytest.approx(epoch + period)
    assert next_grid_ts(epoch, period, epoch + 0.4) == pytest.approx(epoch + period)
    assert next_grid_ts(epoch, period, epoch + period) == pytest.approx(epoch + 2 * period)


def test_enable_anchors_epoch_for_both_rings(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._schedule_nmpc_worker = lambda: None  # type: ignore[method-assign]
    assert runtime._last_nmpc_ts is None
    before = time.time()
    asyncio.run(runtime.update_config({"system_enabled": True}))
    epoch = runtime._last_nmpc_ts
    assert epoch is not None
    assert epoch >= before
    assert runtime._last_control_ts == pytest.approx(epoch)
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["last_nmpc_ts"] == pytest.approx(epoch)
    assert attrs["last_run_ts"] == pytest.approx(epoch)


def test_control_cycle_does_not_move_epoch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_700_000_000.0
    runtime._anchor_schedule_epoch(epoch)
    runtime._schedule_nmpc_worker = lambda: None  # type: ignore[method-assign]
    asyncio.run(runtime.run_control_cycle())
    assert runtime._last_control_ts == pytest.approx(epoch)
    assert runtime._last_nmpc_ts == pytest.approx(epoch)
    assert runtime._last_control_ran_ts is not None


def test_slow_slot_due_follows_epoch_not_finish_time(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_000.0
    runtime._anchor_schedule_epoch(epoch)
    runtime._last_nmpc_slow_slot = 0
    assert runtime._nmpc_slow_slot_due(now=epoch + 1799.0) is False
    assert runtime._nmpc_slow_slot_due(now=epoch + 1800.0) is True


def test_nmpc_due_ignores_fast_step_count() -> None:
    room = Room("living_room", 5e6, 0.05, temperature=18.0, setpoint=21.0)
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    ctrl = HeatingMPCController(HouseModel([room]), [heater], horizon=2, dt=900.0)
    ctrl.set_accepted_path(np.full((ctrl.timing.n_slow, 1), 0.4), np.full((2, 1), 21.0))
    ctrl._nmpc_k = ctrl.timing.m + 5
    assert ctrl.nmpc_due is False


def test_worker_finish_does_not_reset_two_hour_remaining(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = time.time()
    runtime._anchor_schedule_epoch(epoch)
    started = threading.Event()

    def _fake_solve():
        started.set()
        time.sleep(0.08)
        return {
            "accepted": False,
            "u_star": np.zeros((1, 1)),
            "t_ref": np.zeros((2, 1)),
            "fun": 1.0,
        }

    runtime.control_engine.solve_nmpc_blocking = _fake_solve  # type: ignore[method-assign]
    runtime.control_engine.mark_nmpc_busy = lambda: None  # type: ignore[method-assign]
    runtime._schedule_nmpc_worker()
    assert started.wait(timeout=2.0)
    thread = runtime._nmpc_thread
    assert thread is not None
    thread.join(timeout=2.0)
    assert runtime._last_nmpc_ts == pytest.approx(epoch)
    elapsed = time.time() - epoch
    remaining = 1800.0 - (elapsed % 1800.0)
    assert remaining < 1800.0
    assert remaining > 1800.0 - 2.0
