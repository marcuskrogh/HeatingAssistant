"""Capability probe for non-linear MPC NLP backends (Ipopt preferred, SciPy fallback)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mbc.control import IpoptNLPBackend, NLPProblem, ScipyNLPBackend

BACKEND_IPOPT = "ipopt"
BACKEND_SCIPY = "scipy"


@dataclass(frozen=True)
class NonlinearBackendCapability:
    """Result of the restart-time non-linear NLP capability probe."""

    available: bool
    backend: str | None = None
    ipopt_available: bool = False
    reason: str | None = None


# Backward-compatible alias used by older call sites / tests.
IpoptCapability = NonlinearBackendCapability


def _tiny_problem() -> NLPProblem:
    return NLPProblem(
        objective=lambda x: float((x[0] - 1.0) ** 2),
        objective_jac=lambda x: np.array([2.0 * (x[0] - 1.0)], dtype=float),
        x0=np.array([3.0], dtype=float),
        lb=np.array([-10.0], dtype=float),
        ub=np.array([10.0], dtype=float),
        constraints=(),
    )


def _probe_backend(backend, *, name: str) -> tuple[bool, str | None]:
    problem = _tiny_problem()
    try:
        result = backend.solve(problem)
    except Exception as exc:  # noqa: BLE001 — any probe failure means unavailable
        return False, str(exc)
    if not result.success:
        return False, result.message or f"{name} probe did not converge"
    x_opt = float(np.asarray(result.x, dtype=float)[0])
    if not np.isclose(x_opt, 1.0, atol=1e-3):
        return False, f"{name} probe returned x={x_opt:.6g}"
    return True, None


def probe_nonlinear_backend() -> NonlinearBackendCapability:
    """Probe Ipopt first, then SciPy, for non-linear MPC readiness."""
    ipopt_ok, ipopt_reason = _probe_backend(
        IpoptNLPBackend(options={"print_level": 0, "max_iter": 50, "tol": 1e-8}),
        name="IPOPT",
    )
    if ipopt_ok:
        return NonlinearBackendCapability(
            available=True,
            backend=BACKEND_IPOPT,
            ipopt_available=True,
        )

    scipy_ok, scipy_reason = _probe_backend(
        ScipyNLPBackend(
            method="SLSQP",
            options={"maxiter": 50, "ftol": 1e-10, "disp": False},
        ),
        name="SciPy",
    )
    if scipy_ok:
        return NonlinearBackendCapability(
            available=True,
            backend=BACKEND_SCIPY,
            ipopt_available=False,
            reason=ipopt_reason,
        )

    reason_parts = [p for p in (ipopt_reason, scipy_reason) if p]
    return NonlinearBackendCapability(
        available=False,
        backend=None,
        ipopt_available=False,
        reason="; ".join(reason_parts) if reason_parts else "no NLP backend available",
    )


def probe_ipopt_capability() -> NonlinearBackendCapability:
    """Backward-compatible alias for :func:`probe_nonlinear_backend`."""
    return probe_nonlinear_backend()
