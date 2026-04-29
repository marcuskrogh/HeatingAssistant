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
    P⁻   = A P[k−1] Aᵀ + Q

  **Filtering** (measurement update, Joseph stabilised form):
    S    = C P⁻ Cᵀ + R                       (innovation covariance)
    K    = P⁻ Cᵀ S⁻¹                         (Kalman gain)
    x̂    = x̂⁻ + K (y − C x̂⁻)               (corrected state)
    P    = (I − K C) P⁻ (I − K C)ᵀ + K R Kᵀ (Joseph form)

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
    R   – measurement noise covariance        R ∈ ℝˡˣˡ
    P   – state estimation error covariance   P ∈ ℝⁿˣⁿ
    K   – Kalman gain                         K ∈ ℝⁿˣˡ
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from cvxopt import matrix

from .._utils import _eye, _zeros, _symmetrise

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
    Q : cvxopt.matrix (n, n), optional
        Process noise covariance.  Default: 0.01 · Iₙ.
    R : cvxopt.matrix (l, l), optional
        Measurement noise covariance.  Default: 0.1 · Iˡ.
    P0 : cvxopt.matrix (n, n), optional
        Initial state covariance.  Default: Iₙ.
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
        Q: matrix | None = None,
        R: matrix | None = None,
        P0: matrix | None = None,
    ) -> None:
        self._model = model
        n = model.n_x
        l = model.C.size[0]

        # Noise covariances
        self._Q: matrix = Q if Q is not None else matrix(0.0, (n, n))
        if Q is None:
            for i in range(n):
                self._Q[i, i] = 0.01

        self._R: matrix = R if R is not None else matrix(0.0, (l, l))
        if R is None:
            for i in range(l):
                self._R[i, i] = 0.1

        # State covariance
        self._P: matrix = P0 if P0 is not None else _eye(n)

        # State estimate x̂  (initialised from the model)
        x0 = model.x
        self._x_hat: matrix = matrix(list(x0), (n, 1))

        # Memory for previous input and disturbance
        self._u_prev: matrix = _zeros(model.n_u, 1)
        self._d_prev: matrix = _zeros(model.n_d, 1)

        self._first: bool = True

        # Last Kalman innovation ν = y − C x̂⁻  (set after each filtering step)
        self._last_innovation: Optional[matrix] = None

    # ── Public properties ────────────────────────────────────────────────

    @property
    def x_hat(self) -> matrix:
        """Current state estimate x̂[k] ∈ ℝⁿ (column vector, copy)."""
        return matrix(self._x_hat)

    @property
    def P(self) -> matrix:
        """Current covariance P[k] ∈ ℝⁿˣⁿ (copy)."""
        return matrix(self._P)

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """
        Most recent Kalman innovation ν = y − C x̂⁻ as a plain Python list.

        Returns ``None`` until the first filtering step has been completed
        (i.e. until the second call to :meth:`update`).
        """
        if self._last_innovation is None:
            return None
        n = self._last_innovation.size[0]
        return [float(self._last_innovation[i]) for i in range(n)]

    # ── Prediction step ──────────────────────────────────────────────────

    def predict(
        self,
        A: matrix,
        B: matrix,
        E: matrix,
    ) -> tuple[matrix, matrix]:
        """
        Time update (prediction).

        Propagates the state estimate and covariance one step forward
        using the previous applied input u[k−1] and disturbance d[k−1]:

            x̂⁻  = A x̂ + B u + E d
            P⁻   = A P Aᵀ + Q

        Returns
        -------
        x_pred : (n, 1) predicted state estimate.
        P_pred : (n, n) predicted covariance.
        """
        x_pred = A * self._x_hat + B * self._u_prev + E * self._d_prev
        P_pred = A * self._P * A.T + self._Q
        return x_pred, P_pred

    # ── Filtering step (Joseph form) ─────────────────────────────────────

    def filter(
        self,
        y: matrix,
        x_pred: matrix,
        P_pred: matrix,
        C: matrix,
    ) -> tuple[matrix, matrix]:
        """
        Measurement update (filtering) using the Joseph stabilised form.

        Given the predicted state and covariance from the prediction step,
        fuses the measurement y[k] to produce the corrected estimate:

            S   = C P⁻ Cᵀ + R               (innovation covariance)
            K   = P⁻ Cᵀ S⁻¹                 (Kalman gain)
            x̂   = x̂⁻ + K (y − C x̂⁻)        (corrected state)

            I_KC = I − K C
            P    = I_KC P⁻ I_KCᵀ + K R Kᵀ   (Joseph form)

        Returns
        -------
        x_hat : (n, 1) corrected state estimate.
        P     : (n, n) corrected covariance (symmetric, PSD).
        """
        from cvxopt import lapack
        n = P_pred.size[0]

        # Innovation covariance  S = C P⁻ Cᵀ + R
        S = C * P_pred * C.T + self._R

        # Solve  S Kᵀ = (P⁻ Cᵀ)ᵀ  for Kᵀ  via Cholesky factorisation
        # (S is symmetric positive-definite).  Equivalent to K = P⁻ Cᵀ S⁻¹.
        Kt = matrix(P_pred * C.T)      # n × l, will be overwritten
        S_copy = matrix(S)              # posv overwrites S
        lapack.posv(S_copy, Kt)
        K = Kt                          # n × l

        # Innovation  ν = y − C x̂⁻
        nu = y - C * x_pred

        # Corrected state  x̂ = x̂⁻ + K ν
        x_hat = x_pred + K * nu

        # Joseph form covariance  P = (I−KC) P⁻ (I−KC)ᵀ + K R Kᵀ
        I_n = _eye(n)
        I_KC = I_n - K * C
        P = I_KC * P_pred * I_KC.T + K * self._R * K.T
        P = _symmetrise(P)

        # Store innovation for external inspection (e.g. diagnostics sensors)
        self._last_innovation = matrix(nu)

        return x_hat, P

    # ── Combined update ──────────────────────────────────────────────────

    def update(self, y: matrix, d: matrix) -> matrix:
        """
        Assimilate measurement y[k] and return corrected estimate x̂[k].

        On the first call the state is bootstrapped directly from the
        measurement (left pseudo-inverse of C).  Subsequent calls run
        the full predict → filter cycle.

        Parameters
        ----------
        y : (l, 1) measurement vector.
        d : (p, 1) current disturbance vector.

        Returns
        -------
        x_hat : (n, 1) corrected state estimate (copy).
        """
        C = self._model.C

        if self._first:
            # Bootstrap:  x̂ = C⁺ y  (Moore–Penrose pseudoinverse for C
            # with full column rank; for C = I this reduces to x̂ = y).
            from cvxopt import lapack
            CtC = C.T * C
            Cty = C.T * y
            lapack.posv(CtC, Cty)
            self._x_hat = Cty
            self._first = False
        else:
            # Discretise at previous disturbance
            A, B, E = self._model.discretize(self._d_prev)

            # Prediction step
            x_pred, P_pred = self.predict(A, B, E)

            # Filtering step (Joseph form)
            self._x_hat, self._P = self.filter(y, x_pred, P_pred, C)

        self._d_prev = matrix(d)
        return matrix(self._x_hat)

    # ── Action recording ─────────────────────────────────────────────────

    def record_action(self, u: matrix) -> None:
        """Record the applied control action u[k] for the next prediction."""
        self._u_prev = matrix(u)
