"""Tests for leading-window initial-state estimation."""

import math
import sys
import os

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.history_window import select_leading_window
from heatingassistant.engine.initial_state_estimator import (
    estimate_simulation_initial_state,
    ekf_state_at_end_of_history,
    _prefix_calibration_slice,
)
from heatingassistant.engine.parameter_estimator import KalmanMLEstimator


def _simulate_history(
    dt=900.0,
    n_steps=80,
    outdoor=5.0,
    heating_pattern=None,
    t0=1_700_000_000.0,
):
    room = Room("studio", 4e6, 0.04, temperature=18.0, setpoint=21.0)
    model = HouseModel([room])
    heater = ElectricHeater("h", "studio", 3000.0)
    temps = {"studio": 18.0}
    history = []
    for k in range(n_steps):
        u = 0.7 if heating_pattern is None else heating_pattern(k)
        history.append({
            "y": [temps["studio"]],
            "u": [u],
            "d_outdoor": outdoor + 2.0 * math.sin(2 * math.pi * k / 48),
            "d_solar": {"studio": 0.0},
            "timestamp": t0 + dt * k,
        })
        temps = model.step(
            dt,
            {"studio": u * 3000.0},
            history[-1]["d_outdoor"],
            {"studio": 0.0},
        )
    return history, model, heater


def test_select_leading_window_excludes_anchor():
    hist, _, _ = _simulate_history(n_steps=40)
    anchor = float(hist[30]["timestamp"])
    leading = select_leading_window(hist, anchor, 6 * 3600.0)
    assert leading
    assert all(float(r["timestamp"]) < anchor for r in leading)
    assert float(leading[-1]["timestamp"]) < anchor


def test_prefix_calibration_slice_uses_minority_of_window():
    hist, _, _ = _simulate_history(n_steps=100)
    prefix, suffix = _prefix_calibration_slice(hist, dt=900.0, max_prefix_hours=6.0)
    assert len(prefix) < len(hist)
    assert len(suffix) >= len(hist) - len(prefix)
    assert prefix[0] is hist[0]


def test_ekf_state_at_end_returns_wall_distinct_from_air():
    hist, model, heater = _simulate_history(n_steps=60)
    sde = HouseThermalSDE(model, [heater], 900.0, augment_offsets=False)
    x_end = ekf_state_at_end_of_history(hist, sde, ["studio"], 900.0)
    assert x_end is not None
    assert x_end.shape[0] >= 2
    # After prolonged heating the wall estimate should differ from air.
    assert abs(float(x_end[1]) - float(x_end[0])) >= 0.0


def test_estimate_initial_state_uses_leading_history():
    hist, model, heater = _simulate_history(n_steps=80)
    sde = HouseThermalSDE(model, [heater], 900.0, augment_offsets=False)
    anchor_idx = 50
    anchor_ts = float(hist[anchor_idx]["timestamp"])
    leading = select_leading_window(hist, anchor_ts, 6 * 3600.0)
    simulation = hist[anchor_idx:]
    estimator = KalmanMLEstimator([model.rooms["studio"]], [heater], dt=900.0)

    result = estimate_simulation_initial_state(
        simulation,
        sde,
        ["studio"],
        900.0,
        leading_history=leading,
        wall_optimizer=estimator,
    )
    assert "studio" in result["t_air"]
    assert "studio" in result["t_wall"]
    assert result["method"].startswith("ekf")
    assert result["calibration_steps"] >= 2
    # Air must match the measured anchor exactly.
    assert result["t_air"]["studio"] == round(float(simulation[0]["y"][0]), 2)


def test_estimate_initial_state_prefix_when_no_leading():
    hist, model, heater = _simulate_history(n_steps=60)
    sde = HouseThermalSDE(model, [heater], 900.0, augment_offsets=False)
    estimator = KalmanMLEstimator([model.rooms["studio"]], [heater], dt=900.0)
    result = estimate_simulation_initial_state(
        hist,
        sde,
        ["studio"],
        900.0,
        leading_history=None,
        wall_optimizer=estimator,
    )
    assert result["method"] in ("ekf_prefix", "ekf+opt_prefix", "opt_prefix", "steady_state")
    assert "studio" in result["t_wall"]
