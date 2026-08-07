"""Regression: online internal-gain estimation is removed from the state space.

The per-room internal-gain deviation used to be promoted to an augmented CD-EKF
state (an Ornstein–Uhlenbeck process).  That extra unobserved state hurt
identifiability and, because it was never persisted, reset to zero on every
restart — making the controller appear to "change dynamics" after a stop/start.

These tests lock in that the estimation and control models now share a single,
un-augmented state space and that the public gain API degrades gracefully to the
configured (offline-identified) nominal gain.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from heatingassistant.engine.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.controller import (
    HouseThermalSDE,
    HeatingMPCController,
)


def _make_model_and_sources(internal_gain_living=0.0):
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[RoomConnection("bedroom", 0.2)],
        temperature=18.0,
        setpoint=21.0,
        internal_gain=internal_gain_living,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=3_000_000.0,
        r_external=0.08,
        connections=[RoomConnection("living_room", 0.2)],
        temperature=17.0,
        setpoint=20.0,
    )
    model = HouseModel([living, bedroom])
    sources = [
        ElectricHeater("lr_heater", "living_room", max_power=2000.0),
        ElectricHeater("br_heater", "bedroom", max_power=1500.0),
    ]
    return model, sources


def _make_controller(internal_gain=0.0):
    model, sources = _make_model_and_sources(internal_gain_living=internal_gain)
    controller = HeatingMPCController(
        model=model,
        heat_sources=sources,
        horizon=6,
        dt=900.0,
        measurement_dt=900.0,
        tracking_weight=1.0,
        energy_weight=0.01,
        smoothing_weight=0.1,
        soft_constraint_weight=10.0,
        terminal_weight=100.0,
    )
    return model, sources, controller


def test_sde_has_no_gain_block():
    """The 2R2C SDE state is exactly [T_a (n), T_w (n)] — no gain augmentation."""
    model, sources = _make_model_and_sources()
    sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
    # Two rooms × two nodes → nx = 4, with no gain block appended.
    assert sde.nx == 4
    assert not hasattr(sde, "_augment_gain")
    assert not hasattr(sde, "set_fixed_gain_dev")
    assert not hasattr(sde, "gain_deviation_from_state")
    # Diffusion matrix is sized to the physical state only.
    sig = sde.sigma(
        np.zeros(sde.nx), np.zeros(sde.nu), np.zeros(sde.nd), np.array([]), 0.0
    )
    assert sig.shape == (4, 4)


def test_estimation_and_control_models_share_state_space():
    """No augmented EKF state: estimation nx equals control nx exactly."""
    model, sources, controller = _make_controller()
    assert controller._system.nx == controller._control_system.nx
    assert controller.gain_estimation_enabled is False


def test_estimated_gains_fall_back_to_nominal():
    """The gain API returns the configured nominal gain, with no online deviation."""
    model, sources, controller = _make_controller(internal_gain=150.0)
    gains = controller.estimated_internal_gains
    assert gains["living_room"] == 150.0
    assert gains["bedroom"] == 0.0


def test_compute_runs_without_gain_estimation():
    model, sources, controller = _make_controller()
    now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    actions = controller.compute(outdoor_temp=2.0, now=now)
    assert set(actions.keys()) == {"lr_heater", "br_heater"}
    for v in actions.values():
        assert np.isfinite(v)
    assert len(controller.predictions) == controller.horizon
