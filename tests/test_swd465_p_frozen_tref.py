"""SWD-465: P tracks the accept-time NMPC T_ref for the slow interval."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.nmpc_p import p_command
from heatingassistant.engine.thermal_model import HouseModel, Room

_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
_ROOM = "living_room"
_EPOCH = _NOW.timestamp()


def _ctrl() -> HeatingMPCController:
    room = Room(
        _ROOM, 5e6, 0.05, temperature=22.0, setpoint=21.0, comfort_offset=2.0
    )
    heater = ElectricHeater("h", _ROOM, max_power=2000.0)
    return HeatingMPCController(
        HouseModel([room]),
        [heater],
        nmpc_period=7200.0,
        nmpc_fast_substeps=4,
        nmpc_horizon_h=4.0,
        p_deadband=0.0,
        u_ref_gate=0.0,
    )


def test_set_accepted_path_copies_t_ref_away_from_caller_buffer() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    n_slow = ctrl.timing.n_slow
    t_ref = np.linspace(25.0, 28.0, n_fast).reshape(n_fast, 1)
    u_star = np.full((n_slow, 1), 0.4)
    ctrl.set_accepted_path(u_star, t_ref, now=_EPOCH, plan_epoch=_EPOCH)
    t_ref[:, 0] = 0.0
    u_star[:, 0] = 0.0
    assert ctrl._nmpc_T_ref is not None
    assert ctrl._nmpc_U is not None
    assert float(ctrl._nmpc_T_ref[0, 0]) == pytest.approx(25.0)
    assert float(ctrl._nmpc_U[0, 0]) == pytest.approx(0.4)


def test_compute_with_new_disturbances_does_not_retarget_p() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    n_slow = ctrl.timing.n_slow
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(n_fast, 1)
    u_star = np.full((n_slow, 1), 0.4)
    outdoor_solve = [8.0] * n_fast
    outdoor_new = [-18.0] * n_fast
    solar0 = {_ROOM: 0.0}

    ctrl._outdoor_forecast = list(outdoor_solve)
    ctrl._solar_forecast = [dict(solar0) for _ in range(n_fast)]
    ctrl.set_accepted_path(u_star, t_ref, now=_EPOCH, plan_epoch=_EPOCH)
    frozen_t = np.asarray(ctrl._nmpc_T_ref, dtype=float).copy()
    frozen_u = np.asarray(ctrl._nmpc_U, dtype=float).copy()
    assert ctrl.rebuild_forecast_from_plan() is True
    pred_before = float(ctrl.predictions[0][_ROOM])

    ctrl.compute(
        outdoor_new[0],
        solar_gains=solar0,
        now=_NOW,
        outdoor_forecast=outdoor_new,
    )

    assert ctrl._nmpc_T_ref is not None
    assert ctrl._nmpc_U is not None
    assert np.allclose(ctrl._nmpc_T_ref, frozen_t)
    assert np.allclose(ctrl._nmpc_U, frozen_u)

    k = int(ctrl._nmpc_k)
    idx = min(max(k, 0), n_fast - 1)
    n = min(idx // int(ctrl.timing.m), n_slow - 1)
    t_hat = float(ctrl._ekf.x_hat[0])
    src = ctrl._sources[0]
    expected = p_command(
        float(frozen_u[n, 0]),
        float(frozen_t[idx, 0]),
        t_hat,
        float(src.p_gain),
        float(src.u_min),
        float(src.u_max),
        u_ref_gate=ctrl._u_ref_gate,
        p_deadband=ctrl._p_deadband,
    )
    actual = float(ctrl._p_command_vector(None, None, None)[0])
    assert actual == pytest.approx(expected)

    pred0 = float(ctrl.predictions[0][_ROOM])
    assert pred0 == pytest.approx(pred_before)
    assert pred0 == pytest.approx(float(frozen_t[0, 0]))
    plotted = np.array(
        [float(step[_ROOM]) for step in ctrl.predictions], dtype=float
    )
    # compute() publishes remaining T_ref at k, then advances k.
    assert plotted == pytest.approx(frozen_t[:n_fast, 0], abs=1e-12)
    assert float(np.max(plotted[: int(ctrl.timing.m)]) - np.min(plotted[: int(ctrl.timing.m)])) > 0.2


def test_p_follows_fast_grid_t_ref_inside_slow_u_hold() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    n_slow = ctrl.timing.n_slow
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(n_fast, 1)
    u_star = np.full((n_slow, 1), 0.4)
    ctrl.set_accepted_path(u_star, t_ref, now=_EPOCH, plan_epoch=_EPOCH)
    t_hat = float(ctrl._ekf.x_hat[0])
    src = ctrl._sources[0]

    def _p_at(k: int) -> float:
        ctrl._nmpc_k = k
        return float(ctrl._p_command_vector(None, None, None)[0])

    u0 = _p_at(0)
    u1 = _p_at(1)
    expected0 = p_command(
        0.4,
        float(t_ref[0, 0]),
        t_hat,
        float(src.p_gain),
        float(src.u_min),
        float(src.u_max),
        u_ref_gate=ctrl._u_ref_gate,
        p_deadband=ctrl._p_deadband,
    )
    expected1 = p_command(
        0.4,
        float(t_ref[1, 0]),
        t_hat,
        float(src.p_gain),
        float(src.u_min),
        float(src.u_max),
        u_ref_gate=ctrl._u_ref_gate,
        p_deadband=ctrl._p_deadband,
    )
    assert u0 == pytest.approx(expected0)
    assert u1 == pytest.approx(expected1)
    assert u0 != pytest.approx(u1)


def test_forecast_t_none_without_accepted_path() -> None:
    ctrl = _ctrl()
    assert ctrl._forecast_T(ctrl.horizon) is None
    assert ctrl.rebuild_forecast_from_plan() is False


def test_forecast_t_pads_last_row_when_index_past_end() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(n_fast, 1)
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.4), t_ref, now=_EPOCH, plan_epoch=_EPOCH
    )
    ctrl._nmpc_k = n_fast + 3
    remaining = ctrl._forecast_T(n_fast)
    assert remaining is not None
    assert remaining.shape == (n_fast, 1)
    assert remaining == pytest.approx(np.full((n_fast, 1), float(t_ref[-1, 0])))
