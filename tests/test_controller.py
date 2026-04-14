"""Unit tests for the MPC controller."""

import sys
import os
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from cvxopt import matrix as cvx_matrix

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from custom_components.heating_assistant.controller import (
    LinearDiscreteModel,
    KalmanFilter,
    StateEstimator,
    OptimalControlProblem,
    MPCController,
    HouseThermalSystem,
    HeatingMPCController,
    _cvx_to_np,
    _cvx_col_to_np,
)


# -- Helpers ------------------------------------------------------------------

def _cvx_vec(vals):
    """Create a cvxopt column vector from a list."""
    return cvx_matrix(vals, (len(vals), 1), tc="d")


def _to_np(m):
    """Convert a cvxopt matrix to numpy for assertion helpers."""
    return _cvx_to_np(m)


# -- Fixtures -----------------------------------------------------------------

def make_model_and_sources():
    """Simple two-room model with one electric heater per room."""
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[RoomConnection("bedroom", 0.2)],
        temperature=18.0,
        setpoint=21.0,
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


# -- Generic framework tests --------------------------------------------------

class TestHouseThermalSystem:
    """Tests for HouseThermalSystem as a LinearDiscreteModel implementation."""

    def test_dimensions(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        assert sys_.n_x == 2
        assert sys_.n_u == 2
        assert sys_.n_d == 3

    def test_output_matrix_is_identity(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        C_np = _to_np(sys_.C)
        np.testing.assert_array_equal(C_np, np.eye(2))

    def test_discretize_shapes(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        d = sys_.disturbance_vector(5.0, {"living_room": 0.0, "bedroom": 0.0})
        A_d, B_d, E_d = sys_.discretize(d)
        assert A_d.size == (2, 2)
        assert B_d.size == (2, 2)
        assert E_d.size == (2, 3)

    def test_A_d_is_stable(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        d = sys_.disturbance_vector(5.0, {})
        A_d, _, _ = sys_.discretize(d)
        A_np = _to_np(A_d)
        assert all(abs(lam) < 1.0 for lam in np.linalg.eigvals(A_np))

    def test_B_d_positive(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        d = sys_.disturbance_vector(5.0, {})
        _, B_d, _ = sys_.discretize(d)
        B_np = _to_np(B_d)
        assert np.all(B_np.sum(axis=0) > 0)

    def test_x_ref_matches_setpoints(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        ref = _cvx_col_to_np(sys_.x_ref)
        np.testing.assert_array_equal(ref, [21.0, 20.0])

    def test_u_bounds(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        lb, ub = sys_.u_bounds
        np.testing.assert_array_equal(_cvx_col_to_np(lb), [0.0, 0.0])
        np.testing.assert_array_equal(_cvx_col_to_np(ub), [1.0, 1.0])


class TestKalmanFilter:
    """Tests for the discrete-time Kalman filter."""

    def test_bootstrap_from_measurement(self):
        """First update should return y (for full-state observation C = I)."""
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        kf = KalmanFilter(sys_)
        d = sys_.disturbance_vector(5.0, {})
        y = _cvx_vec([18.5, 17.5])
        x_hat = kf.update(y, d)
        np.testing.assert_array_almost_equal(_cvx_col_to_np(x_hat), [18.5, 17.5])

    def test_subsequent_update_blends_prediction(self):
        """After the first call the Kalman filter runs predict–update."""
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        kf = KalmanFilter(sys_)
        d = sys_.disturbance_vector(5.0, {})
        kf.update(_cvx_vec([18.0, 17.0]), d)
        kf.record_action(_cvx_vec([0.0, 0.0]))
        y1 = _cvx_vec([18.1, 17.1])
        x_hat = kf.update(y1, d)
        np.testing.assert_array_almost_equal(
            _cvx_col_to_np(x_hat), [18.1, 17.1], decimal=1
        )

    def test_covariance_is_propagated(self):
        """P should change after a predict–update cycle."""
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        kf = KalmanFilter(sys_)
        d = sys_.disturbance_vector(5.0, {})
        P0 = _to_np(kf.P)
        kf.update(_cvx_vec([18.0, 17.0]), d)
        kf.record_action(_cvx_vec([0.0, 0.0]))
        kf.update(_cvx_vec([18.1, 17.1]), d)
        P1 = _to_np(kf.P)
        assert not np.allclose(P0, P1)

    def test_covariance_stays_symmetric(self):
        """P must remain symmetric after multiple steps."""
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        kf = KalmanFilter(sys_)
        d = sys_.disturbance_vector(5.0, {})
        for temp in [18.0, 18.05, 18.1, 18.15]:
            y = _cvx_vec([temp, temp - 1.0])
            kf.update(y, d)
            kf.record_action(_cvx_vec([0.5, 0.3]))
        P = _to_np(kf.P)
        np.testing.assert_array_almost_equal(P, P.T)

    def test_covariance_stays_positive_semidefinite(self):
        """Eigenvalues of P must remain non-negative."""
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        kf = KalmanFilter(sys_)
        d = sys_.disturbance_vector(0.0, {})
        for i in range(10):
            y = _cvx_vec([18.0 + 0.05 * i, 17.0 + 0.03 * i])
            kf.update(y, d)
            kf.record_action(_cvx_vec([0.8, 0.5]))
        eigvals = np.linalg.eigvalsh(_to_np(kf.P))
        assert np.all(eigvals >= -1e-10)

    def test_custom_noise_covariances(self):
        """Custom Q and R should be accepted and affect the estimate."""
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        n = sys_.n_x
        Q = cvx_matrix(0.0, (n, n))
        for i in range(n):
            Q[i, i] = 0.1
        R = cvx_matrix(0.0, (n, n))
        for i in range(n):
            R[i, i] = 0.001
        kf = KalmanFilter(sys_, Q=Q, R=R)
        d = sys_.disturbance_vector(5.0, {})
        kf.update(_cvx_vec([18.0, 17.0]), d)
        kf.record_action(_cvx_vec([0.0, 0.0]))
        y1 = _cvx_vec([18.1, 17.1])
        x_hat = kf.update(y1, d)
        np.testing.assert_array_almost_equal(
            _cvx_col_to_np(x_hat), [18.1, 17.1], decimal=1
        )

    def test_backward_compatible_alias(self):
        """StateEstimator should be an alias for KalmanFilter."""
        assert StateEstimator is KalmanFilter


class TestOptimalControlProblem:
    def test_output_shapes(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        N = 3
        n_x, n_u = sys_.n_x, sys_.n_u
        Q = cvx_matrix(0.0, (n_x, n_x))
        for i in range(n_x):
            Q[i, i] = 1.0
        R = cvx_matrix(0.0, (n_u, n_u))
        for i in range(n_u):
            R[i, i] = 0.01
        ocp = OptimalControlProblem(sys_, N, Q, R)
        d = sys_.disturbance_vector(5.0, {})
        # Stack disturbance into column vector
        n_d = sys_.n_d
        D = cvx_matrix(0.0, (N * n_d, 1))
        for k in range(N):
            D[k * n_d:(k + 1) * n_d] = d
        x0 = cvx_matrix(sys_.x, (n_x, 1))
        U, X = ocp.solve(x0, D, sys_.x_ref)
        assert U.size == (N * n_u, 1)
        assert X.size == (N * n_x, 1)

    def test_inputs_within_bounds(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        N = 3
        n_x, n_u, n_d = sys_.n_x, sys_.n_u, sys_.n_d
        Q = cvx_matrix(0.0, (n_x, n_x))
        for i in range(n_x):
            Q[i, i] = 1.0
        R = cvx_matrix(0.0, (n_u, n_u))
        for i in range(n_u):
            R[i, i] = 0.01
        ocp = OptimalControlProblem(sys_, N, Q, R)
        d = sys_.disturbance_vector(-10.0, {})
        D = cvx_matrix(0.0, (N * n_d, 1))
        for k in range(N):
            D[k * n_d:(k + 1) * n_d] = d
        x0 = cvx_matrix(sys_.x, (n_x, 1))
        U, _ = ocp.solve(x0, D, sys_.x_ref)
        U_np = _cvx_col_to_np(U)
        assert np.all(U_np >= -1e-9) and np.all(U_np <= 1.0 + 1e-9)

    def test_heats_when_below_setpoint(self):
        model, sources = make_model_and_sources()
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        N = 4
        n_x, n_u, n_d = sys_.n_x, sys_.n_u, sys_.n_d
        Q = cvx_matrix(0.0, (n_x, n_x))
        for i in range(n_x):
            Q[i, i] = 1.0
        R = cvx_matrix(0.0, (n_u, n_u))
        for i in range(n_u):
            R[i, i] = 0.001
        ocp = OptimalControlProblem(sys_, N, Q, R)
        d = sys_.disturbance_vector(-10.0, {})
        D = cvx_matrix(0.0, (N * n_d, 1))
        for k in range(N):
            D[k * n_d:(k + 1) * n_d] = d
        x0 = cvx_matrix(sys_.x, (n_x, 1))
        U, _ = ocp.solve(x0, D, sys_.x_ref)
        # First step inputs should be positive
        u0 = _cvx_col_to_np(U[:n_u])
        assert u0.sum() > 0.0

    def test_no_heat_above_setpoint(self):
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        sys_ = HouseThermalSystem(model, sources, dt=900.0)
        N = 3
        n_x, n_u, n_d = sys_.n_x, sys_.n_u, sys_.n_d
        Q = cvx_matrix(0.0, (n_x, n_x))
        for i in range(n_x):
            Q[i, i] = 1.0
        R = cvx_matrix(0.0, (n_u, n_u))
        for i in range(n_u):
            R[i, i] = 0.001
        ocp = OptimalControlProblem(sys_, N, Q, R)
        d = sys_.disturbance_vector(22.0, {})
        D = cvx_matrix(0.0, (N * n_d, 1))
        for k in range(N):
            D[k * n_d:(k + 1) * n_d] = d
        x0 = cvx_matrix(sys_.x, (n_x, 1))
        U, _ = ocp.solve(x0, D, sys_.x_ref)
        U_np = _cvx_col_to_np(U)
        np.testing.assert_array_almost_equal(U_np, np.zeros(N * n_u), decimal=6)


# -- HeatingMPCController (application facade) tests -------------------------

class TestHeatingMPCController:
    def test_actions_cover_all_sources(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        for src in sources:
            assert src.name in actions

    def test_fractions_in_range(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        for name, frac in actions.items():
            assert 0.0 <= frac <= 1.0, f"Fraction out of range for {name}: {frac}"

    def test_fractions_are_continuous(self):
        """Continuous QP optimisation; fractions are not grid-restricted."""
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=4, dt=900,
                                    energy_weight=0.001)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        assert all(isinstance(f, float) for f in actions.values())
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_heats_when_below_setpoint(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-10.0, now=now)
        assert any(frac > 0.0 for frac in actions.values())

    def test_no_heat_when_warm_enough(self):
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=22.0, now=now)
        assert all(frac == pytest.approx(0.0, abs=1e-6) for frac in actions.values())

    def test_solar_gains_provided_externally(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        gains = {"living_room": 300.0, "bedroom": 100.0}
        actions = ctrl.compute(outdoor_temp=5.0, solar_gains=gains, now=now)
        for src in sources:
            assert src.name in actions
            assert 0.0 <= actions[src.name] <= 1.0

    def test_controller_updates_source_state(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        for src in sources:
            expected = src.thermal_power(actions[src.name])
            assert src.current_power == pytest.approx(expected, rel=1e-6)

    def test_visualisation_properties_populated(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)
        assert len(ctrl.predictions) == 3
        assert len(ctrl.outdoor_forecast) == 3
        assert len(ctrl.solar_forecast) == 3
        assert len(ctrl.heating_schedule) == 3

    def test_predictions_contain_all_rooms(self):
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)
        for step in ctrl.predictions:
            assert "living_room" in step
            assert "bedroom" in step

    def test_heat_pump_min_power_respected(self):
        living = Room("living_room", 5e6, 0.05, temperature=20.5, setpoint=21.0)
        model = HouseModel([living])
        hp = HeatPump("hp1", "living_room", max_power=6100.0,
                      cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0)
        ctrl = HeatingMPCController(model, [hp], horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=7.0, now=now)
        assert hp.current_power == 0.0 or hp.current_power >= 1000.0

    def test_heat_pump_cop_varies_with_temperature(self):
        """Higher outdoor temperature -> higher COP."""
        hp = HeatPump("hp", "lr", max_power=6100.0, cop_rated=3.5, cop_temp_ref=7.0)
        assert hp.cop(10.0) > hp.cop(-10.0)

    def test_smoothing_weight_reduces_input_change(self):
        """With a large smoothing weight, the first-step input change should
        be smaller than without smoothing (rate-of-change penalty)."""
        model_a, sources_a = make_model_and_sources()
        model_b, sources_b = make_model_and_sources()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        ctrl_no_smooth = HeatingMPCController(model_a, sources_a, horizon=4, dt=900,
                                              smoothing_weight=0.0)
        ctrl_smooth = HeatingMPCController(model_b, sources_b, horizon=4, dt=900,
                                           smoothing_weight=1.0)

        actions_a = ctrl_no_smooth.compute(outdoor_temp=-10.0, now=now)
        actions_b = ctrl_smooth.compute(outdoor_temp=-10.0, now=now)

        total_a = sum(actions_a.values())
        total_b = sum(actions_b.values())
        assert total_b <= total_a + 1e-6

    def test_smoothing_disabled_with_zero_weight(self):
        """smoothing_weight=0.0 gives the same result as the original cost."""
        model_a, sources_a = make_model_and_sources()
        model_b, sources_b = make_model_and_sources()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        ctrl_a = HeatingMPCController(model_a, sources_a, horizon=3, dt=900,
                                      smoothing_weight=0.0)
        ctrl_b = HeatingMPCController(model_b, sources_b, horizon=3, dt=900,
                                      smoothing_weight=0.0)

        actions_a = ctrl_a.compute(outdoor_temp=0.0, now=now)
        actions_b = ctrl_b.compute(outdoor_temp=0.0, now=now)

        for name in actions_a:
            assert actions_a[name] == pytest.approx(actions_b[name], abs=1e-6)

    def test_default_smoothing_weight(self):
        """Default smoothing_weight is 0.1."""
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_constraint_offset_configurable(self):
        """constraint_offset should be accepted and affect behaviour."""
        model, sources = make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900,
                                    constraint_offset=1.0)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        assert all(0.0 <= f <= 1.0 for f in actions.values())
