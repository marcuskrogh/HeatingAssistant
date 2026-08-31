"""Room-view Forecast plots remaining accept-time T_ref, not U* resim."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.nmpc_ocp import roll_fast_air_path, step_hold
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


def _d_fast(
    ctrl: HeatingMPCController,
    outdoor: list[float],
    solar: list[dict[str, float]],
) -> list:
    plant = ctrl._control_system
    rows = []
    for k, tout in enumerate(outdoor):
        if k < len(solar):
            s = solar[k]
        elif solar:
            s = solar[-1]
        else:
            s = {}
        rows.append(plant.disturbance_vector(float(tout), s))
    return rows


def _roll_air(
    ctrl: HeatingMPCController,
    U_fast: np.ndarray,
    outdoor: list[float],
    solar: list[dict[str, float]],
    x0: np.ndarray | None = None,
) -> np.ndarray:
    return roll_fast_air_path(
        ctrl._control_system,
        ctrl._ekf.x_hat if x0 is None else x0,
        U_fast,
        _d_fast(ctrl, outdoor, solar),
        dt_s=float(ctrl._timing.dt_s),
        n_int=ctrl._n_int_steps,
        n_rooms=ctrl._system._n_rooms,
    )


def _plotted(ctrl: HeatingMPCController) -> np.ndarray:
    return np.array(
        [float(step["living_room"]) for step in ctrl.predictions], dtype=float
    )


def _remaining_t_ref(ctrl: HeatingMPCController) -> np.ndarray:
    air = ctrl._forecast_T(ctrl.horizon)
    assert air is not None
    return np.asarray(air, dtype=float)[:, 0]


def test_forecast_uses_accept_time_t_ref_not_ocp_resim():
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    temps = _install_plan(ctrl, U)
    n_fast = ctrl.horizon
    U_rem = ctrl._forecast_U(n_fast)
    U_fast = np.repeat(U, ctrl.timing.m, axis=0)
    assert np.allclose(U_rem, U_fast[:n_fast])
    resim = _roll_air(
        ctrl,
        U_rem,
        [5.0] * n_fast,
        [{"living_room": 0.0} for _ in range(n_fast)],
    )[:, 0]
    assert temps == pytest.approx(np.full(n_fast, 22.0), abs=1e-12)
    assert float(np.max(np.abs(resim - 22.0))) > 0.05


def test_resim_matches_mean_ocp_when_u_x0_d_match() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    outdoor = [5.0] * n_fast
    x0 = ctrl._ekf.x_hat.copy()
    solar0 = {"living_room": 0.0}
    plan = ctrl.solve_nmpc(
        outdoor_temp=5.0,
        now=_NOW,
        outdoor_forecast=outdoor,
        solar_gains=solar0,
    )
    if not plan.get("accepted"):
        pytest.skip("NMPC did not accept a plan")
    t_ocp = np.asarray(plan["t_ref"], dtype=float).reshape(-1, ctrl._system._n_rooms)
    solar_seq = ctrl._forecast_solar(_NOW)
    solar_seq[0] = dict(solar0)
    U_fast = np.repeat(np.asarray(plan["u_star"], dtype=float), ctrl.timing.m, axis=0)[
        :n_fast
    ]
    air = _roll_air(ctrl, U_fast, outdoor, solar_seq[:n_fast], x0=x0)
    assert float(np.max(np.abs(air - t_ocp[: air.shape[0]]))) < 1e-12

    ctrl._outdoor_forecast = list(outdoor)
    ctrl._solar_forecast = [dict(step) for step in solar_seq[:n_fast]]
    assert ctrl.apply_nmpc_result(plan, now=_NOW.timestamp()) is True
    assert _plotted(ctrl) == pytest.approx(t_ocp[:, 0], abs=1e-12)


def test_remaining_forecast_matches_t_ref_tail() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    outdoor = [5.0] * n_fast
    solar = [{"living_room": 0.0} for _ in range(n_fast)]
    x0 = ctrl._ekf.x_hat.copy()
    plan = ctrl.solve_nmpc(
        outdoor_temp=5.0,
        now=_NOW,
        outdoor_forecast=outdoor,
        solar_gains=solar[0],
    )
    if not plan.get("accepted"):
        pytest.skip("NMPC did not accept a plan")
    t_ocp = np.asarray(plan["t_ref"], dtype=float).reshape(-1, 1)
    U_fast = np.repeat(np.asarray(plan["u_star"], dtype=float), ctrl.timing.m, axis=0)[
        :n_fast
    ]
    start = 3
    x = x0.copy()
    p = np.array([], dtype=float)
    d_rows = _d_fast(ctrl, outdoor, solar)
    for k in range(start):
        x = step_hold(
            ctrl._control_system, x, U_fast[k], d_rows[k], p, float(ctrl._timing.dt_s), ctrl._n_int_steps
        )
    _, P = ctrl.ekf_state
    assert ctrl.restore_ekf_state(x, P) is True
    ctrl.set_accepted_path(plan["u_star"], t_ocp)
    ctrl._outdoor_forecast = list(outdoor)
    ctrl._solar_forecast = [dict(s) for s in solar]
    ctrl._nmpc_k = start
    assert ctrl.rebuild_forecast_from_plan() is True
    plotted = _plotted(ctrl)
    assert plotted == pytest.approx(_remaining_t_ref(ctrl), abs=1e-12)
    n_overlap = n_fast - start
    assert plotted[:n_overlap] == pytest.approx(t_ocp[start:, 0], abs=1e-12)


def test_changed_outdoor_does_not_move_forecast_off_t_ref() -> None:
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    n_fast = ctrl.horizon
    t_ref = np.linspace(20.0, 24.0, n_fast).reshape(-1, 1)
    ctrl.set_accepted_path(U, t_ref)
    ctrl._outdoor_forecast = [5.0] * n_fast
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    assert ctrl.rebuild_forecast_from_plan() is True
    t_cold = _plotted(ctrl)
    ctrl._outdoor_forecast = [25.0] * n_fast
    assert ctrl.rebuild_forecast_from_plan() is True
    t_hot = _plotted(ctrl)
    assert t_hot == pytest.approx(t_cold, abs=1e-12)
    assert t_hot == pytest.approx(t_ref.ravel(), abs=1e-12)


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
    published_t = _plotted(ctrl)
    assert len(published_t) == n_fast
    assert published_t == pytest.approx(np.full(n_fast, 22.0), abs=1e-12)


def test_rebuild_without_solar_still_plots_remaining_t_ref():
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
    published = _plotted(ctrl)
    remaining_u = ctrl._forecast_U(n_fast)
    remaining_t = _remaining_t_ref(ctrl)
    resim = _roll_air(ctrl, remaining_u, [5.0] * n_fast, [])[:, 0]
    assert published == pytest.approx(remaining_t, abs=1e-12)
    assert published == pytest.approx(
        [float(t_ref[min(start + k, n_fast - 1), 0]) for k in range(n_fast)]
    )
    assert published != pytest.approx(resim, abs=1e-3)
    watts = [float(step["living_room"]) for step in ctrl.heating_schedule]
    first = ctrl._system.display_heating_powers(remaining_u[0], 5.0)["living_room"]
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
    assert _plotted(ctrl) == pytest.approx(np.full(n_fast, 21.0), abs=1e-12)


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
    assert _plotted(ctrl) == pytest.approx(np.full(n_fast, 21.0), abs=1e-12)


def test_forecast_after_ticks_is_remaining_t_ref() -> None:
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
    ctrl._outdoor_forecast = list(outdoor)
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    assert ctrl.apply_nmpc_result(plan, now=_NOW.timestamp()) is True
    assert float(np.max(np.abs(_plotted(ctrl) - t_ocp[:, 0]))) < 1e-9

    ticks = 3
    for _ in range(ticks):
        ctrl.compute(
            outdoor_temp=5.0,
            solar_gains={"living_room": 0.0},
            now=_NOW,
            outdoor_forecast=outdoor,
        )
    assert ctrl._nmpc_k == ticks
    assert ctrl.rebuild_forecast_from_plan() is True
    remaining = _remaining_t_ref(ctrl)
    assert _plotted(ctrl) == pytest.approx(remaining, abs=1e-12)
    frozen = np.array(
        [float(t_ocp[min(ticks + k, n_fast - 1), 0]) for k in range(n_fast)]
    )
    assert remaining == pytest.approx(frozen, abs=1e-12)


def test_resim_holds_horizon_mean_wind_like_ocp() -> None:
    ctrl = _ctrl()
    n_fast = ctrl.horizon
    outdoor = [5.0] * n_fast
    solar = [{"living_room": 0.0} for _ in range(n_fast)]
    wind_seq = [1.0 + 4.0 * (k % 2) for k in range(n_fast)]
    mean_w = float(np.mean(wind_seq))
    ctrl.set_wind_speed(mean_w)
    U = _distinct_U(ctrl)
    U_fast = np.repeat(U, ctrl.timing.m, axis=0)[:n_fast]
    x0 = ctrl._ekf.x_hat.copy()
    expected = _roll_air(ctrl, U_fast, outdoor, solar, x0=x0)[:, 0]
    plotted = ctrl._compute_nonlinear_predictions(
        U_fast,
        outdoor,
        solar,
        ctrl._system._room_list,
        ctrl._system._n_rooms,
        wind_seq=wind_seq,
    )
    temps = np.array([float(step["living_room"]) for step in plotted], dtype=float)
    assert temps == pytest.approx(expected, abs=1e-12)

    plant = ctrl._control_system
    x = x0.copy()
    p = np.array([], dtype=float)
    d_rows = _d_fast(ctrl, outdoor, solar)
    per_step = []
    restore = plant._wind_speed
    try:
        for k in range(n_fast):
            plant.set_wind_speed(wind_seq[k])
            x = step_hold(
                plant,
                x,
                U_fast[k],
                d_rows[k],
                p,
                float(ctrl._timing.dt_s),
                ctrl._n_int_steps,
            )
            per_step.append(float(x[0]))
    finally:
        plant.set_wind_speed(restore)
    assert float(np.max(np.abs(np.array(per_step, dtype=float) - expected))) > 1e-6


def test_changed_solar_does_not_move_forecast() -> None:
    ctrl = _ctrl()
    U = _distinct_U(ctrl)
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(U, np.full((n_fast, 1), 22.0))
    ctrl._outdoor_forecast = [5.0] * n_fast
    ctrl._solar_forecast = [{"living_room": 0.0} for _ in range(n_fast)]
    assert ctrl.rebuild_forecast_from_plan() is True
    t_dark = _plotted(ctrl)
    ctrl._solar_forecast = [{"living_room": 800.0} for _ in range(n_fast)]
    assert ctrl.rebuild_forecast_from_plan() is True
    t_sun = _plotted(ctrl)
    assert t_sun == pytest.approx(t_dark, abs=1e-12)
    assert t_sun == pytest.approx(np.full(n_fast, 22.0), abs=1e-12)
