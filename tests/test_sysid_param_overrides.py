"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import numpy as np

from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.engine.sysid import _build_sim_model, run_sysid_ekf
from custom_components.heating_assistant.model_diagnostics import (
    compute_open_loop_predictions,
)


def _model():
    room = Room("studio", 4e6, 0.05, temperature=20.0, setpoint=21.0)
    return HouseModel([room])


def _heater(scale=1.0):
    return ElectricHeater("h", "studio", 3000.0, power_scale=scale)


def _history(dt=900.0, n=80, u=0.8):
    """Heater driven hard so the heater scale visibly moves the prediction."""
    history = []
    t0 = 1_000_000.0
    for i in range(n):
        history.append({
            "y": [20.0 + 0.02 * (i % 10)],
            "u": [u],
            "d_outdoor": 2.0,
            "d_solar": {"studio": 100.0},
            "timestamp": t0 + dt * i,
        })
    return history


# ---------------------------------------------------------------------------
# _build_sim_model applies the full per-room parameter set
# ---------------------------------------------------------------------------

def test_build_sim_model_applies_all_overrides():
    model = _model()
    overrides = {
        "studio": {
            "thermal_mass": 8e6,
            "r_external": 0.08,
            "internal_gain": 250.0,
            "solar_scale": 1.7,
            "c_air_fraction": 0.12,
            "r_aw_fraction": 0.21,
        }
    }
    sim = _build_sim_model(model, overrides, ["studio"])
    room = sim.rooms["studio"]
    assert room.thermal_mass == 8e6
    assert room.r_external == 0.08
    assert room.internal_gain == 250.0
    assert room.solar_scale == 1.7
    # Split fractions are clipped to their valid ranges but should track input.
    assert abs(room.c_air_fraction - 0.12) < 1e-6
    assert abs(room.r_aw_fraction - 0.21) < 1e-6
    # The live model must be untouched (deep copy).
    assert model.rooms["studio"].thermal_mass == 4e6
    assert model.rooms["studio"].internal_gain == 0.0


# ---------------------------------------------------------------------------
# EKF reconstruction responds to a heater power-scale change
# ---------------------------------------------------------------------------

def test_ekf_reconstruction_responds_to_heater_scale():
    dt = 900.0
    model = _model()
    history = _history(dt)
    horizon_steps = len(history)

    res_full = run_sysid_ekf(
        history, model, [_heater(1.0)], ["studio"], dt,
        horizon_steps=horizon_steps, room_params={}, sigma_w=0.1, sigma_v=0.5,
    )
    res_half = run_sysid_ekf(
        history, model, [_heater(0.3)], ["studio"], dt,
        horizon_steps=horizon_steps, room_params={}, sigma_w=0.1, sigma_v=0.5,
    )

    pred_full = [s["predicted"] for s in res_full["per_room"]["studio"]["simulation"]]
    pred_half = [s["predicted"] for s in res_half["per_room"]["studio"]["simulation"]]
    diff = np.max(np.abs(np.array(pred_full) - np.array(pred_half)))
    assert diff > 1e-3, f"heater scale had no effect on reconstruction (max diff {diff:.2e})"


# ---------------------------------------------------------------------------
# EKF reconstruction responds to an internal-gain change
# ---------------------------------------------------------------------------

def test_ekf_reconstruction_responds_to_internal_gain():
    dt = 900.0
    model = _model()
    history = _history(dt, u=0.0)  # no heater, isolate internal gain
    horizon_steps = len(history)

    res_zero = run_sysid_ekf(
        history, model, [_heater(1.0)], ["studio"], dt,
        horizon_steps=horizon_steps,
        room_params={"studio": {"internal_gain": 0.0}},
        sigma_w=0.1, sigma_v=0.5,
    )
    res_gain = run_sysid_ekf(
        history, model, [_heater(1.0)], ["studio"], dt,
        horizon_steps=horizon_steps,
        room_params={"studio": {"internal_gain": 1500.0}},
        sigma_w=0.1, sigma_v=0.5,
    )

    p0 = [s["predicted"] for s in res_zero["per_room"]["studio"]["simulation"]]
    pg = [s["predicted"] for s in res_gain["per_room"]["studio"]["simulation"]]
    diff = np.max(np.abs(np.array(p0) - np.array(pg)))
    assert diff > 1e-3, f"internal gain had no effect on reconstruction (max diff {diff:.2e})"


# ---------------------------------------------------------------------------
# Open-loop simulation responds to a heater power-scale change
# ---------------------------------------------------------------------------

def test_open_loop_responds_to_heater_scale():
    dt = 900.0
    model = _model()
    history = _history(dt)

    sys_full = HouseThermalSDE(model, [_heater(1.0)], dt)
    sys_half = HouseThermalSDE(model, [_heater(0.3)], dt)

    res_full = compute_open_loop_predictions(
        history=history, system=sys_full, room_names=["studio"],
        n_rooms=1, dt=dt, segment_length=40,
    )
    res_half = compute_open_loop_predictions(
        history=history, system=sys_half, room_names=["studio"],
        n_rooms=1, dt=dt, segment_length=40,
    )
    pf = [s["predicted"] for s in res_full["per_room"]["studio"]["simulation"]]
    ph = [s["predicted"] for s in res_half["per_room"]["studio"]["simulation"]]
    diff = np.max(np.abs(np.array(pf) - np.array(ph)))
    assert diff > 1e-3, f"heater scale had no effect on open-loop (max diff {diff:.2e})"
