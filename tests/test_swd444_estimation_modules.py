"""Lock KalmanMLEstimator NLP helpers after the SWD-444 extract."""

from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator
from heatingassistant.engine.estimation.nlp_eval import (
    RegularizedMseCache,
    WallInitMseCache,
    solve_lbfgs,
)


def test_nlp_eval_helpers_are_importable() -> None:
    assert RegularizedMseCache is not None
    assert WallInitMseCache is not None
    assert callable(solve_lbfgs)
    assert callable(KalmanMLEstimator.estimate)
    assert callable(KalmanMLEstimator.estimate_wall_initial_only)
