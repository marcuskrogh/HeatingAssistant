"""CD-EKF wrapper that records the Kalman innovation."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from mbc.estimation import ContinuousDiscreteEKF

from ..wall_physics import apply_wall_ss_fusion, block_air_wall_kalman_gain


class _InnovationEKF(ContinuousDiscreteEKF):
    """CD-EKF that records the Kalman innovation after each measurement fusion.

    CDLinearizedMPCController calls ``estimator.step(y, u_prev, d_prev, p, t)``
    which combines predict + update.  This subclass intercepts that call to
    compute and store ``nu = y - hm(xhat-)`` between the two phases, making the
    innovation available via the ``last_innovation`` property after each step.
    Before the air update, air-wall covariance is zeroed so air innovations
    cannot assign model mismatch to the unmeasured wall (the wall ODE already
    forbids Tw falling below min(Ta, Tout) when Q_wall >= 0).
    After the air update, the wall block is fused with the 2R2C algebraic
    steady state (Kalman measurement, not a clip).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_innovation: Optional[List[float]] = None

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """Innovation ν = y − hm(x̂⁻) from the most recent step, or None."""
        return self._last_innovation

    def step(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
        mask: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        self.predict(u, d, p, t)
        x_prior = self._x.copy()
        y_hat = self._model.hm(x_prior, u, d, p, 0.0)
        self._last_innovation = (np.asarray(y, dtype=float) - y_hat).tolist()
        block_air_wall_kalman_gain(self)
        self.update(y, u, d, p, mask=mask)
        apply_wall_ss_fusion(self, y, d)
        return np.asarray(self._x, dtype=float), np.asarray(self.P, dtype=float)
