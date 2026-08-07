"""Focused unit tests for estimation submodules (model_build, warmstart, sensitivity)."""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heatingassistant.engine.estimation.model_build import (
    _build_parametric_system,
    _build_rooms_from_theta,
    _build_system,
    _theta_model_quantities,
)
from heatingassistant.engine.estimation.sensitivity import (
    _cd_ped_neg_ll_and_grad,
    _dFdtheta_const,
    _dfdtheta_step,
    _simulation_mse_and_grad,
)
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout
from heatingassistant.engine.estimation.warmstart import (
    _initial_state_and_covariance,
    _physics_informed_theta,
    _pin_locked_params,
    _update_wall_init_prior_from_history,
)
from heatingassistant.engine.thermal_model import Room, RoomConnection
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)

_SENTINEL = 1e10


def _exciting_std_history(est, *, n_steps: int = 80, dt: float = 60.0):
    """Synthetic CD history with alternating heat and varying outdoor temp."""
    std = []
    t0 = 1_700_000_000.0
    temp = 20.0
    for k in range(n_steps):
        u_frac = 0.85 if (k // 8) % 2 == 0 else 0.05
        t_out = 5.0 + 4.0 * math.sin(2.0 * math.pi * k / 24.0)
        temp += 0.03 * u_frac - 0.012 * (temp - t_out)
        std.append({
            "ym": np.array([temp]),
            "u": np.array([u_frac]),
            "d": np.array([t_out, 0.0, 0.0]),
            "t": t0 + k * dt,
        })
    return std


def _to_std_history(est, history, *, dt: float = 60.0, t0: float = 1_700_000_000.0):
    """Convert HA history to CD-EKF std format with monotonic timestamps."""
    std = est._convert_history_std(history, use_ym=True)
    for k, rec in enumerate(std):
        rec["t"] = t0 + k * dt
    return std


def _single_room_theta(est, layout: _ThetaLayout) -> np.ndarray:
    """Prior θ vector aligned with *layout*."""
    blocks = [
        est._log_mass_prior,
        est._log_r_prior,
        est._q_int_prior,
        np.tile(est._t_wall_init_prior, layout.n_wall_segs),
    ]
    if layout.identifiable_sources:
        blocks.append(
            np.array([est._log_alpha_prior_full[s] for s in layout.identifiable_sources])
        )
    if layout.identifiable_pairs:
        blocks.append(
            np.array([
                est._connection_r_priors[est._connection_pairs.index(p)]
                for p in layout.identifiable_pairs
            ])
        )
    if layout.identifiable_solar:
        blocks.append(
            np.array([est._log_solar_prior_full[i] for i in layout.identifiable_solar])
        )
    if layout.identifiable_splits:
        blocks.append(
            np.array([est._c_air_prior_full[i] for i in layout.identifiable_splits])
        )
        blocks.append(
            np.array([est._r_aw_prior_full[i] for i in layout.identifiable_splits])
        )
    theta = np.concatenate(blocks)
    assert len(theta) == layout.size
    return theta


# ---------------------------------------------------------------------------
# model_build.py
# ---------------------------------------------------------------------------


class TestThetaModelQuantities:
    """_theta_model_quantities mirrors the θ → physical mapping."""

    def test_capacitance_and_conductance_split(self):
        room = make_single_room(thermal_mass=4_000_000.0, r_external=0.04)
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        theta = _single_room_theta(est, layout)

        quants = _theta_model_quantities(est, layout, theta)
        C_tot = math.exp(float(theta[0]))
        ua = math.exp(-float(theta[1]))
        fc = float(est._c_air_prior_full[0])
        f_inf = float(room.infiltration_fraction)
        rf = float(est._r_aw_prior_full[0])
        g_cond = (1.0 - f_inf) * ua

        np.testing.assert_allclose(quants["C_a"], [fc * C_tot])
        np.testing.assert_allclose(quants["C_w"], [(1.0 - fc) * C_tot])
        np.testing.assert_allclose(quants["g_inf"], [f_inf * ua])
        np.testing.assert_allclose(quants["g_aw"], [g_cond / rf])
        np.testing.assert_allclose(quants["g_we"], [g_cond / (1.0 - rf)])


class TestBuildRoomsFromTheta:
    """_build_rooms_from_theta bakes structural parameters into Room objects."""

    def test_mass_resistance_and_inter_room_r(self):
        rooms = [
            Room(
                "a",
                thermal_mass=5e6,
                r_external=0.05,
                connections=[RoomConnection("b", 0.3)],
                temperature=20.0,
            ),
            Room(
                "b",
                thermal_mass=3e6,
                r_external=0.08,
                connections=[RoomConnection("a", 0.3)],
                temperature=19.0,
            ),
        ]
        est = make_kalman_ml_estimator(rooms, make_electric_heaters(rooms), dt=60.0)
        layout = _ThetaLayout(
            n_rooms=2,
            identifiable_sources=[],
            identifiable_pairs=[(0, 1)],
        )
        log_mass = np.log([4.5e6, 2.8e6])
        log_r = np.log([0.04, 0.07])
        log_r_ij_map = {(0, 1): math.log(0.25)}

        built = _build_rooms_from_theta(
            est, layout, log_mass, log_r, log_r_ij_map,
        )
        assert built[0].thermal_mass == pytest.approx(4.5e6)
        assert built[1].r_external == pytest.approx(0.07)
        assert built[0].connections[0].r_value == pytest.approx(0.25)
        assert built[0].internal_gain == 0.0


class TestBuildSystem:
    """Legacy _build_system and full _build_parametric_system entry points."""

    def test_build_system_returns_sde(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], make_electric_heaters([room]), dt=60.0)
        log_mass = est._log_mass_prior
        log_r = est._log_r_prior

        system = _build_system(est, log_mass, log_r)
        assert system is not None
        # Legacy builder keeps offset augmentation on: 2R2C (air+wall) + bias.
        assert system.nx == 3
        assert system.nym == 1

    def test_build_parametric_system_strips_wall_init_block(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0], identifiable_pairs=[])
        theta = _single_room_theta(est, layout)

        system = _build_parametric_system(est, layout, theta)
        assert system is not None
        # Parametric path disables offset augmentation: 2R2C only.
        assert system.nx == 2
        assert system.nym == 1

    def test_build_system_returns_none_on_empty_parameters(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        assert _build_system(est, np.array([]), np.array([])) is None


# ---------------------------------------------------------------------------
# warmstart.py
# ---------------------------------------------------------------------------


class TestUpdateWallInitPriorFromHistory:
    """_update_wall_init_prior_from_history sets MAP prior from first record."""

    def test_empty_history_is_noop(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        before = est._t_wall_init_prior.copy()
        _update_wall_init_prior_from_history(est, [])
        np.testing.assert_array_equal(est._t_wall_init_prior, before)

    def test_falls_back_to_air_when_outdoor_missing(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        _update_wall_init_prior_from_history(est, [{"y": [23.5]}])
        assert est._t_wall_init_prior[0] == pytest.approx(23.5)


class TestPinLockedParams:
    """_pin_locked_params collapses bounds to a single value."""

    def test_locks_thermal_mass_to_log_space(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        theta_prior = _single_room_theta(est, layout)
        bounds = [(lo, hi) for lo, hi in zip(
            np.full(layout.size, -10.0), np.full(layout.size, 10.0),
        )]

        _pin_locked_params(
            est,
            bounds,
            theta_prior,
            {"thermal_mass": {"living_room": 6_000_000.0}},
            layout,
            layout.identifiable_sources,
            layout.identifiable_solar,
            layout.identifiable_splits,
        )
        expected = math.log(6_000_000.0)
        assert bounds[0] == (expected, expected)
        assert theta_prior[0] == pytest.approx(expected)


class TestPhysicsInformedTheta:
    """Coarse OLS warm start from history."""

    def test_returns_none_when_history_too_short(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0], identifiable_pairs=[])
        history = generate_history([room], sources, n_steps=10)
        std = _to_std_history(est, history)
        theta_prior = _single_room_theta(est, layout)
        lb = np.full(layout.size, -20.0)
        ub = np.full(layout.size, 20.0)

        assert _physics_informed_theta(
            est, std, layout, theta_prior, lb, ub, nominal_dt=60.0,
        ) is None

    def test_fits_mass_and_resistance_from_excited_synthetic_data(self):
        true_mass = 3_500_000.0
        true_r = 0.06
        room = make_single_room(thermal_mass=true_mass, r_external=true_r)
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0], identifiable_pairs=[])
        std = _exciting_std_history(est, n_steps=80)
        theta_prior = _single_room_theta(est, layout)
        lb = np.full(layout.size, -20.0)
        ub = np.full(layout.size, 20.0)

        theta_fit = _physics_informed_theta(
            est, std, layout, theta_prior, lb, ub, nominal_dt=60.0,
        )
        assert theta_fit is not None
        # Coarse OLS — check the fit moved mass/r away from the default prior.
        assert theta_fit[0] != pytest.approx(float(theta_prior[0]), abs=0.05)
        assert theta_fit[1] != pytest.approx(float(theta_prior[1]), abs=0.05)
        assert math.exp(theta_fit[0]) > 0.0
        assert math.exp(-theta_fit[1]) > 0.0


class TestInitialStateAndCovariance:
    """_initial_state_and_covariance sizes x0/P0 for 2R2C systems."""

    def test_shapes_and_latent_uncertainty(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0], identifiable_pairs=[])
        theta = _single_room_theta(est, layout)
        system = _build_parametric_system(est, layout, theta)
        assert system is not None

        ym0 = np.array([20.5])
        u0 = np.array([0.5])
        d0 = np.array([5.0, 0.0, 0.0])
        x0, P0 = _initial_state_and_covariance(est, system, ym0, u=u0, d=d0)

        assert x0.shape == (system.nx,)
        assert P0.shape == (system.nx, system.nx)
        assert P0[0, 0] == pytest.approx(est._R_var * 10.0)
        # Latent (wall + emitter) states start with 4× air-node uncertainty.
        assert P0[1, 1] == pytest.approx(est._R_var * 40.0)


