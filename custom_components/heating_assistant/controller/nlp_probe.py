"""Capability probe for non-linear MPC NLP (SciPy only)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mbc.control import NLPProblem, ScipyNLPBackend

BACKEND_SCIPY = "scipy"


@dataclass(frozen=True)
class NonlinearBackendCapability:
    """Result of the restart-time non-linear NLP capability probe."""

    available: bool
    backend: str | None = None
    reason: str | None = None


def _tiny_problem() -> NLPProblem:
    return NLPProblem(
        objective=lambda x: float((x[0] - 1.0) ** 2),
        objective_jac=lambda x: np.array([2.0 * (x[0] - 1.0)], dtype=float),
        x0=np.array([3.0], dtype=float),
        lb=np.array([-10.0], dtype=float),
        ub=np.array([10.0], dtype=float),
        constraints=(),
    )


def probe_nonlinear_backend() -> NonlinearBackendCapability:
    """Probe SciPy NLP for non-linear MPC readiness."""
    backend = ScipyNLPBackend(
        method="SLSQP",
        options={"maxiter": 50, "ftol": 1e-10, "disp": False},
    )
    problem = _tiny_problem()
    try:
        result = backend.solve(problem)
    except Exception as exc:  # noqa: BLE001 — any probe failure means unavailable
        return NonlinearBackendCapability(
            available=False,
            backend=None,
            reason=str(exc),
        )
    if not result.success:
        return NonlinearBackendCapability(
            available=False,
            backend=None,
            reason=result.message or "SciPy probe did not converge",
        )
    x_opt = float(np.asarray(result.x, dtype=float)[0])
    if not np.isclose(x_opt, 1.0, atol=1e-3):
        return NonlinearBackendCapability(
            available=False,
            backend=None,
            reason=f"SciPy probe returned x={x_opt:.6g}",
        )
    return NonlinearBackendCapability(
        available=True,
        backend=BACKEND_SCIPY,
    )
