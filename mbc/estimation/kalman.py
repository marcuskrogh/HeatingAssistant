"""
Discrete-time Kalman Filter (state estimator).

Implements the standard discrete-time Kalman filter for a linear
discrete-time system of the form:

    x[k+1] = A x[k] + B u[k] + E d[k]       (state transition)
    y[k]   = C x[k]                          (observation)

Algorithm
---------
The filter alternates between two steps each time a new measurement
y[k] arrives:

  **Prediction** (time update):
    x̂⁻  = A x̂[k−1] + B u[k−1] + E d[k−1]
    P⁻   = A P[k−1] Aᵀ + Q                     (standard form, G = I)
    P⁻   = A P[k−1] Aᵀ + G Q Gᵀ               (noise-separated form, M.Sc. Ch. 5.4)

  **Filtering** (measurement update, Joseph stabilised form):
    S    = C P⁻ Cᵀ + R                       (innovation covariance)
    K    = P⁻ Cᵀ S⁻¹                         (Kalman gain)
    x̂    = x̂⁻ + K (y − C x̂⁻)               (corrected state)
    P    = (I − K C) P⁻ (I − K C)ᵀ + K R Kᵀ (Joseph form)

    If a boolean mask is supplied, outputs with mask[i]=False are treated
    as missing and the measurement update is skipped for those channels
    (M.Sc. Ch. 5.5).  When all outputs are masked, the full update is
    skipped (prediction-only step).

The Joseph form  P = (I−KC) P⁻ (I−KC)ᵀ + K R Kᵀ  guarantees that
the posterior covariance remains symmetric positive-semidefinite even
in the presence of finite-precision arithmetic, unlike the conventional
form  P = (I−KC) P⁻.

Notation
--------
    n   – state dimension                     x ∈ ℝⁿ
    m   – input dimension                     u ∈ ℝᵐ
    p   – disturbance dimension               d ∈ ℝᵖ
    l   – output dimension                    y ∈ ℝˡ
    Q   – process noise covariance            Q ∈ ℝⁿˣⁿ
    G   – noise input matrix                  G ∈ ℝⁿˣⁿ  (default I)
    R   – measurement noise covariance        R ∈ ℝˡˣˡ
    P   – state estimation error covariance   P ∈ ℝⁿˣⁿ
    K   – Kalman gain                         K ∈ ℝⁿˣˡ
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

import numpy as np

from .._utils import _any_to_np1d, _any_to_np2d

if TYPE_CHECKING:
    from ..models import LinearDiscreteModel


# ── Kalman Filter ────────────────────────────────────────────────────────


class KalmanFilter:
    """
    Discrete-time Kalman filter with Joseph stabilised covariance update.

    Parameters
    ----------
    model : LinearDiscreteModel
        Linear discrete-time plant providing dimensions, output matrix C,
        initial state x, and the ``discretize(d)`` method.
    Q : numpy or cvxopt matrix (n, n), optional
        Process noise covariance.  Default: 0.01 · Iₙ.
    R : numpy or cvxopt matrix (l, l), optional
        Measurement noise covariance.  Default: 0.1 · Iˡ.
    P0 : numpy or cvxopt matrix (n, n), optional
        Initial state covariance.  Default: Iₙ.
    noise_matrix : numpy or cvxopt matrix (n, n), optional
        Noise input matrix G so that the prediction covariance step becomes
        ``P⁻ = A P Aᵀ + G Q Gᵀ`` (M.Sc. Ch. 5.4).  When ``None`` (default),
        the standard form ``P⁻ = A P Aᵀ + Q`` is used (equivalent to G = I).
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
        Q=None,
        R=None,
        P0=None,
        noise_matrix=None,
    ) -> None:
        self._model = model
        n = model.nx
        l = model.Cm.shape[0]  # model.Cm is numpy

        # Noise covariances (numpy)
        self._Q_np: np.ndarray = _any_to_np2d(Q) if Q is not None else 0.01 * np.eye(n)
        self._R_np: np.ndarray = _any_to_np2d(R) if R is not None else 0.1 * np.eye(l)

        # Noise input matrix G (M.Sc. Ch. 5.4); None means standard form
        self._G_np: np.ndarray | None = _any_to_np2d(noise_matrix) if noise_matrix is not None else None

        # State error covariance (numpy)
        self._P_np: np.ndarray = _any_to_np2d(P0) if P0 is not None else np.eye(n)

        # State estimate x̂ (numpy; initialised from the model)
        self._x_np: np.ndarray = np.array(list(model.x), dtype=float)

        # Memory for previous input and disturbance (numpy)
        self._u_prev_np: np.ndarray = np.zeros(model.nu)
        self._d_prev_np: np.ndarray = np.zeros(model.nd)

        self._first: bool = True

        # Last Kalman innovation ν = y − C x̂⁻  (set after each filtering step)
        self._last_innovation_np: Optional[np.ndarray] = None

    # ── Public properties ────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Current state estimate x̂[k] ∈ ℝⁿ (1-D array, copy)."""
        return self._x_np.copy()

    @property
    def P(self) -> np.ndarray:
        """Current covariance P[k] ∈ ℝⁿˣⁿ (2-D array, copy)."""
        return self._P_np.copy()

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """
        Most recent Kalman innovation ν = y − C x̂⁻ as a plain Python list.

        Returns ``None`` until the first filtering step has been completed
        (i.e. until the second call to :meth:`update`).
        """
        if self._last_innovation_np is None:
            return None
        return [float(v) for v in self._last_innovation_np]

    # ── Prediction step ──────────────────────────────────────────────────

    def predict(
        self,
        A_np: np.ndarray,
        B_np: np.ndarray,
        E_np: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Time update (prediction).

        Propagates the state estimate and covariance one step forward
        using the previous applied input u[k−1] and disturbance d[k−1]:

            x̂⁻  = A x̂ + B u + E d
            P⁻   = A P Aᵀ + Q

        Parameters
        ----------
        A_np : (n, n) ndarray  — state transition matrix.
        B_np : (n, m) ndarray  — input matrix.
        E_np : (n, p) ndarray  — disturbance matrix.

        Returns
        -------
        x_pred : (n,) predicted state estimate.
        P_pred : (n, n) predicted covariance.
        """
        x_pred_np = (
            A_np @ self._x_np
            + B_np @ self._u_prev_np
            + E_np @ self._d_prev_np
        )
        if self._G_np is None:
            P_pred_np = A_np @ self._P_np @ A_np.T + self._Q_np
        else:
            P_pred_np = A_np @ self._P_np @ A_np.T + self._G_np @ self._Q_np @ self._G_np.T

        return x_pred_np, P_pred_np

    # ── Filtering step (Joseph form) ─────────────────────────────────────

    def filter(
        self,
        y_np: np.ndarray,
        x_pred_np: np.ndarray,
        P_pred_np: np.ndarray,
        C_np: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Measurement update (filtering) using the Joseph stabilised form.

        Given the predicted state and covariance from the prediction step,
        fuses the measurement y[k] to produce the corrected estimate:

            S   = C P⁻ Cᵀ + R               (innovation covariance)
            K   = P⁻ Cᵀ S⁻¹                 (Kalman gain)
            x̂   = x̂⁻ + K (y − C x̂⁻)        (corrected state)

            I_KC = I − K C
            P    = I_KC P⁻ I_KCᵀ + K R Kᵀ   (Joseph form)

        Parameters
        ----------
        y_np      : (l,) ndarray  — measurement vector.
        x_pred_np : (n,) ndarray  — predicted state estimate.
        P_pred_np : (n, n) ndarray — predicted covariance.
        C_np      : (l, n) ndarray — output matrix.

        Returns
        -------
        x_hat_np : (n,) corrected state estimate.
        P_np     : (n, n) corrected covariance (symmetric, PSD).
        """
        n = P_pred_np.shape[0]
        R_np = self._R_np

        # Innovation covariance  S = C P⁻ Cᵀ + R
        S_np = C_np @ P_pred_np @ C_np.T + R_np

        # Kalman gain  K = P⁻ Cᵀ S⁻¹  via  S Kᵀ = C P⁻
        K_np = np.linalg.solve(S_np, C_np @ P_pred_np).T  # (n, l)

        # Innovation  ν = y − C x̂⁻
        nu_np = y_np - C_np @ x_pred_np

        # Corrected state  x̂ = x̂⁻ + K ν
        x_hat_np = x_pred_np + K_np @ nu_np

        # Joseph form  P = (I−KC) P⁻ (I−KC)ᵀ + K R Kᵀ
        I_KC = np.eye(n) - K_np @ C_np
        P_np = I_KC @ P_pred_np @ I_KC.T + K_np @ R_np @ K_np.T
        P_np = (P_np + P_np.T) * 0.5

        self._last_innovation_np = nu_np
        return x_hat_np, P_np

    # ── Combined update ──────────────────────────────────────────────────

    def update(
        self,
        y,
        d,
        mask: list[bool] | None = None,
    ) -> np.ndarray:
        """
        Assimilate measurement y[k] and return corrected estimate x̂[k].

        On the first call the state is bootstrapped directly from the
        measurement (left pseudo-inverse of C).  Subsequent calls run
        the full predict → filter cycle.

        Parameters
        ----------
        y : (l,) numpy or cvxopt — measurement vector.
        d : (p,) numpy or cvxopt — current disturbance vector.
        mask : list of bool, length l, optional
            When provided, only outputs where ``mask[i] is True`` are used
            in the measurement update.  If all entries are ``False`` the
            measurement update is skipped entirely (prediction-only step,
            M.Sc. Ch. 5.5).  ``None`` (default) uses all outputs.

        Returns
        -------
        x_hat : (n,) corrected state estimate (copy).
        """
        C_np = self._model.Cm  # model.Cm is numpy
        y_np = _any_to_np1d(y)
        l = C_np.shape[0]
        n = self._model.nx

        if mask is not None:
            active = [i for i, m in enumerate(mask) if m]
        else:
            active = list(range(l))

        if self._first:
            # Bootstrap:  x̂ = C⁺ y  (Moore-Penrose pseudoinverse, minimum-norm)
            # lstsq handles both overdetermined (nym ≥ nx) and underdetermined
            # (nym < nx) cases without requiring C to have full column rank.
            self._x_np, _, _, _ = np.linalg.lstsq(C_np, y_np, rcond=None)
            self._first = False
        else:
            # Use constant discrete-time matrices (no LPV scheduling)
            A_np = self._model.Ad
            B_np = self._model.Bd
            E_np = self._model.Ed

            x_pred_np, P_pred_np = self.predict(A_np, B_np, E_np)

            if not active:
                # All outputs masked — skip measurement update (M.Sc. Ch. 5.5)
                self._x_np = x_pred_np
                self._P_np = P_pred_np
            elif len(active) == l:
                # All outputs available — full update
                x_hat_np, P_np = self.filter(y_np, x_pred_np, P_pred_np, C_np)
                self._x_np = x_hat_np
                self._P_np = P_np
            else:
                # Partial update: restrict C, R, y to active rows
                C_sub = C_np[np.ix_(active, list(range(n)))]
                R_sub = self._R_np[np.ix_(active, active)]
                y_sub = y_np[active]
                R_orig = self._R_np
                self._R_np = R_sub
                x_hat_np, P_np = self.filter(y_sub, x_pred_np, P_pred_np, C_sub)
                self._R_np = R_orig
                self._x_np = x_hat_np
                self._P_np = P_np

        self._d_prev_np = _any_to_np1d(d)
        return self._x_np.copy()

    # ── Action recording ─────────────────────────────────────────────────

    def record_action(self, u) -> None:
        """Record the applied control action u[k] for the next prediction."""
        self._u_prev_np = _any_to_np1d(u)