# ---------------------------------------------------------------------------
# sensitivity.py
# ---------------------------------------------------------------------------


class TestDfdthetaStep:
    """Analytic ∂f/∂θ at a fixed state."""

    def test_log_mass_rows_scale_negative_drift(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0], identifiable_pairs=[])
        theta = _single_room_theta(est, layout)
        model = _build_parametric_system(est, layout, theta)
        assert model is not None

        quants = _theta_model_quantities(est, layout, theta)
        ntheta, nx = len(theta), int(model.nx)
        x = np.asarray(model.initial_state_from_measurement(
            np.array([20.0]), np.array([0.5]), np.array([5.0, 0.0, 0.0]),
        ))
        u_k = np.array([0.5])
        d_k = np.array([5.0, 0.0, 0.0])
        f_val = model.f(x, u_k, d_k, model.params, 0.0)

        D = _dfdtheta_step(
            est, quants, layout, model, f_val, x, u_k, d_k, ntheta, nx,
        )
        assert D.shape == (ntheta, nx)
        assert D[0, 0] == pytest.approx(-f_val[0])
        assert D[0, 1] == pytest.approx(-f_val[1])


class TestDFdthetaConst:
    """Analytic ∂(∂f/∂x)/∂θ — constant w.r.t. state for 2R2C."""

    def test_log_mass_block_negates_f_jacobian_rows(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        theta = _single_room_theta(est, layout)
        model = _build_parametric_system(est, layout, theta)
        assert model is not None

        quants = _theta_model_quantities(est, layout, theta)
        ntheta, nx = len(theta), int(model.nx)
        F_phys = model._F

        D = _dFdtheta_const(est, quants, layout, model, ntheta, nx)
        assert D.shape == (ntheta, nx, nx)
        np.testing.assert_allclose(D[0, 0, :2], -F_phys[0, :])
        np.testing.assert_allclose(D[0, 1, :2], -F_phys[1, :])


class TestSimulationMseAndGrad:
    """Open-loop MSE objective — edge cases only (no FD gradient duplication)."""

    def test_non_finite_theta_returns_sentinel(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        history = generate_history([room], sources, n_steps=30)
        std = _to_std_history(est, history)
        theta = _single_room_theta(est, layout)
        theta[0] = np.inf

        mse, grad = _simulation_mse_and_grad(
            est, theta, layout, std, nominal_dt=60.0,
            min_segment_steps=5, max_window_steps=20,
        )
        assert mse == _SENTINEL
        assert np.all(grad == 0.0)

    def test_short_history_returns_sentinel(self):
        room = make_single_room()
        est = make_kalman_ml_estimator([room], [], dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        theta = _single_room_theta(est, layout)
        std = [{"ym": np.array([20.0]), "u": np.array([]), "d": np.array([5.0, 0.0, 0.0]),
                "t": 0.0}]

        mse, grad = _simulation_mse_and_grad(
            est, theta, layout, std, nominal_dt=60.0,
            min_segment_steps=20, max_window_steps=60,
        )
        assert mse == _SENTINEL
        assert np.all(grad == 0.0)

    def test_finite_mse_on_synthetic_segment(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        history = generate_history([room], sources, n_steps=40, seed=5)
        std = _to_std_history(est, history)
        theta = _single_room_theta(est, layout)

        mse, grad = _simulation_mse_and_grad(
            est, theta, layout, std, nominal_dt=60.0,
            min_segment_steps=5, max_window_steps=30,
        )
        assert np.isfinite(mse) and mse < _SENTINEL
        assert np.all(np.isfinite(grad))


class TestCdPedNegLlAndGrad:
    """EKF PED likelihood gradient — sentinel edge cases only."""

    def test_non_finite_theta_returns_sentinel(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        history = generate_history([room], sources, n_steps=25)
        std = _to_std_history(est, history)
        theta = _single_room_theta(est, layout)
        theta[1] = np.nan
        system = _build_parametric_system(est, layout, _single_room_theta(est, layout))
        x0, P0 = _initial_state_and_covariance(
            est, system, std[0]["ym"], u=std[0]["u"], d=std[0]["d"],
        )

        neg_ll, grad = _cd_ped_neg_ll_and_grad(
            est, theta, layout, std, x0, P0,
        )
        assert neg_ll == _SENTINEL
        assert np.all(grad == 0.0)

    def test_finite_neg_ll_on_short_history(self):
        room = make_single_room()
        sources = make_electric_heaters([room])
        est = make_kalman_ml_estimator([room], sources, dt=60.0)
        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[], identifiable_pairs=[])
        history = generate_history([room], sources, n_steps=25, seed=3)
        std = _to_std_history(est, history)
        theta = _single_room_theta(est, layout)
        system = _build_parametric_system(est, layout, theta)
        assert system is not None
        x0, P0 = _initial_state_and_covariance(
            est, system, std[0]["ym"], u=std[0]["u"], d=std[0]["d"],
        )

        neg_ll, grad = _cd_ped_neg_ll_and_grad(
            est, theta, layout, std, x0, P0,
        )
        assert np.isfinite(neg_ll) and neg_ll < _SENTINEL
        assert np.all(np.isfinite(grad))
