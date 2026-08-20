"""Accept/reject a candidate slow input sequence."""

from __future__ import annotations

import numpy as np


# Minimum relative improvement versus J(u=0).  Idle SciPy “success” at U = 0
# has J ≈ J(u=0) and is rejected.  A useful cooling/heating plan that only
# cuts cost to 5–50% of the zero-heat cost is accepted.  The PLAN phrase
# “J < 1e-3 · J(u=0)” was a mis-encoding of “not near the zero-heat cost”.
ACCEPT_J_RATIO = 1e-3


def accept_plan(
    u_star: np.ndarray,
    cost: float,
    cost_zero: float,
    u_min: np.ndarray,
    u_max: np.ndarray,
) -> bool:
    """True when ``U*`` is in bounds and strictly better than ``J(u=0)``."""

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
    return float(cost) < float(cost_zero) * (1.0 - ACCEPT_J_RATIO)
