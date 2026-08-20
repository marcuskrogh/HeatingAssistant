"""Accept/reject a candidate slow input sequence."""

from __future__ import annotations

import numpy as np


ACCEPT_J_RATIO = 1e-3


def accept_plan(
    u_star: np.ndarray,
    cost: float,
    cost_zero: float,
    u_min: np.ndarray,
    u_max: np.ndarray,
) -> bool:
    """True when ``U*`` is in bounds and ``J`` is in-band versus ``J(u=0)``."""

    u = np.asarray(u_star, dtype=float)
    lo = np.asarray(u_min, dtype=float)
    hi = np.asarray(u_max, dtype=float)
    if u.size == 0 or not np.isfinite(u).all():
        return False
    if not np.isfinite(cost):
        return False
    if np.any(u < lo - 1e-9) or np.any(u > hi + 1e-9):
        return False
    if not np.isfinite(cost_zero) or cost_zero <= 0.0:
        return cost < 10.0
    return float(cost) < float(ACCEPT_J_RATIO) * float(cost_zero)
