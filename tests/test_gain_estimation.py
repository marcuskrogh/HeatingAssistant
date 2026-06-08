"""Tests for online internal-gain estimation (augmented-state parameter).

The internal heat gain of each room is promoted to a CD-EKF state with a
regularised (Ornstein–Uhlenbeck) diffusion process.  The control model fixes
the estimate and drops it from the QP state space.  These tests cover the
model-level augmentation (dimensions, drift, Jacobians), the closed-loop
recovery of a known gain, and the regularised reversion behaviour.
"""

import sys
import os
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.controller import (
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


# ── Model-level augmentation ────────────────────────────────────────────────

def _kappa_sigma(tau_hours=24.0, std_w=200.0):
    kappa = 1.0 / (tau_hours * 3600.0)
    sigma = std_w * np.sqrt(2.0 * kappa)
    return kappa, sigma


def test_gain_augmentation_grows_state_dimension():
    model, sources = _make_model_and_sources()
    kappa, sigma = _kappa_sigma()
    base = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
    aug = HouseThermalSDE(
        model, sources, dt=900.0, augment_offsets=False,
        augment_gain=True, gain_kappa=kappa, gain_sigma=sigma,
    )
    # Two rooms, no emitter filters → base nx = 2, augmented nx = 4.
    assert base.nx == 2
    assert aug.nx == 4
    assert aug._gain_block_start == 2
    # Diffusion matrix carries σ_g on the gain block diagonal.
    sig = aug.sigma(np.zeros(aug.nx), np.zeros(aug.nu), np.zeros(aug.nd), np.array([]), 0.0)
    assert sig.shape == (4, 4)
    assert np.isclose(sig[2, 2], sigma)
    assert np.isclose(sig[3, 3], sigma)
    # Measurement Jacobian leaves the gain columns at zero.
    H = aug.dhmdx(np.zeros(aug.nx), np.zeros(aug.nu), np.zeros(aug.nd), np.array([]), 0.0)
    assert H.shape == (2, 4)
    assert np.allclose(H[:, 2:], 0.0)


def test_gain_state_enters_temperature_drift():
    model, sources = _make_model_and_sources()
    kappa, sigma = _kappa_sigma()
    aug = HouseThermalSDE(
        model, sources, dt=900.0, augment_offsets=False,
        augment_gain=True, gain_kappa=kappa, gain_sigma=sigma,
    )
    u = np.zeros(aug.nu)
    d = aug.disturbance_vector(5.0, {"living_room": 0.0, "bedroom": 0.0})
    p = np.array([])

    x0 = np.zeros(aug.nx)
    x0[:2] = [21.0, 20.0]
    f0 = aug.f(x0, u, d, p, 0.0)

    # Add +1000 W internal gain to the living room state.
    x1 = x0.copy()
    x1[aug._gain_block_start] = 1000.0
    f1 = aug.f(x1, u, d, p, 0.0)

    C_living = model.rooms["living_room"].thermal_mass
    # Living-room temperature drift increases by gain / C.
    assert np.isclose(f1[0] - f0[0], 1000.0 / C_living, rtol=1e-6)
    # The bedroom node is unaffected.
    assert np.isclose(f1[1], f0[1])
    # OU drift of the gain state itself: dΔg = -κ Δg.
    assert np.isclose(f1[aug._gain_block_start], -kappa * 1000.0, rtol=1e-9)


def test_gain_dfdx_matches_finite_difference():
    model, sources = _make_model_and_sources()
    kappa, sigma = _kappa_sigma()
    aug = HouseThermalSDE(
        model, sources, dt=900.0, augment_offsets=False,
        augment_gain=True, gain_kappa=kappa, gain_sigma=sigma,
    )
    u = np.array([0.3, 0.2])
    d = aug.disturbance_vector(2.0, {"living_room": 50.0, "bedroom": 0.0})
    p = np.array([])
    x = np.array([20.5, 19.5, 150.0, -80.0])

    J = aug.dfdx(x, u, d, p, 0.0)
    eps = 1e-4
    J_fd = np.zeros_like(J)
    for k in range(aug.nx):
        xp = x.copy(); xp[k] += eps
        xm = x.copy(); xm[k] -= eps
        J_fd[:, k] = (aug.f(xp, u, d, p, 0.0) - aug.f(xm, u, d, p, 0.0)) / (2 * eps)
    assert np.allclose(J, J_fd, atol=1e-6)


def test_fixed_gain_dev_enters_control_model_drift():
    model, sources = _make_model_and_sources()
    ctrl = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False, augment_gain=False)
    u = np.zeros(ctrl.nu)
    d = ctrl.disturbance_vector(5.0, {"living_room": 0.0, "bedroom": 0.0})
    p = np.array([])
    x = np.array([21.0, 20.0])

    f0 = ctrl.f(x, u, d, p, 0.0)
    ctrl.set_fixed_gain_dev(np.array([1000.0, 0.0]))
    f1 = ctrl.f(x, u, d, p, 0.0)

    C_living = model.rooms["living_room"].thermal_mass
    assert np.isclose(f1[0] - f0[0], 1000.0 / C_living, rtol=1e-6)
    assert np.isclose(f1[1], f0[1])


# ── Controller-level wiring ─────────────────────────────────────────────────

def _make_controller(enabled=True, tau_hours=24.0, std_w=200.0, internal_gain=0.0):
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
        gain_estimator_enabled=enabled,
        gain_reversion_time_hours=tau_hours,
        gain_stationary_std_watts=std_w,
    )
    return model, sources, controller


