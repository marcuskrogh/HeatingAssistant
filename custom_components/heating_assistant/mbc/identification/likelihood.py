"""
Prediction-error decomposition (PED) Kalman-filter log-likelihood.

For a linear discrete-time system

    x[k+1] = A x[k] + B u[k] + E d[k] + offset
    y[k]   = C x[k]      (C = I assumed for efficiency)

the prediction-error decomposition negative log-likelihood is

    -log L(θ) = ½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

where:
    νₖ  = yₖ − x̂ₖ⁻               one-step-ahead innovation (C = I)
    Sₖ  = Pₖ⁻ + R                innovation covariance (C = I)

The model is provided via a *factory* callable
``model_factory(θ) → model`` that returns any object exposing the
``LinearDiscreteModel`` interface (``nx``, ``nu``, ``nd``,
``discretize(d_cvx)``, ``predict_offset(d_np)``).  The ``d`` argument
passed to ``discretize`` is the recorded disturbance from the history; LTI
model implementations may ignore it.

History format (linear PED)
---------------------------
Each entry in *history* is a ``dict`` with keys:

    "y" : (n,) ndarray   – measured state (temperature, etc.)
    "u" : (m,) ndarray   – applied control input (raw, unscaled)
    "d" : (p,) ndarray   – disturbance vector (raw, without model biases)

The model's ``predict_offset`` method is responsible for any additive
correction term that is not captured by E @ d (e.g. q_int in the
HeatingAssistant application).

For the nonlinear continuous-discrete PED (``cd_ped_neg_log_likelihood``),
the history format uses ``"ym"`` instead of ``"y"`` and optionally ``"t"``
for a time-stamp.  See ``cd_ped_neg_log_likelihood`` for details.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np

from .._utils import _any_to_np2d


# ── Constants ─────────────────────────────────────────────────────────────────

#: Sentinel value returned when the likelihood cannot be evaluated (numerical
#: failure, invalid parameters, factory exception, etc.).
_INVALID_LIKELIHOOD = 1e10


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
        Returns a model object exposing ``nx``, ``nu``, ``nd``,
        ``discretize(d_cvx) → (A_cvx, B_cvx, E_cvx)``, and optionally
        ``predict_offset(d_np) → np.ndarray``.
        May raise any exception for invalid θ; the sentinel ``_INVALID_LIKELIHOOD``
        is returned in that case.
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
        Negative log-likelihood, or ``_INVALID_LIKELIHOOD`` on any numerical
        failure.
    """
    if len(history) < 2:
        return _INVALID_LIKELIHOOD

    if not np.all(np.isfinite(theta)):
        return _INVALID_LIKELIHOOD

    try:
        model = model_factory(theta)
    except Exception:
        return _INVALID_LIKELIHOOD

    n = model.nx
    n_u = model.nu
    n_d = model.nd

    # Bootstrap state estimate from first measurement
    try:
        first = history[0]
        x_hat = np.asarray(first["y"][:n], dtype=float).copy()
        P = np.eye(n)

        u_prev = np.zeros(n_u)
        raw_u = np.asarray(first["u"], dtype=float)
        u_prev[:min(n_u, len(raw_u))] = raw_u[:n_u]

        d_prev = np.zeros(n_d)
        raw_d = np.asarray(first["d"], dtype=float)
        d_prev[:min(n_d, len(raw_d))] = raw_d[:n_d]
    except (KeyError, TypeError, ValueError):
        return _INVALID_LIKELIHOOD

    neg_ll = 0.0

    for record in history[1:]:
        try:
            y = np.asarray(record["y"][:n], dtype=float)

            d_cur = np.zeros(n_d)
            raw_d_cur = np.asarray(record["d"], dtype=float)
            d_cur[:min(n_d, len(raw_d_cur))] = raw_d_cur[:n_d]

            u_cur = np.zeros(n_u)
            raw_u_cur = np.asarray(record["u"], dtype=float)
            u_cur[:min(n_u, len(raw_u_cur))] = raw_u_cur[:n_u]
        except (KeyError, TypeError, ValueError):
            return _INVALID_LIKELIHOOD

        # Discretise at the previous disturbance
        try:
            A_raw, B_raw, E_raw = model.discretize(np.asarray(d_prev, dtype=float))
        except Exception:
            return _INVALID_LIKELIHOOD

        A = _any_to_np2d(A_raw)
        B = _any_to_np2d(B_raw)
        E = _any_to_np2d(E_raw)

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
                return _INVALID_LIKELIHOOD
            neg_ll += 0.5 * (logdet + float(nu @ np.linalg.solve(S, nu)))
        except np.linalg.LinAlgError:
            return _INVALID_LIKELIHOOD

        # Kalman update  (C = I → K = P_pred S⁻¹)
        try:
            K = np.linalg.solve(S.T, P_pred.T).T
        except np.linalg.LinAlgError:
            return _INVALID_LIKELIHOOD

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


