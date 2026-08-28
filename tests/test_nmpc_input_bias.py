"""NMPC input bias (u_ref) steps on accept; warm-start recedes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater, HeatPump
from heatingassistant.engine.nmpc_ocp import shift_slow_plan
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _ctrl() -> HeatingMPCController:
    room = Room(
        "living_room", 5e6, 0.05, temperature=24.0, setpoint=24.0, comfort_offset=2.0
    )
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    return HeatingMPCController(
        HouseModel([room]),
        [heater],
        nmpc_period=7200.0,
        nmpc_fast_substeps=8,
        nmpc_horizon_h=6.0,
    )


def test_shift_slow_plan_recedes_one_interval() -> None:
    U = np.array([[0.1], [0.2], [0.3], [0.4]])
    shifted = shift_slow_plan(U)
    assert shifted.shape == U.shape
    assert np.allclose(shifted[:, 0], [0.2, 0.3, 0.4, 0.4])
    assert np.allclose(shift_slow_plan(np.array([[0.5]])), [[0.5]])


def test_accept_steps_u_ref_before_compute() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    n_slow = ctrl.timing.n_slow
    T = np.full((n_fast, 1), 24.0)
    ctrl.set_accepted_path(np.full((n_slow, 1), -0.4), T, now=_NOW.timestamp())
    ctrl.refresh_p_command()
    assert float(ctrl._u_prev[0]) == pytest.approx(-0.4, abs=0.05)

    plan = {
        "accepted": True,
        "u_star": np.full((n_slow, 1), 0.5),
        "t_ref": T,
        "fun": 1.0,
        "elapsed_s": 0.1,
    }
    assert ctrl.apply_nmpc_result(plan, now=_NOW.timestamp(), plan_epoch=_NOW.timestamp())
    # New bias is on the command without waiting for compute() / EKF.
    assert float(ctrl._p_command_vector(None, None, None)[0]) == pytest.approx(
        0.5, abs=0.05
    )
    assert float(ctrl._u_prev[0]) == pytest.approx(0.5, abs=0.05)
    warm = np.asarray(ctrl._nmpc_warm, dtype=float).reshape(n_slow, 1)
    assert float(warm[0, 0]) == pytest.approx(0.5)
    assert float(warm[-1, 0]) == pytest.approx(0.5)


def test_accept_shifts_nonconstant_warm_start() -> None:
    ctrl = _ctrl()
    n_slow = ctrl.timing.n_slow
    T = np.full((ctrl.horizon, 1), 24.0)
    U = np.linspace(-0.8, 0.0, n_slow).reshape(n_slow, 1)
    ctrl.set_accepted_path(U, T, now=_NOW.timestamp())
    warm = np.asarray(ctrl._nmpc_warm, dtype=float).reshape(n_slow, 1)
    assert np.allclose(warm[:-1], U[1:])
    assert float(warm[-1, 0]) == pytest.approx(float(U[-1, 0]))


def test_engine_accept_exposes_p_tags() -> None:
    engine = ControlEngine(
        {
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
            "rooms": [{"name": "Living Room", "setpoint": 21.0, "temperature": 21.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                    "output_tag": "heater_heat",
                }
            ],
        }
    )
    ctrl = engine._controller
    assert ctrl is not None
    n_slow = ctrl.timing.n_slow
    T = np.full((ctrl.horizon, 1), 21.0)
    plan = {
        "accepted": True,
        "u_star": np.full((n_slow, 1), 0.4),
        "t_ref": T,
        "fun": 1.0,
    }
    assert engine.apply_nmpc_result(plan, now=_NOW.timestamp()) is True
    assert engine._last_p_actions["heater_heat"] == pytest.approx(0.4, abs=0.08)


def test_worker_publishes_p_without_control_cycle(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "temperature": 21.0,
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                    "output_tag": "heater_heat",
                }
            ],
        },
    )
    cycles = {"n": 0}

    async def counted(*, wait_for_lock: bool = False):
        cycles["n"] += 1
        return {}

    runtime.run_control_cycle = counted  # type: ignore[method-assign]
    ctrl = runtime.control_engine._controller
    assert ctrl is not None
    n_slow = ctrl.timing.n_slow
    T = np.full((ctrl.horizon, 1), 21.0)
    runtime.control_engine.solve_nmpc_blocking = lambda: {  # type: ignore[method-assign]
        "accepted": True,
        "u_star": np.full((n_slow, 1), 0.35),
        "t_ref": T,
        "fun": 1.0,
    }
    runtime._nmpc_worker_thread()
    assert cycles["n"] == 0
    assert float(ctrl._u_prev[0]) == pytest.approx(0.35, abs=0.1)
    heat = runtime.actuator_outputs.get("heater_heat")
    if heat is None and runtime.actuator_outputs:
        heat = next(iter(runtime.actuator_outputs.values()))
    assert heat is not None
    assert float(heat) == pytest.approx(0.35, abs=0.1)


def test_p_command_not_first_order_when_t_matches_ref() -> None:
    hp = HeatPump("hp", "living_room", max_power=4000.0, hvac_mode="heat_cool")
    room = Room(
        "living_room", 5e6, 0.05, temperature=24.0, setpoint=24.0, comfort_offset=2.0
    )
    ctrl = HeatingMPCController(
        HouseModel([room]),
        [hp],
        nmpc_period=7200.0,
        nmpc_fast_substeps=8,
        nmpc_horizon_h=6.0,
    )
    n_slow = ctrl.timing.n_slow
    T = np.full((ctrl.horizon, 1), 24.0)
    ctrl.apply_nmpc_result(
        {
            "accepted": True,
            "u_star": np.full((n_slow, 1), -0.7),
            "t_ref": T,
            "fun": 1.0,
        },
        now=_NOW.timestamp(),
        plan_epoch=_NOW.timestamp(),
    )
    u0 = float(ctrl._u_prev[0])
    ctrl.apply_nmpc_result(
        {
            "accepted": True,
            "u_star": np.full((n_slow, 1), 0.4),
            "t_ref": T,
            "fun": 1.0,
        },
        now=_NOW.timestamp() + 7200.0,
        plan_epoch=_NOW.timestamp() + 7200.0,
    )
    u1 = float(ctrl._u_prev[0])
    assert u0 == pytest.approx(-0.7, abs=0.08)
    assert u1 == pytest.approx(0.4, abs=0.08)
    assert (u1 - u0) > 0.9