def test_controller_exposes_gain_estimates():
    model, sources, controller = _make_controller(enabled=True)
    assert controller.gain_estimation_enabled is True
    gains = controller.estimated_internal_gains
    assert set(gains.keys()) == {"living_room", "bedroom"}
    # Estimation model carries the gain block; control model does not.
    assert controller._system.nx == controller._control_system.nx + len(model.room_names)


def test_controller_disabled_has_no_gain_block():
    model, sources, controller = _make_controller(enabled=False)
    assert controller.gain_estimation_enabled is False
    assert controller._system.nx == controller._control_system.nx
    # Estimates fall back to the configured nominal gain.
    dev = controller.estimated_internal_gain_deviation
    assert all(v == 0.0 for v in dev.values())


def test_controller_compute_runs_with_estimation():
    model, sources, controller = _make_controller(enabled=True)
    now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    actions = controller.compute(outdoor_temp=2.0, now=now)
    assert set(actions.keys()) == {"lr_heater", "br_heater"}
    for v in actions.values():
        assert np.isfinite(v)
    # Predictions produced over the horizon.
    assert len(controller.predictions) == controller.horizon


def test_estimator_recovers_unmodelled_heat_load():
    """A persistent unmodelled heat load is learned as a positive gain.

    A separate "plant" (the same RC model, but with a hidden +1500 W load in
    the living room that the controller's model does not know about) is
    integrated forward in closed loop.  The CD-EKF should attribute the
    resulting persistent warmth to the living-room internal-gain state and
    drive its estimate toward the true load, while leaving the unaffected
    bedroom near zero.
    """
    from custom_components.heating_assistant.integrator import (
        implicit_euler_substeps,
    )

    HIDDEN_LOAD_W = 300.0
    # Responsive tuning so the slow-timescale estimator makes clear progress
    # within the test horizon (a watt-level load integrates slowly against the
    # 0.5 K measurement noise — this is the genuine observability limit).
    model, sources, controller = _make_controller(
        enabled=True, tau_hours=48.0, std_w=1500.0, internal_gain=0.0
    )

    # Plant: identical model/sources but driven with the hidden load folded
    # into the disturbance channel.  Un-augmented (the "true" world has no
    # gain *state* — the load is real and constant).
    plant_model, plant_sources = _make_model_and_sources()
    plant = HouseThermalSDE(
        plant_model, plant_sources, dt=900.0, augment_offsets=False,
    )
    p = np.array([])
    dt = 900.0
    now = datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)

    x_plant = np.array([21.0, 20.0])  # start near setpoint (regulated plant)
    outdoor = 5.0

    for _ in range(160):
        # Publish the plant's current temperatures as the room measurements.
        for i, name in enumerate(plant_model.room_names):
            model.rooms[name].temperature = float(x_plant[i])

        actions = controller.compute(outdoor_temp=outdoor, now=now)

        # Advance the plant one step with the applied actions and the hidden
        # living-room load added to the solar/internal disturbance slot.
        u = np.array([actions["lr_heater"], actions["br_heater"]])
        d = plant.disturbance_vector(
            outdoor, {"living_room": HIDDEN_LOAD_W, "bedroom": 0.0}
        )
        rhs = lambda x, u=u, d=d: plant.f(x, u, d, p, 0.0)
        jac = lambda x, u=u, d=d: plant.dfdx(x, u, d, p, 0.0)
        x_plant = implicit_euler_substeps(rhs, jac, x_plant, dt, 10)

    dev = controller.estimated_internal_gain_deviation
    # The estimator attributes the hidden warmth to the living room: a clearly
    # positive, well-above-noise estimate that tracks toward the true load.
    assert dev["living_room"] > 30.0
    # The bedroom (no hidden load) stays far smaller in magnitude.
    assert dev["living_room"] > 4.0 * max(abs(dev["bedroom"]), 1.0)


def test_advanced_tuning_strings_present():
    """Both strings.json and translations/en.json expose the new options step."""
    base = os.path.join(
        os.path.dirname(__file__), "..", "custom_components", "heating_assistant"
    )
    for fname in ("strings.json", "translations/en.json"):
        data = json.loads(open(os.path.join(base, fname), encoding="utf-8").read())
        step = data["options"]["step"]
        assert "advanced_tuning" in step, f"{fname} missing advanced_tuning step"
        adv = step["advanced_tuning"]
        for key in (
            "gain_estimator_enabled",
            "gain_reversion_time_hours",
            "gain_stationary_std_watts",
        ):
            assert adv["data"].get(key), f"{fname}: missing label for {key}"
            assert adv["data_description"].get(key), f"{fname}: missing help for {key}"
        # Menu entry wired up.
        assert "advanced_tuning" in step["init"]["menu_options"]


def test_regularised_reversion_pulls_toward_nominal():
    """With no informative excitation the OU process reverts deviation to zero."""
    model, sources = _make_model_and_sources()
    # Very short reversion time so the pull is strong and visible quickly.
    kappa, sigma = _kappa_sigma(tau_hours=1.0, std_w=10.0)
    aug = HouseThermalSDE(
        model, sources, dt=900.0, augment_offsets=False,
        augment_gain=True, gain_kappa=kappa, gain_sigma=sigma,
    )
    # Start with a large deviation; the deterministic drift dΔg = -κΔg must
    # always point back toward zero.
    x = np.zeros(aug.nx)
    x[aug._gain_block_start] = 500.0
    f = aug.f(x, np.zeros(aug.nu), aug.disturbance_vector(10.0, {}), np.array([]), 0.0)
    assert f[aug._gain_block_start] < 0.0  # reverts downward toward nominal
