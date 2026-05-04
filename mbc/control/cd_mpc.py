"""
Model Predictive Controller for linear continuous-discrete systems.

``CDMPCController`` composes a ``CDKalmanFilter`` and a
``CDOptimalControlProblem`` and implements the receding-horizon policy
for a ``LinearContinuousDiscreteModel``.

Control loop (M.Sc. thesis, Ch. 5)
------------------------------------
At each measurement time t_k:

  1. **Estimate**   x̂[k] ← estimator.update(y[k], d[k])
  2. **Optimise**   U*   ← ocp.solve(x̂[k], D, x_ref)
  3. **Apply**      u[k]  = U*[0:m]           (receding horizon)
  4. **Record**     estimator.record_action(u[k])

The ZOH discretisation of the continuous dynamics is performed inside
``CDKalmanFilter.update`` (via ``model.discretize``) and inside
``CDOptimalControlProblem.solve`` (same call).  Both use the sampling
interval ``model.dt`` implicitly.

Notation
--------
    n   – state dimension          x ∈ ℝⁿ
    m   – input dimension          u ∈ ℝᵐ
    p   – disturbance dimension    d ∈ ℝᵖ
    l   – output dimension         y ∈ ℝˡ
    N   – prediction horizon
"""

from __future__ import annotations

from typing import Tuple, TYPE_CHECKING

import numpy as np
from cvxopt import matrix

from .._utils import _zeros, _np_to_cvx
from ..estimation.cd_kalman import CDKalmanFilter
from .cd_ocp import CDOptimalControlProblem

if TYPE_CHECKING:
    from ..models import LinearContinuousDiscreteModel


class CDMPCController:
    """
    Model predictive controller for a linear continuous-discrete system.

    Composes a ``CDKalmanFilter`` and a ``CDOptimalControlProblem`` into a
    single receding-horizon controller.

    Parameters
    ----------
    model     : LinearContinuousDiscreteModel
        Plant model providing ``nu``, ``nd``, ``x_ref``, ``discretize``,
        and ``discretize_noise``.
    estimator : CDKalmanFilter
        State estimator.
    ocp       : CDOptimalControlProblem
        Optimal control problem (QP solver).
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        estimator: CDKalmanFilter,
        ocp: CDOptimalControlProblem,
    ) -> None:
        self._model = model
        self._estimator = estimator
        self._ocp = ocp
        self._u_prev: matrix = _zeros(model.nu, 1)

    def step(
        self,
        y: matrix,
        D: matrix,
    ) -> Tuple[matrix, matrix, matrix]:
        """
        Execute one MPC step.

        Runs the estimate → optimise → apply cycle:

          1. ``estimator.update(y, d[0])`` → x̂[k]
          2. ``ocp.solve(x̂[k], D, x_ref)`` → (U*, X*)
          3. Return u[k] = U*[0:m]
          4. ``estimator.record_action(u[k])``

        Parameters
        ----------
        y : (l, 1) cvxopt column — current measurement y[k].
        D : (N·p, 1) cvxopt column — stacked disturbance forecast
            [d[0]; d[1]; …; d[N−1]] over the prediction horizon.

        Returns
        -------
        u     : (m, 1) cvxopt column — optimal input u[k].
        U_seq : (N·m, 1) cvxopt column — full optimal input sequence.
        X_seq : (N·n, 1) cvxopt column — predicted state trajectory x[1], …, x[N].
        """
        n_u = self._model.nu
        n_d = self._model.nd
        d0 = D[:n_d]

        x_hat = self._estimator.update(y, d0)
        x_ref_cvx = _np_to_cvx(
            np.asarray(self._model.x_ref, dtype=float).reshape(-1, 1)
        )
        U_seq, X_seq = self._ocp.solve(
            x_hat, D, x_ref_cvx, u_prev=self._u_prev
        )
        u = U_seq[:n_u]
        self._u_prev = matrix(u)
        self._estimator.record_action(u)
        return u, U_seq, X_seq
