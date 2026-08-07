"""
Implicit-Euler integration helpers for the HeatingAssistant thermal model.

Used by everything that propagates the continuous-time thermal dynamics
locally (visualisation prediction, `HouseModel.step`, the open-loop
diagnostic) so the integration scheme matches what the MPC controller
solves and what the EKF state propagation will use once the upstream
``mbc`` package exposes an integrator option.

For the residential thermal model the drift ``f(x, u, d, p, t)`` is
affine in the state ``x`` (heat-pump COP varies with the *disturbance*
``T_outdoor``, not with the state itself), so the implicit-Euler step

    R(x_next) = x_next - x_curr - h * f(x_next, u, d, p, t) = 0

reduces to a single ``n × n`` linear solve and Newton converges in one
iteration.  The helper is written for the general case (Newton iteration
on the residual) so the same code-path handles any later model
nonlinearity in ``x`` without modification — the affine case just hits
the tolerance on the first pass.

L-stability of implicit Euler is what we are buying: it stays accurate
on the slow modes regardless of step size and damps fast modes
correctly, which matters once Phase 1 introduces 2R2C + slab dynamics
with eigenvalue spreads of ~10³–10⁴.
"""

from __future__ import annotations

from typing import Callable

import numpy as np


_DEFAULT_NEWTON_TOL = 1e-10
_DEFAULT_NEWTON_MAX_ITER = 20


class ImplicitEulerConvergenceError(RuntimeError):
    """Raised when the Newton iteration inside ``implicit_euler_step`` fails."""


def implicit_euler_step(
    rhs: Callable[[np.ndarray], np.ndarray],
    jacobian: Callable[[np.ndarray], np.ndarray],
    x_curr: np.ndarray,
    h: float,
    newton_tol: float = _DEFAULT_NEWTON_TOL,
    newton_max_iter: int = _DEFAULT_NEWTON_MAX_ITER,
) -> np.ndarray:
    """
    Advance ``x`` by one implicit-Euler step of size ``h``.

    Solves the nonlinear residual

        R(x_next) = x_next - x_curr - h * rhs(x_next)

    via Newton iteration with Jacobian ``I - h * jacobian(x_next)``.

    Parameters
    ----------
    rhs : callable
        Right-hand side of the ODE: ``rhs(x)`` returns ``dx/dt`` for state
        ``x``.  All other arguments (inputs, disturbances, parameters,
        time) should be captured by the caller via closure so that this
        helper stays state-only.
    jacobian : callable
        Jacobian of the right-hand side: ``jacobian(x)`` returns
        ``∂rhs/∂x`` as an ``(n, n)`` array.
    x_curr : (n,) ndarray
        State at the start of the step.
    h : float
        Step size.
    newton_tol : float, optional
        Convergence tolerance on ``‖R(x_next)‖₂``.  Default ``1e-10``.
    newton_max_iter : int, optional
        Maximum Newton iterations before raising.  Default ``20``.

    Returns
    -------
    x_next : (n,) ndarray
        State at the end of the step.

    Raises
    ------
    ImplicitEulerConvergenceError
        If Newton fails to converge within ``newton_max_iter`` iterations.
    """
    x_next = np.asarray(x_curr, dtype=float).copy()
    n = x_next.shape[0]
    eye = np.eye(n)
    last_residual_norm = np.inf

    for _ in range(newton_max_iter):
        r = x_next - x_curr - h * rhs(x_next)
        last_residual_norm = float(np.linalg.norm(r))
        if last_residual_norm < newton_tol:
            return x_next
        jac = jacobian(x_next)
        delta = np.linalg.solve(eye - h * jac, -r)
        x_next = x_next + delta

    raise ImplicitEulerConvergenceError(
        f"Implicit-Euler Newton did not converge in {newton_max_iter} "
        f"iterations; final residual norm {last_residual_norm:.3e}"
    )


def implicit_euler_substeps(
    rhs: Callable[[np.ndarray], np.ndarray],
    jacobian: Callable[[np.ndarray], np.ndarray],
    x_curr: np.ndarray,
    dt: float,
    n_steps: int,
    newton_tol: float = _DEFAULT_NEWTON_TOL,
    newton_max_iter: int = _DEFAULT_NEWTON_MAX_ITER,
) -> np.ndarray:
    """
    Apply ``n_steps`` implicit-Euler sub-steps over an interval of length
    ``dt``.  Used by every call site that previously did

        h = dt / n_steps
        for _ in range(n_steps):
            x = x + h * f(x, ...)

    so the integration scheme is uniform across the codebase.

    Parameters
    ----------
    rhs, jacobian
        See :func:`implicit_euler_step`.  In the typical zero-order-hold
        scenario the inputs / disturbances / parameters held constant over
        the sub-stepping should be captured by closure; ``rhs`` and
        ``jacobian`` receive only the state.
    x_curr : (n,) ndarray
        State at the start of the interval.
    dt : float
        Total interval length.
    n_steps : int
        Number of equidistant sub-steps.  Must be positive.
    newton_tol, newton_max_iter
        Forwarded to :func:`implicit_euler_step` for each sub-step.

    Returns
    -------
    x_next : (n,) ndarray
        State at the end of the interval.
    """
    if n_steps <= 0:
        raise ValueError(f"n_steps must be positive, got {n_steps}")
    h = dt / n_steps
    x = np.asarray(x_curr, dtype=float).copy()
    for _ in range(n_steps):
        x = implicit_euler_step(
            rhs, jacobian, x, h,
            newton_tol=newton_tol, newton_max_iter=newton_max_iter,
        )
    return x
