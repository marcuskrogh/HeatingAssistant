"""
Prediction-error decomposition (PED) Kalman-filter log-likelihood.

For a linear discrete-time system

    x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k] + offset(d)
    y[k]   = C x[k]      (C = I assumed for efficiency)

the prediction-error decomposition negative log-likelihood is

    -log L(θ) = ½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

where:
    νₖ  = yₖ − x̂ₖ⁻               one-step-ahead innovation (C = I)
    Sₖ  = Pₖ⁻ + R                innovation covariance (C = I)

The model is provided via a *factory* callable
``model_factory(θ) → model`` that returns any object exposing the
``LinearDiscreteModel`` interface (``n_x``, ``n_u``, ``n_d``,
``discretize(d_cvx)``, ``predict_offset(d_np)``).

History format
--------------
Each entry in *history* is a ``dict`` with keys:

    "y" : (n,) ndarray   – measured state (temperature, etc.)
    "u" : (m,) ndarray   – applied control input (raw, unscaled)
    "d" : (p,) ndarray   – disturbance vector (raw, without model biases)

The model's ``predict_offset`` method is responsible for any additive
correction term that is not captured by E @ d (e.g. q_int in the
HeatingAssistant application).
"""

from __future__ import annotations

from typing import Callable, Dict, List

import numpy as np


# ── Helpers ──────────────────────────────────────────────────────────────────


def _np_to_cvx(a: np.ndarray):
    """Convert a 1-D or 2-D numpy array to a cvxopt dense column / matrix."""
    from cvxopt import matrix as cvx_matrix  # lazy import
    if a.ndim == 1:
        return cvx_matrix(a.tolist(), (len(a), 1), tc="d")
    rows, cols = a.shape
    return cvx_matrix(a.tolist(), (rows, cols), tc="d")


def _cvx_to_np(m) -> np.ndarray:
    """Convert a cvxopt dense matrix to a 2-D numpy array (column-major)."""
    rows, cols = m.size
    return np.array(list(m), dtype=float).reshape((rows, cols), order="F")


# ── Core likelihood ───────────────────────────────────────────────────────────


