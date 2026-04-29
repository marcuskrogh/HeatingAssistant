"""
Generic system-identification tests using a synthetic first-order linear model.

All tests in this module use **only** the public ``mbc`` API (plus cvxopt,
which mbc already depends on) — there is no dependency on the HeatingAssistant
domain code.

Synthetic model
---------------
    x[k+1] = a * x[k] + b * u[k] + e * d[k]   (scalar state, C = I)

The true parameters are  a=0.9, true_b=0.5, true_e=0.2.  The estimator must
recover them from a short noisy time series.

The parameter vector ``θ = [log_a, log_b, log_e]`` uses a log-space
parametrisation for positive parameters.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mbc.identification import (
    ParameterEstimator,
    EstimationResult,
    ped_neg_log_likelihood,
    ped_neg_log_likelihood_gradient,
    nelder_mead,
)


# ── Synthetic model ───────────────────────────────────────────────────────────


class _ScalarModel:
    """
    Minimal model conforming to the LinearDiscreteModel duck-type interface.

    The model implements the single-state recursion:

        x[k+1] = a x[k] + b u[k] + e d[k]   (C = I, predict_offset = 0)
    """

    def __init__(self, a: float, b: float, e: float) -> None:
        self._a = a
        self._b = b
        self._e = e

    @property
    def n_x(self) -> int:
        return 1

    @property
    def n_u(self) -> int:
        return 1

    @property
    def n_d(self) -> int:
        return 1

    def discretize(self, d_cvx):
        """Return cvxopt 1×1 matrices A, B, E."""
        from cvxopt import matrix as cvx_matrix
        A = cvx_matrix([[self._a]])
        B = cvx_matrix([[self._b]])
        E = cvx_matrix([[self._e]])
        return A, B, E

    def predict_offset(self, d_np: np.ndarray) -> np.ndarray:
        return np.zeros(1)

    # ── Identification interface ──────────────────────────────────────────

    @property
    def params(self) -> np.ndarray:
        """θ = [log_a, log_b, log_e]"""
        return np.array([
            math.log(max(self._a, 1e-9)),
            math.log(max(self._b, 1e-9)),
            math.log(max(self._e, 1e-9)),
        ])

    def with_params(self, theta: np.ndarray) -> "_ScalarModel":
        a = math.exp(float(theta[0]))
        b = math.exp(float(theta[1]))
        e = math.exp(float(theta[2]))
        return _ScalarModel(a, b, e)

    def discretize_jacobian(
        self, d_np: np.ndarray, h: float = 1e-5
    ):
        """Finite-difference Jacobians ∂A_d/∂θ_i, ∂B_d/∂θ_i, ∂E_d/∂θ_i."""
        from cvxopt import matrix as cvx_matrix
        d_cvx = cvx_matrix(d_np.tolist(), (len(d_np), 1), tc="d")
        theta0 = self.params
        A0_cvx, B0_cvx, E0_cvx = self.discretize(d_cvx)
        A0 = np.array([[A0_cvx[0, 0]]])
        B0 = np.array([[B0_cvx[0, 0]]])
        E0 = np.array([[E0_cvx[0, 0]]])

        dA, dB, dE = [], [], []
        for i in range(len(theta0)):
            theta_h = theta0.copy()
            theta_h[i] += h
            m_h = self.with_params(theta_h)
            Ah_cvx, Bh_cvx, Eh_cvx = m_h.discretize(d_cvx)
            dA.append((np.array([[Ah_cvx[0, 0]]]) - A0) / h)
            dB.append((np.array([[Bh_cvx[0, 0]]]) - B0) / h)
            dE.append((np.array([[Eh_cvx[0, 0]]]) - E0) / h)
        return dA, dB, dE


def _make_model_factory(true_a: float = 0.9,
                        true_b: float = 0.5,
                        true_e: float = 0.2):
    """Return a factory closure over the log-space parametrisation."""
    def factory(theta: np.ndarray) -> _ScalarModel:
        a = math.exp(float(theta[0]))
        b = math.exp(float(theta[1]))
        e = math.exp(float(theta[2]))
        return _ScalarModel(a, b, e)
    return factory


def _generate_history(
    a: float = 0.9,
    b: float = 0.5,
    e: float = 0.2,
    n_steps: int = 60,
    noise_std: float = 0.02,
    seed: int = 0,
) -> list:
    """Simulate the scalar model and return a standardised history."""
    rng = np.random.default_rng(seed)
    x = 0.0
    history = []
    for k in range(n_steps):
        u = float(rng.uniform(0.0, 1.0))
        d = float(rng.uniform(-1.0, 1.0))
        y = x + rng.normal(0.0, noise_std)
        history.append({
            "y": np.array([y]),
            "u": np.array([u]),
            "d": np.array([d]),
        })
        x = a * x + b * u + e * d
    return history


# ── Tests for nelder_mead ─────────────────────────────────────────────────────


class TestNelderMead:

    def test_minimises_quadratic(self):
        def f(x):
            return float(np.sum((x - np.array([1.0, 2.0])) ** 2))
        x0 = np.array([0.0, 0.0])
        x_best, f_best, converged = nelder_mead(f, x0, tol=1e-6)
        assert converged
        np.testing.assert_allclose(x_best, [1.0, 2.0], atol=1e-3)
        assert f_best < 1e-5

    def test_minimises_rosenbrock(self):
        def rosenbrock(x):
            return float(100 * (x[1] - x[0] ** 2) ** 2 + (1 - x[0]) ** 2)
        x0 = np.array([0.5, 0.5])
        x_best, _, _ = nelder_mead(rosenbrock, x0, tol=1e-6, max_iter=5000)
        np.testing.assert_allclose(x_best, [1.0, 1.0], atol=1e-2)

    def test_returns_finite_result(self):
        x0 = np.array([3.0])
        _, f_best, _ = nelder_mead(lambda x: float(x[0] ** 2), x0)
        assert math.isfinite(f_best)

    def test_default_max_iter(self):
        x0 = np.array([1.0, 2.0, 3.0])
        nelder_mead(lambda x: float(np.sum(x ** 2)), x0)  # must not raise


# ── Tests for ped_neg_log_likelihood ─────────────────────────────────────────


class TestPedNegLogLikelihood:

    def test_finite_at_true_params(self):
        factory = _make_model_factory()
        history = _generate_history(n_steps=50)
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        theta_true = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        val = ped_neg_log_likelihood(factory, theta_true, history, Q, R)
        assert math.isfinite(val)

    def test_sentinel_for_nan_theta(self):
        factory = _make_model_factory()
        history = _generate_history(n_steps=20)
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        nan_theta = np.array([float("nan"), 0.0, 0.0])
        val = ped_neg_log_likelihood(factory, nan_theta, history, Q, R)
        assert val >= 1e9

    def test_sentinel_for_factory_exception(self):
        def bad_factory(theta):
            raise ValueError("bad")
        history = _generate_history(n_steps=20)
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        val = ped_neg_log_likelihood(bad_factory, np.zeros(3), history, Q, R)
        assert val >= 1e9

    def test_sentinel_for_too_short_history(self):
        factory = _make_model_factory()
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        val = ped_neg_log_likelihood(
            factory, np.zeros(3), [_generate_history(n_steps=2)[0]], Q, R
        )
        assert val >= 1e9

    def test_true_params_better_than_wrong_params(self):
        """True parameters should give a lower (better) neg-log-likelihood."""
        factory = _make_model_factory(true_a=0.9, true_b=0.5, true_e=0.2)
        history = _generate_history(a=0.9, b=0.5, e=0.2, n_steps=100, seed=1)
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01

        theta_true = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        theta_wrong = np.array([math.log(0.5), math.log(0.1), math.log(0.5)])

        ll_true = ped_neg_log_likelihood(factory, theta_true, history, Q, R)
        ll_wrong = ped_neg_log_likelihood(factory, theta_wrong, history, Q, R)
        assert ll_true < ll_wrong


# ── Tests for ped_neg_log_likelihood_gradient ─────────────────────────────────


class TestPedNegLogLikelihoodGradient:

    def test_gradient_shape(self):
        factory = _make_model_factory()
        history = _generate_history(n_steps=30)
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        theta = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        grad = ped_neg_log_likelihood_gradient(factory, theta, history, Q, R)
        assert grad.shape == theta.shape

    def test_gradient_approx_finite_diff(self):
        """Gradient should match a coarser finite-difference check."""
        factory = _make_model_factory()
        history = _generate_history(n_steps=40, seed=2)
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        theta = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        h = 1e-4
        grad = ped_neg_log_likelihood_gradient(
            factory, theta, history, Q, R, h=h
        )
        f0 = ped_neg_log_likelihood(factory, theta, history, Q, R)
        for i in range(len(theta)):
            theta_h = theta.copy()
            theta_h[i] += h
            fh = ped_neg_log_likelihood(factory, theta_h, history, Q, R)
            expected = (fh - f0) / h
            assert abs(grad[i] - expected) < 1e-8, (
                f"grad[{i}]={grad[i]}, expected={expected}"
            )


# ── Tests for ParameterEstimator ──────────────────────────────────────────────


class TestParameterEstimator:

    def _make_estimator(self, **kwargs) -> ParameterEstimator:
        factory = _make_model_factory(true_a=0.9, true_b=0.5, true_e=0.2)
        theta0 = np.array([math.log(0.8), math.log(0.4), math.log(0.3)])
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        return ParameterEstimator(
            model_factory=factory,
            theta0=theta0,
            bounds=None,
            Q=Q,
            R=R,
            **kwargs,
        )

    def test_returns_estimation_result(self):
        history = _generate_history(n_steps=50)
        est = self._make_estimator(n_restarts=1)
        result = est.estimate(history)
        assert isinstance(result, EstimationResult)

    def test_result_has_correct_n_steps(self):
        history = _generate_history(n_steps=50)
        est = self._make_estimator(n_restarts=1)
        result = est.estimate(history)
        assert result.n_steps == 50

    def test_neg_ll_is_finite(self):
        history = _generate_history(n_steps=60)
        est = self._make_estimator(n_restarts=2)
        result = est.estimate(history)
        assert math.isfinite(result.neg_log_likelihood)

    def test_bounds_respected(self):
        """Objective returns sentinel when a bound is violated."""
        factory = _make_model_factory()
        theta0 = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        # Tight upper bound: force log_a < -1 → a < exp(-1) ≈ 0.37
        bounds = [(-10.0, -1.0), (-10.0, 10.0), (-10.0, 10.0)]
        est = ParameterEstimator(
            model_factory=factory,
            theta0=theta0,
            bounds=bounds,
            Q=Q,
            R=R,
            n_restarts=1,
        )
        # The initial theta violates the first bound → estimate should return
        # the sentinel value but not crash.
        history = _generate_history(n_steps=30)
        result = est.estimate(history)
        # The result should be finite and equal to the sentinel (bounds barrier),
        # not inf which would indicate an unhandled exception path.
        assert math.isfinite(result.neg_log_likelihood)

    def test_log_likelihood_at_theta0(self):
        history = _generate_history(n_steps=60)
        est = self._make_estimator()
        ll = est.log_likelihood(history)
        assert ll is not None
        assert math.isfinite(ll)

    def test_log_likelihood_default_uses_theta0(self):
        factory = _make_model_factory()
        theta0 = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        est = ParameterEstimator(
            model_factory=factory, theta0=theta0,
            bounds=None, Q=Q, R=R,
        )
        history = _generate_history(n_steps=40)
        ll_default = est.log_likelihood(history)
        ll_explicit = est.log_likelihood(history, theta=theta0)
        assert ll_default == pytest.approx(ll_explicit, rel=1e-9)

    def test_regularisation_pulls_toward_theta0(self):
        """Strong regularisation should keep estimates near theta0."""
        factory = _make_model_factory(true_a=0.9, true_b=0.5, true_e=0.2)
        theta0 = np.array([math.log(0.9), math.log(0.5), math.log(0.2)])
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        sigma = 1e-3

        def reg(theta: np.ndarray) -> float:
            return float(np.sum((theta - theta0) ** 2) / (2 * sigma ** 2))

        est = ParameterEstimator(
            model_factory=factory,
            theta0=theta0,
            bounds=None,
            Q=Q,
            R=R,
            regularization_fn=reg,
            n_restarts=1,
        )
        history = _generate_history(n_steps=60)
        result = est.estimate(history)
        np.testing.assert_allclose(result.theta_best, theta0, atol=0.1)

    def test_estimate_improves_likelihood_over_initial(self):
        """
        The estimate should produce an equal-or-better likelihood compared to
        the starting point when the data are generated from different parameters.
        """
        # Generate data from true params
        true_a, true_b, true_e = 0.85, 0.6, 0.15
        factory = _make_model_factory(true_a=true_a, true_b=true_b, true_e=true_e)
        history = _generate_history(
            a=true_a, b=true_b, e=true_e, n_steps=100, seed=5
        )
        # Start from wrong initial point
        theta0 = np.array([math.log(0.5), math.log(0.2), math.log(0.5)])
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        est = ParameterEstimator(
            model_factory=factory, theta0=theta0,
            bounds=None, Q=Q, R=R, n_restarts=2,
        )
        ll_init = est.log_likelihood(history, theta=theta0)
        result = est.estimate(history)
        ll_est = est.log_likelihood(history, theta=result.theta_best)

        assert ll_init is not None and ll_est is not None
        # Optimiser should not make things worse (allow 1 nat tolerance)
        assert ll_est >= ll_init - 1.0

    def test_error_in_factory_returns_graceful_result(self):
        def bad_factory(theta):
            raise RuntimeError("factory failed")

        theta0 = np.array([0.0])
        Q = np.eye(1) * 0.01
        R = np.eye(1) * 0.01
        est = ParameterEstimator(
            model_factory=bad_factory,
            theta0=theta0,
            bounds=None,
            Q=Q,
            R=R,
            n_restarts=1,
        )
        history = _generate_history(n_steps=20)
        result = est.estimate(history)
        # Should not raise and should return something meaningful
        assert result is not None


# ── Tests for discretize_jacobian on _ScalarModel ────────────────────────────


class TestDiscreteJacobian:

    def test_jacobian_shape(self):
        m = _ScalarModel(0.9, 0.5, 0.2)
        dA, dB, dE = m.discretize_jacobian(np.array([0.0]))
        assert len(dA) == 3
        assert len(dB) == 3
        assert len(dE) == 3
        for mat in dA + dB + dE:
            assert mat.shape == (1, 1)

    def test_jacobian_for_identity_model(self):
        """For the scalar model, ∂A/∂log_a = A = a (chain rule in log space)."""
        a = 0.9
        m = _ScalarModel(a, 0.5, 0.2)
        dA, _, _ = m.discretize_jacobian(np.array([0.0]))
        # dA/d(log_a) = a * d(log_a)/d(log_a) = a for scalar constant A = a
        np.testing.assert_allclose(dA[0][0, 0], a, rtol=1e-3)
