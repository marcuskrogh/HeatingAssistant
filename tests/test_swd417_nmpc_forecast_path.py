"""Room-view Forecast resims the remaining NMPC U*, not an unshifted replay."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
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


def test_apply_publishes_ocp_air_path_not_ekf_reroll():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    n_fast = ctrl.horizon
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(-1, 1)
    ctrl.set_accepted_path(U, t_ref)
    ctrl._outdoor_forecast = [5.0] * n_fast
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    assert ctrl.rebuild_forecast_from_plan() is True
    temps = np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )
    U_rem = ctrl._forecast_U(n_fast)
    U_fast = np.repeat(U, ctrl.timing.m, axis=0)
    assert np.allclose(U_rem, U_fast[:n_fast])
    assert temps == pytest.approx(t_ref.ravel().tolist())
    reroll = ctrl._compute_nonlinear_predictions(
        U_rem,
        [5.0] * n_fast,
        [{"living_room": 0.0} for _ in range(n_fast)],
        ctrl._system._room_list,
        ctrl._system._n_rooms,
    )
    reroll_t = np.array([float(step["living_room"]) for step in reroll])
    assert float(np.max(np.abs(reroll_t - temps))) > 0.05


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


def test_remaining_forecast_is_shifted_ocp_air_path():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    n_fast = ctrl.horizon
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(-1, 1)
    ctrl.set_accepted_path(U, t_ref)
    ctrl._outdoor_forecast = [5.0] * n_fast
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    start = 3
    ctrl._nmpc_k = start
    assert ctrl.rebuild_forecast_from_plan() is True
    t_rem = np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )
    last = n_fast - 1
    expected = np.array(
        [float(t_ref[min(start + k, last), 0]) for k in range(n_fast)]
    )
    assert t_rem == pytest.approx(expected)


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

    published_t = np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )
    assert published_t == pytest.approx(np.full(n_fast, 22.0))


def test_rebuild_without_solar_uses_shifted_t_ref():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    n_fast = ctrl.horizon
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(-1, 1)
    ctrl.set_accepted_path(U, t_ref)
    ctrl._outdoor_forecast = [5.0] * n_fast
    ctrl._solar_forecast = []
    start = 3
    ctrl._nmpc_k = start
    assert ctrl.rebuild_forecast_from_plan() is True
    published = [float(step["living_room"]) for step in ctrl.predictions]
    last = n_fast - 1
    expected = [float(t_ref[min(start + k, last), 0]) for k in range(n_fast)]
    assert published == pytest.approx(expected)
    watts = [float(step["living_room"]) for step in ctrl.heating_schedule]
    remaining = ctrl._forecast_U(n_fast)
    first = ctrl._system.display_heating_powers(remaining[0], 5.0)["living_room"]
    assert watts[0] == pytest.approx(first, abs=1.0)


def test_compute_keeps_new_plan_index_when_apply_lands_during_roll():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    _install_plan(ctrl, U)
    n_fast = ctrl.horizon
    m = ctrl.timing.m
    new_U = U + 0.35
    new_T = np.full((n_fast, 1), 21.0)
    orig = ctrl._publish_plan_rollout
    fired = {"n": 0}

    def inject(*args, **kwargs):
        if fired["n"] == 0:
            fired["n"] += 1
            ctrl.set_accepted_path(new_U, new_T)
        return orig(*args, **kwargs)

    ctrl._publish_plan_rollout = inject  # type: ignore[method-assign]
    ctrl.compute(
        outdoor_temp=5.0,
        solar_gains={"living_room": 0.0},
        now=_NOW,
        outdoor_forecast=[5.0] * n_fast,
    )
    assert ctrl._nmpc_k == 0
    remaining = ctrl._forecast_U(n_fast)
    assert np.allclose(remaining[0], np.repeat(new_U, m, axis=0)[0])


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


def test_forecast_matches_mean_ocp_air_path_after_ticks() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    outdoor = [5.0] * n_fast
    plan = ctrl.solve_nmpc(
        outdoor_temp=5.0,
        now=_NOW,
        outdoor_forecast=outdoor,
        solar_gains={"living_room": 0.0},
    )
    if not plan.get("accepted"):
        pytest.skip("NMPC did not accept a plan")
    t_ocp = np.asarray(plan["t_ref"], dtype=float).reshape(-1, ctrl._system._n_rooms)
    assert t_ocp.shape[0] == n_fast
    ctrl._outdoor_forecast = list(outdoor)
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    assert ctrl.apply_nmpc_result(plan, now=_NOW.timestamp()) is True
    plotted = np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )
    assert float(np.max(np.abs(plotted - t_ocp[:, 0]))) < 1e-9

    ticks = 3
    for i in range(ticks):
        ctrl.compute(
            outdoor_temp=5.0,
            solar_gains={"living_room": 0.0},
            now=_NOW,
            outdoor_forecast=outdoor,
        )
    plotted = np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )
    start = ticks - 1
    last = n_fast - 1
    expected = np.array(
        [float(t_ocp[min(start + k, last), 0]) for k in range(n_fast)]
    )
    assert plotted == pytest.approx(expected)
    assert ctrl._nmpc_k == ticks
