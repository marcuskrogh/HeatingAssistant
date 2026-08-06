"""Solver-backend visibility — SciPy NLP only."""

from __future__ import annotations

import numpy as np
from mbc.control import NLPProblem, ScipyNLPBackend

from custom_components.heating_assistant.controller import nlp_probe


def test_scipy_backend_engages() -> None:
    problem = NLPProblem(
        objective=lambda x: float((x[0] - 1.0) ** 2),
        objective_jac=lambda x: np.array([2.0 * (x[0] - 1.0)]),
        x0=np.array([3.0]),
        lb=np.array([-10.0]),
        ub=np.array([10.0]),
        constraints=(),
    )
    backend = ScipyNLPBackend(
        method="SLSQP",
        options={"maxiter": 50, "ftol": 1e-10, "disp": False},
    )
    res = backend.solve(problem)
    assert res.success
    assert np.isclose(float(np.asarray(res.x)[0]), 1.0, atol=1e-4)


def test_nlp_probe_uses_scipy_only(monkeypatch) -> None:
    class FakeScipy:
        def __init__(self, **_kwargs):
            pass

        def solve(self, _problem):
            return type(
                "Result",
                (),
                {"success": True, "x": np.array([1.0]), "message": "", "fun": 0.0},
            )()

    monkeypatch.setattr(nlp_probe, "ScipyNLPBackend", FakeScipy)

    result = nlp_probe.probe_nonlinear_backend()

    assert result.available is True
    assert result.backend == nlp_probe.BACKEND_SCIPY
