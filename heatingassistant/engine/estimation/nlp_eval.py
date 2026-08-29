"""Cached simulation-MSE objective/Jacobian for KalmanMLEstimator NLP solves."""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

import numpy as np
from mbc.control import NLPProblem, ScipyNLPBackend

from .constants import _T_WALL_MIN_LAM, _T_WALL_PRIOR_STD

_LOGGER = logging.getLogger("heatingassistant.engine.estimation.kalman_ml")


class RegularizedMseCache:
    """Cache (theta, value, grad) for the joint PE L-BFGS-B objective."""

    def __init__(
        self,
        estimator: Any,
        layout: Any,
        std_history: list,
        identifiable_pairs: list,
        dataset_start_timestamps: Optional[List[float]],
    ) -> None:
        self._est = estimator
        self._layout = layout
        self._std_history = std_history
        self._identifiable_pairs = identifiable_pairs
        self._dataset_start_timestamps = dataset_start_timestamps
        self._cache: List[Optional[object]] = [None, None, None]

    def invalidate(self) -> None:
        self._cache[0] = None

    def eval(self, theta: np.ndarray) -> None:
        if self._cache[0] is not None and np.array_equal(theta, self._cache[0]):
            return
        mse, g_mse = self._est._simulation_mse_and_grad(
            theta,
            self._layout,
            self._std_history,
            nominal_dt=self._est._dt,
            max_window_steps=self._est._max_window_steps,
            min_segment_steps=self._est._min_segment_steps,
            dataset_start_ts=self._dataset_start_timestamps,
        )
        reg = self._est._compute_regularization_theta(theta, self._layout)
        reg_grad = self._est._compute_regularization_gradient(
            theta, self._layout, self._identifiable_pairs
        )
        self._cache[0] = theta.copy()
        self._cache[1] = mse + reg
        self._cache[2] = g_mse + reg_grad

    def fun(self, theta: np.ndarray) -> float:
        self.eval(theta)
        return float(self._cache[1])  # type: ignore[arg-type]

    def jac(self, theta: np.ndarray) -> np.ndarray:
        self.eval(theta)
        return np.asarray(self._cache[2], dtype=float)  # type: ignore[arg-type]


class WallInitMseCache:
    """Cache (theta, value, grad) for the t_wall_init-only L-BFGS-B objective."""

    def __init__(
        self,
        estimator: Any,
        layout: Any,
        std_history: list,
        min_lam: Optional[float],
    ) -> None:
        self._est = estimator
        self._layout = layout
        self._std_history = std_history
        self._min_lam = min_lam
        self._cache: List[Optional[object]] = [None, None, None]

    def eval(self, theta: np.ndarray) -> None:
        if self._cache[0] is not None and np.array_equal(theta, self._cache[0]):
            return
        mse, g_mse = self._est._simulation_mse_and_grad(
            theta,
            self._layout,
            self._std_history,
            nominal_dt=self._est._dt,
            max_window_steps=self._est._max_window_steps,
            min_segment_steps=self._est._min_segment_steps,
        )
        a_tw, b_tw = self._layout.idx_t_wall_init
        t_wall = theta[a_tw:b_tw]
        floor = _T_WALL_MIN_LAM if self._min_lam is None else float(self._min_lam)
        lam_tw = max(self._est._regularization, floor)
        reg = lam_tw * float(
            np.sum((t_wall - self._est._t_wall_init_prior) ** 2)
        ) / (_T_WALL_PRIOR_STD ** 2)
        reg_grad = np.zeros(len(theta))
        reg_grad[a_tw:b_tw] = (
            2.0 * lam_tw * (t_wall - self._est._t_wall_init_prior)
            / (_T_WALL_PRIOR_STD ** 2)
        )
        self._cache[0] = theta.copy()
        self._cache[1] = mse + reg
        self._cache[2] = g_mse + reg_grad

    def fun(self, theta: np.ndarray) -> float:
        self.eval(theta)
        return float(self._cache[1])  # type: ignore[arg-type]

    def jac(self, theta: np.ndarray) -> np.ndarray:
        self.eval(theta)
        return np.asarray(self._cache[2], dtype=float)  # type: ignore[arg-type]


def solve_lbfgs(
    fun: Callable[[np.ndarray], float],
    jac: Callable[[np.ndarray], np.ndarray],
    x0: np.ndarray,
    lb: np.ndarray,
    ub: np.ndarray,
    *,
    invalidate: Callable[[], None] | None = None,
    backend: ScipyNLPBackend | None = None,
) -> Optional[Tuple[float, np.ndarray, bool]]:
    """Run SciPy L-BFGS-B from one start; return (f, theta, ok)."""

    if invalidate is not None:
        invalidate()
    if backend is None:
        backend = ScipyNLPBackend(
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-6},
        )
    problem = NLPProblem(
        objective=fun,
        objective_jac=jac,
        x0=x0,
        lb=lb,
        ub=ub,
        constraints=(),
    )
    try:
        res = backend.solve(problem)
    except Exception as exc:
        _LOGGER.debug("Optimiser failed: %s", exc)
        return None
    if not np.isfinite(res.fun):
        return None
    return float(res.fun), np.asarray(res.x, dtype=float), bool(res.success)
