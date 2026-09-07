"""Single-start N-step MLE: one L-BFGS, N-step phase only."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from heatingassistant.engine.estimation import nlp_eval
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)


pytestmark = pytest.mark.unit


def test_nstep_estimate_runs_one_lbfgs_and_nstep_phase_only() -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)
    snaps: list[dict] = []
    real_solve = nlp_eval.solve_lbfgs
    calls: list[int] = []

    def _count(*args, **kwargs):
        calls.append(1)
        return real_solve(*args, **kwargs)

    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=60.0,
        n_horizon_steps=8,
        origin_stride=4,
        max_compute_s=30.0,
        use_nstep_pem=True,
        on_progress=snaps.append,
    )
    with patch(
        "heatingassistant.engine.estimation.kalman_ml.solve_lbfgs",
        side_effect=_count,
    ):
        result = est.estimate(history)
    assert result["success"] is True
    assert len(calls) == 1
    assert snaps
    assert all(item.get("phase") == "nstep_pem" for item in snaps)


def test_tiled_oe_estimate_still_runs_one_lbfgs() -> None:
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)
    real_solve = nlp_eval.solve_lbfgs
    calls: list[int] = []

    def _count(*args, **kwargs):
        calls.append(1)
        return real_solve(*args, **kwargs)

    est = make_kalman_ml_estimator(
        [room],
        sources,
        dt=60.0,
        max_compute_s=30.0,
        use_nstep_pem=False,
        on_progress=lambda _snap: None,
    )
    with patch(
        "heatingassistant.engine.estimation.kalman_ml.solve_lbfgs",
        side_effect=_count,
    ):
        result = est.estimate(history)
    assert result["success"] is True
    assert len(calls) == 1
