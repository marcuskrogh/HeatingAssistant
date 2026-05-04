"""
Generic Model Predictive Controller.

Composes a KalmanFilter and an OptimalControlProblem for any
LinearDiscreteModel and implements the receding-horizon policy.
"""

from __future__ import annotations

from typing import Tuple, TYPE_CHECKING

from cvxopt import matrix

from .._utils import _zeros
from ..estimation import KalmanFilter
from .ocp import OptimalControlProblem

if TYPE_CHECKING:
    from ..models import LinearDiscreteModel


class MPCController:
    """
    Generic model predictive controller.

    Composes a KalmanFilter and an OptimalControlProblem for any
    LinearDiscreteModel and implements the receding-horizon policy:

      1. Estimate  x̂[k] ← estimator.update(y[k], d[k])
      2. Optimise  U*    ← ocp.solve(x̂[k], D, r)
      3. Apply     u[k]  = U*[0]   (receding horizon, discard rest)
      4. Record    estimator.record_action(u[k])

    Parameters
    ----------
    model     : LinearDiscreteModel
    estimator : KalmanFilter
    ocp       : OptimalControlProblem
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
        estimator: KalmanFilter,
        ocp: OptimalControlProblem,
    ) -> None:
        self._model = model
        self._estimator = estimator
        self._ocp = ocp
        self._u_prev: matrix = matrix(0.0, (model.nu, 1))

    def step(
        self,
        y: matrix,
        D: matrix,
    ) -> Tuple[matrix, matrix, matrix]:
        """
        Execute one MPC step.

        Parameters
        ----------
        y : (l, 1) current measurement vector  (cvxopt column).
        D : (N·p, 1) stacked disturbance forecast  (cvxopt column).

        Returns
        -------
        u     : (m, 1) optimal input for the current step.
        U_seq : (N·m, 1) full optimal input sequence.
        X_seq : (N·n, 1) predicted state trajectory.
        """
        n_u = self._model.nu
        n_d = self._model.nd
        d0 = D[:n_d]
        x_hat = self._estimator.update(y, d0)
        U_seq, X_seq = self._ocp.solve(
            x_hat, D, self._model.x_ref, u_prev=self._u_prev,
        )
        u = U_seq[:n_u]
        self._u_prev = matrix(u)
        self._estimator.record_action(u)
        return u, U_seq, X_seq
