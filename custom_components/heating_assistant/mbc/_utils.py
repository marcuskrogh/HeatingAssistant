"""
Shared utility functions for array conversion and discretisation.

Provides helpers used across the mbc sub-packages:

  - ``_any_to_np1d`` / ``_any_to_np2d``                   – coerce any array-like
                                                            (numpy, list, or legacy
                                                            cvxopt matrix) to numpy
  - ``_zoh_full``                                         – ZOH discretisation
  - ``_van_loan``                                         – exact discrete process-noise
                                                            covariance (Van Loan 1978)
  - ``_newton_solve``                                     – Newton iteration on F(x) = 0
  - ``_fd_jacobian``                                      – forward finite-difference Jacobian
                                                            of an arbitrary scalar/vector
                                                            function (centralises the FD
                                                            kernel used by all model defaults)
  - ``_cholesky_psd``                                     – Cholesky factor with diagonal
                                                            jitter fallback for numerically
                                                            non-positive-definite matrices

Constants:

  - ``H_FD``                  – default forward-FD step (1e-5)
  - ``CHOLESKY_JITTER``       – default Cholesky regularisation (1e-10)
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy.linalg import expm as _expm  # noqa: F401 — re-exported for callers


# ── Array coercion ─────────────────────────────────────────────────────────
#
# The toolbox is numpy-native.  ``_any_to_np*`` additionally accept legacy
# cvxopt matrices (if a caller still has cvxopt installed) so that external
# code passing cvxopt columns keeps working; cvxopt itself is not a dependency.


def _any_to_np1d(v) -> np.ndarray:
    """Convert a cvxopt column vector, list, or numpy array to a 1-D float array."""
    try:
        from cvxopt import matrix as _cvx_matrix
        if isinstance(v, _cvx_matrix):
            return np.array(list(v), dtype=float)
    except ImportError:
        pass
    return np.asarray(v, dtype=float).ravel()


def _any_to_np2d(v) -> np.ndarray:
    """Convert a cvxopt matrix, list-of-lists, or numpy array to a 2-D float array."""
    try:
        from cvxopt import matrix as _cvx_matrix
        if isinstance(v, _cvx_matrix):
            rows, cols = v.size
            return np.array(list(v), dtype=float).reshape((rows, cols), order="F")
    except ImportError:
        pass
    return np.asarray(v, dtype=float)


# ── Matrix exponential and ZOH discretisation ─────────────────────────────


def _zoh_full(
    A_c: np.ndarray,
    B_c: np.ndarray,
    E_c: np.ndarray,
    dt: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Zero-order hold discretisation of  ẋ = A_c x + B_c u + E_c d.

    Uses the augmented-matrix method (no matrix inverse required):

        expm([[A_c, B_c, E_c],   =  [[A_d,  B_d,  E_d ],
              [ 0,   0,   0 ],        [ 0,   I_m,   0  ],
              [ 0,   0,   0 ]] * dt)  [ 0,    0,   I_p ]]

    Returns (A_d, B_d, E_d) as numpy arrays.

    Parameters
    ----------
    A_c : (n, n) ndarray  — continuous state matrix.
    B_c : (n, m) ndarray  — continuous input matrix.
    E_c : (n, p) ndarray  — continuous disturbance matrix.
    dt  : float           — sampling interval.
    """
    n, m, p = A_c.shape[0], B_c.shape[1], E_c.shape[1]
    size = n + m + p
    M = np.zeros((size, size))
    M[:n, :n] = A_c
    M[:n, n:n + m] = B_c
    M[:n, n + m:] = E_c
    E = _expm(M * dt)
    return E[:n, :n], E[:n, n:n + m], E[:n, n + m:]


def _van_loan(
    A_c: np.ndarray,
    G: np.ndarray,
    Q_c: np.ndarray,
    dt: float,
) -> np.ndarray:
    """
    Exact discrete process-noise covariance via the Van Loan (1978) method.

    For the continuous-time SDE  dx = A_c x dt + G dw,  cov(dw dw^T) = Q_c dt,
    the exact discrete covariance is

        Q_d = ∫₀^{dt} expm(A_c τ) G Q_c Gᵀ expm(A_c τ)ᵀ dτ

    Computed via the augmented 2n×2n matrix (Van Loan, 1978):

        M = [[-A_c,   G Q_c Gᵀ],   * dt
              [  0,   A_cᵀ    ]]

        expm(M) = [[expm(-A_c dt),   expm(-A_c dt) Q_d],
                   [      0,         expm(A_cᵀ dt)    ]]

    so that  Q_d = A_d · E[:n, n:]  where A_d = expm(A_c dt).

    The result is symmetrised numerically to eliminate floating-point skew.

    Parameters
    ----------
    A_c : (n, n) ndarray  — continuous state matrix.
    G   : (n, q) ndarray  — noise input matrix.
    Q_c : (q, q) ndarray  — continuous process-noise covariance.
    dt  : float           — sampling interval.

    Returns
    -------
    Q_d : (n, n) ndarray — discrete process-noise covariance.
    """
    n = A_c.shape[0]
    M = np.zeros((2 * n, 2 * n))
    M[:n, :n] = -A_c
    M[:n, n:] = G @ Q_c @ G.T
    M[n:, n:] = A_c.T
    E = _expm(M * dt)
    A_d = E[n:, n:].T          # expm(A_c dt)
    Q_d = A_d @ E[:n, n:]      # expm(A_c dt) · (expm(-A_c dt) Q_d)
    return (Q_d + Q_d.T) * 0.5  # symmetrise


