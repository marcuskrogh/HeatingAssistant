"""SWD-564: physical envelope on wall temperature (EKF + PE)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mbc.estimation import ContinuousDiscreteEKFParams, IntegrationScheme

from heatingassistant.engine.controller.ekf import _InnovationEKF
from heatingassistant.engine.controller.sde import HouseThermalSDE
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.engine.wall_constraints import (
    WALL_PROCESS_NOISE_FRACTION,
    WALL_SLACK_ABOVE_K,
    WALL_SLACK_BELOW_K,
    accumulate_wall_envelope_penalty,
    apply_t_wall_init_envelope_bounds,
    clip_wall_temperature,
    envelope_limits,
    envelope_signed_violation,
    project_wall_block,
)


def test_overnight_wall_below_outdoor_is_outside_envelope() -> None:
    """The reported overnight crash (Tw≈6 °C, Tout>10 °C, Ta≈22 °C) is illegal."""
    lo, hi = envelope_limits(22.0, 11.0)
    assert lo == pytest.approx(11.0 - WALL_SLACK_BELOW_K)
    assert hi == pytest.approx(22.0 + WALL_SLACK_ABOVE_K)
    assert envelope_signed_violation(6.0, 22.0, 11.0) < 0.0
    assert clip_wall_temperature(6.0, 22.0, 11.0) == pytest.approx(lo)


def test_solar_overshoot_stays_inside_slack() -> None:
    lo, hi = envelope_limits(22.0, 10.0)
    assert clip_wall_temperature(28.0, 22.0, 10.0) == pytest.approx(28.0)
    assert clip_wall_temperature(40.0, 22.0, 10.0) == pytest.approx(hi)


def test_project_wall_block_clips_only_wall_nodes() -> None:
    x = np.array([22.0, 21.0, 6.0, 40.0, 0.0, 0.0])
    project_wall_block(x, [22.0, 21.0], 11.0, 2)
    assert x[0] == pytest.approx(22.0)
    assert x[1] == pytest.approx(21.0)
    assert x[2] == pytest.approx(11.0 - WALL_SLACK_BELOW_K)
    assert x[3] <= 21.0 + WALL_SLACK_ABOVE_K + 1e-9


def test_pe_tw0_box_uses_first_sample_envelope() -> None:
    history = [
        {"y": [22.0], "d_outdoor": 11.0, "timestamp": 100.0},
        {"y": [22.5], "d_outdoor": 5.0, "timestamp": 200.0},
    ]
    layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
    bounds = [(0.0, 0.0)] * 3 + [(-30.0, 60.0)]
    theta = np.array([0.0, 0.0, 0.0, -20.0])
    apply_t_wall_init_envelope_bounds(
        bounds, layout, history, 1, theta_prior=theta,
    )
    lo, hi = envelope_limits(22.0, 11.0)
    assert bounds[3] == (lo, hi)
    assert theta[3] == pytest.approx(lo)


def test_nstep_objective_penalises_wall_outside_envelope() -> None:
    x_ok = np.array([22.0, 18.0])
    x_bad = np.array([22.0, 6.0])
    sx = np.zeros((2, 2))
    sx[1, 1] = 1.0
    sse0, g0 = accumulate_wall_envelope_penalty(0.0, np.zeros(2), x_ok, sx, [22.0], 11.0, 1)
    sse1, g1 = accumulate_wall_envelope_penalty(0.0, np.zeros(2), x_bad, sx, [22.0], 11.0, 1)
    assert sse0 == pytest.approx(0.0)
    assert sse1 > 0.0
    assert g1[1] != pytest.approx(0.0)
    assert g0[1] == pytest.approx(0.0)


def test_live_ekf_projects_unphysical_wall() -> None:
    room = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=22.0,
        wall_temperature=6.0,
    )
    model = HouseModel([room])
    sources = [ElectricHeater("h", "living_room", max_power=2000.0)]
    sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
    x0 = np.array(sde.x, dtype=float)
    n = sde._n_rooms
    x0[n] = 6.0
    ekf = _InnovationEKF(
        sde, x0, np.eye(sde.nx),
        params=ContinuousDiscreteEKFParams(
            n_steps=5,
            scheme=IntegrationScheme.IMPLICIT_EULER,
        ),
    )
    y = np.array([22.0])
    u = np.zeros(sde.nu)
    d = sde.disturbance_vector(11.0, {})
    x_hat, _ = ekf.step(y, u, d, np.array([]), 0.0)
    lo, hi = envelope_limits(22.0, 11.0)
    assert lo <= x_hat[n] <= hi
    assert x_hat[n] > 6.0


def test_wall_process_noise_fraction_on_sde() -> None:
    room = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=20.0,
    )
    model = HouseModel([room])
    sources = [ElectricHeater("h", "living_room", max_power=2000.0)]
    sde = HouseThermalSDE(model, sources, dt=900.0, sigma_w=0.1)
    sig = sde.sigma(
        np.array(sde.x),
        np.zeros(sde.nu),
        sde.disturbance_vector(5.0, {}),
        np.array([]),
        0.0,
    )
    diag = np.diag(sig)
    assert diag[0] == pytest.approx(0.1)
    assert diag[1] == pytest.approx(0.1 * WALL_PROCESS_NOISE_FRACTION)