# ── Nonlinear continuous-discrete PED likelihood ─────────────────────────────


def cd_ped_neg_log_likelihood(
    model_factory: Callable[[np.ndarray], object],
    theta: np.ndarray,
    history: List[Dict[str, np.ndarray]],
    x0: np.ndarray,
    P0: np.ndarray,
    dt: float,
    n_steps: int = 10,
) -> float:
    """
    CD-EKF prediction-error decomposition (PED) negative log-likelihood.

    Uses the continuous-discrete extended Kalman filter to propagate the state
    estimate and covariance via the linearised continuous Riccati ODE, and
    accumulates the Gaussian log-likelihood contributions from the discrete
    measurement innovations:

        −log L(θ) = ½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

    where, at each step k:

        x̂_k⁻, P_k⁻  — prior obtained by integrating the drift ODE and
                        linearised Riccati ODE from t_{k−1} to t_k using
                        ``n_steps`` forward-Euler sub-steps.
        H_k = ∂hm/∂x   evaluated at (x̂_k⁻, u_k, d_k, p, t_k)
        νₖ  = ym_k − hm(x̂_k⁻, u_k, d_k, p, t_k)   innovation
        Sₖ  = H_k P_k⁻ H_kᵀ + Rm                   innovation covariance

    After computing the likelihood contribution the state is updated using the
    Joseph-form Kalman correction.

    Parameters
    ----------
    model_factory : callable  θ → model
        Returns a :class:`~mbc.models.ContinuousDiscreteModel` (or any object
        exposing ``f``, ``sigma``, ``hm``, ``dfdx``, ``dhmdx``, ``Rm``, and
        ``params``).  ``model.params`` is used as the parameter vector ``p``
        passed to all model function calls.  May raise any exception for
        invalid ``θ``; the sentinel ``_INVALID_LIKELIHOOD`` is returned in
        that case.
    theta : (ntheta,) ndarray
        Parameter vector passed to *model_factory*.
    history : list of dicts
        Records ``{"ym": (nym,) ndarray, "u": (nu,) ndarray,
        "d": (nd,) ndarray}``.  An optional ``"t": float`` key overrides the
        default uniform time-stamp ``k * dt``.  Must contain at least 2
        entries.

        **Convention**: entry ``k`` holds the measurement ``ym_k`` at time
        ``t_k``, together with the control input ``u_k`` and disturbance
        ``d_k`` that are applied during ``[t_k, t_{k+1}]`` (zero-order hold).
        The inputs from entry ``k`` are used for the *prediction* from ``t_k``
        to ``t_{k+1}``; the inputs from entry ``k+1`` are used in the
        *measurement function* at time ``t_{k+1}``.

    x0 : (nx,) ndarray
        Initial state estimate at time ``t_0 = history[0].get("t", 0.0)``.
    P0 : (nx, nx) ndarray
        Initial state covariance.
    dt : float
        Nominal sampling interval.  If ``"t"`` keys are present in the
        history records the actual interval between records ``k`` and ``k+1``
        is used instead, but ``dt`` is still used to set the sub-step size
        ``h = dt / n_steps``.
    n_steps : int, optional
        Number of forward-Euler sub-steps per sampling interval.  Default: 10.

    Returns
    -------
    neg_ll : float
        Negative log-likelihood, or ``_INVALID_LIKELIHOOD`` on any numerical
        failure.
    """
    if len(history) < 2:
        return _INVALID_LIKELIHOOD

    if not np.all(np.isfinite(theta)):
        return _INVALID_LIKELIHOOD

    try:
        model = model_factory(theta)
    except Exception:
        return _INVALID_LIKELIHOOD

    try:
        p = np.asarray(model.params, dtype=float)
    except Exception:
        p = np.array([], dtype=float)

    try:
        x = np.asarray(x0, dtype=float).copy()
        P = np.asarray(P0, dtype=float).copy()
        nx = x.shape[0]
    except (ValueError, TypeError):
        return _INVALID_LIKELIHOOD

    h_sub = dt / n_steps
    neg_ll = 0.0

    for k in range(len(history) - 1):
        # Inputs applied during [t_k, t_{k+1}]
        try:
            rec_k = history[k]
            u_k = np.asarray(rec_k["u"], dtype=float)
            d_k = np.asarray(rec_k["d"], dtype=float)
            t_k = float(rec_k.get("t", k * dt))
        except (KeyError, TypeError, ValueError):
            return _INVALID_LIKELIHOOD

        # Measurement and inputs at t_{k+1}
        try:
            rec_next = history[k + 1]
            ym_next = np.asarray(rec_next["ym"], dtype=float)
            u_next = np.asarray(rec_next["u"], dtype=float)
            d_next = np.asarray(rec_next["d"], dtype=float)
            t_next = float(rec_next.get("t", (k + 1) * dt))
        except (KeyError, TypeError, ValueError):
            return _INVALID_LIKELIHOOD

        # ── Predict: integrate drift ODE and Riccati ODE ──────────────────
        t_j = t_k
        for _ in range(n_steps):
            try:
                F_j = model.dfdx(x, u_k, d_k, p, t_j)     # (nx, nx)
                G_j = model.sigma(x, u_k, d_k, p, t_j)    # (nx, nw)
                f_j = model.f(x, u_k, d_k, p, t_j)        # (nx,)
            except Exception:
                return _INVALID_LIKELIHOOD

            P_dot = F_j @ P + P @ F_j.T + G_j @ G_j.T
            x = x + h_sub * f_j
            P = P + h_sub * P_dot
            P = (P + P.T) * 0.5
            t_j += h_sub

        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(P))):
            return _INVALID_LIKELIHOOD

        # ── Innovation ────────────────────────────────────────────────────
        try:
            H = model.dhmdx(x, u_next, d_next, p, t_next)   # (nym, nx)
            y_hat = model.hm(x, u_next, d_next, p, t_next)  # (nym,)
            Rm = model.Rm                                    # (nym, nym)
        except Exception:
            return _INVALID_LIKELIHOOD

        nu = ym_next - y_hat          # (nym,)
        S = H @ P @ H.T + Rm          # (nym, nym)

        # Likelihood contribution: ½(log|S| + νᵀ S⁻¹ ν)
        try:
            sign, logdet = np.linalg.slogdet(S)
            if sign <= 0:
                return _INVALID_LIKELIHOOD
            neg_ll += 0.5 * (logdet + float(nu @ np.linalg.solve(S, nu)))
        except np.linalg.LinAlgError:
            return _INVALID_LIKELIHOOD

        if not np.isfinite(neg_ll):
            return _INVALID_LIKELIHOOD

        # ── Update: Kalman correction (Joseph form) ───────────────────────
        try:
            Kt = np.linalg.solve(S, H @ P)   # (nym, nx)
            K = Kt.T                          # (nx, nym)
        except np.linalg.LinAlgError:
            return _INVALID_LIKELIHOOD

        IKH = np.eye(nx) - K @ H
        x = x + K @ nu
        P = IKH @ P @ IKH.T + K @ Rm @ K.T
        P = (P + P.T) * 0.5

    return neg_ll


