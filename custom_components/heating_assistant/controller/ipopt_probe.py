"""IPOPT capability probe for the non-linear MPC mode."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mbc.control import IpoptNLPBackend, NLPProblem


@dataclass(frozen=True)
class IpoptCapability:
    """Result of the restart-time IPOPT capability probe."""

    available: bool
    reason: str | None = None


def probe_ipopt_capability() -> IpoptCapability:
    """Solve a tiny NLP with IPOPT and report whether the backend is usable."""
    problem = NLPProblem(
        objective=lambda x: float((x[0] - 1.0) ** 2),
        objective_jac=lambda x: np.array([2.0 * (x[0] - 1.0)], dtype=float),
        x0=np.array([3.0], dtype=float),
        lb=np.array([-10.0], dtype=float),
        ub=np.array([10.0], dtype=float),
        constraints=(),
    )
    backend = IpoptNLPBackend(
        options={"print_level": 0, "max_iter": 50, "tol": 1e-8}
    )
    try:
        result = backend.solve(problem)
    except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
        return IpoptCapability(False, str(exc))
    if not result.success:
        return IpoptCapability(False, result.message or "IPOPT probe did not converge")
    x_opt = float(np.asarray(result.x, dtype=float)[0])
    if not np.isclose(x_opt, 1.0, atol=1e-4):
        return IpoptCapability(False, f"IPOPT probe returned x={x_opt:.6g}")
    return IpoptCapability(True)