# ── Numerical-method tuning constants ────────────────────────────────────


H_FD: float = 1e-5
"""Default forward finite-difference step used by :func:`_fd_jacobian`."""

CHOLESKY_JITTER: float = 1e-10
"""Default diagonal jitter used by :func:`_cholesky_psd` on near-singular matrices."""


# ── Forward finite-difference Jacobian ───────────────────────────────────


def _fd_jacobian(
    func: Callable[[np.ndarray], np.ndarray],
    x: np.ndarray,
    h: float = H_FD,
    m_out: int | None = None,
) -> np.ndarray:
    """
    Forward finite-difference Jacobian of a vector-valued function.

    Computes

        J[i, k] = (func(x + h e_k)[i] − func(x)[i]) / h,    i ∈ {0, …, m−1},
                                                            k ∈ {0, …, n−1},

    where ``e_k`` is the k-th standard basis vector of ℝⁿ.  This is the
    single FD kernel used by all model-default Jacobians in
    :mod:`mbc.models`.

    Parameters
    ----------
    func : callable (n,) ndarray → (m,) ndarray
        Function whose Jacobian is approximated.
    x : (n,) ndarray
        Evaluation point.
    h : float, optional
        Forward-difference step size.  Default: :data:`H_FD`.
    m_out : int or None, optional
        Output dimension hint.  Required when ``n = 0`` to determine the
        empty-Jacobian shape ``(m_out, 0)`` without evaluating ``func``.
        Ignored when ``n > 0``.

    Returns
    -------
    J : (m, n) ndarray
        Forward-FD Jacobian.  When ``n = 0`` returns an empty ``(m_out, 0)``
        array (and never calls ``func`` if ``m_out`` is supplied).
    """
    n = x.shape[0]
    if n == 0:
        if m_out is None:
            m_out = func(x).shape[0]
        return np.empty((m_out, 0))
    f0 = func(x)
    m = f0.shape[0]
    J = np.empty((m, n))
    for k in range(n):
        x_fwd = x.copy()
        x_fwd[k] += h
        J[:, k] = (func(x_fwd) - f0) / h
    return J


# ── Cholesky with diagonal jitter ────────────────────────────────────────


def _cholesky_psd(
    M: np.ndarray,
    jitter: float = CHOLESKY_JITTER,
) -> np.ndarray:
    """
    Lower Cholesky factor of a symmetric positive-(semi)-definite matrix
    with diagonal jitter fallback.

    Attempts ``np.linalg.cholesky(M)``; on :class:`numpy.linalg.LinAlgError`
    (numerically non-PD ``M``) retries with ``M + jitter · I``.  Used by
    sigma-point and ensemble samplers that are robust to mild jitter on a
    near-singular covariance.

    Parameters
    ----------
    M : (n, n) ndarray  — symmetric positive-(semi)-definite matrix.
    jitter : float, optional
        Diagonal regularisation added on the fallback path.
        Default: :data:`CHOLESKY_JITTER`.

    Returns
    -------
    L : (n, n) ndarray  — lower-triangular Cholesky factor with ``L Lᵀ ≈ M``.
    """
    try:
        return np.linalg.cholesky(M)
    except np.linalg.LinAlgError:
        return np.linalg.cholesky(M + jitter * np.eye(M.shape[0]))


# ── Newton's method ──────────────────────────────────────────────────────


def _newton_solve(
    residual: Callable[[np.ndarray], np.ndarray],
    jacobian: Callable[[np.ndarray], np.ndarray],
    x0: np.ndarray,
    tol: float = 1e-10,
    max_iter: int = 50,
) -> np.ndarray:
    """
    Solve the nonlinear system F(x) = 0 via Newton's method.

    Starting from the initial guess ``x0``, iterates

        x ← x − J(x)⁻¹ F(x)

    until ``‖F(x)‖ < tol`` or ``max_iter`` iterations have been taken.

    Parameters
    ----------
    residual : callable (x,) → (n,)
        Residual function F(x).
    jacobian : callable (x,) → (n, n)
        Jacobian of F with respect to x.
    x0 : (n,) ndarray
        Initial guess.
    tol : float, optional
        Convergence tolerance on ``‖F(x)‖``.  Default: 1e-10.
    max_iter : int, optional
        Maximum number of Newton iterations.  Default: 50.

    Returns
    -------
    x : (n,) ndarray
        Approximate solution satisfying ``‖F(x)‖ < tol`` (or the best
        iterate after ``max_iter`` steps).
    """
    x = x0.copy()
    for _ in range(max_iter):
        F = residual(x)
        if np.linalg.norm(F) < tol:
            break
        J = jacobian(x)
        x = x - np.linalg.solve(J, F)
    return x
