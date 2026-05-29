"""
Model Predictive Controller for linear continuous-discrete systems.

:class:`CDMPCController` composes a :class:`~mbc.estimation.CDKalmanFilter`
and a :class:`~mbc.control.CDOptimalControlProblem` and implements the
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
:class:`CDOptimalControlProblem`.
"""

from __future__ import annotations

from typing import Any, Tuple, TYPE_CHECKING

import numpy as np

from .._utils import _any_to_np1d
from ..estimation.cd_kalman import CDKalmanFilter
from .cd_ocp import CDOptimalControlProblem
from .ocp import _shift_warm_start

if TYPE_CHECKING:
    from ..models import LinearContinuousDiscreteModel


class CDMPCController:
    """
    MPC controller for a linear continuous-discrete plant.

    Composes a :class:`~mbc.estimation.CDKalmanFilter` and a
    :class:`~mbc.control.CDOptimalControlProblem` into a single
    receding-horizon controller.  The previously-applied ``(u, d)`` are
    tracked internally so that the estimator's predict step has the
    correct ZOH inputs over the just-completed interval.

    Parameters
    ----------
    model     : LinearContinuousDiscreteModel
        Plant model providing ``nu``, ``nd``, ``x_ref``, ``discretize``,
        and ``discretize_noise``.
    estimator : CDKalmanFilter
        State estimator (continuous ODE integration internally).
    ocp       : CDOptimalControlProblem
        Optimal control problem (lifted-batch QP on ZOH-discretised matrices).
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        estimator: CDKalmanFilter,
        ocp: CDOptimalControlProblem,
        warm_start: bool = False,
    ) -> None:
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
        D: Any,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Execute one closed-loop CD-MPC step.

        Parameters
        ----------
        ym : (nym,) array-like — measurement ``ym[k]``.
        D  : (N · nd,) array-like — stacked disturbance forecast
             ``[d[k]; d[k+1]; …; d[k + N − 1]]``.

        Returns
        -------
        u     : (nu,) ndarray — optimal input ``u_k``.
        U_seq : (N · nu,) ndarray — full optimal input sequence.
        X_seq : (N · nx,) ndarray — predicted state trajectory.
        """
        nu = self._model.nu
        nd = self._model.nd

        # Step 2: estimate using the previously-applied (u, d)
        ym_np = _any_to_np1d(ym)
        x_hat_np, _ = self._estimator.step(
            ym_np, self._u_prev_np, self._d_prev_np,
        )

        # Step 3: optimise (OCP returns numpy 1-D arrays)
        D_np = _any_to_np1d(D)
        x_ref_np = np.asarray(self._model.x_ref, dtype=float).reshape(-1)
        warm = None
        if self._warm_start and self._prev_U is not None:
            warm = _shift_warm_start(
                self._prev_U, self._prev_X, nu, self._model.nx
            )
        U_seq, X_seq = self._ocp.solve(
            x_hat_np, D_np, x_ref_np, u_prev=self._u_prev_np, warm_start=warm,
        )

        # Step 4: cache state for the next step
        u = U_seq[:nu]
        self._u_prev_np = np.asarray(u, dtype=float).copy()
        self._d_prev_np = D_np[:nd].copy()
        self._prev_U, self._prev_X = U_seq, X_seq

        return u, U_seq, X_seq
