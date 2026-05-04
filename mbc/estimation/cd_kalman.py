"""
Continuous-Discrete Kalman Filter.

Implements the Kalman filter for a linear continuous-discrete stochastic
system following the continuous-time formulation of the CD-EKF (Ph.D. Ch. 7.3),
specialised to a linear system where no Jacobian linearisation is required.

Model
-----
    dx = (A x + B u + E d) dt + G dw(t),   dw(t) ~ N(0, I dt)
    y[k] = C x[k] + v[k],                  v[k] ~ N(0, R)

The filter operates in two steps at each measurement time t_k:

**Prediction** — continuous-time ODE integration over [t_{k-1}, t_k]:

    dx̂/dt  = A_c x̂ + B_c u + E_c d                           (§7.3a)
    dP/dt  = A_c P + P A_cᵀ + G Q_c Gᵀ                       (§7.3b)

Both ODEs are integrated forward from t_{k-1} to t_k with ``n_steps``
forward-Euler sub-steps of size h = dt / n_steps.  Inputs u and
disturbances d are held constant over the interval (zero-order hold).

**Filtering** (measurement update, Joseph stabilised form, §7.8–7.11):

    e_k   = y_k − C x̂[k|k−1]
    R_e   = C P[k|k−1] Cᵀ + R
    K     = P[k|k−1] Cᵀ R_e⁻¹
    x̂[k]  = x̂[k|k−1] + K e_k
    P[k]  = (I − K C) P[k|k−1] (I − K C)ᵀ + K R Kᵀ

The Joseph form guarantees that P remains symmetric positive semi-definite
in finite-precision arithmetic.

Missing observations (M.Sc. thesis, Ch. 5.5) are handled by the ``mask``
argument of :meth:`update`: outputs with ``mask[i] = False`` are excluded
from the measurement update.

Notation (consistent with Ph.D. thesis, Ch. 7.3)
-------------------------------------------------
    n   – state dimension                x ∈ ℝⁿ
    m   – input dimension                u ∈ ℝᵐ
    p   – disturbance dimension          d ∈ ℝᵖ
    l   – output dimension               y ∈ ℝˡ
    Q_c – continuous process-noise cov.  Q_c ∈ ℝᵍˣᵍ
    R   – measurement noise cov.         R ∈ ℝˡˣˡ
    P   – state error covariance         P ∈ ℝⁿˣⁿ
    K   – Kalman gain                    K ∈ ℝⁿˣˡ
    e   – innovation  y − C x̂⁻          e ∈ ℝˡ
"""

from __future__ import annotations

from typing import Optional, List, TYPE_CHECKING

import numpy as np

from .._utils import _np_to_cvx, _any_to_np1d

if TYPE_CHECKING:
    from ..models import LinearContinuousDiscreteModel


