"""Grey-box thermal parameter estimation subpackage."""

from __future__ import annotations

from .constants import MIN_HISTORY_STEPS

__all__ = ["KalmanMLEstimator", "MIN_HISTORY_STEPS"]


def __getattr__(name: str):
    if name == "KalmanMLEstimator":
        from ..parameter_estimator import KalmanMLEstimator

        return KalmanMLEstimator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
