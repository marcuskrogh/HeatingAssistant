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
        self._armed_predict_dt: Optional[float] = None

    def arm_predict_dt(self, dt: float) -> None:
        """Use ``dt`` seconds for the next predict instead of the nominal sample.

        ``0`` skips the time update. The value is consumed by that predict.
        """

        self._armed_predict_dt = float(dt)

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        armed = self._armed_predict_dt
        if armed is None:
            return super().predict(u, d, p, t)
        self._armed_predict_dt = None
        if float(armed) <= 1e-9:
            return self._x.copy(), self._P.copy()
        return self.predict_for(float(armed), u, d, p, t)

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

