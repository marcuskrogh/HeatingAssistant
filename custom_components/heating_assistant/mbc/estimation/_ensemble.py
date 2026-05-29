"""
Internal helpers shared between ensemble-based continuous-discrete state
estimators (CD-EnKF and CD-PF).

ControlToolbox §SDE prescribes that every particle is propagated
independently through the *full* SDE with its own realisation of the
Wiener increment, with state-dependent diffusion evaluated per particle.
This module centralises that propagation kernel so the EnKF and PF do
not duplicate it.
"""

from __future__ import annotations

import numpy as np


def _propagate_em_ensemble(
    model,
    X: np.ndarray,
    u: np.ndarray,
    d: np.ndarray,
    p: np.ndarray,
    t: float,
    h: float,
    n_steps: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Propagate an ensemble through the SDE via per-particle Euler-Maruyama.

    For each particle ``i = 1, …, N`` and each sub-step ``n = 0, …, n_steps − 1``,

        X_{n+1}[:, i] = X_n[:, i] + h · f(X_n[:, i], u, d, p, t_n)
                                  + sigma(X_n[:, i], u, d, p, t_n) · z · √h,
        z ~ N(0, I)            (independent per particle and sub-step).

    Parameters
    ----------
    model   : ContinuousDiscreteModel
    X       : (nx, N) ensemble at time ``t``.
    u, d, p : ZOH input, disturbance, parameter vector over ``[t, t + h n_steps]``.
    t       : current time.
    h       : sub-step size.
    n_steps : number of Euler-Maruyama sub-steps.
    rng     : NumPy ``Generator`` for the Wiener increments.

    Returns
    -------
    X_next : (nx, N) ensemble after ``n_steps`` sub-steps.
    """
    sqrt_h = np.sqrt(h)
    N = X.shape[1]
    t_j = t
    for _ in range(n_steps):
        X_new = np.empty_like(X)
        for i in range(N):
            xi = X[:, i]
            f_i = model.f(xi, u, d, p, t_j)
            sigma_i = model.sigma(xi, u, d, p, t_j)
            z_i = rng.standard_normal(sigma_i.shape[1])
            X_new[:, i] = xi + h * f_i + sigma_i @ z_i * sqrt_h
        X = X_new
        t_j += h
    return X


def _ensemble_measurements(
    model,
    X: np.ndarray,
    u: np.ndarray,
    d: np.ndarray,
    p: np.ndarray,
    t: float = 0.0,
) -> np.ndarray:
    """
    Evaluate the measurement function at every ensemble member.

    Returns ``Z`` with ``Z[:, i] = hm(X[:, i], u, d, p, t)`` — the
    per-particle predicted measurement vector.

    Parameters
    ----------
    model   : ContinuousDiscreteModel
    X       : (nx, N) ensemble.
    u, d, p : input, disturbance, parameter vectors.
    t       : evaluation time (default 0.0).

    Returns
    -------
    Z : (nym, N) ndarray of predicted measurements per particle.
    """
    return np.column_stack([
        model.hm(X[:, i], u, d, p, t) for i in range(X.shape[1])
    ])
