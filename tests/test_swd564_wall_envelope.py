"""SWD-564: 2R2C wall SS in the CD-EKF and grey-box PE (no post-clip)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mbc.estimation import ContinuousDiscreteEKFParams, IntegrationScheme

from heatingassistant.engine.controller.ekf import _InnovationEKF
from heatingassistant.engine.controller.sde import HouseThermalSDE
from heatingassistant.engine.estimation.regularization import (
    _compute_regularization_theta,
    tw0_ss_means,
)
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.engine.wall_physics import (
    WALL_LAG_SIGMA_K,
    WALL_PROCESS_NOISE_FRACTION,
    accumulate_wall_ss_penalty,
    fuse_wall_equilibrium,
    wall_ss_from_room,
    wall_steady_state,
)


def _living() -> Room:
    return Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=22.0,
        wall_temperature=6.0,
    )


def test_overnight_wall_ss_is_air_outdoor_mix_not_a_floor() -> None:
    """Tw≈6 °C is far from 2R2C SS (mix of 22 °C air and 11 °C outdoor)."""
    room = _living()
    mu = wall_ss_from_room(room, 22.0, 11.0)
    g_inf, g_aw, g_we = room.conductances()
    g_wout = g_we + room.sky_radiative_ua + room.thermal_bridge_psi_l
    rho = g_aw / (g_aw + g_wout)
    expect = rho * 22.0 + (1.0 - rho) * 11.0
    assert mu == pytest.approx(expect)
    assert mu > 11.0
    assert abs(mu - 6.0) > abs(mu - 22.0)
    # Not a hardcoded clip onto min(Ta, Tout) − slack.
    assert mu != pytest.approx(11.0 - 1.5)


def test_solar_raises_algebraic_wall_ss() -> None:
    room = _living()
    dark = wall_ss_from_room(room, 22.0, 10.0, 0.0)
    sun = wall_ss_from_room(room, 22.0, 10.0, 800.0)
    assert sun > dark


def test_fuse_wall_equilibrium_pulls_toward_ss_without_clipping() -> None:
    n = 1
    x = np.array([22.0, 6.0, 0.0])
    P = np.eye(3) * (float(WALL_LAG_SIGMA_K) ** 2)
    mu = wall_steady_state([22.0], 11.0, [20.0], [1.0])
    r = np.array([float(WALL_LAG_SIGMA_K) ** 2])
    x_new, p_new = fuse_wall_equilibrium(x, P, mu, r, n)
    assert x_new[0] == pytest.approx(22.0)
    assert x_new[1] > 6.0
    assert abs(x_new[1] - float(mu[0])) < abs(6.0 - float(mu[0]))
    assert x_new[1] != pytest.approx(11.0 - 1.5)
    assert p_new[1, 1] < P[1, 1]


def test_pe_tw0_map_mean_is_theta_dependent_ss() -> None:
    from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator

    room = Room("a", 4e6, 0.04, temperature=22.0, r_aw_fraction=0.05)
    est = KalmanMLEstimator([room], [], dt=900.0)
    layout = _ThetaLayout(
        n_rooms=1, identifiable_sources=[], identifiable_pairs=[],
    )
    est._tw0_ta = np.array([[22.0]])
    est._tw0_tout = np.array([11.0])
    est._tw0_qsol = np.array([[0.0]])
    theta = np.concatenate([
        est._log_mass_prior, est._log_r_prior, est._q_int_prior, np.array([6.0]),
    ])
    mu = tw0_ss_means(est, layout, theta)
    assert mu[0] == pytest.approx(wall_ss_from_room(room, 22.0, 11.0), abs=0.2)
    r_far = _compute_regularization_theta(est, theta, layout)
    theta_ok = theta.copy()
    theta_ok[3] = mu[0]
    r_ok = _compute_regularization_theta(est, theta_ok, layout)
    assert r_far > r_ok


def test_nstep_objective_penalises_wall_away_from_ss() -> None:
    room = _living()
    _g_inf, g_aw, g_we = room.conductances()
    quants = {
        "g_aw": np.array([g_aw]),
        "g_we": np.array([g_we]),
        "rf": np.array([room.r_aw_fraction]),
        "s": np.array([1.0]),
        "facade": np.array([0.0]),
        "wall_frac": 0.5,
    }
    mu = wall_steady_state([22.0], 11.0, quants["g_aw"], quants["g_we"])
    x_ok = np.array([22.0, float(mu[0])])
    x_bad = np.array([22.0, 6.0])
    sx = np.zeros((2, 2))
    sx[1, 1] = 1.0
    sse0, g0 = accumulate_wall_ss_penalty(
        0.0, np.zeros(2), x_ok, sx, [22.0], 11.0, quants, n_rooms=1,
    )
    sse1, g1 = accumulate_wall_ss_penalty(
        0.0, np.zeros(2), x_bad, sx, [22.0], 11.0, quants, n_rooms=1,
    )
    assert sse0 == pytest.approx(0.0, abs=1e-9)
    assert sse1 > 0.0
    assert g1[1] != pytest.approx(0.0)
    assert g0[1] == pytest.approx(0.0)


def test_live_ekf_fuses_unphysical_wall_toward_ss() -> None:
    room = _living()
    model = HouseModel([room])
    sources = [ElectricHeater("h", "living_room", max_power=2000.0)]
    sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
    x0 = np.array(sde.x, dtype=float)
    n = sde._n_rooms
    x0[n] = 6.0
    p0 = np.eye(sde.nx) * (float(WALL_LAG_SIGMA_K) ** 2)
    ekf = _InnovationEKF(
        sde, x0, p0,
        params=ContinuousDiscreteEKFParams(
            n_steps=5,
            scheme=IntegrationScheme.IMPLICIT_EULER,
        ),
    )
    y = np.array([22.0])
    u = np.zeros(sde.nu)
    d = sde.disturbance_vector(11.0, {})
    x_hat, _ = ekf.step(y, u, d, np.array([]), 0.0)
    mu = float(sde.wall_equilibrium(y, 11.0)[0])
    assert x_hat[n] > 6.0
    assert abs(x_hat[n] - mu) < abs(6.0 - mu)
    assert x_hat[n] != pytest.approx(11.0 - 1.5)


def test_overnight_filter_recovers_toward_ss() -> None:
    room = _living()
    model = HouseModel([room])
    sources = [ElectricHeater("h", "living_room", max_power=2000.0)]
    sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
    x0 = np.array(sde.x, dtype=float)
    n = sde._n_rooms
    x0[n] = 6.0
    p0 = np.eye(sde.nx)
    p0[n, n] = float(WALL_LAG_SIGMA_K) ** 2
    ekf = _InnovationEKF(
        sde, x0, p0,
        params=ContinuousDiscreteEKFParams(
            n_steps=5,
            scheme=IntegrationScheme.IMPLICIT_EULER,
        ),
    )
    y = np.array([22.0])
    u = np.zeros(sde.nu)
    d = sde.disturbance_vector(11.0, {})
    x_hat = x0
    for k in range(16):
        x_hat, _ = ekf.step(y, u, d, np.array([]), 900.0 * k)
    mu = float(sde.wall_equilibrium(y, 11.0)[0])
    assert x_hat[n] > 11.0
    assert abs(x_hat[n] - mu) < 3.0


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


def test_no_wall_constraints_module() -> None:
    import importlib.util
    spec = importlib.util.find_spec("heatingassistant.engine.wall_constraints")
    assert spec is None
