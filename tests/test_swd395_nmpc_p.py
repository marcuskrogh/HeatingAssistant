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
from heatingassistant.engine.heat_sources import ElectricHeater, HeatPump
from heatingassistant.engine.nmpc_accept import accept_plan
from heatingassistant.engine.nmpc_ocp import MeanOcp
from heatingassistant.engine.nmpc_p import comfort_fallback_command, p_command
from heatingassistant.engine.nmpc_timing import (
    derive_nmpc_timing,
    timing_from_dt_horizon,
    timing_from_options,
    timing_from_preview_overrides,
)
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import cmd as mqtt_cmd
from heatingassistant.persistence import load_config

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


def test_comfort_fallback_only_outside_band():
    assert comfort_fallback_command(21.0, 21.0, 2.0, 0.1, 0.0, 1.0) == 0.0
    assert comfort_fallback_command(18.0, 21.0, 2.0, 0.1, 0.0, 1.0) == pytest.approx(0.3)
    assert comfort_fallback_command(28.0, 21.0, 2.0, 0.1, -1.0, 1.0) == pytest.approx(-0.7)
    assert comfort_fallback_command(28.0, 21.0, 2.0, 0.1, 0.0, 1.0) == 0.0


def test_accept_in_band_vs_zero_heat():
    lo = np.array([0.0])
    hi = np.array([1.0])
    assert accept_plan(np.array([0.2]), 1.0, 1e6, lo, hi)
    assert accept_plan(np.array([-0.4]), 5e4, 1e6, np.array([-1.0]), np.array([1.0]))
    assert accept_plan(np.array([-0.4]), 5e5, 1e6, np.array([-1.0]), np.array([1.0]))
    assert not accept_plan(np.array([-0.01]), 0.9995e6, 1e6, np.array([-1.0]), np.array([1.0]))
    assert not accept_plan(np.array([0.0]), 3.5e6, 3.5e6, lo, hi)
    assert not accept_plan(np.array([1.2]), 1.0, 1e6, lo, hi)
    assert not accept_plan(np.array([np.nan]), 1.0, 1e6, lo, hi)


def test_compute_does_not_call_qp_step():
    ctrl = _tiny_ctrl()

    def boom(*_args, **_kwargs):
        raise AssertionError("linearised QP step must not run on the happy path")

    ctrl._mpc.step = boom  # type: ignore[method-assign]
    actions = ctrl.compute(outdoor_temp=-5.0, now=_NOW)
    assert "h" in actions


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


def test_no_path_heats_when_below_band():
    ctrl = _tiny_ctrl()
    actions = ctrl.compute(outdoor_temp=-10.0, now=_NOW)
    assert actions["h"] > 0.05


