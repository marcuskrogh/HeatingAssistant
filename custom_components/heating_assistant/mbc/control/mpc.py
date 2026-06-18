"""
Model Predictive Controller for linear discrete-time systems.

Composes a :class:`~mbc.estimation.KalmanFilter` and an
:class:`StandardLinearDiscreteOCP` for any
:class:`~mbc.models.LinearDiscreteModel` and implements the receding-horizon
policy described in ControlToolbox §EMPC — *ENMPC Algorithm*, specialised
to the linear case.

At each measurement time t_k:

    1. **Measure**   ym[k]                                  (passed to ``step``)
    2. **Estimate**  x̂[k|k] = κ(x̂[k-1|k-1], u[k-1], d[k-1], ym[k])
                                                            (estimator.step)
    3. **Optimise**  U* = λ(x̂[k|k], …)                       (ocp.solve)
    4. **Apply**     u[k] = U*[0:nu]                          (returned to caller)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Tuple, TYPE_CHECKING

import numpy as np

from .._utils import _any_to_np1d
from ..estimation import KalmanFilter
from .mpc_forecast import ForecastAwareMPC
from .ocp import StandardLinearDiscreteOCP, _shift_warm_start

if TYPE_CHECKING:
    from ..models import LinearDiscreteModel


class LinearDiscreteMPC(ABC):
    """
    Abstract MPC for a linear discrete-time plant, estimator, and discrete-time OCP.
    """

    @abstractmethod
    def step(
        self,
        ym: Any,
        D: Any | None = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Execute one closed-loop MPC step.

        Parameters
        ----------
        ym : (nym,) array-like  — measurement ``ym[k]``.
        D  : (N · nd,) array-like, optional — stacked disturbance forecast.
             When omitted, the forecast set via :meth:`set_disturbance_forecast`
             is used.

        Returns
        -------
        u     : (nu,) ndarray — optimal input ``u_k``.
        U_seq : (N · nu,) ndarray — full optimal input sequence.
        X_seq : (N · nx,) ndarray — predicted state trajectory.
        """


class StandardLinearDiscreteMPC(ForecastAwareMPC, LinearDiscreteMPC):
    """
    Standard MPC for a linear discrete-time plant, estimator, and discrete-time OCP.

    Composes a :class:`~mbc.estimation.KalmanFilter` with a
    :class:`StandardLinearDiscreteOCP`.  Horizon disturbance forecasts are
    configured via :meth:`set_disturbance_forecast` before :meth:`step`.
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
        estimator: KalmanFilter,
        ocp: StandardLinearDiscreteOCP,
        warm_start: bool = False,
    ) -> None:
        ForecastAwareMPC.__init__(self, ocp._N, model.nd)
        self._model = model
        self._estimator = estimator
        self._ocp = ocp
        self._warm_start = bool(warm_start)
        self._u_prev_np: np.ndarray = np.zeros(model.nu)
        self._d_prev_np: np.ndarray = np.zeros(model.nd)
        self._prev_U: np.ndarray | None = None
        self._prev_X: np.ndarray | None = None

    def step(
        self,
        ym: Any,
        D: Any | None = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        nu = self._model.nu
        nd = self._model.nd

        ym_np = _any_to_np1d(ym)
        x_hat_np, _ = self._estimator.step(
            ym_np, self._u_prev_np, self._d_prev_np,
        )

        D_np = self._resolve_disturbance_forecast(D)
        x_ref_np = np.asarray(self._model.x_ref, dtype=float).reshape(-1)
        warm = None
        if self._warm_start and self._prev_U is not None:
            warm = _shift_warm_start(
                self._prev_U, self._prev_X, nu, self._model.nx
            )
        U_seq, X_seq = self._ocp.solve(
            x_hat_np, D_np, x_ref_np, u_prev=self._u_prev_np, warm_start=warm,
        )

        u = U_seq[:nu]
        self._u_prev_np = np.asarray(u, dtype=float).copy()
        self._d_prev_np = D_np[:nd].copy()
        self._prev_U, self._prev_X = U_seq, X_seq

        return u, U_seq, X_seq

    def _resolve_disturbance_forecast(self, D: Any | None) -> np.ndarray:
        if self._forecast.disturbance_forecast is not None:
            return np.asarray(self._forecast.disturbance_forecast, dtype=float).reshape(-1)
        if D is not None:
            return _any_to_np1d(D)
        if self._nd == 0:
            return np.zeros(self._N * self._nd, dtype=float)
        raise ValueError(
            "disturbance forecast required: pass D to step() or call "
            "set_disturbance_forecast() first"
        )


# Backward-compatible alias (deprecated).
MPCController = StandardLinearDiscreteMPC
