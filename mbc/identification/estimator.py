"""
Generic parameter estimator for linear discrete-time models.

``ParameterEstimator`` maximises the Kalman-filter prediction-error
decomposition (PED) log-likelihood over a model-parameter vector *θ*,
optionally regularised by a caller-supplied penalty term.

The estimator is intentionally model-agnostic: all domain knowledge is
encapsulated in the *model_factory* callable and the optional
*regularization_fn*.  The caller is responsible for:

  * Choosing the parametrisation of *θ* (e.g. log-space for positive params).
  * Providing an appropriate *model_factory(θ) → model* that returns any
    object compatible with the ``LinearDiscreteModel`` interface.
  * Supplying *bounds* to keep the search within a physically meaningful
    region.
  * Converting the raw measurement / input / disturbance history into the
    standardised format expected by :func:`ped_neg_log_likelihood`.

Usage example::

    from mbc.identification import ParameterEstimator

    estimator = ParameterEstimator(
        model_factory=my_factory,
        theta0=initial_guess,
        bounds=[(-5, 5)] * len(initial_guess),
        Q=np.eye(n) * 0.01,
        R=np.eye(n) * 0.25,
        regularization_fn=my_prior,
        n_restarts=3,
    )
    result = estimator.estimate(std_history)
    print(result.theta_best, result.neg_log_likelihood)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import numpy as np

from .likelihood import (
    ped_neg_log_likelihood,
    _INVALID_LIKELIHOOD,
)
from ._nelder_mead import nelder_mead

_LOGGER = logging.getLogger(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class EstimationResult:
    """
    Outcome of a :class:`ParameterEstimator` run.

    Attributes
    ----------
    theta_best          : (p,) ndarray — best parameter vector found.
    neg_log_likelihood  : float — value of the (regularised) objective at
                          *theta_best*.
    converged           : bool — True if the optimizer reported convergence.
    n_steps             : int — number of history steps used.
    message             : str — human-readable summary.
    """

    theta_best: np.ndarray
    neg_log_likelihood: float
    converged: bool
    n_steps: int
    message: str = ""


# ── Estimator ─────────────────────────────────────────────────────────────────


class ParameterEstimator:
    """
    Multi-start optimizer for the Kalman PED log-likelihood.

    Parameters
    ----------
    model_factory : callable  θ → model
        Returns a model implementing ``discretize``, ``n_x``, ``n_u``,
        ``n_d``, and optionally ``predict_offset``.
        May raise any exception for invalid *θ*; the objective returns the
        sentinel ``1e10`` in that case.
    theta0 : (p,) ndarray
        Initial (prior) parameter vector.  The first optimisation restart
        uses this as the starting point.
    bounds : list of (lo, hi) pairs, optional
        Per-parameter box constraints in parameter space.  Violated
        parameters cause the objective to return the sentinel ``1e10``.
        ``None`` means unbounded.
    Q : (n, n) ndarray
        Process-noise covariance for the Kalman filter.
    R : (n, n) ndarray
        Measurement-noise covariance.
    regularization_fn : callable  θ → float, optional
        Regularisation penalty added to the negative log-likelihood.
        Set to ``None`` for unregularised maximum-likelihood estimation.
    n_restarts : int
        Number of random restarts (including the initial point).
        Default: 3.
    restart_perturbation : float
        Standard deviation of the Gaussian noise added to *theta0* for
        additional restarts (in parameter space).  Default: 0.5.
    use_gradient : bool
        If ``True``, attempt a gradient-based optimiser (requires scipy).
        Falls back to Nelder–Mead if scipy is not available.
        Default: ``False``.
    perturbation_fn : callable, optional
        If provided, called as ``perturbation_fn(theta0, rng, restart_idx)``
        for restarts 1, 2, … to produce the starting point for that restart.
        If ``None``, Gaussian noise of std ``restart_perturbation`` is added
        to *theta0*.
    """

    def __init__(
        self,
        model_factory: Callable[[np.ndarray], object],
        theta0: np.ndarray,
        bounds: Optional[List[Tuple[float, float]]],
        Q: np.ndarray,
        R: np.ndarray,
        regularization_fn: Optional[Callable[[np.ndarray], float]] = None,
        n_restarts: int = 3,
        restart_perturbation: float = 0.5,
        use_gradient: bool = False,
        perturbation_fn: Optional[
            Callable[[np.ndarray, "np.random.Generator", int], np.ndarray]
        ] = None,
    ) -> None:
        self._model_factory = model_factory
        self._theta0 = np.asarray(theta0, dtype=float)
        if bounds is not None and len(bounds) != len(self._theta0):
            raise ValueError(
                f"bounds length ({len(bounds)}) must equal theta0 length "
                f"({len(self._theta0)})"
            )
        self._bounds = bounds
        self._Q = Q
        self._R = R
        self._reg_fn = regularization_fn
        self._n_restarts = n_restarts
        self._restart_pert = restart_perturbation
        self._use_gradient = use_gradient
        self._perturbation_fn = perturbation_fn

    # ── Public API ─────────────────────────────────────────────────────────

    def log_likelihood(
        self,
        history: List[dict],
        theta: Optional[np.ndarray] = None,
    ) -> Optional[float]:
        """
        Evaluate the *unregularised* log-likelihood at *theta*.

        Parameters
        ----------
        history : standardised history records.
        theta   : parameter vector (defaults to ``theta0``).

        Returns
        -------
        float or None
            Log-likelihood (positive = better), or ``None`` on failure.
        """
        if theta is None:
            theta = self._theta0
        neg_ll = ped_neg_log_likelihood(
            self._model_factory, theta, history, self._Q, self._R
        )
        if not np.isfinite(neg_ll) or neg_ll >= _INVALID_LIKELIHOOD:
            return None
        return float(-neg_ll)

    def estimate(self, history: List[dict]) -> EstimationResult:
        """
        Run the multi-start optimisation over *history*.

        Parameters
        ----------
        history : list of dicts
            Standardised records ``{"y": ndarray, "u": ndarray, "d": ndarray}``.

        Returns
        -------
        EstimationResult
        """
        n_steps = len(history)

        def objective(theta: np.ndarray) -> float:
            # Box-constraint guard
            if self._bounds is not None:
                for i, (lo, hi) in enumerate(self._bounds):
                    if lo is not None and theta[i] < lo:
                        return _INVALID_LIKELIHOOD
                    if hi is not None and theta[i] > hi:
                        return _INVALID_LIKELIHOOD

            neg_ll = ped_neg_log_likelihood(
                self._model_factory, theta, history, self._Q, self._R
            )
            if neg_ll >= _INVALID_LIKELIHOOD:
                return neg_ll

            if self._reg_fn is not None:
                neg_ll += self._reg_fn(theta)
            return neg_ll

        rng = np.random.default_rng(0)
        best_theta = self._theta0.copy()
        best_f = float("inf")
        best_converged = False

        try:
            for restart in range(self._n_restarts):
                if restart == 0:
                    x0 = self._theta0.copy()
                elif self._perturbation_fn is not None:
                    x0 = self._perturbation_fn(self._theta0, rng, restart)
                else:
                    x0 = self._theta0 + rng.normal(
                        0.0, self._restart_pert, size=self._theta0.size
                    )

                if self._use_gradient:
                    x_best, f_best, conv = self._gradient_step(
                        objective, x0, history
                    )
                else:
                    x_best, f_best, conv = nelder_mead(
                        objective,
                        x0,
                        tol=1e-4,
                        max_iter=400 * len(x0),
                    )

                if f_best < best_f:
                    best_f = f_best
                    best_theta = x_best
                    best_converged = conv

        except Exception as exc:
            _LOGGER.error("ParameterEstimator failed: %s", exc)
            return EstimationResult(
                theta_best=self._theta0.copy(),
                neg_log_likelihood=float("inf"),
                converged=False,
                n_steps=n_steps,
                message=f"Estimation error: {exc}",
            )

        msg = (
            "Converged." if best_converged
            else "Reached iteration limit (result may be approximate)."
        )
        return EstimationResult(
            theta_best=best_theta,
            neg_log_likelihood=float(best_f),
            converged=best_converged,
            n_steps=n_steps,
            message=msg,
        )

    # ── Internal ───────────────────────────────────────────────────────────

    def _gradient_step(
        self,
        objective: Callable[[np.ndarray], float],
        x0: np.ndarray,
        history: List[dict],
    ) -> Tuple[np.ndarray, float, bool]:
        """Gradient-based minimisation, falling back to Nelder–Mead."""
        try:
            from scipy.optimize import minimize  # optional dependency

            # Always use finite-difference gradient of the *full* objective
            # (likelihood + regularization + bounds guard) so the gradient is
            # consistent with the function being minimized, regardless of
            # whether a regularization_fn is set.
            h = 1e-5

            def grad(theta: np.ndarray) -> np.ndarray:
                f0 = objective(theta)
                g = np.zeros(len(theta), dtype=float)
                for i in range(len(theta)):
                    th = theta.copy()
                    th[i] += h
                    g[i] = (objective(th) - f0) / h
                return g

            bounds_scipy = self._bounds  # list of (lo, hi) or None
            res = minimize(
                objective,
                x0,
                jac=grad,
                method="L-BFGS-B",
                bounds=bounds_scipy,
                options={"maxiter": 500, "ftol": 1e-8, "gtol": 1e-5},
            )
            return res.x, float(res.fun), res.success

        except ImportError:
            _LOGGER.debug(
                "scipy not available; falling back to Nelder–Mead for "
                "gradient-based step."
            )
            return nelder_mead(
                objective,
                x0,
                tol=1e-4,
                max_iter=400 * len(x0),
            )
