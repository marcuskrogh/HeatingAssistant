"""
Unit tests for the implicit-Euler integrator helper.

The helper is used by ``HouseModel.step``, the controller's visualisation
prediction loop, and the open-loop diagnostic simulation, so its correctness
underpins anything that propagates the thermal model locally.  These tests
cover:

- Closed-form correctness on a scalar linear ODE (matches the analytical
  implicit-Euler update ``x_next = x / (1 - h a)`` for ``a < 0``).
- L-stability on a stiff scalar problem where explicit Euler diverges
  but implicit Euler stays bounded.
- Multi-state linear system: matches the analytical
  ``x_next = (I - h F)^{-1} x`` for a non-diagonal ``F``.
- Newton convergence in a single iteration when the right-hand side is
  affine in the state (the operational case for the heating model).
- Newton convergence for a mildly nonlinear right-hand side.
- ``ImplicitEulerConvergenceError`` raised when Newton genuinely fails.
- Symmetry: ``implicit_euler_substeps`` with ``n_steps=1`` is identical to
  ``implicit_euler_step``.
- Equivalence with the legacy explicit-Euler update in the small-step
  limit (the bit-equivalent acceptance criterion for closed-loop
  behaviour on non-stiff systems).
"""

from __future__ import annotations

import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.integrator import (  # noqa: E402
    ImplicitEulerConvergenceError,
    implicit_euler_step,
    implicit_euler_substeps,
)


# ---------------------------------------------------------------------------
# Closed-form correctness
# ---------------------------------------------------------------------------


def test_scalar_linear_matches_closed_form() -> None:
    """``dx/dt = a x``  →  ``x_next = x / (1 - h a)`` for ``a < 0``."""
    a = -0.5
    h = 2.0
    x0 = np.array([3.0])

    def rhs(x: np.ndarray) -> np.ndarray:
        return a * x

    def jac(_x: np.ndarray) -> np.ndarray:
        return np.array([[a]])

    expected = x0 / (1.0 - h * a)
    got = implicit_euler_step(rhs, jac, x0, h)
    assert np.allclose(got, expected, atol=1e-12)


def test_multistate_linear_matches_closed_form() -> None:
    """``dx/dt = F x``  →  ``x_next = (I - h F)^{-1} x``."""
    F = np.array([[-1.0, 0.5], [0.2, -2.0]])
    x0 = np.array([1.0, 4.0])
    h = 0.3

    def rhs(x: np.ndarray) -> np.ndarray:
        return F @ x

    def jac(_x: np.ndarray) -> np.ndarray:
        return F

    expected = np.linalg.solve(np.eye(2) - h * F, x0)
    got = implicit_euler_step(rhs, jac, x0, h)
    assert np.allclose(got, expected, atol=1e-12)


def test_affine_rhs_converges_in_one_newton_correction() -> None:
    """Affine ``rhs``: the residual is linear in ``x_next``, so a single
    Newton correction already lands on the exact solution.  The loop then
    verifies the residual is under tolerance on its next pass — so
    ``newton_max_iter=2`` (one correction + one verification) is sufficient.
    """
    F = np.array([[-0.5, 0.1], [0.1, -0.7]])
    bias = np.array([0.2, -0.1])
    x0 = np.array([1.0, 2.0])
    h = 0.5

    def rhs(x: np.ndarray) -> np.ndarray:
        return F @ x + bias

    def jac(_x: np.ndarray) -> np.ndarray:
        return F

    x_next = implicit_euler_step(rhs, jac, x0, h, newton_max_iter=2)

    residual = x_next - x0 - h * (F @ x_next + bias)
    assert np.linalg.norm(residual) < 1e-12


# ---------------------------------------------------------------------------
# L-stability
# ---------------------------------------------------------------------------


def test_stiff_scalar_stays_bounded_where_explicit_diverges() -> None:
    """``dx/dt = -1000 x`` with ``h = 0.01`` (stiffness ratio 10).

    Explicit Euler iterate is ``x_{k+1} = (1 - 1000 * 0.01) x_k = -9 x_k``,
    so |x| grows unboundedly.  Implicit Euler iterate is
    ``x_{k+1} = x_k / (1 + 1000 * 0.01) = x_k / 11``, so it damps toward zero.
    """
    a = -1000.0
    h = 0.01
    x = np.array([1.0])

    def rhs(state: np.ndarray) -> np.ndarray:
        return a * state

    def jac(_state: np.ndarray) -> np.ndarray:
        return np.array([[a]])

    for _ in range(100):
        x = implicit_euler_step(rhs, jac, x, h)
    assert np.all(np.isfinite(x))
    assert abs(x[0]) < 1.0  # damped
    # The explicit-Euler comparison: ``(1 + h a)^100 = (-9)^100 = 9^100`` —
    # the explicit iterate would overflow.  Spot-check that we are
    # qualitatively different.
    explicit_ratio = abs(1.0 + h * a) ** 100
    assert explicit_ratio > 1e90


