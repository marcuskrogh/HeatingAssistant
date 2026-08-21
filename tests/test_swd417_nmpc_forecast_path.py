"""Room-view Forecast resims the remaining NMPC U*, not an unshifted replay."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.integrator import implicit_euler_substeps
from heatingassistant.engine.thermal_model import HouseModel, Room

_NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)


def _ctrl() -> HeatingMPCController:
    room = Room(
        "living_room",
        5e6,
        0.05,
        temperature=22.0,
        setpoint=21.0,
        comfort_offset=2.0,
    )
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    return HeatingMPCController(
        HouseModel([room]),
        [heater],
        nmpc_period=1800.0,
        nmpc_fast_substeps=4,
        nmpc_horizon_h=1.5,
    )


def _distinct_U(ctrl: HeatingMPCController) -> np.ndarray:
    n_slow = ctrl.timing.n_slow
    return np.array([[0.15 + 0.25 * n] for n in range(n_slow)], dtype=float)


def _install_plan(ctrl: HeatingMPCController, U: np.ndarray) -> np.ndarray:
    n_fast = ctrl.horizon
    t_ref = np.full((n_fast, 1), 22.0)
    ctrl.set_accepted_path(U, t_ref)
    ctrl._outdoor_forecast = [5.0] * n_fast
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    assert ctrl.rebuild_forecast_from_plan() is True
    return np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )


def _roll_state(
    ctrl: HeatingMPCController,
    U_fast: np.ndarray,
    n_steps: int,
) -> np.ndarray:
    x = ctrl._ekf.x_hat.copy()
    p = np.array([], dtype=float)
    outdoor = 5.0
    solar = {"living_room": 0.0}
    for k in range(n_steps):
        u_k = U_fast[k]
        d_k = ctrl._control_system.disturbance_vector(outdoor, solar)
        rhs = lambda xx, u=u_k, d=d_k: ctrl._system.f(xx, u, d, p, 0.0)
        jac = lambda xx, u=u_k, d=d_k: ctrl._system.dfdx(xx, u, d, p, 0.0)
        x = implicit_euler_substeps(rhs, jac, x, ctrl._dt, ctrl._n_int_steps)
    return x


def test_apply_resim_matches_full_remaining_u_from_x0():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    temps = _install_plan(ctrl, U)
    n_fast = ctrl.horizon
    U_rem = ctrl._forecast_U(n_fast)
    U_fast = np.repeat(U, ctrl.timing.m, axis=0)
    assert np.allclose(U_rem, U_fast[:n_fast])
    reroll = ctrl._compute_nonlinear_predictions(
        U_rem,
        [5.0] * n_fast,
        [{"living_room": 0.0} for _ in range(n_fast)],
        ctrl._system._room_list,
        ctrl._system._n_rooms,
    )
    reroll_t = np.array([float(step["living_room"]) for step in reroll])
    assert float(np.max(np.abs(reroll_t - temps))) < 0.05


def test_remaining_u_is_two_hour_zoh_shifted_by_plan_index():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    _install_plan(ctrl, U)
    m = ctrl.timing.m
    n_fast = ctrl.horizon
    U_fast = np.repeat(U, m, axis=0)
    start = 3
    ctrl._nmpc_k = start
    remaining = ctrl._forecast_U(n_fast)
    expected = np.vstack(
        [U_fast[start:], np.tile(U_fast[-1:], (start, 1))]
    )
    assert np.allclose(remaining, expected)
    assert remaining.shape[0] == n_fast
    first_hold = m - (start % m)
    assert np.allclose(remaining[:first_hold], U_fast[start])
    assert np.allclose(remaining[first_hold : first_hold + m], U[1 + start // m])


def test_remaining_resim_matches_t_ref_tail_when_state_followed_the_plan():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    t_full = _install_plan(ctrl, U)
    n_fast = ctrl.horizon
    U_fast = np.repeat(U, ctrl.timing.m, axis=0)
    start = 3
    x3 = _roll_state(ctrl, U_fast, start)
    _, P = ctrl.ekf_state
    assert ctrl.restore_ekf_state(x3, P) is True
    ctrl._nmpc_k = start
    assert ctrl.rebuild_forecast_from_plan() is True
    t_rem = np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )
    n_overlap = n_fast - start
    err = float(np.max(np.abs(t_rem[:n_overlap] - t_full[start:])))
    assert err < 0.05

    ctrl._nmpc_k = 0
    U_unshifted = np.asarray(ctrl._nmpc_U_fast[:n_fast], dtype=float)
    t_wrong = ctrl._compute_nonlinear_predictions(
        U_unshifted,
        [5.0] * n_fast,
        [{"living_room": 0.0} for _ in range(n_fast)],
        ctrl._system._room_list,
        ctrl._system._n_rooms,
    )
    t_wrong_v = np.array([float(step["living_room"]) for step in t_wrong])
    wrong_err = float(np.max(np.abs(t_wrong_v[:n_overlap] - t_full[start:])))
    assert wrong_err > err + 0.1


def test_compute_publishes_remaining_u_then_advances_plan_index():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    _install_plan(ctrl, U)
    n_fast = ctrl.horizon
    U_fast = np.repeat(U, ctrl.timing.m, axis=0)
    assert ctrl._nmpc_k == 0
    ctrl.compute(
        outdoor_temp=5.0,
        solar_gains={"living_room": 0.0},
        now=_NOW,
        outdoor_forecast=[5.0] * n_fast,
    )
    assert ctrl._nmpc_k == 1
    first = ctrl._system.display_heating_powers(U_fast[0], 5.0)["living_room"]
    published = float(ctrl.heating_schedule[0]["living_room"])
    assert published == pytest.approx(first, rel=1e-6, abs=1.0)
    remaining = ctrl._forecast_U(n_fast)
    assert np.allclose(remaining[0], U_fast[1])


def test_rebuild_forecast_pads_short_outdoor_on_remaining_u():
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.4), np.full((n_fast, 1), 21.0)
    )
    ctrl._outdoor_forecast = [5.0]
    assert ctrl.rebuild_forecast_from_plan() is True
    assert len(ctrl.heating_schedule) == n_fast
    watts = [float(step["living_room"]) for step in ctrl.heating_schedule]
    assert max(abs(w) for w in watts) > 1.0
    assert np.ptp(watts) < 1.0
