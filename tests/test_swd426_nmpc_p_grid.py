"""SWD-426: shared NMPC/P grid, independent solves, KPI computing flags."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.nmpc_timing import (
    derive_nmpc_timing,
    grid_remaining_s,
    grid_slot_index,
    slow_slot_start_s,
)
from heatingassistant.mqtt.bridge import InMemoryMqttBus

_STATIC = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "app"
    / "static"
)


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


def test_remaining_times_coincide_on_substep_grid() -> None:
    timing = derive_nmpc_timing(7200.0, 8, 36.0)
    epoch = 1_000.0
    now = epoch + 7200.0 - 563.0
    nmpc_rem = grid_remaining_s(epoch, timing.period_s, now)
    control_rem = grid_remaining_s(epoch, timing.dt_s, now)
    assert nmpc_rem == pytest.approx(563.0)
    assert control_rem == pytest.approx(563.0)

    boundary = epoch + 7200.0
    assert grid_remaining_s(epoch, timing.period_s, boundary) == pytest.approx(7200.0)
    assert grid_remaining_s(epoch, timing.dt_s, boundary) == pytest.approx(900.0)


def test_slow_slot_start_and_fast_index() -> None:
    epoch = 1_000.0
    period = 1800.0
    dt = 900.0
    now = epoch + 1800.0 + 20.0
    assert slow_slot_start_s(epoch, period, now) == pytest.approx(epoch + 1800.0)
    assert grid_slot_index(epoch + 1800.0, dt, now) == 0
    assert grid_slot_index(epoch + 1800.0, dt, epoch + 1800.0 + 900.0) == 1


def test_hass_states_share_epoch_and_derived_dt(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_700_000_000.0
    runtime._anchor_schedule_epoch(epoch)
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["last_nmpc_ts"] == pytest.approx(epoch)
    assert attrs["last_run_ts"] == pytest.approx(epoch)
    assert attrs["dt_s"] == pytest.approx(900.0)
    assert attrs["nmpc_period_s"] == pytest.approx(1800.0)
    assert attrs["nmpc_computing"] is False
    assert attrs["control_computing"] is False


def test_restore_coerces_control_stamp_to_nmpc_epoch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._last_nmpc_ts = 1_700_000_000.0
    runtime._last_control_ts = 1_700_000_500.0
    runtime._save_runtime_state()

    restarted = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options=runtime.options
    )
    assert restarted._last_nmpc_ts == pytest.approx(1_700_000_000.0)
    assert restarted._last_control_ts == pytest.approx(1_700_000_000.0)


def test_sync_p_index_uses_plan_epoch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_700_000_000.0
    runtime._anchor_schedule_epoch(epoch)
    ctrl = runtime.control_engine._controller
    assert ctrl is not None
    n_fast = ctrl.timing.n_fast
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.4),
        np.full((n_fast, 1), 21.0),
        plan_epoch=epoch,
    )
    runtime._sync_p_fast_index(epoch + 900.0)
    assert ctrl._nmpc_k == 1


def test_p_runs_while_nmpc_busy_on_previous_plan(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_700_000_000.0
    runtime._anchor_schedule_epoch(epoch)
    runtime._schedule_nmpc_worker = lambda: None  # type: ignore[method-assign]
    ctrl = runtime.control_engine._controller
    assert ctrl is not None
    n_fast = ctrl.timing.n_fast
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.4),
        np.full((n_fast, 1), 21.0),
        plan_epoch=epoch,
    )
    ctrl._nmpc_busy = True
    runtime._nmpc_computing = True
    runtime.tag_values["living_temp"] = 18.0
    runtime.tag_statuses["living_temp"] = "GOOD"
    asyncio.run(runtime.run_control_cycle())
    assert ctrl._nmpc_busy is True
    assert runtime._nmpc_computing is True
    assert runtime._control_computing is False
    assert "heater" in runtime.actuator_outputs
    assert abs(float(runtime.actuator_outputs["heater"])) > 0.0


def test_nmpc_worker_does_not_run_p_on_accept(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    cycles = {"n": 0}

    async def counted(*, wait_for_lock: bool = False):
        cycles["n"] += 1
        return {}

    runtime.run_control_cycle = counted  # type: ignore[method-assign]
    runtime.control_engine.apply_nmpc_result = (  # type: ignore[method-assign]
        lambda _result, **_kwargs: True
    )
    runtime.control_engine.solve_nmpc_blocking = lambda: {  # type: ignore[method-assign]
        "accepted": True,
        "u_star": np.array([[0.4]]),
        "t_ref": np.array([[21.0]]),
        "fun": 1.0,
    }
    runtime.control_engine.consume_watchdog_notification = lambda: None  # type: ignore[method-assign]
    runtime._nmpc_computing = True
    runtime._nmpc_worker_thread()
    assert cycles["n"] == 0
    assert runtime._nmpc_computing is False


def test_computing_flags_during_nmpc_worker(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_700_000_000.0
    runtime._anchor_schedule_epoch(epoch)
    started = threading.Event()
    release = threading.Event()

    def _fake_solve():
        started.set()
        release.wait(timeout=2.0)
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
    assert runtime._nmpc_computing is True
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["nmpc_computing"] is True
    release.set()
    thread = runtime._nmpc_thread
    assert thread is not None
    thread.join(timeout=2.0)
    assert runtime._nmpc_computing is False


def test_control_cycle_does_not_schedule_nmpc(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    epoch = 1_700_000_000.0
    runtime._anchor_schedule_epoch(epoch)
    scheduled = {"n": 0}
    runtime._schedule_nmpc_worker = lambda: scheduled.__setitem__(  # type: ignore[method-assign]
        "n", scheduled["n"] + 1
    )
    asyncio.run(runtime.run_control_cycle())
    assert scheduled["n"] == 0


def test_panel_wires_shared_epoch_and_computing_overlay() -> None:
    countdown = (_STATIC / "js" / "components" / "countdown.js").read_text(
        encoding="utf-8"
    )
    gauge = (_STATIC / "js" / "components" / "gauge.js").read_text(encoding="utf-8")
    css = (_STATIC / "css" / "industrial.css").read_text(encoding="utf-8")
    overview = (_STATIC / "js" / "pages" / "overview.js").read_text(encoding="utf-8")
    room = (_STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    utils = (_STATIC / "js" / "utils.js").read_text(encoding="utf-8")
    assert "lastRunAttr: 'last_nmpc_ts'" in countdown
    assert countdown.count("lastRunAttr: 'last_nmpc_ts'") >= 2
    assert "useEntityLastUpdated: true" not in countdown
    assert "export function setGaugeComputing" in gauge
    assert "gauge--computing" in css
    assert "kpi-shimmer" in css
    assert "isComputeInProgress" in utils
    assert "setGaugeComputing" in overview
    assert "setGaugeComputing" in room
    assert "paintComputeLoading" in room
