"""
Discrete-time Kalman Filter.

Implements the standard discrete-time Kalman filter for a linear
discrete-time stochastic system

    x[k+1] = Ad x[k] + Bd u[k] + Ed d[k] + Gd w[k],   w[k] ~ N(0, Qd)
    ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],       v[k] ~ N(0, Rm)

The filter notation matches the continuous-discrete state-estimation
documents (ControlToolbox §SDE / §SDAE state estimation): innovation
``e_k``, innovation covariance ``R_e``, Kalman gain ``K``, measurement
output matrix ``Cm``, process-noise covariance ``Qd``, measurement-noise
covariance ``Rm``.

Time update over ``[t_{k-1}, t_k]``
-----------------------------------
    x̂_{k|k-1}  = Ad x̂_{k-1|k-1} + Bd u[k−1] + Ed d[k−1]
    P_{k|k-1}  = Ad P_{k-1|k-1} Adᵀ + Gd Qd Gdᵀ

Inputs and disturbances are zero-order hold over each interval.

Measurement update at t_k (Joseph form)
---------------------------------------
    ŷ^m_{k|k-1} = Cm x̂_{k|k-1}
    e_k         = ym_k − ŷ^m_{k|k-1}                    (innovation)
    R_e         = Cm P_{k|k-1} Cmᵀ + Rm                  (innovation covariance)
    K_k         = P_{k|k-1} Cmᵀ R_e⁻¹                    (Kalman gain)

    x̂_{k|k}    = x̂_{k|k-1} + K_k e_k
    P_{k|k}    = (I − K_k Cm) P_{k|k-1} (I − K_k Cm)ᵀ + K_k Rm K_kᵀ        (Joseph)

The Joseph stabilising form preserves symmetry and positive
semi-definiteness of ``P_{k|k}`` in finite-precision arithmetic.

Missing observations (M.Sc. thesis Ch. 5.5) are handled by the optional
``mask`` argument: outputs with ``mask[i] = False`` are excluded from the
measurement update.  When all entries are ``False`` the update step is
skipped (prediction-only).
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

import numpy as np

from .._utils import _any_to_np1d, _any_to_np2d

if TYPE_CHECKING:
    from ..models import LinearDiscreteModel


class KalmanFilter:
    """
    Discrete-time Kalman filter with Joseph-stabilised covariance update.

    The filter reads ``Qd``, ``Rm`` and ``Gd`` directly from the supplied
    ``LinearDiscreteModel``; the constructor only takes the initial state
    estimate and covariance.  This matches the continuous-discrete EKF
    interface (:class:`~mbc.estimation.ContinuousDiscreteEKF`) and removes
    redundant tuning parameters.

    Parameters
    ----------
    model : LinearDiscreteModel
        Plant model providing ``Ad``, ``Bd``, ``Ed``, ``Gd``, ``Cm``,
        ``Qd``, ``Rm`` and ``predict_offset``.
    x0 : (nx,) ndarray, optional
        Initial state estimate ``x̂_{0|0}``.  Defaults to ``np.array(model.x)``.
    P0 : (nx, nx) ndarray, optional
        Initial state error covariance ``P_{0|0}``.  Defaults to ``I_{nx}``.
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
        x0: np.ndarray | None = None,
        P0: np.ndarray | None = None,
    ) -> None:
        self._model = model
        nx = model.nx

        # State estimate and covariance
        self._x_np: np.ndarray = (
            np.asarray(x0, dtype=float).copy() if x0 is not None
            else np.array(list(model.x), dtype=float)
        )
        self._P_np: np.ndarray = (
            _any_to_np2d(P0).copy() if P0 is not None
            else np.eye(nx)
        )

        # Last innovation (set after each measurement update)
        self._last_innovation_np: Optional[np.ndarray] = None

    # ── Public properties ────────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Current state estimate x̂ ∈ ℝⁿˣ (copy)."""
        return self._x_np.copy()

    @property
    def P(self) -> np.ndarray:
        """Current state error covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        return self._P_np.copy()

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """
        Most recent innovation ``e_k = ym_k − Cm x̂_{k|k-1}`` as a plain
        Python list, or ``None`` until the first measurement update has
        been performed.
        """
        if self._last_innovation_np is None:
            return None
        return [float(v) for v in self._last_innovation_np]

    # ── Filter steps ─────────────────────────────────────────────────────────

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Time update: propagate the state estimate and covariance one
        sampling interval forward using the input ``u`` and disturbance
        ``d`` applied (ZOH) over ``[t_{k-1}, t_k]``.

            x̂_{k|k-1} = Ad x̂ + Bd u + Ed d + offset(d)
            P_{k|k-1} = Ad P Adᵀ + Gd Qd Gdᵀ

        Parameters
        ----------
        u : (nu,) ndarray  — input applied over the just-completed interval.
        d : (nd,) ndarray  — disturbance applied over the same interval.

        Returns
        -------
        x_pred : (nx,) predicted state estimate x̂_{k|k-1}.
        P_pred : (nx, nx) predicted covariance P_{k|k-1}.
        """
        model = self._model
        Ad = model.Ad
        Bd = model.Bd
        Ed = model.Ed
        Gd = model.Gd
        Qd = model.Qd

        u_np = _any_to_np1d(u)
        d_np = _any_to_np1d(d)

        x_pred = (
            Ad @ self._x_np
            + Bd @ u_np
            + Ed @ d_np
            + model.predict_offset(d_np)
        )
        P_pred = Ad @ self._P_np @ Ad.T + Gd @ Qd @ Gd.T

        self._x_np = x_pred
        self._P_np = P_pred
        return x_pred.copy(), P_pred.copy()

    def update(
        self,
        ym: np.ndarray,
        mask: list[bool] | np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Measurement update at time ``t_k`` using the Joseph stabilising
        form (ControlToolbox §SDE-CD-EKF — *Measurement Update*).

            e_k    = ym_k − Cm x̂_{k|k-1}
            R_e    = Cm P_{k|k-1} Cmᵀ + Rm
            K_k    = P_{k|k-1} Cmᵀ R_e⁻¹
            x̂_{k|k} = x̂_{k|k-1} + K_k e_k
            P_{k|k} = (I − K_k Cm) P_{k|k-1} (I − K_k Cm)ᵀ + K_k Rm K_kᵀ

        Parameters
        ----------
        ym : (nym,) ndarray
            Measurement vector at time ``t_k``.
        mask : (nym,) bool ndarray or list, optional
            When provided, only outputs where ``mask[i]`` is ``True`` are
            used in the update.  If every entry is ``False`` the update is
            skipped (prediction-only step).  ``None`` (default) uses all
            outputs.

        Returns
        -------
        x_hat : (nx,) corrected state estimate x̂_{k|k}.
        P     : (nx, nx) corrected covariance P_{k|k}.
        """
        model = self._model
        nx = model.nx
        Cm = model.Cm
        Rm = model.Rm
        ym_np = _any_to_np1d(ym)

        if mask is not None:
            active = np.where(np.asarray(mask, dtype=bool))[0]
            if len(active) == 0:
                # All channels masked — keep prior, no measurement assimilation.
                return self._x_np.copy(), self._P_np.copy()
            Cm = Cm[active, :]
            Rm = Rm[np.ix_(active, active)]
            ym_np = ym_np[active]

        x_pred = self._x_np
        P_pred = self._P_np

        # Innovation and its covariance
        e = ym_np - Cm @ x_pred
        R_e = Cm @ P_pred @ Cm.T + Rm

        # Kalman gain  K = P Cmᵀ R_e⁻¹  via  R_e Kᵀ = Cm P
        Kt = np.linalg.solve(R_e, Cm @ P_pred)
        K = Kt.T

        x_new = x_pred + K @ e

        # Joseph form
        IKC = np.eye(nx) - K @ Cm
        P_new = IKC @ P_pred @ IKC.T + K @ Rm @ K.T
        P_new = 0.5 * (P_new + P_new.T)

        self._last_innovation_np = e.copy()
        self._x_np = x_new
        self._P_np = P_new
        return x_new.copy(), P_new.copy()

    def step(
        self,
        ym: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray | None = None,
        t: float | None = None,
        mask: list[bool] | np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Combined time + measurement update.

        Propagates the estimate from ``t_{k-1}`` to ``t_k`` using the
        previously-applied ``u`` and ``d`` (ZOH over the just-completed
        interval), then fuses the measurement ``ym``.

        Parameters
        ----------
        ym : (nym,) ndarray  — measurement at time ``t_k``.
        u  : (nu,) ndarray   — input applied over the previous interval.
        d  : (nd,) ndarray   — disturbance applied over the previous interval.
        p  : ignored          — accepted for interface compatibility with
                                continuous-discrete estimators (LTI: no parameters).
        t  : ignored          — accepted for interface compatibility (LTI: time-invariant).
        mask : (nym,) bool ndarray, optional — see :meth:`update`.

        Returns
        -------
        x_hat : (nx,) corrected state estimate x̂_{k|k}.
        P     : (nx, nx) corrected covariance P_{k|k}.
        """
        self.predict(u, d)
        return self.update(ym, mask=mask)
