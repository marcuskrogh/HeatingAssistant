"""CD-EKF wrapper that records the Kalman innovation."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from mbc.estimation import ContinuousDiscreteEKF


class _InnovationEKF(ContinuousDiscreteEKF):
    """CD-EKF that records the Kalman innovation after each measurement fusion.

    CDLinearizedMPCController calls ``estimator.step(y, u_prev, d_prev, p, t)``
    which combines predict + update.  This subclass intercepts that call to
    compute and store ``ν = y − hm(x̂⁻)`` between the two phases, making the
    innovation available via the ``last_innovation`` property after each step.
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
        return self.update(y, u, d, p, mask=mask)

