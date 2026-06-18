"""
Model Predictive Controller for linear continuous-discrete systems.

:class:`StandardLinearContinuousMPC` composes a
:class:`~mbc.estimation.CDKalmanFilter` and a
:class:`~mbc.control.StandardLinearContinuousDiscreteOCP` and implements the
receding-horizon policy described in ControlToolbox §EMPC —
*ENMPC Algorithm*, specialised to the linear continuous-discrete case.

At each measurement time t_k:

    1. **Measure**   ym[k]                                  (passed to ``step``)
    2. **Estimate**  x̂[k|k] = κ(x̂[k-1|k-1], u[k-1], d[k-1], ym[k])
                                                            (estimator.step,
                                                             continuous ODE
                                                             integration)
    3. **Optimise**  U* = λ(x̂[k|k], …)                       (ocp.solve, ZOH-QP)
    4. **Apply**     u[k] = U*[0:nu]                          (returned to caller)

The estimator integrates the continuous-time matrices ``A``, ``B``, ``E``
directly via ODE integration; the OCP uses ZOH-discretised matrices
``(Ad, Bd, Ed)`` computed once at construction time inside
:class:`StandardLinearContinuousDiscreteOCP`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Tuple, TYPE_CHECKING

import numpy as np

from .._utils import _any_to_np1d
from ..estimation.cd_kalman import CDKalmanFilter
from .cd_ocp import StandardLinearContinuousDiscreteOCP
from .mpc_forecast import HorizonProfileMPC
from .ocp import _shift_warm_start

if TYPE_CHECKING:
    from ..models import LinearContinuousDiscreteModel


class LinearContinuousMPC(ABC):
    """
    Abstract MPC for a linear continuous-discrete plant, CD estimator, and discrete-time OCP.
    """

    @abstractmethod
    def step(
        self,
        ym: Any,
        D: Any | None = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Execute one closed-loop CD-MPC step.

        Parameters
        ----------
        ym : (nym,) array-like — measurement ``ym[k]``.
        D  : (N · nd,) array-like, optional — stacked disturbance forecast.
             When omitted, the profile set via :meth:`set_disturbance_profile`
             is used.

        Returns
        -------
        u     : (nu,) ndarray — optimal input ``u_k``.
        U_seq : (N · nu,) ndarray — full optimal input sequence.
        X_seq : (N · nx,) ndarray — predicted state trajectory.
        """


class StandardLinearContinuousMPC(HorizonProfileMPC, LinearContinuousMPC):
    """
    Standard MPC for a linear continuous-discrete plant, CD estimator, and discrete-time OCP.

    Composes a :class:`~mbc.estimation.CDKalmanFilter` with a
    :class:`~mbc.control.StandardLinearContinuousDiscreteOCP` (ZOH-discretised QP).
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        estimator: CDKalmanFilter,
        ocp: StandardLinearContinuousDiscreteOCP,
        warm_start: bool = False,
    ) -> None:
        HorizonProfileMPC.__init__(self, ocp._N, model.nd)
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
        if self._horizon_profile.disturbance_profile is not None:
            return np.asarray(self._horizon_profile.disturbance_profile, dtype=float).reshape(-1)
        if D is not None:
            return _any_to_np1d(D)
        if self._nd == 0:
            return np.zeros(self._N * self._nd, dtype=float)
        raise ValueError(
            "disturbance forecast required: pass D to step() or call "
            "set_disturbance_profile() first"
        )


# Backward-compatible aliases (deprecated).
CDMPCController = StandardLinearContinuousMPC
LinearContinuousMPCController = StandardLinearContinuousMPC
