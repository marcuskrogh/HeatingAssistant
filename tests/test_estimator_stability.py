"""
Regression tests for the system-identification accuracy/stability fixes.

Two defects made the automatic identification perform poorly:

1. The open-loop simulation objective integrated with **explicit** forward
   Euler at the 900 s sampling interval.  The 2R2C air node has a ~300 s time
   constant, so h/τ ≈ 3 — past the explicit stability limit of 2 — and the air
   mode was amplified ~2× per step.  The trajectory and its sensitivity blew up
   (the ``overflow encountered in dot/matmul`` warnings), corrupting the
   objective/gradient and leaving ``r_external`` estimates off by >200 %.
   The fix integrates with L-stable backward Euler (matching the live CD-EKF).

2. Heater power-scale α traded off against thermal mass / resistance along a
   degenerate ridge and slid to a bound (0.3 or 3.0).  A heater's rated power
   is known a priori, so α now carries a tight MAP prior that keeps the joint
   optimum physical.

These tests pin the behaviour: on identifiable (diurnally-forced) synthetic
data the estimator recovers ``r_external`` to within a sane factor and never
returns a bound-slammed heater scale, and the objective stays finite at
extreme trial points instead of overflowing.
"""

import math
import warnings

import numpy as np

from custom_components.heating_assistant.thermal_model import HouseModel, Room
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.parameter_estimator import (
    KalmanMLEstimator,
    _ThetaLayout,
    _LOG_ALPHA_LO,
    _LOG_ALPHA_HI,
)


def _identifiable_history(true_mass, true_r, true_scale, n=192, dt=900.0, seed=1):
    """48 h of on/off heating under a diurnal outdoor swing.

    The varying outdoor temperature excites the envelope-loss term
    independently of the heater, which (together with the heater duty cycle)
    makes C, R_ext and α jointly identifiable — unlike a constant-outdoor
    single room, which is genuinely degenerate.
    """
    rng = np.random.default_rng(seed)
    model = HouseModel([Room("a", thermal_mass=true_mass, r_external=true_r,
                             temperature=20.0)])
    t0 = 1_700_000_000.0
    hist = []
    temps = {"a": 20.0}
    for k in range(n):
        u = 0.6 if (k // 8) % 2 == 0 else 0.0
        outdoor = 3.0 + 8.0 * math.sin(2 * math.pi * (k % 96) / 96.0)
        hist.append({
            "y": [temps["a"] + 0.02 * rng.standard_normal()],
            "u": [u],
            "d_outdoor": outdoor,
            "d_solar": {"a": 0.0},
            "timestamp": t0 + dt * k,
        })
        temps = model.step(dt, {"a": u * 2000.0 * true_scale}, outdoor, {"a": 0.0})
    return hist


def _estimate(history):
    est_rooms = [Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)]
    sources = [ElectricHeater(name="ha", room="a", max_power=2000.0)]
    est = KalmanMLEstimator(est_rooms, sources, dt=900.0, regularization=0.01)
    return est.estimate(history)


def test_r_external_recovered_within_factor_two():
    """r_external must land within ~2× of truth (was off by >200 % before).

    Averaged over several seeds to avoid pinning a single noisy draw.
    """
    warnings.filterwarnings("ignore")
    true_r = 0.03
    ratios = []
    for seed in (1, 2, 3):
        hist = _identifiable_history(3e6, true_r, 0.8, seed=seed)
        res = _estimate(hist)
        assert res["success"]
        r_hat = res["estimated_params"]["a"]["r_external"]
        ratios.append(r_hat / true_r)
    mean_ratio = float(np.mean(ratios))
    assert 0.5 < mean_ratio < 2.0, (
        f"r_external mean ratio {mean_ratio:.2f} outside [0.5, 2.0]; "
        f"per-seed ratios={ratios}"
    )


def test_heater_scale_stays_physical():
    """The identified heater scale must never collapse onto a bound."""
    warnings.filterwarnings("ignore")
    lo = math.exp(_LOG_ALPHA_LO)
    hi = math.exp(_LOG_ALPHA_HI)
    for true_scale in (0.6, 0.8, 1.2):
        hist = _identifiable_history(3e6, 0.03, true_scale, seed=1)
        res = _estimate(hist)
        s_hat = res["estimated_heater_scales"]["ha"]
        assert lo + 1e-3 < s_hat < hi - 1e-3, (
            f"heater scale {s_hat} slammed to a bound for truth {true_scale}"
        )


def test_objective_finite_at_extreme_theta():
    """Backward Euler keeps the objective finite where explicit Euler overflowed.

    A tiny thermal mass / large resistance pushes the air-node time constant
    far below the 900 s step — the regime that diverged under explicit Euler.
    """
    warnings.filterwarnings("error")  # surface any numpy overflow as an error
    hist = _identifiable_history(3e6, 0.03, 0.8, seed=1)
    est_rooms = [Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)]
    sources = [ElectricHeater(name="ha", room="a", max_power=2000.0)]
    est = KalmanMLEstimator(est_rooms, sources, dt=900.0)
    std = est._convert_history_std(hist, use_ym=True)
    layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0],
                          identifiable_pairs=[])
    # Extreme but in-bounds θ: very small mass (fast air node), large R.
    theta = np.concatenate([
        np.array([math.log(2e4)]),   # log_mass — tiny
        np.array([math.log(5.0)]),   # log_r — large
        np.array([0.0]),             # q_int
        np.array([20.0]),            # t_wall_init
        np.array([0.0]),             # log_alpha
    ])
    val, grad = est._simulation_mse_and_grad(
        theta, layout, std, nominal_dt=900.0,
        min_segment_steps=4, max_window_steps=20,
    )
    assert np.isfinite(val)
    assert np.all(np.isfinite(grad))
