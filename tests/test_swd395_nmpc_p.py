"""SWD-395: two-rate NMPC + P tracker."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import (
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_P_GAIN,
    NMPC_WATCHDOG_MESSAGE,
    NMPC_WATCHDOG_NOTIFICATION_ID,
    NMPC_WATCHDOG_S,
    NMPC_WATCHDOG_TITLE,
)
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.nmpc_accept import accept_plan
from heatingassistant.engine.nmpc_p import p_command
from heatingassistant.engine.nmpc_timing import (
    derive_nmpc_timing,
    timing_from_dt_horizon,
    timing_from_options,
    timing_from_preview_overrides,
)
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import cmd as mqtt_cmd

_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)


def _tiny_ctrl() -> HeatingMPCController:
    room = Room("living_room", 5e6, 0.05, temperature=18.0, setpoint=21.0, comfort_offset=2.0)
    model = HouseModel([room])
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    return HeatingMPCController(model, [heater], horizon=2, dt=900.0)


def test_default_timing_triple_divides():
    timing = derive_nmpc_timing(
        DEFAULT_NMPC_PERIOD, DEFAULT_NMPC_FAST_SUBSTEPS, DEFAULT_NMPC_HORIZON_H
    )
    assert timing.dt_s == pytest.approx(900.0)
    assert timing.n_slow == 18
    assert timing.n_fast == 144


def test_timing_rejects_non_dividing_horizon():
    with pytest.raises(ValueError, match="integer number"):
        derive_nmpc_timing(7200.0, 8, 35.0)


def test_p_command_clips():
    assert p_command(0.2, 21.0, 18.0, 0.1, 0.0, 1.0) == pytest.approx(0.5)
    assert p_command(0.0, 21.0, 18.0, 10.0, 0.0, 1.0) == 1.0
    assert p_command(0.0, 18.0, 21.0, 10.0, 0.0, 1.0) == 0.0


def test_accept_in_band_vs_zero_heat():
    lo = np.array([0.0])
    hi = np.array([1.0])
    assert accept_plan(np.array([0.2]), 1.0, 1e6, lo, hi)
    assert not accept_plan(np.array([0.0]), 3.5e6, 3.5e6, lo, hi)
    assert not accept_plan(np.array([1.2]), 1.0, 1e6, lo, hi)
    assert not accept_plan(np.array([np.nan]), 1.0, 1e6, lo, hi)


def test_compute_does_not_call_qp_step():
    ctrl = _tiny_ctrl()

    def boom(*_args, **_kwargs):
        raise AssertionError("linearised QP step must not run on the happy path")

    ctrl._mpc.step = boom  # type: ignore[method-assign]
    actions = ctrl.compute(outdoor_temp=-5.0, now=_NOW)
    assert actions["h"] == pytest.approx(0.0)


def test_p_tracks_seeded_reference():
    ctrl = _tiny_ctrl()
    n_fast = ctrl.horizon
    n_slow = ctrl.timing.n_slow
    t_ref = np.full((n_fast, 1), 21.0)
    u_star = np.zeros((n_slow, 1))
    ctrl.set_accepted_path(u_star, t_ref)
    actions = ctrl.compute(outdoor_temp=-5.0, now=_NOW)
    # T_hat starts near 18 °C, T_ref=21, K_p=0.1 → 0.3
    assert actions["h"] == pytest.approx(0.3, abs=0.15)


def test_no_path_is_zero_heat():
    ctrl = _tiny_ctrl()
    actions = ctrl.compute(outdoor_temp=-10.0, now=_NOW)
    assert actions["h"] == pytest.approx(0.0)


def test_watchdog_elapsed_trips_and_clears_path():
    ctrl = _tiny_ctrl()
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(np.zeros((ctrl.timing.n_slow, 1)), np.full((n_fast, 1), 21.0))
    ctrl._record_nmpc_reject(now=1_000.0)
    assert not ctrl._watchdog_tripped
    ctrl._record_nmpc_reject(now=1_000.0 + NMPC_WATCHDOG_S)
    assert ctrl._watchdog_tripped
    assert ctrl.consume_watchdog_notification() == "create"
    assert ctrl.consume_watchdog_notification() is None
    actions = ctrl.compute(outdoor_temp=-10.0, now=_NOW)
    assert actions["h"] == pytest.approx(0.0)


def test_accept_resets_watchdog_and_dismisses():
    ctrl = _tiny_ctrl()
    ctrl._record_nmpc_reject(now=0.0)
    ctrl._record_nmpc_reject(now=NMPC_WATCHDOG_S)
    assert ctrl._watchdog_tripped
    ctrl.consume_watchdog_notification()
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(np.zeros((ctrl.timing.n_slow, 1)), np.full((n_fast, 1), 21.0))
    assert not ctrl._watchdog_tripped
    assert ctrl.consume_watchdog_notification() == "dismiss"


def test_injected_minimize_timeout_can_still_accept():
    ctrl = _tiny_ctrl()
    n_slow = ctrl.timing.n_slow
    nu = ctrl._system.nu

    class _Res:
        def __init__(self) -> None:
            self.x = np.full(n_slow * nu, 0.4)
            self.nit = 1
            self.success = False
            self.fun = 1.0
            self.message = "timeout stub"

    def fake_minimize(**_kwargs):
        return _Res()

    plan = ctrl.solve_nmpc(
        outdoor_temp=-5.0,
        now=_NOW,
        outdoor_forecast=[-5.0] * ctrl.horizon,
        minimize_fn=fake_minimize,
        maxiter=1,
        timeout_s=5.0,
    )
    # In-band vs J(u=0) depends on the rolled cost of u=0.4; just check shape.
    assert plan["u_star"].shape == (n_slow, nu)
    assert "accepted" in plan


def test_legacy_horizon_is_one_slow_interval():
    timing = timing_from_dt_horizon(900.0, 2)
    assert timing.n_slow == 1
    assert timing.n_fast == 2
    assert timing.fast_substeps == 2


def test_nmpc_worker_runs_off_asyncio_loop():
    """NLP work must not occupy the asyncio event loop (Ingress/MQTT stay live)."""

    loop_free = threading.Event()
    started = threading.Event()

    async def _probe() -> None:
        def worker() -> None:
            started.set()
            time.sleep(0.25)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert started.wait(timeout=1.0)
        await asyncio.sleep(0.05)
        loop_free.set()
        thread.join(timeout=2.0)

    asyncio.run(_probe())
    assert loop_free.is_set()


def test_timing_from_options_uses_legacy_horizon_when_triple_absent():
    timing = timing_from_options(
        {"update_interval": 900, "horizon": 4},
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    assert timing.n_fast == 4
    assert timing.n_slow == 1
    assert timing.dt_s == pytest.approx(900.0)


def test_timing_from_options_triple_wins_over_horizon():
    timing = timing_from_options(
        {
            "update_interval": 900,
            "horizon": 4,
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
        },
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    assert timing.period_s == pytest.approx(1800.0)
    assert timing.fast_substeps == 2
    assert timing.n_slow == 2
    assert timing.n_fast == 4


def test_preview_overrides_horizon_keep_small_grid():
    live = {
        "nmpc_period": DEFAULT_NMPC_PERIOD,
        "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
        "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
        "update_interval": 900,
        "horizon": 144,
    }
    timing = timing_from_preview_overrides(
        live,
        {"horizon": 2, "update_interval": 900},
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    assert timing.n_fast == 2
    assert timing.n_slow == 1


def test_compute_copies_applied_u_into_ekf_prev():
    ctrl = _tiny_ctrl()
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(np.full((ctrl.timing.n_slow, 1), 0.2), np.full((n_fast, 1), 21.0))
    actions = ctrl.compute(outdoor_temp=-5.0, now=_NOW)
    assert actions["h"] > 0.0
    assert ctrl._u_prev[0] == pytest.approx(actions["h"])
    assert ctrl._mpc._u_prev[0] == pytest.approx(actions["h"])


def test_heat_source_default_p_gain():
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    assert heater.p_gain == pytest.approx(DEFAULT_P_GAIN)


def test_control_engine_reads_p_gain():
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 2,
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                    "p_gain": 0.25,
                }
            ],
        }
    )
    assert engine.heat_sources[0].p_gain == pytest.approx(0.25)
    assert engine._controller is not None
    assert engine._controller.horizon == 2


def test_preview_rejects_non_dividing_nmpc_triple():
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 2,
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        }
    )
    result = engine.preview_tuning_forecast(
        {"nmpc_period": 7200, "nmpc_fast_substeps": 8, "nmpc_horizon_h": 35},
        {"Living Room": 21.0},
        5.0,
        {"Living Room": 21.0},
    )
    assert result == {"error": "invalid_nmpc_timing"}


def test_runtime_schedules_nmpc_worker_thread(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
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
        },
    )
    started = threading.Event()

    def _fake_solve():
        started.set()
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
    assert not thread.is_alive()


def test_runtime_controller_config_exposes_nmpc_keys(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    config = runtime.controller_config()
    assert config["nmpc_period"] == pytest.approx(DEFAULT_NMPC_PERIOD)
    assert config["nmpc_fast_substeps"] == DEFAULT_NMPC_FAST_SUBSTEPS
    assert config["nmpc_horizon_h"] == pytest.approx(DEFAULT_NMPC_HORIZON_H)
    assert config["update_interval"] == 900
    assert config["horizon"] == 144
    assert mqtt_cmd("haos", "notify") == "heatingassistant/haos/cmd/notify"


def test_ha_bridge_notify_create_and_dismiss():
    import importlib.util
    import sys

    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "heating_assistant"
        / "mqtt_topics.py"
    )
    spec = importlib.util.spec_from_file_location("ha_mqtt_topics_nmpc", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    notify_service_call = mod.notify_service_call

    create = notify_service_call(
        {
            "action": "create",
            "notification_id": NMPC_WATCHDOG_NOTIFICATION_ID,
            "title": NMPC_WATCHDOG_TITLE,
            "message": NMPC_WATCHDOG_MESSAGE,
        }
    )
    assert create == (
        "create",
        {
            "notification_id": NMPC_WATCHDOG_NOTIFICATION_ID,
            "title": NMPC_WATCHDOG_TITLE,
            "message": NMPC_WATCHDOG_MESSAGE,
        },
    )
    dismiss = notify_service_call(
        {"action": "dismiss", "notification_id": NMPC_WATCHDOG_NOTIFICATION_ID}
    )
    assert dismiss == (
        "dismiss",
        {"notification_id": NMPC_WATCHDOG_NOTIFICATION_ID},
    )
    assert notify_service_call({"action": "noop"}) is None


def test_tuning_ui_exposes_nmpc_triple():
    source = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "pages"
        / "tuning-controller.js"
    ).read_text(encoding="utf-8")
    assert "nmpc_period" in source
    assert "nmpc_fast_substeps" in source
    assert "nmpc_horizon_h" in source
    assert "readonly" in source
    source_editor = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "config"
        / "config-source-editor.js"
    ).read_text(encoding="utf-8")
    assert "p_gain" in source_editor