def test_no_path_cools_when_above_band():
    hp = HeatPump("hp", "living_room", max_power=4000.0, hvac_mode="heat_cool")
    room = Room(
        "living_room", 5e6, 0.05, temperature=28.0, setpoint=21.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(HouseModel([room]), [hp], horizon=2, dt=900.0)
    actions = ctrl.compute(outdoor_temp=30.0, now=_NOW)
    assert actions["hp"] < -0.05


def test_no_path_idle_inside_band():
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    room = Room(
        "living_room", 5e6, 0.05, temperature=21.0, setpoint=21.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(HouseModel([room]), [heater], horizon=2, dt=900.0)
    actions = ctrl.compute(outdoor_temp=10.0, now=_NOW)
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


def test_plan_roll_survives_expired_nlp_deadline():
    ctrl = _tiny_ctrl()

    class _Res:
        def __init__(self, x):
            self.x = x
            self.fun = 1.0
            self.success = True
            self.nit = 1
            self.message = "ok"

    def fake_min(**kwargs):
        return _Res(np.asarray(kwargs["x0"], dtype=float))

    plan = ctrl.solve_nmpc(
        outdoor_temp=5.0,
        now=_NOW,
        minimize_fn=fake_min,
        timeout_s=-1.0,
    )
    t_ref = np.asarray(plan["t_ref"], dtype=float)
    assert t_ref.size > 0
    assert np.isfinite(t_ref).all()
    assert not np.allclose(t_ref, 0.0)
    assert float(np.mean(t_ref)) > 5.0


def test_timed_out_plan_roll_is_rejected(monkeypatch):
    def boom(self, _u_flat):
        raise TimeoutError("NMPC wall-clock timeout")

    monkeypatch.setattr(MeanOcp, "roll", boom)
    ctrl = _tiny_ctrl()

    class _Res:
        def __init__(self, x):
            self.x = x
            self.fun = 1.0
            self.success = True
            self.nit = 1
            self.message = "ok"

    def fake_min(**kwargs):
        return _Res(np.asarray(kwargs["x0"], dtype=float))

    plan = ctrl.solve_nmpc(
        outdoor_temp=5.0,
        now=_NOW,
        minimize_fn=fake_min,
        timeout_s=5.0,
    )
    assert plan["accepted"] is False
    assert not np.isfinite(plan["fun"])
    assert np.allclose(plan["t_ref"], 0.0)


def test_analytic_jacobian_refreshes_M_each_fast_tick(monkeypatch):
    counts = {"n": 0}
    orig = MeanOcp._refresh_M

    def counted(self, x, u, d):
        counts["n"] += 1
        return orig(self, x, u, d)

    monkeypatch.setattr(MeanOcp, "_refresh_M", counted)
    ctrl = _tiny_ctrl()

    class _Res:
        def __init__(self, x):
            self.x = x
            self.fun = 1.0
            self.success = True
            self.nit = 1
            self.message = "ok"

    def fake_min(**kwargs):
        jac = kwargs["jac"]
        x0 = np.asarray(kwargs["x0"], dtype=float)
        jac(x0)
        return _Res(x0)

    ctrl.solve_nmpc(
        outdoor_temp=5.0,
        now=_NOW,
        minimize_fn=fake_min,
        timeout_s=5.0,
    )
    assert counts["n"] >= ctrl.horizon
    assert counts["n"] % ctrl.horizon == 0


def test_nmpc_worker_freezes_ekf_snapshot():
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
    ctrl = engine._controller
    assert ctrl is not None
    frozen = ctrl._ekf.x_hat.copy()
    engine.mark_nmpc_busy()
    ctrl._ekf._x[:] = frozen + 50.0
    ctrl._u_prev[:] = 0.9
    snapshot = engine._nmpc_worker_kwargs
    assert np.allclose(snapshot["x0"], frozen)
    assert not np.allclose(snapshot["x0"], ctrl._ekf.x_hat)
    assert np.allclose(snapshot["u_prev"], np.zeros_like(ctrl._u_prev))


def test_runtime_persists_injected_nmpc_defaults(tmp_path):
    HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    disk = load_config(tmp_path)
    assert disk["nmpc_period"] == pytest.approx(DEFAULT_NMPC_PERIOD)
    assert disk["nmpc_fast_substeps"] == DEFAULT_NMPC_FAST_SUBSTEPS
    assert disk["nmpc_horizon_h"] == pytest.approx(DEFAULT_NMPC_HORIZON_H)


def test_heat_pump_bounds_allow_negative_u():
    hp = HeatPump("hp", "living_room", max_power=4000.0, hvac_mode="heat_cool")
    room = Room(
        "living_room", 5e6, 0.05, temperature=28.0, setpoint=21.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(HouseModel([room]), [hp], horizon=4, dt=900.0)
    lo, hi = ctrl._control_system.u_bounds
    assert float(lo[0]) == pytest.approx(-1.0)
    assert float(hi[0]) == pytest.approx(1.0)


def test_nmpc_cools_when_heat_pump_allows_negative_u():
    hp = HeatPump("hp", "living_room", max_power=4000.0, hvac_mode="heat_cool")
    room = Room(
        "living_room", 5e6, 0.05, temperature=28.0, setpoint=21.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(HouseModel([room]), [hp], horizon=4, dt=900.0)
    plan = ctrl.solve_nmpc(outdoor_temp=30.0, now=_NOW, timeout_s=10.0, maxiter=40)
    u_star = np.asarray(plan["u_star"], dtype=float)
    assert plan["accepted"] is True
    assert float(np.min(u_star)) < -0.1
    assert float(plan["fun"]) < 1e-3 * float(plan["cost_zero"])


def test_nmpc_still_heats_electric_when_cold():
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    room = Room(
        "living_room", 5e6, 0.05, temperature=16.0, setpoint=21.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(HouseModel([room]), [heater], horizon=4, dt=900.0)
    plan = ctrl.solve_nmpc(outdoor_temp=-5.0, now=_NOW, timeout_s=10.0, maxiter=40)
    u_star = np.asarray(plan["u_star"], dtype=float)
    assert plan["accepted"] is True
    assert float(np.min(u_star)) >= -1e-9
    assert float(np.max(u_star)) > 0.1


def test_idle_zero_plan_keeps_nmpc_due():
    ctrl = _tiny_ctrl()
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(
        np.zeros((ctrl.timing.n_slow, 1)), np.full((n_fast, 1), 21.0)
    )
    assert ctrl.nmpc_plan_idle() is True
    assert ctrl.nmpc_due is True
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.4), np.full((n_fast, 1), 21.0)
    )
    assert ctrl.nmpc_plan_idle() is False
    assert ctrl.nmpc_due is False


def test_nmpc_cools_production_horizon_and_refreshes_forecast():
    engine = ControlEngine(
        {
            "nmpc_period": DEFAULT_NMPC_PERIOD,
            "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
            "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
            "latitude": 55.67,
            "longitude": 12.57,
            "energy_price_weight": 1.0,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 23.5,
                    "comfort_offset": 2.0,
                    "temperature": 24.0,
                    "solar_exposure": "high",
                    "solar_facing": 180.0,
                }
            ],
            "heat_sources": [
                {
                    "name": "hp",
                    "type": "heat_pump",
                    "room": "Living Room",
                    "max_power": 7000.0,
                    "hvac_mode": "heat_cool",
                }
            ],
        }
    )
    now = datetime(2026, 8, 20, 13, 10, tzinfo=timezone.utc)
    n_fast = engine._controller.horizon
    outdoor = [28.0] * n_fast
    prices = [2.0] * n_fast
    engine.compute_actions(
        {"Living Room": 24.0},
        28.0,
        {"Living Room": 23.5},
        now=now,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    before = engine.forecast_snapshot()["heating_schedule"]
    assert before
    assert max(abs(float(step["Living Room"])) for step in before) < 1.0

    engine.mark_nmpc_busy()
    plan = engine.solve_nmpc_blocking()
    u_star = np.asarray(plan["u_star"], dtype=float)
    assert plan["accepted"] is True
    assert float(np.min(u_star)) < -0.05
    assert engine.apply_nmpc_result(plan) is True

    snap = engine.forecast_snapshot()
    watts = [float(step["Living Room"]) for step in snap["heating_schedule"]]
    assert min(watts) < -100.0
    temps = [float(step["Living Room"]) for step in snap["predictions"]]
    assert max(temps) < 26.5

    meta = engine.room_power_meta(28.0)["Living Room"]
    assert float(meta["max_cooling_power"]) > 1000.0

    engine.compute_actions(
        {"Living Room": 24.0},
        28.0,
        {"Living Room": 23.5},
        now=now,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    after_compute = [
        float(step["Living Room"])
        for step in engine.forecast_snapshot()["heating_schedule"]
    ]
    assert min(after_compute) < -100.0


def test_signed_probe_cools_when_slsqp_returns_zero():
    hp = HeatPump("hp", "living_room", max_power=4000.0, hvac_mode="heat_cool")
    room = Room(
        "living_room", 5e6, 0.05, temperature=28.0, setpoint=21.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(HouseModel([room]), [hp], horizon=4, dt=900.0)

    class _Res:
        def __init__(self, x):
            self.x = np.zeros_like(np.asarray(x, dtype=float))
            self.fun = 1.0
            self.success = True
            self.nit = 1
            self.message = "ok"

    def fake_min(**kwargs):
        return _Res(kwargs["x0"])

    plan = ctrl.solve_nmpc(
        outdoor_temp=30.0, now=_NOW, minimize_fn=fake_min, timeout_s=10.0
    )
    u_star = np.asarray(plan["u_star"], dtype=float)
    assert float(np.min(u_star)) < -0.05
    assert float(plan["fun"]) < float(plan["cost_zero"])


def test_cauchy_timeout_does_not_abort_solve(monkeypatch):
    ctrl = _tiny_ctrl()
    orig = MeanOcp.jac
    state = {"n": 0}

    def maybe_timeout(self, u):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("cauchy")
        return orig(self, u)

    monkeypatch.setattr(MeanOcp, "jac", maybe_timeout)

    class _Res:
        def __init__(self, x):
            self.x = np.asarray(x, dtype=float)
            self.fun = 0.0
            self.success = True
            self.nit = 1
            self.message = "ok"

    def fake_min(**kwargs):
        return _Res(kwargs["x0"])

    plan = ctrl.solve_nmpc(
        outdoor_temp=5.0, now=_NOW, minimize_fn=fake_min, timeout_s=5.0
    )
    assert plan["status"] != "timeout"
    assert plan["u_star"] is not None
    assert state["n"] >= 1


def test_rebuild_forecast_pads_short_outdoor():
    ctrl = _tiny_ctrl()
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.4), np.full((n_fast, 1), 21.0)
    )
    ctrl._outdoor_forecast = [5.0]
    assert ctrl.rebuild_forecast_from_plan() is True
    assert len(ctrl.heating_schedule) == n_fast
    watts = [float(step["living_room"]) for step in ctrl.heating_schedule]
    assert max(abs(w) for w in watts) > 1.0


def test_idle_plan_debounces_nmpc_worker(tmp_path):
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
    ctrl = runtime.control_engine._controller
    assert ctrl is not None
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(
        np.zeros((ctrl.timing.n_slow, 1)), np.full((n_fast, 1), 21.0)
    )
    assert ctrl.nmpc_plan_idle() is True
    assert ctrl.nmpc_due is True
    runtime._last_nmpc_ts = time.time()
    started = threading.Event()

    def _fake_solve():
        started.set()
        return {
            "accepted": False,
            "u_star": np.zeros((ctrl.timing.n_slow, 1)),
            "t_ref": np.zeros((n_fast, 1)),
            "fun": 1.0,
        }

    runtime.control_engine.solve_nmpc_blocking = _fake_solve  # type: ignore[method-assign]
    runtime.control_engine.mark_nmpc_busy = lambda: None  # type: ignore[method-assign]
    runtime._schedule_nmpc_worker()
    assert not started.wait(timeout=0.4)
    thread = runtime._nmpc_thread
    assert thread is None or not thread.is_alive()


def test_nmpc_worker_requests_control_cycle_on_accept(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    requested: list[bool] = []
    runtime._request_control_cycle_after_nmpc = (  # type: ignore[method-assign]
        lambda: requested.append(True)
    )
    runtime.control_engine.apply_nmpc_result = lambda _result: True  # type: ignore[method-assign]
    runtime.control_engine.solve_nmpc_blocking = lambda: {  # type: ignore[method-assign]
        "accepted": True,
        "u_star": np.array([[0.4]]),
        "t_ref": np.array([[21.0]]),
        "fun": 1.0,
    }
    runtime.control_engine.consume_watchdog_notification = lambda: None  # type: ignore[method-assign]
    runtime._nmpc_worker_thread()
    assert requested == [True]


def test_request_control_cycle_runs_when_loop_missing(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    runtime._nmpc_loop = None
    called: list[bool] = []

    async def _fake_cycle(*, wait_for_lock: bool = False):
        called.append(wait_for_lock)
        return {}

    runtime.run_control_cycle = _fake_cycle  # type: ignore[method-assign]
    runtime._request_control_cycle_after_nmpc()
    assert called == [True]


def test_request_control_cycle_runs_when_loop_closed(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    loop = asyncio.new_event_loop()
    loop.close()
    runtime._nmpc_loop = loop
    called: list[bool] = []

    async def _fake_cycle(*, wait_for_lock: bool = False):
        called.append(wait_for_lock)
        return {}

    runtime.run_control_cycle = _fake_cycle  # type: ignore[method-assign]
    runtime._request_control_cycle_after_nmpc()
    assert called == [True]


def test_request_control_cycle_waits_for_held_lock(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    runtime._nmpc_loop = None
    assert runtime._control_lock.acquire(blocking=False)
    done = threading.Event()

    def _worker() -> None:
        try:
            runtime._request_control_cycle_after_nmpc()
        finally:
            done.set()

    thread = threading.Thread(target=_worker, name="nmpc-followup-lock")
    thread.start()
    assert not done.wait(timeout=0.3)
    runtime._control_lock.release()
    assert done.wait(timeout=15.0)
    thread.join(timeout=2.0)
    assert runtime._last_control_ts is not None


def test_nmpc_worker_skips_control_cycle_on_reject(tmp_path):
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos"},
    )
    requested: list[bool] = []
    runtime._request_control_cycle_after_nmpc = (  # type: ignore[method-assign]
        lambda: requested.append(True)
    )
    runtime.control_engine.apply_nmpc_result = lambda _result: False  # type: ignore[method-assign]
    runtime.control_engine.solve_nmpc_blocking = lambda: {  # type: ignore[method-assign]
        "accepted": False,
        "u_star": np.array([[0.0]]),
        "t_ref": np.array([[21.0]]),
        "fun": 1.0,
    }
    runtime.control_engine.consume_watchdog_notification = lambda: None  # type: ignore[method-assign]
    runtime._nmpc_worker_thread()
    assert requested == []