def test_stiff_multistate_stays_well_conditioned() -> None:
    """Two-eigenvalue system with eigenvalues -1 and -1000 (ratio 1000).

    Implicit Euler stays L-stable; explicit Euler would need
    ``h < 2/1000 = 0.002`` to be stable for the fast mode.
    """
    F = np.diag([-1.0, -1000.0])
    h = 0.01
    x = np.array([1.0, 1.0])

    def rhs(state: np.ndarray) -> np.ndarray:
        return F @ state

    def jac(_state: np.ndarray) -> np.ndarray:
        return F

    for _ in range(50):
        x = implicit_euler_step(rhs, jac, x, h)
    assert np.all(np.isfinite(x))
    # Fast mode (e₂ = -1000) should be damped to near-zero;
    # slow mode (e₁ = -1) should be only slightly decayed.
    assert abs(x[0]) < 1.0 and abs(x[0]) > 0.5
    assert abs(x[1]) < 1e-10


# ---------------------------------------------------------------------------
# Newton on nonlinear RHS
# ---------------------------------------------------------------------------


def test_nonlinear_rhs_newton_converges() -> None:
    """``dx/dt = -x^3`` is nonlinear in ``x``.  Newton must iterate, but
    should converge well within the iteration cap.
    """
    h = 0.1
    x0 = np.array([2.0])

    def rhs(x: np.ndarray) -> np.ndarray:
        return -(x ** 3)

    def jac(x: np.ndarray) -> np.ndarray:
        return np.array([[-3.0 * x[0] ** 2]])

    x_next = implicit_euler_step(rhs, jac, x0, h)
    # Verify residual
    residual = x_next - x0 - h * (-(x_next ** 3))
    assert np.linalg.norm(residual) < 1e-9


def test_newton_failure_raises() -> None:
    """An ``rhs`` that returns NaN propagates through the residual and the
    Newton correction, so the convergence check is never satisfied.  After
    ``newton_max_iter`` iterations the helper should raise rather than
    return a NaN-poisoned state.
    """
    h = 1.0
    x0 = np.array([1.0])

    def rhs(_x: np.ndarray) -> np.ndarray:
        return np.array([np.nan])

    def jac(_x: np.ndarray) -> np.ndarray:
        return np.array([[0.0]])

    with pytest.raises(ImplicitEulerConvergenceError):
        implicit_euler_step(rhs, jac, x0, h, newton_max_iter=5)


# ---------------------------------------------------------------------------
# Sub-stepping
# ---------------------------------------------------------------------------


def test_substeps_equals_single_step_when_n_steps_is_one() -> None:
    F = np.array([[-0.4, 0.1], [0.05, -0.6]])
    x0 = np.array([1.0, 2.0])
    dt = 0.5

    def rhs(x: np.ndarray) -> np.ndarray:
        return F @ x

    def jac(_x: np.ndarray) -> np.ndarray:
        return F

    one = implicit_euler_substeps(rhs, jac, x0, dt, 1)
    direct = implicit_euler_step(rhs, jac, x0, dt)
    assert np.allclose(one, direct, atol=1e-14)


def test_substeps_more_accurate_than_single_step_on_linear() -> None:
    """Finer sub-stepping converges to the exact ``exp(F dt) x``."""
    F = np.array([[-0.4, 0.1], [0.05, -0.6]])
    x0 = np.array([1.0, 2.0])
    dt = 0.5
    from scipy.linalg import expm

    exact = expm(F * dt) @ x0
    err_1 = np.linalg.norm(implicit_euler_substeps(rhs := lambda x: F @ x,
                                                    jac := lambda _x: F,
                                                    x0, dt, 1) - exact)
    err_100 = np.linalg.norm(implicit_euler_substeps(rhs, jac, x0, dt, 100) - exact)
    assert err_100 < err_1
    assert err_100 < 1e-3


def test_substeps_zero_raises() -> None:
    def rhs(x: np.ndarray) -> np.ndarray:
        return x

    def jac(_x: np.ndarray) -> np.ndarray:
        return np.eye(1)

    with pytest.raises(ValueError):
        implicit_euler_substeps(rhs, jac, np.array([1.0]), 0.5, 0)


# ---------------------------------------------------------------------------
# Equivalence with explicit Euler on benign (non-stiff) systems
# ---------------------------------------------------------------------------


def test_matches_explicit_in_small_step_limit() -> None:
    """On a non-stiff system with very small ``h``, implicit and explicit
    Euler should agree to high precision (both first-order; difference is
    O(h²)).  This protects the bit-equivalent closed-loop acceptance
    criterion for the live controller on non-stiff configurations.
    """
    F = np.array([[-0.05, 0.01], [0.02, -0.08]])
    x0 = np.array([20.0, 19.0])
    h = 0.01  # very small relative to the slowest eigenvalue (1/0.05 = 20)

    def rhs(x: np.ndarray) -> np.ndarray:
        return F @ x

    def jac(_x: np.ndarray) -> np.ndarray:
        return F

    implicit = implicit_euler_step(rhs, jac, x0, h)
    explicit = x0 + h * (F @ x0)
    assert np.allclose(implicit, explicit, atol=1e-3)
