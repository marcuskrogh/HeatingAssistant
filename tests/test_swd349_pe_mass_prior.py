"""SWD-349: PE thermal-mass bounds and MAP toward the selected room size."""

from __future__ import annotations

import math

import numpy as np
import pytest

from heatingassistant.engine.estimation.constants import (
    _LOG_MASS_HI,
    _MASS_BOUND_FACTOR,
    _MASS_PRIOR_WEIGHT,
    _log_mass_bounds,
)
from heatingassistant.engine.estimation.regularization import (
    _compute_regularization_theta,
)
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.parameter_estimator import KalmanMLEstimator
from heatingassistant.engine.thermal_model import Room
from tests.helpers.estimation_fixtures import generate_history


pytestmark = pytest.mark.unit

LIVING_ROOM_C0 = 9_000_000.0  # config-presets "large" / living room


def _living_room(**kwargs) -> Room:
    kw = dict(
        name="living_room",
        thermal_mass=LIVING_ROOM_C0,
        r_external=0.05,
        temperature=20.0,
        setpoint=21.0,
    )
    kw.update(kwargs)
    return Room(**kw)


def test_living_room_log_mass_hi_is_five_times_prior_not_global_cap():
    lo, hi = _log_mass_bounds(math.log(LIVING_ROOM_C0))
    assert math.exp(hi) == pytest.approx(LIVING_ROOM_C0 * _MASS_BOUND_FACTOR)
    assert math.exp(lo) == pytest.approx(LIVING_ROOM_C0 / _MASS_BOUND_FACTOR)
    assert hi < _LOG_MASS_HI
    assert math.exp(hi) < 5e8


def test_log_mass_bounds_clip_tiny_prior_to_global_floor():
    lo, hi = _log_mass_bounds(math.log(1.0))
    assert lo == pytest.approx(math.log(1e4))
    assert hi > lo


def test_mass_prior_weight_scales_log_c_penalty():
    rooms = [_living_room()]
    sources = [ElectricHeater("living_room_heater", "living_room", max_power=2000.0)]
    est = KalmanMLEstimator(rooms, sources, dt=900.0)
    layout = _ThetaLayout(
        n_rooms=1,
        identifiable_sources=[],
        identifiable_pairs=[],
    )
    theta = np.concatenate([
        est._log_mass_prior,
        est._log_r_prior,
        est._q_int_prior,
        est._t_wall_init_prior,
    ])
    theta = theta.copy()
    theta[0] += math.log(_MASS_BOUND_FACTOR)

    est._mass_prior_weight = _MASS_PRIOR_WEIGHT
    r_hi = _compute_regularization_theta(est, theta, layout)
    est._mass_prior_weight = 1.0
    r_lo = _compute_regularization_theta(est, theta, layout)
    expected = (_MASS_PRIOR_WEIGHT - 1.0) * est._regularization * (
        math.log(_MASS_BOUND_FACTOR) ** 2
    )
    assert r_hi - r_lo == pytest.approx(expected, rel=1e-6)


def test_weak_window_does_not_return_global_mass_cap():
    """Constant-outdoor data used to slam C to 500 MJ/K; stay in the size box."""
    true_room = _living_room(thermal_mass=8_000_000.0)
    sources = [ElectricHeater("living_room_heater", "living_room", max_power=2000.0)]
    history = generate_history(
        [true_room], sources, dt=900.0, n_steps=48, heating_fraction=0.5,
    )
    prior_room = _living_room()
    est = KalmanMLEstimator(
        [prior_room], sources, dt=900.0, max_window_steps=24,
    )
    result = est.estimate(history)
    assert result["success"] is True
    mass = result["estimated_params"]["living_room"]["thermal_mass"]
    lo, hi = _log_mass_bounds(math.log(LIVING_ROOM_C0))
    assert math.exp(lo) <= mass <= math.exp(hi) + 1.0
    assert mass < 5e8 * 0.5


def test_locked_thermal_mass_can_pin_outside_relative_box():
    rooms = [_living_room()]
    sources = [ElectricHeater("living_room_heater", "living_room", max_power=2000.0)]
    history = generate_history([rooms[0]], sources, dt=900.0, n_steps=24)
    est = KalmanMLEstimator(rooms, sources, dt=900.0, max_window_steps=20)
    locked = {"thermal_mass": {"living_room": 5e8}}
    result = est.estimate(history, locked_params=locked)
    assert result["success"] is True
    assert result["estimated_params"]["living_room"]["thermal_mass"] == pytest.approx(
        5e8, rel=1e-6
    )
