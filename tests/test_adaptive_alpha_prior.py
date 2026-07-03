"""Tests for adaptive heater-scale prior weighting."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.parameter_estimator import (
    _adaptive_alpha_prior_weight,
    _ALPHA_PRIOR_WEIGHT,
    _ALPHA_PRIOR_WEIGHT_EXCITED,
    KalmanMLEstimator,
)
from custom_components.heating_assistant.thermal_model import Room
from custom_components.heating_assistant.heat_sources import ElectricHeater


def test_adaptive_alpha_weaker_when_excited():
    excited = [{"u": [0.0 if i % 4 else 1.0]} for i in range(80)]
    constant = [{"u": [0.5]} for _ in range(80)]
    assert _adaptive_alpha_prior_weight(excited, 1, 10) == _ALPHA_PRIOR_WEIGHT_EXCITED
    assert _adaptive_alpha_prior_weight(constant, 1, 10) == _ALPHA_PRIOR_WEIGHT


def test_wall_init_prior_uses_air_outdoor_blend():
    rooms = [Room("a", 4e6, 0.04, temperature=20.0)]
    est = KalmanMLEstimator(rooms, [], dt=900.0)
    est._update_wall_init_prior_from_history([
        {"y": [22.0], "d_outdoor": 10.0},
    ])
    assert est._t_wall_init_prior[0] == 16.0
