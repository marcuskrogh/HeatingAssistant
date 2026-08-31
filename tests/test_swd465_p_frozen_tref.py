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
    heater.p_gain = 0.4
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
    t_ref = np.linspace(25.0, 28.0, n_fast).reshape(n_fast, 1)
    u_star = np.full((n_slow, 1), 0.4)
    outdoor_solve = [8.0] * n_fast
    outdoor_new = [-18.0] * n_fast
    solar_new = {_ROOM: 900.0}

    ctrl._outdoor_forecast = list(outdoor_solve)
    ctrl._solar_forecast = [{_ROOM: 0.0} for _ in range(n_fast)]
    ctrl.set_accepted_path(u_star, t_ref, now=_EPOCH, plan_epoch=_EPOCH)
    frozen_t = np.asarray(ctrl._nmpc_T_ref, dtype=float).copy()
    frozen_u = np.asarray(ctrl._nmpc_U, dtype=float).copy()
    assert ctrl.rebuild_forecast_from_plan() is True

    ctrl.compute(
        outdoor_new[0],
        solar_gains=solar_new,
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
    assert abs(pred0 - float(frozen_t[idx, 0])) > 0.2
    tracking_resim = p_command(
        float(frozen_u[n, 0]),
        pred0,
        t_hat,
        float(src.p_gain),
        float(src.u_min),
        float(src.u_max),
        u_ref_gate=ctrl._u_ref_gate,
        p_deadband=ctrl._p_deadband,
    )
    assert actual != pytest.approx(tracking_resim)
