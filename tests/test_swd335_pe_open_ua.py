"""SWD-335/336: identified contact-gated UA_open in production open-loop PE."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

import math
import numpy as np
import pytest

from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.estimation.identifiability import _check_identifiable_open_ua
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.model_diagnostics import compute_open_loop_predictions
from heatingassistant.engine.parameter_estimator import KalmanMLEstimator
from heatingassistant.engine.parameter_lifecycle import apply_estimated_parameters
from heatingassistant.engine.thermal_model import HouseModel, Room
from mbc.control import ScipyNLPBackend


pytestmark = pytest.mark.unit

ROOM = "studio"
DT = 900.0


def _room(**kwargs):
    kw = dict(
        name=ROOM,
        thermal_mass=4e6,
        r_external=0.05,
        temperature=20.0,
        setpoint=21.0,
    )
    kw.update(kwargs)
    return Room(**kw)


def _heater():
    return ElectricHeater("h", ROOM, 3000.0, power_scale=1.0)


def _history(
    n: int = 48,
    *,
    open_range=None,
    corrupt_open: bool = False,
    u: float = 0.5,
) -> list[dict]:
    t0 = 1_700_000_000.0
    history = []
    for i in range(n):
        is_open = open_range is not None and open_range[0] <= i < open_range[1]
        temp = 20.0 + 0.02 * (i % 10)
        if is_open and corrupt_open:
            temp = -50.0
        history.append(
            {
                "y": [temp],
                "u": [u if (i // 6) % 2 == 0 else 0.05],
                "d_outdoor": 2.0,
                "d_solar": {ROOM: 0.0},
                "timestamp": t0 + DT * i,
                "window_open": {ROOM: bool(is_open)},
            }
        )
    return history


def _theta_with_ua(est: KalmanMLEstimator, layout: _ThetaLayout, ua: float) -> np.ndarray:
    blocks = [
        est._log_mass_prior,
        est._log_r_prior,
        est._q_int_prior,
        np.array([est._t_wall_init_prior[0]]),
    ]
    if layout.identifiable_sources:
        blocks.append(
            np.array([est._log_alpha_prior_full[s] for s in layout.identifiable_sources])
        )
    if layout.identifiable_ua:
        blocks.append(np.array([float(ua)] * len(layout.identifiable_ua)))
    theta = np.concatenate(blocks)
    assert len(theta) == layout.size
    return theta


def _eval_mse(est, history, layout, theta):
    std = est._convert_history_std(history, use_ym=True)
    mse, grad = est._simulation_mse_and_grad(
        theta,
        layout,
        std,
        nominal_dt=est._dt,
        max_window_steps=est._max_window_steps,
        min_segment_steps=est._min_segment_steps,
    )
    return mse, grad


@contextmanager
def _capped_scipy(maxiter: int = 8) -> Iterator[None]:
    orig = ScipyNLPBackend.__init__

    def _init(self, *, method="L-BFGS-B", options=None, scaling=None, **kwargs):  # type: ignore[no-untyped-def]
        options = dict(options or {})
        options["maxiter"] = min(int(options.get("maxiter", maxiter)), maxiter)
        orig(self, method=method, options=options, scaling=scaling, **kwargs)

    with patch.object(ScipyNLPBackend, "__init__", _init):
        yield


def test_ua_layout_appends_after_envelope_splits():
    layout = _ThetaLayout(
        n_rooms=1,
        identifiable_sources=[],
        identifiable_pairs=[],
        identifiable_ua=[0],
    )
    closed = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
    assert layout.size == closed.size + 1
    assert layout.idx_ua_open == (closed.size, closed.size + 1)


def test_open_ua_gate_requires_segment_minimum():
    few = _history(20, open_range=(10, 13))  # 3 open steps
    enough = _history(20, open_range=(10, 16))  # 6 open steps
    assert _check_identifiable_open_ua(few, [ROOM], min_open_steps=4) == []
    assert _check_identifiable_open_ua(enough, [ROOM], min_open_steps=4) == [0]


def test_few_open_samples_keep_swd322_exclusion():
    """Below N_min the objective still ignores corrupted open-window samples."""
    est = KalmanMLEstimator([_room()], [_heater()], dt=DT)
    layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
    theta = _theta_with_ua(est, layout, 0.0)
    clean = _history(40, open_range=(38, 40), corrupt_open=False)
    corrupt = _history(40, open_range=(38, 40), corrupt_open=True)
    mse_clean, _ = _eval_mse(est, clean, layout, theta)
    mse_corrupt, _ = _eval_mse(est, corrupt, layout, theta)
    assert np.isclose(mse_clean, mse_corrupt, rtol=0, atol=1e-9)


def test_identifiable_ua_includes_open_samples_in_objective():
    """Enough open samples: corrupted open-window air changes the OE."""
    est = KalmanMLEstimator([_room()], [_heater()], dt=DT)
    layout = _ThetaLayout(
        n_rooms=1,
        identifiable_sources=[],
        identifiable_pairs=[],
        identifiable_ua=[0],
    )
    theta = _theta_with_ua(est, layout, 8.0)
    clean = _history(40, open_range=(20, 36), corrupt_open=False)
    corrupt = _history(40, open_range=(20, 36), corrupt_open=True)
    mse_clean, _ = _eval_mse(est, clean, layout, theta)
    mse_corrupt, _ = _eval_mse(est, corrupt, layout, theta)
    assert not np.isclose(mse_clean, mse_corrupt, atol=1e-6)


def test_true_ua_fits_open_window_plant_better_than_zero():
    """Open-loop MSE at the plant UA is lower than UA=0 on the same θ."""
    plant_ua = 18.0
    model = HouseModel([_room(ua_open=plant_ua, temperature=20.0, wall_temperature=18.0)])
    t0 = 1_700_000_000.0
    history = []
    for i in range(36):
        is_open = i >= 12
        tout = 0.0
        duty = 0.5 if (i // 6) % 2 == 0 else 0.0
        rec = {
            "y": [float(model.rooms[ROOM].temperature)],
            "u": [duty],
            "d_outdoor": tout,
            "d_solar": {ROOM: 0.0},
            "timestamp": t0 + DT * i,
            "window_open": {ROOM: is_open},
        }
        history.append(rec)
        model.step(
            DT,
            {ROOM: duty * 3000.0},
            tout,
            {ROOM: 0.0},
            window_open={ROOM: is_open},
        )

    est = KalmanMLEstimator([_room()], [_heater()], dt=DT)
    layout = _ThetaLayout(
        n_rooms=1,
        identifiable_sources=[],
        identifiable_pairs=[],
        identifiable_ua=[0],
    )
    mse_zero, _ = _eval_mse(est, history, layout, _theta_with_ua(est, layout, 0.0))
    mse_true, _ = _eval_mse(est, history, layout, _theta_with_ua(est, layout, plant_ua))
    assert mse_true < mse_zero


def test_house_model_step_applies_extra_ua_when_open():
    closed = HouseModel([_room(ua_open=25.0, temperature=20.0, wall_temperature=20.0)])
    opened = HouseModel([_room(ua_open=25.0, temperature=20.0, wall_temperature=20.0)])
    kwargs = dict(dt=DT, heat_inputs={ROOM: 0.0}, outdoor_temp=0.0, solar_gains={ROOM: 0.0})
    t_closed = closed.step(**kwargs, window_open={ROOM: False})[ROOM]
    t_open = opened.step(**kwargs, window_open={ROOM: True})[ROOM]
    assert t_open < t_closed


def test_house_thermal_sde_applies_identified_ua_on_simulated_air():
    room = _room(ua_open=20.0, temperature=20.0, wall_temperature=20.0)
    system = HouseThermalSDE(HouseModel([room]), [_heater()], DT)
    x = np.array([20.0, 20.0, 0.0])
    u = np.array([0.0])
    d = np.zeros(1 + 2)
    d[0] = 0.0
    p = np.zeros(3)
    system.set_window_open({ROOM: False})
    f_closed = system.f(x, u, d, p, 0.0)
    system.set_window_open({ROOM: True})
    f_open = system.f(x, u, d, p, 0.0)
    assert f_open[0] < f_closed[0]


def test_open_loop_diagnostic_gaps_only_when_ua_not_modelled():
    history = _history(24, open_range=(10, 16), corrupt_open=True)
    unmodelled = HouseThermalSDE(HouseModel([_room(ua_open=0.0)]), [_heater()], DT)
    modelled = HouseThermalSDE(HouseModel([_room(ua_open=12.0)]), [_heater()], DT)
    kwargs = dict(history=history, room_names=[ROOM], n_rooms=1, dt=DT, segment_length=None)
    sim_gap = compute_open_loop_predictions(system=unmodelled, **kwargs)["per_room"][ROOM]["simulation"]
    sim_keep = compute_open_loop_predictions(system=modelled, **kwargs)["per_room"][ROOM]["simulation"]
    t0 = history[0]["timestamp"]
    gapped = [
        e for e in sim_gap
        if e["measured"] is None and 10 <= round((e["time"] - t0) / DT) < 16
    ]
    kept = [
        e for e in sim_keep
        if 10 <= round((e["time"] - t0) / DT) < 16
    ]
    assert gapped
    assert kept
    assert all(e["measured"] is not None for e in kept)


def test_apply_estimated_parameters_writes_ua_open():
    model = HouseModel([_room(ua_open=0.0)])
    apply_estimated_parameters(
        model,
        [_heater()],
        {},
        estimated_params={ROOM: {"thermal_mass": 4e6, "r_external": 0.05}},
        estimated_ua_open={ROOM: 12.5},
    )
    assert model.rooms[ROOM].ua_open == pytest.approx(12.5)


def test_store_identified_parameters_writes_ua_open():
    from heatingassistant.engine.const import CONF_ESTIMATED_PARAMS
    from heatingassistant.engine.parameter_lifecycle import store_identified_parameters

    model = HouseModel([_room(ua_open=0.0)])
    options: dict = {}
    store_identified_parameters(
        model,
        [_heater()],
        options,
        ROOM,
        4e6,
        0.05,
        ua_open=18.0,
    )
    assert model.rooms[ROOM].ua_open == pytest.approx(18.0)
    snap = options[CONF_ESTIMATED_PARAMS]
    assert snap["rooms"][ROOM]["ua_open"] == pytest.approx(18.0)


@pytest.mark.ondemand
def test_identified_ua_open_loop_val_bar():
    """PLAN AC4/8 on the SWD-329/332 hold-out (capped SciPy, production estimate).

    Closed-window rooms must not regress vs today_combined (0.45 / 0.46 / 0.99 °C).
    Weak openings are below ``N_min`` (~45 min vs 4 steps), so UA stays 0 (exclusion).
    Strong openings identify UA and must beat assumed-UA ``window_ua`` on those rows.
    Full-grid mean vs 0.83 °C is not asserted: that bar mixes below-``N_min``
    weak openings where assumed-UA still models the leak and identified-UA does not.
    """
    from tests.test_swd329_pe_robustness import (
        procedure_today_combined,
        procedure_window_ua,
        run_bakeoff,
    )

    id_rows = run_bakeoff(
        procedures=[("today_combined", procedure_today_combined)],
        paths=("open_loop",),
    )
    ua_rows = run_bakeoff(
        procedures=[("window_ua", procedure_window_ua)],
        paths=("open_loop",),
    )
    assert len(id_rows) == 9
    assert all(r.success and math.isfinite(r.val_rmse) for r in id_rows)
    closed = {r.scenario: r.val_rmse for r in id_rows if r.scenario.endswith("__win_none")}
    assert closed["occ_none__win_none"] <= 0.46
    assert closed["occ_weak__win_none"] <= 0.47
    assert closed["occ_strong__win_none"] <= 1.00
    weak = [r for r in id_rows if r.scenario.endswith("__win_weak")]
    assert all(float(r.theta.get("ua_open") or 0.0) == 0.0 for r in weak)
    strong_id = [r for r in id_rows if r.scenario.endswith("__win_strong")]
    strong_ua = [r for r in ua_rows if r.scenario.endswith("__win_strong")]
    assert all(float(r.theta.get("ua_open") or 0.0) > 0.0 for r in strong_id)
    id_mean = sum(r.val_rmse for r in strong_id) / len(strong_id)
    ua_mean = sum(r.val_rmse for r in strong_ua) / len(strong_ua)
    assert id_mean < ua_mean


def test_estimate_few_open_samples_reports_zero_ua():
    history = _history(24, open_range=(20, 22))
    est = KalmanMLEstimator([_room()], [_heater()], dt=DT, regularization=0.01)
    est._physics_informed_theta = lambda *a, **k: None  # type: ignore[method-assign]
    with _capped_scipy(maxiter=6):
        result = est.estimate(history)
    assert result["success"] is True
    assert result["identifiable_ua_rooms"] == []
    assert result["estimated_ua_open"][ROOM] == 0.0


def test_estimate_enough_open_samples_identifies_ua_block():
    history = _history(36, open_range=(8, 28))
    est = KalmanMLEstimator([_room()], [_heater()], dt=DT, regularization=0.01)
    est._physics_informed_theta = lambda *a, **k: None  # type: ignore[method-assign]
    with _capped_scipy(maxiter=6):
        result = est.estimate(history)
    assert result["success"] is True
    assert ROOM in result["identifiable_ua_rooms"]
    ua = result["estimated_ua_open"][ROOM]
    assert 0.0 <= ua <= 50.0
    assert "Open-contact UA estimated" in result["message"]