def ped_neg_log_likelihood(
    model_factory: Callable[[np.ndarray], object],
    theta: np.ndarray,
    history: List[Dict[str, np.ndarray]],
    Q: np.ndarray,
    R: np.ndarray,
) -> float:
    """
    Evaluate the PED Kalman-filter *negative* log-likelihood at *theta*.

    Parameters
    ----------
    model_factory : callable  θ → model
        Returns a model object exposing ``n_x``, ``n_u``, ``n_d``,
        ``discretize(d_cvx) → (A_cvx, B_cvx, E_cvx)``, and optionally
        ``predict_offset(d_np) → np.ndarray``.
        May raise any exception for invalid θ; the sentinel ``1e10`` is
        returned in that case.
    theta : (p,) ndarray
        Parameter vector passed to *model_factory*.
    history : list of dicts
        Standardised history records ``{"y": ndarray, "u": ndarray,
        "d": ndarray}``.  Must contain at least 2 entries.
    Q : (n, n) ndarray
        Process-noise covariance.
    R : (n, n) ndarray
        Measurement-noise covariance.  (Assumes C = I.)

    Returns
    -------
    neg_ll : float
        Negative log-likelihood, or ``1e10`` on any numerical failure.
    """
    if len(history) < 2:
        return 1e10

    if not np.all(np.isfinite(theta)):
        return 1e10

    try:
        model = model_factory(theta)
    except Exception:
        return 1e10

    n = model.n_x
    n_u = model.n_u
    n_d = model.n_d

    # Bootstrap state estimate from first measurement
    first = history[0]
    x_hat = np.asarray(first["y"][:n], dtype=float).copy()
    P = np.eye(n)

    u_prev = np.zeros(n_u)
    raw_u = np.asarray(first["u"], dtype=float)
    u_prev[:min(n_u, len(raw_u))] = raw_u[:n_u]

    d_prev = np.zeros(n_d)
    raw_d = np.asarray(first["d"], dtype=float)
    d_prev[:min(n_d, len(raw_d))] = raw_d[:n_d]

    neg_ll = 0.0

    for record in history[1:]:
        y = np.asarray(record["y"][:n], dtype=float)

        d_cur = np.zeros(n_d)
        raw_d_cur = np.asarray(record["d"], dtype=float)
        d_cur[:min(n_d, len(raw_d_cur))] = raw_d_cur[:n_d]

        u_cur = np.zeros(n_u)
        raw_u_cur = np.asarray(record["u"], dtype=float)
        u_cur[:min(n_u, len(raw_u_cur))] = raw_u_cur[:n_u]

        # Discretise at the previous disturbance
        try:
            A_cvx, B_cvx, E_cvx = model.discretize(_np_to_cvx(d_prev))
        except Exception:
            return 1e10

        A = _cvx_to_np(A_cvx)
        B = _cvx_to_np(B_cvx)
        E = _cvx_to_np(E_cvx)

        # Optional additive offset (e.g. known constant heat gain)
        offset: np.ndarray
        try:
            offset = np.asarray(model.predict_offset(d_prev), dtype=float)
        except AttributeError:
            offset = np.zeros(n)

        # One-step-ahead prediction
        x_pred = A @ x_hat + B @ u_prev + E @ d_prev + offset
        P_pred = A @ P @ A.T + Q

        # Innovation and its covariance  (C = I)
        nu = y - x_pred
        S = P_pred + R

        # Likelihood contribution: ½(log|S| + νᵀ S⁻¹ ν)
        try:
            sign, logdet = np.linalg.slogdet(S)
            if sign <= 0:
                return 1e10
            neg_ll += 0.5 * (logdet + float(nu @ np.linalg.solve(S, nu)))
        except np.linalg.LinAlgError:
            return 1e10

        # Kalman update  (C = I → K = P_pred S⁻¹)
        try:
            K = np.linalg.solve(S.T, P_pred.T).T
        except np.linalg.LinAlgError:
            return 1e10

        IK = np.eye(n) - K
        x_hat = x_pred + K @ nu
        P = IK @ P_pred @ IK.T + K @ R @ K.T
        P = (P + P.T) * 0.5  # enforce symmetry

        u_prev = u_cur
        d_prev = d_cur

    return neg_ll


# ── Gradient ─────────────────────────────────────────────────────────────────


def ped_neg_log_likelihood_gradient(
    model_factory: Callable[[np.ndarray], object],
    theta: np.ndarray,
    history: List[Dict[str, np.ndarray]],
    Q: np.ndarray,
    R: np.ndarray,
    h: float = 1e-5,
) -> np.ndarray:
    """
    Finite-difference gradient of :func:`ped_neg_log_likelihood` w.r.t. *theta*.

    Uses a one-sided forward-difference approximation:

        ∂(-log L) / ∂θ_i ≈ [ f(θ + h eᵢ) − f(θ) ] / h

    If a model returned by ``model_factory`` exposes a ``discretize_jacobian``
    method (returning analytic Jacobians ∂A_d/∂θ_i etc.), the gradient can be
    propagated analytically through the Kalman recursion by overriding this
    function in a subclass or replacing it.  The finite-difference fallback
    here is correct for all models regardless of whether analytic Jacobians
    are available.

    Parameters
    ----------
    model_factory : callable  θ → model
    theta         : (p,) ndarray  — current parameter vector
    history       : standardised history records (same format as
                    :func:`ped_neg_log_likelihood`)
    Q             : (n, n) process-noise covariance
    R             : (n, n) measurement-noise covariance
    h             : finite-difference step size (default: 1e-5)

    Returns
    -------
    grad : (p,) ndarray  — ∂(-log L) / ∂θ  (possibly containing inf/nan
           for degenerate θ)
    """
    f0 = ped_neg_log_likelihood(model_factory, theta, history, Q, R)
    grad = np.zeros(len(theta), dtype=float)
    for i in range(len(theta)):
        theta_h = theta.copy()
        theta_h[i] += h
        fh = ped_neg_log_likelihood(model_factory, theta_h, history, Q, R)
        grad[i] = (fh - f0) / h
    return grad
