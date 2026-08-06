"""Solver-backend visibility.

The estimator (estimation/kalman_ml.py) requests IPOPT and silently falls back
to scipy when cyipopt is missing.  Without this test the slow-tier "IPOPT"
estimation tests can run on the fallback forever without anyone noticing —
this test turns that state into an explicit skip in the pytest summary.
"""

from __future__ import annotations

import numpy as np
import pytest
from mbc.control import IpoptNLPBackend, NLPProblem


def test_ipopt_backend_actually_engages() -> None:
    problem = NLPProblem(
        objective=lambda x: float((x[0] - 1.0) ** 2),
        objective_jac=lambda x: np.array([2.0 * (x[0] - 1.0)]),
        x0=np.array([3.0]),
        lb=np.array([-10.0]),
        ub=np.array([10.0]),
        constraints=(),
    )
    backend = IpoptNLPBackend(options={"print_level": 0, "max_iter": 50, "tol": 1e-8})
    # Same exception set kalman_ml._solve_from treats as "IPOPT unavailable".
    try:
        res = backend.solve(problem)
    except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
        pytest.skip(
            "cyipopt not installed — IPOPT paths are NOT exercised; every "
            f"estimation test runs on the scipy fallback ({exc})"
        )
    assert res.success
    assert np.isclose(float(np.asarray(res.x)[0]), 1.0, atol=1e-4)
