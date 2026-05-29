"""
Continuous-Discrete Kalman Filter for linear continuous-discrete systems.

Specialises the continuous-discrete EKF (ControlToolbox §SDE — *CD-EKF*)
to a linear plant where no Jacobian linearisation is required: ``A`` is
the constant Jacobian of the drift and ``Cm`` is the constant Jacobian of
the measurement function.

Model
-----
    dx(t)  = (A x + B u + E d) dt + G dw(t),     dw ~ N(0, I dt)
    ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],  v ~ N(0, Rm)

Time update over ``[t_{k-1}, t_k]``
-----------------------------------
Forward-Euler integration of the state and Lyapunov ODEs with
``n_steps`` sub-steps of size ``h = dt / n_steps``:

    dx̂_k/dt(t) = A x̂_k(t) + B u + E d
    dP_k/dt(t) = A P_k(t) + P_k(t) Aᵀ + G Gᵀ

Inputs and disturbances are zero-order hold over each sampling interval.

Measurement update at t_k (Joseph form)
---------------------------------------
    e_k = ym_k − Cm x̂_{k|k-1}                   (innovation)
    R_e = Cm P_{k|k-1} Cmᵀ + Rm                  (innovation covariance)
    K_k = P_{k|k-1} Cmᵀ R_e⁻¹                    (Kalman gain)

    x̂_{k|k} = x̂_{k|k-1} + K_k e_k
    P_{k|k} = (I − K_k Cm) P_{k|k-1} (I − K_k Cm)ᵀ + K_k Rm K_kᵀ

The Joseph stabilising form preserves symmetry and positive
semi-definiteness in finite-precision arithmetic.

Missing observations (M.Sc. thesis Ch. 5.5) are handled by the optional
``mask`` argument of :meth:`update`.
"""

from __future__ import annotations

from typing import Optional, List, TYPE_CHECKING

import numpy as np

from .._utils import _any_to_np1d, _any_to_np2d

if TYPE_CHECKING:
    from ..models import LinearContinuousDiscreteModel


class CDKalmanFilter:
    """
    Continuous-discrete Kalman filter for a linear continuous-discrete
    plant (linear specialisation of :class:`~mbc.estimation.ContinuousDiscreteEKF`).

    The filter integrates the state ODE and Lyapunov-type covariance ODE
    continuously over each sampling interval; ``Rm`` is read directly
    from ``model.Rm``.

    Parameters
    ----------
    model : LinearContinuousDiscreteModel
        Linear continuous-discrete plant providing ``A``, ``B``, ``E``,
        ``G``, ``Cm``, ``Rm``, ``dt``, ``x``, ``nx``, ``nu``, ``nd``.
    x0 : (nx,) ndarray, optional
        Initial state estimate ``x̂_{0|0}``.  Defaults to ``np.array(model.x)``.
    P0 : (nx, nx) ndarray, optional
        Initial state error covariance ``P_{0|0}``.  Defaults to ``I_{nx}``.
    n_steps : int, optional
        Forward-Euler sub-steps per sampling interval.  Default: 10.
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        x0: np.ndarray | None = None,
        P0: np.ndarray | None = None,
        n_steps: int = 10,
    ) -> None:
        self._model = model
        nx = model.nx

        # Cache the continuous-time matrices; G Gᵀ is the noise intensity.
        self._A_c: np.ndarray = model.A
        self._B_c: np.ndarray = model.B
        self._E_c: np.ndarray = model.E
        G = np.asarray(model.G, dtype=float)
        self._GGT: np.ndarray = G @ G.T

        # ODE integration parameters
        self._dt: float = model.dt
        self._n_steps: int = int(n_steps)
        self._h: float = model.dt / n_steps

        # State estimate and covariance
        self._x_np: np.ndarray = (
            np.asarray(x0, dtype=float).copy() if x0 is not None
            else np.array(list(model.x), dtype=float)
        )
        self._P_np: np.ndarray = (
            _any_to_np2d(P0).copy() if P0 is not None else np.eye(nx)
        )

        # Last innovation
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
        """Most recent innovation ``e_k = ym_k − Cm x̂_{k|k-1}``."""
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
        Time update — forward-Euler integration of the state and
        Lyapunov-type covariance ODEs over one sampling interval with
        zero-order hold on ``u`` and ``d``.

            dx̂/dt = A x̂ + B u + E d
            dP/dt = A P + P Aᵀ + G Gᵀ

        Parameters
        ----------
        u : (nu,) ndarray  — input applied over the just-completed interval.
        d : (nd,) ndarray  — disturbance applied over the same interval.

        Returns
        -------
        x_pred : (nx,) predicted state estimate x̂_{k|k-1}.
        P_pred : (nx, nx) predicted covariance P_{k|k-1}.
        """
        u_np = _any_to_np1d(u)
        d_np = _any_to_np1d(d)

        x = self._x_np.copy()
        P = self._P_np.copy()
        h = self._h
        A = self._A_c
        Bu = self._B_c @ u_np
        Ed = self._E_c @ d_np
        GGT = self._GGT

        for _ in range(self._n_steps):
            x_dot = A @ x + Bu + Ed
            P_dot = A @ P + P @ A.T + GGT
            x = x + h * x_dot
            P = P + h * P_dot
        P = 0.5 * (P + P.T)

        self._x_np = x
        self._P_np = P
        return x.copy(), P.copy()

    def update(
        self,
        ym: np.ndarray,
        mask: list[bool] | np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Measurement update at time ``t_k`` (Joseph form).

        Parameters
        ----------
        ym : (nym,) ndarray
            Measurement at time ``t_k``.
        mask : (nym,) bool ndarray or list, optional
            See :meth:`KalmanFilter.update`.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        model = self._model
        nx = model.nx
        Cm = model.Cm
        Rm = model.Rm
        ym_np = _any_to_np1d(ym)

        if mask is not None:
            active = np.where(np.asarray(mask, dtype=bool))[0]
            if len(active) == 0:
                return self._x_np.copy(), self._P_np.copy()
            Cm = Cm[active, :]
            Rm = Rm[np.ix_(active, active)]
            ym_np = ym_np[active]

        x_pred = self._x_np
        P_pred = self._P_np

        e = ym_np - Cm @ x_pred
        R_e = Cm @ P_pred @ Cm.T + Rm

        Kt = np.linalg.solve(R_e, Cm @ P_pred)
        K = Kt.T

        x_new = x_pred + K @ e

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
        previously-applied ``u`` and ``d`` (ZOH), then fuses the
        measurement ``ym``.

        Parameters
        ----------
        ym : (nym,) ndarray  — measurement at time ``t_k``.
        u  : (nu,) ndarray   — input applied over the previous interval.
        d  : (nd,) ndarray   — disturbance applied over the previous interval.
        p  : ignored          — for interface compatibility (LTI: no parameters).
        t  : ignored          — for interface compatibility (LTI: time-invariant).
        mask : (nym,) bool ndarray, optional — see :meth:`update`.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        self.predict(u, d)
        return self.update(ym, mask=mask)
