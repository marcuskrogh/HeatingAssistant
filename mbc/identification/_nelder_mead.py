"""
Pure-NumPy Nelder–Mead simplex optimiser.

No external dependencies beyond NumPy.  Suitable for small-to-medium
parameter spaces (≲ 30 parameters) that arise in system identification
of grey-box thermal models.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

import numpy as np


def nelder_mead(
    objective: Callable[[np.ndarray], float],
    x0: np.ndarray,
    tol: float = 1e-4,
    max_iter: Optional[int] = None,
) -> Tuple[np.ndarray, float, bool]:
    """
    Minimise *objective* starting from *x0* using the Nelder–Mead simplex
    method.

    Parameters
    ----------
    objective : callable  f(x) → float
    x0        : (n,) initial parameter vector
    tol       : convergence tolerance on both function-value spread and
                simplex diameter
    max_iter  : maximum number of iterations; defaults to 200 × n

    Returns
    -------
    x_best    : (n,) best parameter vector found
    f_best    : float, objective value at x_best
    converged : bool
    """
    n = len(x0)
    if max_iter is None:
        max_iter = 200 * n

    # Standard Nelder–Mead coefficients
    alpha = 1.0   # reflection
    gamma = 2.0   # expansion
    rho = 0.5     # contraction
    sigma = 0.5   # shrink

    # Initialise simplex: x0 ± 5 % perturbation along each axis
    simplex = np.empty((n + 1, n))
    simplex[0] = x0.copy()
    for i in range(n):
        s = x0.copy()
        delta = 0.05 * abs(x0[i]) if x0[i] != 0.0 else 0.025
        s[i] += delta
        simplex[i + 1] = s

    fvals = np.array([objective(s) for s in simplex])

    converged = False
    for _ in range(max_iter):
        order = np.argsort(fvals)
        simplex = simplex[order]
        fvals = fvals[order]

        f_spread = abs(fvals[-1] - fvals[0])
        x_spread = np.max(np.abs(simplex[1:] - simplex[0]))
        if f_spread < tol and x_spread < tol:
            converged = True
            break

        x_bar = simplex[:-1].mean(axis=0)

        x_r = x_bar + alpha * (x_bar - simplex[-1])
        f_r = objective(x_r)

        if fvals[0] <= f_r < fvals[-2]:
            simplex[-1] = x_r
            fvals[-1] = f_r
        elif f_r < fvals[0]:
            x_e = x_bar + gamma * (x_r - x_bar)
            f_e = objective(x_e)
            if f_e < f_r:
                simplex[-1] = x_e
                fvals[-1] = f_e
            else:
                simplex[-1] = x_r
                fvals[-1] = f_r
        else:
            if f_r < fvals[-1]:
                x_c = x_bar + rho * (x_r - x_bar)
                f_c = objective(x_c)
                if f_c <= f_r:
                    simplex[-1] = x_c
                    fvals[-1] = f_c
                    continue
            else:
                x_c = x_bar + rho * (simplex[-1] - x_bar)
                f_c = objective(x_c)
                if f_c <= fvals[-1]:
                    simplex[-1] = x_c
                    fvals[-1] = f_c
                    continue
            simplex[1:] = simplex[0] + sigma * (simplex[1:] - simplex[0])
            fvals[1:] = np.array([objective(s) for s in simplex[1:]])

    return simplex[0], fvals[0], converged