def cd_ped_neg_log_likelihood_gradient(
    model_factory: Callable[[np.ndarray], object],
    theta: np.ndarray,
    history: List[Dict[str, np.ndarray]],
    x0: np.ndarray,
    P0: np.ndarray,
    dt: float,
    n_steps: int = 10,
    h: float = 1e-5,
) -> np.ndarray:
    """
    Finite-difference gradient of :func:`cd_ped_neg_log_likelihood` w.r.t. *theta*.

    Uses a one-sided forward-difference approximation:

        ∂(−log L) / ∂θ_i ≈ [ f(θ + h eᵢ) − f(θ) ] / h

    Parameters
    ----------
    model_factory : callable  θ → model
    theta         : (ntheta,) ndarray — current parameter vector
    history       : standardised history records (same format as
                    :func:`cd_ped_neg_log_likelihood`)
    x0            : (nx,) ndarray — initial state estimate
    P0            : (nx, nx) ndarray — initial state covariance
    dt            : float — sampling interval
    n_steps       : int — Euler sub-steps per interval
    h             : finite-difference step size (default: 1e-5)

    Returns
    -------
    grad : (ntheta,) ndarray — ∂(−log L) / ∂θ  (may contain inf/nan for
           degenerate θ)
    """
    f0 = cd_ped_neg_log_likelihood(
        model_factory, theta, history, x0, P0, dt, n_steps
    )
    grad = np.zeros(len(theta), dtype=float)
    for i in range(len(theta)):
        theta_h = theta.copy()
        theta_h[i] += h
        fh = cd_ped_neg_log_likelihood(
            model_factory, theta_h, history, x0, P0, dt, n_steps
        )
        grad[i] = (fh - f0) / h
    return grad
