"""
Shared utility functions for numpy/cvxopt conversions and discretisation.

Provides helpers used across the mbc sub-packages:

  - ``_np_to_cvx`` / ``_cvx_to_np`` / ``_cvx_col_to_np``  – array conversion
  - ``_any_to_np1d`` / ``_any_to_np2d``                   – accept cvxopt or numpy
  - ``_eye`` / ``_zeros`` / ``_symmetrise``                 – cvxopt matrix construction
  - ``_expm`` / ``_zoh``                                    – matrix exponential and
                                                              zero-order-hold discretisation
  - ``_zoh_full``                                           – ZOH for systems with multiple
                                                              input matrices (B_c, E_c)
  - ``_van_loan``                                           – exact discrete process-noise
                                                              covariance via Van Loan (1978)
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from cvxopt import matrix


# ── Numpy / cvxopt conversions ────────────────────────────────────────────


def _np_to_cvx(a: np.ndarray) -> matrix:
    """Convert a numpy array to a cvxopt dense matrix (column-major)."""
    if a.ndim == 1:
        return matrix(a.tolist(), (len(a), 1), tc="d")
    rows, cols = a.shape
    return matrix(a.tolist(), (rows, cols), tc="d")


def _cvx_to_np(m: matrix) -> np.ndarray:
    """Convert a cvxopt dense matrix to a 2-D numpy array."""
    rows, cols = m.size
    # cvxopt stores matrices in column-major (Fortran) order
    return np.array(list(m), dtype=float).reshape((rows, cols), order="F")


def _cvx_col_to_np(m: matrix) -> np.ndarray:
    """Convert a cvxopt column vector to a 1-D numpy array."""
    return np.array(list(m), dtype=float)


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


# ── cvxopt matrix construction ────────────────────────────────────────────


def _eye(n: int) -> matrix:
    """Return an n×n identity matrix (cvxopt dense, float)."""
    I = matrix(0.0, (n, n))
    for i in range(n):
        I[i, i] = 1.0
    return I


def _zeros(rows: int, cols: int) -> matrix:
    """Return a rows×cols zero matrix (cvxopt dense, float)."""
    return matrix(0.0, (rows, cols))


def _symmetrise(M: matrix) -> matrix:
    """Force symmetry: M ← ½(M + Mᵀ)."""
    return (M + M.T) * 0.5


# ── Matrix exponential and ZOH discretisation ─────────────────────────────


def _expm(M: np.ndarray) -> np.ndarray:
    """
    Matrix exponential via eigendecomposition.

    For a real matrix M with distinct eigenvalues this is exact.
    Thermal state matrices have real, distinct, negative eigenvalues
    (stable RC circuit), so this approach is numerically well-conditioned.
    """
    vals, vecs = np.linalg.eig(M)
    return np.real(vecs @ np.diag(np.exp(vals)) @ np.linalg.inv(vecs))


def _zoh(Fc: np.ndarray, Gc: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Zero-order hold discretisation of ẋ = Fc x + Gc u.

    Returns (A_d, B_d) such that x[k+1] = A_d x[k] + B_d u[k].

    Using:  A_d = expm(Fc dt),   B_d = Fc⁻¹ (A_d − I) Gc
    """
    n = Fc.shape[0]
    A_d = _expm(Fc * dt)
    B_d = np.linalg.solve(Fc, (A_d - np.eye(n)) @ Gc)
    return A_d, B_d


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


# ── Newton's method ───────────────────────────────────────────────────────


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