class CDKalmanFilter:
    """
    Kalman filter for a linear continuous-discrete stochastic system.

    The prediction step integrates the state ODE (§7.3a) and the matrix
    Riccati ODE (§7.3b) continuously over each sampling interval using
    ``n_steps`` forward-Euler sub-steps.  The system is never discretised;
    inputs are applied as zero-order hold over each sampling interval.

    Parameters
    ----------
    model : LinearContinuousDiscreteModel
        Plant model providing ``A``, ``B``, ``E``, ``G``,
        ``C``, ``Rm``, ``dt``, ``x``, ``nx``, ``nu``,
        ``nd``.
    P0 : cvxopt.matrix (n, n), optional
        Initial state error covariance.  Default: Iₙ.
    n_steps : int, optional
        Number of forward-Euler sub-steps per sampling interval.  Larger
        values give a more accurate ODE integration at the cost of runtime.
        Default: 10.
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        P0: matrix | None = None,
        n_steps: int = 10,
    ) -> None:
        self._model = model
        n = model.nx

        # Continuous-time matrices (numpy, cached for speed)
        self._A_c: np.ndarray = model.A
        self._B_c: np.ndarray = model.B
        self._E_c: np.ndarray = model.E
        G = np.asarray(model.G, dtype=float)
        self._GQcGT: np.ndarray = G @ G.T

        # ODE integration parameters
        self._dt: float = model.dt
        self._n_steps: int = n_steps
        self._h: float = model.dt / n_steps

        # Measurement noise covariance (numpy)
        self._R_np: np.ndarray = model.Rm.copy()

        # State error covariance (numpy internally)
        if P0 is not None:
            rows, cols = P0.size
            self._P_np: np.ndarray = np.array(list(P0), dtype=float).reshape(
                (rows, cols), order="F"
            )
        else:
            self._P_np = np.eye(n)

        # State estimate x̂ (numpy internally)
        self._x_np: np.ndarray = np.array(model.x, dtype=float)

        # Previous input and disturbance (numpy, ZOH over the interval)
        self._u_prev_np: np.ndarray = np.zeros(model.nu)
        self._d_prev_np: np.ndarray = np.zeros(model.nd)

        self._first: bool = True

        # Last Kalman innovation e = y − C x̂⁻
        self._last_innovation_np: Optional[np.ndarray] = None

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Current state estimate x̂[k] ∈ ℝⁿ (1-D array, copy)."""
        return self._x_np.copy()

    @property
    def P(self) -> np.ndarray:
        """Current state error covariance P[k] ∈ ℝⁿˣⁿ (2-D array, copy)."""
        return self._P_np.copy()

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """
        Most recent Kalman innovation e = y − C x̂⁻ as a plain Python list.

        Returns ``None`` until the first filtering step has been completed
        (i.e. until the second call to :meth:`update`).
        """
        if self._last_innovation_np is None:
            return None
        return [float(v) for v in self._last_innovation_np]

    # ── Prediction step (continuous ODE integration) ──────────────────────

    def predict(
        self,
        u_np: np.ndarray,
        d_np: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Time update — integrate state and Riccati ODEs over one sampling
        interval using ``n_steps`` forward-Euler sub-steps.

        Integrates (Ph.D. thesis, §7.3a–b):

            dx̂/dt = A_c x̂ + B_c u + E_c d
            dP/dt = A_c P + P A_cᵀ + G Q_c Gᵀ

        with u and d held constant (ZOH) over [t_{k-1}, t_k].

        Parameters
        ----------
        u_np : (m,) ndarray  — control input applied over the interval.
        d_np : (p,) ndarray  — disturbance over the interval.

        Returns
        -------
        x_pred : (n,) predicted state estimate x̂[k|k−1].
        P_pred : (n, n) predicted error covariance P[k|k−1].
        """
        x = self._x_np.copy()
        P = self._P_np.copy()

        h = self._h
        A = self._A_c
        Bu = self._B_c @ u_np
        Ed = self._E_c @ d_np
        GQG = self._GQcGT

        for _ in range(self._n_steps):
            x_dot = A @ x + Bu + Ed
            P_dot = A @ P + P @ A.T + GQG
            x = x + h * x_dot
            P = P + h * P_dot

        P = (P + P.T) * 0.5

        return x, P

    # ── Filtering step (Joseph stabilised form) ────────────────────────────

    def filter(
        self,
        y_np: np.ndarray,
        x_pred_np: np.ndarray,
        P_pred_np: np.ndarray,
        C_np: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Measurement update (filtering) using the Joseph stabilised form.

        Given the predicted state and covariance, fuses the measurement y[k]
        (Ph.D. thesis, §7.8–7.11):

            R_e = C P[k|k−1] Cᵀ + R
            K   = P[k|k−1] Cᵀ R_e⁻¹
            e   = y − C x̂[k|k−1]
            x̂   = x̂[k|k−1] + K e
            IKC = I − K C
            P   = IKC P[k|k−1] IKCᵀ + K R Kᵀ    (Joseph form, §7.11c)

        Parameters
        ----------
        y_np      : (l,) ndarray  — measurement vector.
        x_pred_np : (n,) ndarray  — predicted state estimate.
        P_pred_np : (n, n) ndarray — predicted covariance.
        C_np      : (l, n) ndarray — output matrix.

        Returns
        -------
        x_hat : (n,) corrected state estimate.
        P     : (n, n) corrected covariance (symmetric, PSD).
        """
        n = P_pred_np.shape[0]
        R_np = self._R_np

        # Innovation covariance  R_e = C P⁻ Cᵀ + R
        R_e = C_np @ P_pred_np @ C_np.T + R_np

        # Kalman gain  K = P⁻ Cᵀ R_e⁻¹  via  R_e Kᵀ = C P⁻
        K = np.linalg.solve(R_e, C_np @ P_pred_np).T  # (n, l)

        # Innovation e = y − C x̂⁻
        e = y_np - C_np @ x_pred_np

        # Corrected state estimate
        x_hat = x_pred_np + K @ e

        # Joseph form:  P = (I−KC) P⁻ (I−KC)ᵀ + K R Kᵀ
        IKC = np.eye(n) - K @ C_np
        P = IKC @ P_pred_np @ IKC.T + K @ R_np @ K.T
        P = (P + P.T) * 0.5

        self._last_innovation_np = e
        return x_hat, P

    # ── Combined update ───────────────────────────────────────────────────

    def update(
        self,
        y,
        d,
        mask: list[bool] | None = None,
    ) -> np.ndarray:
        """
        Assimilate measurement y[k] and return the corrected estimate x̂[k].

        On the first call the state is bootstrapped via the left
        pseudo-inverse of C (reduces to x̂ = y when C = I).  Subsequent
        calls execute the full predict → filter cycle.

        Parameters
        ----------
        y    : (l,) measurement vector (numpy or cvxopt).
        d    : (p,) current disturbance vector (numpy or cvxopt).
        mask : list of bool, length l, optional
            Output availability mask (M.Sc. thesis, Ch. 5.5).  When
            ``mask[i] = False`` output i is excluded from the measurement
            update.  If all entries are ``False`` the update step is skipped
            entirely (prediction-only).  ``None`` uses all outputs.

        Returns
        -------
        x_hat : (n,) corrected state estimate (copy).
        """
        C_np = self._model.Cm.copy()
        y_np = _any_to_np1d(y)
        l = self._model.nym
        n = self._model.nx

        active = list(range(l)) if mask is None else [i for i, m in enumerate(mask) if m]

        if self._first:
            # Bootstrap via minimum-norm solution: x̂ = Cᵀ (C Cᵀ)⁻¹ y
            CCt = C_np @ C_np.T
            alpha = np.linalg.solve(CCt, y_np)
            self._x_np = C_np.T @ alpha
            self._first = False
        else:
            # Continuous ODE prediction using previous u and d (ZOH)
            x_pred_np, P_pred_np = self.predict(self._u_prev_np, self._d_prev_np)

            if not active:
                # All outputs masked — skip measurement update
                self._x_np = x_pred_np
                self._P_np = P_pred_np
            elif len(active) == l:
                # All outputs available — standard update
                x_hat_np, P_np = self.filter(y_np, x_pred_np, P_pred_np, C_np)
                self._x_np = x_hat_np
                self._P_np = P_np
            else:
                # Partial update: restrict C and R to active rows
                C_sub = C_np[np.ix_(active, list(range(n)))]
                R_sub = self._R_np[np.ix_(active, active)]
                y_sub = y_np[active]
                R_orig = self._R_np
                self._R_np = R_sub
                x_hat_np, P_np = self.filter(y_sub, x_pred_np, P_pred_np, C_sub)
                self._R_np = R_orig
                self._x_np = x_hat_np
                self._P_np = P_np

        # Update stored disturbance for next prediction
        self._d_prev_np = _any_to_np1d(d)
        return self._x_np.copy()

    # ── Action recording ──────────────────────────────────────────────────

    def record_action(self, u) -> None:
        """Record the applied control action u[k] for use in the next prediction."""
        self._u_prev_np = _any_to_np1d(u)
