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

from custom_components.heating_assistant.controller import ipopt_probe


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


def test_ipopt_capability_probe_reports_success(monkeypatch) -> None:
    class FakeBackend:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            return type(
                "Result",
                (),
                {"success": True, "x": np.array([1.0]), "message": "", "fun": 0.0},
            )()

    monkeypatch.setattr(ipopt_probe, "IpoptNLPBackend", FakeBackend)

    result = ipopt_probe.probe_ipopt_capability()

    assert result.available is True
    assert result.backend == ipopt_probe.BACKEND_IPOPT
    assert result.ipopt_available is True
    assert result.reason is None


def test_ipopt_capability_probe_falls_back_to_scipy(monkeypatch) -> None:
    class FakeIpopt:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            raise RuntimeError("cyipopt missing")

    class FakeScipy:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            return type(
                "Result",
                (),
                {"success": True, "x": np.array([1.0]), "message": "", "fun": 0.0},
            )()

    monkeypatch.setattr(ipopt_probe, "IpoptNLPBackend", FakeIpopt)
    monkeypatch.setattr(ipopt_probe, "ScipyNLPBackend", FakeScipy)

    result = ipopt_probe.probe_nonlinear_backend()

    assert result.available is True
    assert result.backend == ipopt_probe.BACKEND_SCIPY
    assert result.ipopt_available is False
    assert "cyipopt missing" in (result.reason or "")


def test_ipopt_capability_probe_reports_failure(monkeypatch) -> None:
    class FakeBackend:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            raise RuntimeError("cyipopt missing")

    monkeypatch.setattr(ipopt_probe, "IpoptNLPBackend", FakeBackend)
    monkeypatch.setattr(ipopt_probe, "ScipyNLPBackend", FakeBackend)

    result = ipopt_probe.probe_ipopt_capability()

    assert result.available is False
    assert result.backend is None
    assert "cyipopt missing" in result.reason
