"""
Shared utility functions for numpy/cvxopt conversions and discretisation.

Provides helpers used across the mbc sub-packages:

  - ``_np_to_cvx`` / ``_cvx_to_np`` / ``_cvx_col_to_np``  – array conversion
  - ``_eye`` / ``_zeros`` / ``_symmetrise``                 – cvxopt matrix construction
  - ``_expm`` / ``_zoh``                                    – matrix exponential and
                                                              zero-order-hold discretisation
"""

from __future__ import annotations

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
