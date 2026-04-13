"""
Model Predictive Controller.

Generic Framework
-----------------
LinearDiscreteModel (abstract)
    x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k],   y[k] = C x[k]

    x ∈ ℝⁿ  state,  u ∈ ℝᵐ  input (ZOH over each dt),
    d ∈ ℝᵖ  disturbance,    y ∈ ℝˡ  output.

    Matrices A, B, E may depend on d (LPV); C is time-invariant.

KalmanFilter
    Discrete-time Kalman filter (optimal state estimator):

      Prediction:
        x̂⁻[k] = A x̂[k-1] + B u[k-1] + E d[k-1]
        P⁻[k]  = A P[k-1] Aᵀ + Q_w

      Update (correction):
        S[k]   = C P⁻[k] Cᵀ + R_v              (innovation covariance)
        K[k]   = P⁻[k] Cᵀ S[k]⁻¹              (Kalman gain)
        x̂[k]  = x̂⁻[k] + K[k] (y[k] − C x̂⁻[k])  (state estimate)
        P[k]   = (I − K[k] C) P⁻[k]            (posterior covariance)

    Q_w models process noise (unmodelled disturbances, model mismatch).
    R_v models measurement noise (sensor accuracy).
    The gain K[k] is recomputed at every step from the current covariance,
    giving the minimum-variance estimate under Gaussian noise assumptions.

OptimalControlProblem
    Receding-horizon quadratic regulator:

      min_{U}   Σₖ₌₀ᴺ⁻¹ ‖x[k+1] − r‖²_Q + ‖u[k]‖²_R + ‖x[N] − r‖²_P
      s.t.      x[k+1] = A x[k] + B u[k] + E d[k],   x[0] = x̂
                u_min ≤ u[k] ≤ u_max

    Lifted to U-space (batch formulation):

      X = Ψ x₀ + Γ U + Λ D,
      J = Uᵀ H U + 2 fᵀ U + const,
      H = Γᵀ Q̄ Γ + R̄,   f = Γᵀ Q̄ (Ψ x₀ + Λ D − r̄)

    Solved with projected gradient descent on the box-constrained QP.

MPCController (generic)
    Composes KalmanFilter + OptimalControlProblem for any LinearDiscreteModel:

      1. Estimate  x̂[k] ← estimator.update(y[k], d[k])
      2. Optimise  U*    ← ocp.solve(x̂[k], D, r)
      3. Apply     u[k]  = U*[0]                 (receding horizon)
      4. Record    estimator.record_action(u[k])

House-Heating Application
--------------------------
HouseThermalSystem(LinearDiscreteModel)
    Wraps HouseModel + list of HeatSource objects.

    State        x = [T₁, …, Tₙ]            (room temperatures, °C)
    Input        u = [f₁, …, fₘ]            (setpoint fractions, ∈ [0, 1])
    Disturbance  d = [T_out, Q_sol,1, …, Q_sol,n]  (°C and W)
    Output       y = x                      (full-state observation, C = Iₙ)

    The continuous-time RC model is ZOH-discretised at each step:

      Ṫ = F T + G_u u + G_d d
      F   = C_cap⁻¹ Aᶜ           (state matrix, n × n)
      G_u = C_cap⁻¹ Φ(T_out)     (input matrix, n × m; LPV for heat-pump COP)
      G_d = C_cap⁻¹ [b_ext | Iₙ] (disturbance matrix, n × (1+n))

      A_d = expm(F dt)
      B_d = F⁻¹(A_d − I) G_u
      E_d = F⁻¹(A_d − I) G_d

    On-off sources (e.g. connected to a switch entity) are treated as
    continuous actuators; the fraction u ∈ [0, 1] is interpreted as the
    duty cycle within each sampling interval dt.

HeatingMPCController
    Application facade: builds HouseThermalSystem + MPCController, adds
    solar/outdoor forecasting, applies source set-points, and exposes the
    visualisation properties consumed by the coordinator.

    Public API (unchanged from the previous implementation):
        controller = HeatingMPCController(model, heat_sources, ...)
        actions    = controller.compute(outdoor_temp, solar_gains, now)
        # controller.predictions, .outdoor_forecast, .solar_forecast,
        # .heating_schedule
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from .thermal_model import HouseModel
from .heat_sources import HeatSource
from .solar_model import room_solar_gains


# ============================================================
# Helpers
# ============================================================

def _expm(M: np.ndarray) -> np.ndarray:
    """
    Matrix exponential via eigendecomposition.

    For a real matrix M with distinct eigenvalues this is exact.
    Thermal state matrices have real, distinct, negative eigenvalues
    (stable RC circuit), so this approach is numerically well-conditioned.
    """
    vals, vecs = np.linalg.eig(M)
    return np.real(vecs @ np.diag(np.exp(vals)) @ np.linalg.inv(vecs))


def _zoh(Fc: np.ndarray, Gc: np.ndarray, dt: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    Zero-order hold discretisation of ẋ = Fc x + Gc u.

    Returns (A_d, B_d) such that x[k+1] = A_d x[k] + B_d u[k].

    Using:  A_d = expm(Fc dt),   B_d = Fc⁻¹ (A_d − I) Gc
    """
    n = Fc.shape[0]
    A_d = _expm(Fc * dt)
    B_d = np.linalg.solve(Fc, (A_d - np.eye(n)) @ Gc)
    return A_d, B_d


def _solve_qp_box(
    H: np.ndarray,
    f: np.ndarray,
    lb: np.ndarray,
    ub: np.ndarray,
    n_iter: int = 300,
    tol: float = 1e-8,
) -> np.ndarray:
    """
    Solve the box-constrained QP  min_U ½ Uᵀ H U + fᵀ U  s.t. lb ≤ U ≤ ub
    using projected gradient descent.

    Step size α = 1 / λ_max(H) guarantees convergence.
    Initialised from the clamped unconstrained minimiser.
    """
    try:
        U = np.clip(np.linalg.solve(H, -f), lb, ub)
    except np.linalg.LinAlgError:
        U = np.clip(np.zeros_like(f), lb, ub)

    lam_max = float(np.linalg.eigvalsh(H).max())
    alpha = 1.0 / max(lam_max, 1e-8)

    for _ in range(n_iter):
        U_new = np.clip(U - alpha * (H @ U + f), lb, ub)
        if np.linalg.norm(U_new - U, np.inf) < tol:
            break
        U = U_new
    return U


# ============================================================
# Generic framework
# ============================================================

class LinearDiscreteModel(ABC):
    """
    Abstract interface for a linear discrete-time system:

      x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k],   y[k] = C x[k]

    Matrices A, B, E may depend on the current disturbance d (LPV model);
    C is time-invariant. Subclasses implement ``discretize`` to return
    the ZOH-discretised matrices for a given d.
    """

    @property
    @abstractmethod
    def n_x(self) -> int:
        """State dimension n."""

    @property
    @abstractmethod
    def n_u(self) -> int:
        """Input dimension m."""

    @property
    @abstractmethod
    def n_d(self) -> int:
        """Disturbance dimension p."""

    @property
    @abstractmethod
    def C(self) -> np.ndarray:
        """Output matrix C ∈ ℝˡˣⁿ."""

    @property
    @abstractmethod
    def x(self) -> np.ndarray:
        """Current state x ∈ ℝⁿ."""

    @x.setter
    @abstractmethod
    def x(self, val: np.ndarray) -> None:
        ...

    @property
    @abstractmethod
    def x_ref(self) -> np.ndarray:
        """Reference / setpoint x_ref ∈ ℝⁿ."""

    @property
    @abstractmethod
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """Box constraint on inputs (u_min, u_max), each ∈ ℝᵐ."""

    @abstractmethod
    def discretize(self, d: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Return ZOH-discretised matrices (A_d, B_d, E_d) for disturbance d.

        Allows LPV models to update B_d with the current operating point.
        """


class KalmanFilter:
    """
    Discrete-time Kalman filter (optimal state estimator).

      Prediction:
        x̂⁻[k] = A x̂[k-1] + B u[k-1] + E d[k-1]
        P⁻[k]  = A P[k-1] Aᵀ + Q_w

      Update (correction):
        S[k]   = C P⁻[k] Cᵀ + R_v              (innovation covariance)
        K[k]   = P⁻[k] Cᵀ S[k]⁻¹              (Kalman gain)
        x̂[k]  = x̂⁻[k] + K[k] (y[k] − C x̂⁻[k])  (state estimate)
        P[k]   = (I − K[k] C) P⁻[k]            (posterior covariance)

    Parameters
    ----------
    model : LinearDiscreteModel
    Q_w : (n_x, n_x) process noise covariance, optional.
        Models unmodelled disturbances and model mismatch.
        Default: 0.01 · Iₙ  (low process uncertainty).
    R_v : (n_y, n_y) measurement noise covariance, optional.
        Models sensor noise.
        Default: 0.1 · Iₗ  (moderate sensor uncertainty).
    P0 : (n_x, n_x) initial state covariance, optional.
        Default: Iₙ.
    """

    def __init__(
        self,
        model: LinearDiscreteModel,
        Q_w: Optional[np.ndarray] = None,
        R_v: Optional[np.ndarray] = None,
        P0: Optional[np.ndarray] = None,
    ) -> None:
        self._model = model
        n_x = model.n_x
        n_y = model.C.shape[0]

        self._Q_w: np.ndarray = Q_w if Q_w is not None else 0.01 * np.eye(n_x)
        self._R_v: np.ndarray = R_v if R_v is not None else 0.1 * np.eye(n_y)
        self._P: np.ndarray = P0 if P0 is not None else np.eye(n_x)

        self._x_hat: np.ndarray = model.x.copy()
        self._u_prev: np.ndarray = np.zeros(model.n_u)
        self._d_prev: np.ndarray = np.zeros(model.n_d)
        self._first: bool = True

    @property
    def x_hat(self) -> np.ndarray:
        """Current state estimate x̂[k]."""
        return self._x_hat.copy()

    @property
    def P(self) -> np.ndarray:
        """Current state covariance P[k]."""
        return self._P.copy()

    def update(self, y: np.ndarray, d: np.ndarray) -> np.ndarray:
        """
        Assimilate measurement y[k] and return corrected estimate x̂[k].

        On the first call the state is bootstrapped from the measurement
        to avoid a spurious prediction step.  Subsequent calls run the
        full predict–update cycle with covariance propagation.
        """
        C = self._model.C

        if self._first:
            # Bootstrap: x̂ = Cᵀ(CCᵀ)⁻¹ y  (left inverse of C)
            self._x_hat = np.linalg.solve(C @ C.T, C).T @ y
            self._first = False
        else:
            # ── Prediction step ──────────────────────────────────────────
            A, B, E = self._model.discretize(self._d_prev)
            x_pred = A @ self._x_hat + B @ self._u_prev + E @ self._d_prev
            P_pred = A @ self._P @ A.T + self._Q_w

            # ── Update step ──────────────────────────────────────────────
            S = C @ P_pred @ C.T + self._R_v         # innovation covariance
            K = P_pred @ C.T @ np.linalg.inv(S)      # Kalman gain
            innovation = y - C @ x_pred
            self._x_hat = x_pred + K @ innovation
            self._P = (np.eye(self._model.n_x) - K @ C) @ P_pred

        self._d_prev = d.copy()
        return self._x_hat.copy()

    def record_action(self, u: np.ndarray) -> None:
        """Record the applied action u[k] for use in the next prediction."""
        self._u_prev = u.copy()


# Keep backward-compatible alias
StateEstimator = KalmanFilter


class OptimalControlProblem:
    """
    Receding-horizon quadratic regulator (batch QP formulation).

    Cost:
      J(U) = Σₖ₌₀ᴺ⁻¹ ‖x[k+1] − r‖²_Q + ‖u[k]‖²_R + ‖x[N] − r‖²_P

    Batch form (X = Ψ x₀ + Γ U + Λ D):
      J(U) = Uᵀ H U + 2 fᵀ U + const
      H = Γᵀ Q̄ Γ + R̄,   f = Γᵀ Q̄ (Ψ x₀ + Λ D − r̄)

    Solved with projected gradient on the box constraint u_min ≤ u ≤ u_max.

    Parameters
    ----------
    model : LinearDiscreteModel
    N     : prediction horizon.
    Q     : (n_x, n_x) stage tracking cost.
    R     : (n_u, n_u) input cost.
    P     : (n_x, n_x) terminal cost; defaults to Q.
    S     : (n_u, n_u) input rate-of-change cost; defaults to None (no Δu
            penalty).  When provided the cost function includes an additional
            term  Σ ‖u[k] − u[k−1]‖²_S  which penalises rapid changes in the
            control input, resulting in smoother actuator behaviour.
    """

    def __init__(
        self,
        model: LinearDiscreteModel,
        N: int,
        Q: np.ndarray,
        R: np.ndarray,
        P: Optional[np.ndarray] = None,
        S: Optional[np.ndarray] = None,
    ) -> None:
        self._model = model
        self._N = N
        self._Q = Q
        self._R = R
        self._P = P if P is not None else Q.copy()
        self._S = S  # rate-of-change penalty (Δu cost)

    def solve(
        self,
        x0: np.ndarray,
        D: np.ndarray,
        x_ref: np.ndarray,
        u_prev: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Solve the OCP starting from x0.

        Parameters
        ----------
        x0    : (n_x,) current state estimate x̂.
        D     : (N, n_d) disturbance forecast.
        x_ref : (n_x,) reference (repeated for all horizon steps).
        u_prev : (n_u,) previous applied input (for Δu penalty).
                 Defaults to zeros if not provided.

        Returns
        -------
        U : (N, n_u) optimal input sequence (first row is applied).
        X : (N, n_x) predicted state trajectory x[1], …, x[N].
        """
        N = self._N
        n_x, n_u, n_d = self._model.n_x, self._model.n_u, self._model.n_d

        # LPV: linearise B at the current disturbance d[0]
        A, B, E = self._model.discretize(D[0])

        # ── Powers of A: A_pow[k] = Aᵏ ─────────────────────────────────
        A_pow = [np.eye(n_x)]
        for _ in range(N):
            A_pow.append(A @ A_pow[-1])

        # ── Prediction matrices ──────────────────────────────────────────
        # x[k+1] = A^{k+1} x₀ + Σⱼ₌₀ᵏ A^{k−j} (B u[j] + E d[j])
        Psi    = np.vstack([A_pow[k + 1] for k in range(N)])  # (N·n, n)
        Gamma  = np.zeros((N * n_x, N * n_u))                 # (N·n, N·m)
        Lambda = np.zeros((N * n_x, N * n_d))                 # (N·n, N·p)

        for k in range(N):
            for j in range(k + 1):
                row = slice(k * n_x, (k + 1) * n_x)
                Gamma [row, j * n_u:(j + 1) * n_u] = A_pow[k - j] @ B
                Lambda[row, j * n_d:(j + 1) * n_d] = A_pow[k - j] @ E

        # ── Block-diagonal cost matrices (terminal block uses P) ─────────
        Q_bar = np.kron(np.eye(N), self._Q)
        Q_bar[(N - 1) * n_x:, (N - 1) * n_x:] = self._P
        R_bar = np.kron(np.eye(N), self._R)

        # ── QP matrices ──────────────────────────────────────────────────
        E_free = Psi @ x0 + Lambda @ D.flatten() - np.tile(x_ref, N)
        H = Gamma.T @ Q_bar @ Gamma + R_bar
        f = Gamma.T @ Q_bar @ E_free

        # ── Rate-of-change (Δu) penalty ──────────────────────────────────
        # Adds  Σ ‖u[k] − u[k−1]‖²_S  to discourage rapid input changes.
        #
        # ΔU = D_diff @ U + d0,  where d0 = [−u_prev, 0, …, 0]ᵀ
        #
        # D_diff is the block first-difference matrix:
        #   [ I                ]
        #   [-I   I            ]
        #   [    -I   I        ]
        #   [         ...   I  ]
        #
        # Cost:  ΔUᵀ S̄ ΔU  →  H += D_diffᵀ S̄ D_diff,  f += D_diffᵀ S̄ d0
        if self._S is not None:
            if u_prev is None:
                u_prev = np.zeros(n_u)

            # Build block first-difference matrix D_diff  (N·m × N·m)
            D_diff = np.zeros((N * n_u, N * n_u))
            I_u = np.eye(n_u)
            for k in range(N):
                D_diff[k * n_u:(k + 1) * n_u, k * n_u:(k + 1) * n_u] = I_u
                if k > 0:
                    D_diff[k * n_u:(k + 1) * n_u, (k - 1) * n_u:k * n_u] = -I_u

            # d0 = [-u_prev, 0, …, 0]  (shift term for first difference)
            d0 = np.zeros(N * n_u)
            d0[:n_u] = -u_prev

            S_bar = np.kron(np.eye(N), self._S)
            H += D_diff.T @ S_bar @ D_diff
            f += D_diff.T @ S_bar @ d0

        # ── Projected gradient on box constraint ─────────────────────────
        u_min, u_max = self._model.u_bounds
        U_flat = _solve_qp_box(H, f, np.tile(u_min, N), np.tile(u_max, N))

        # ── Predicted trajectory ─────────────────────────────────────────
        X_flat = Psi @ x0 + Gamma @ U_flat + Lambda @ D.flatten()

        return U_flat.reshape(N, n_u), X_flat.reshape(N, n_x)


class MPCController:
    """
    Generic model predictive controller.

    Composes a KalmanFilter and an OptimalControlProblem for any
    LinearDiscreteModel and implements the receding-horizon policy:

      1. Estimate  x̂[k] ← estimator.update(y[k], d[k])
      2. Optimise  U*    ← ocp.solve(x̂[k], D[k:k+N], r)
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
        model: LinearDiscreteModel,
        estimator: KalmanFilter,
        ocp: OptimalControlProblem,
    ) -> None:
        self._model = model
        self._estimator = estimator
        self._ocp = ocp
        self._u_prev: np.ndarray = np.zeros(model.n_u)

    def step(
        self,
        y: np.ndarray,
        D: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Execute one MPC step.

        Parameters
        ----------
        y : (n_y,) current measurement vector.
        D : (N, n_d) disturbance forecast over the horizon.

        Returns
        -------
        u     : (n_u,) optimal input for the current step.
        U_seq : (N, n_u) full optimal input sequence.
        X_seq : (N, n_x) predicted state trajectory.
        """
        x_hat = self._estimator.update(y, D[0])
        U_seq, X_seq = self._ocp.solve(x_hat, D, self._model.x_ref, u_prev=self._u_prev)
        u = U_seq[0]
        self._u_prev = u.copy()
        self._estimator.record_action(u)
        return u, U_seq, X_seq


# ============================================================
# House-heating application
# ============================================================

class HouseThermalSystem(LinearDiscreteModel):
    """
    House thermal model as a linear discrete-time system.

    Continuous-time RC circuit per room i:

      Cᵢ Ṫᵢ = Σⱼ∈adj(i) (Tⱼ − Tᵢ)/Rᵢⱼ + (T_out − Tᵢ)/Rᵢ,ext
              + Q_heat,i + Q_sol,i

    In matrix form: Ṫ = F T + G_u(T_out) u + G_d d

      F        = C_cap⁻¹ Aᶜ              (n × n, time-invariant)
      G_u[:,j] = φⱼ(T_out) / Cᵢ         (n × m, LPV via heat-pump COP)
      G_d      = C_cap⁻¹ [b_ext | Iₙ]   (n × (1+n), time-invariant)
      d        = [T_out, Q_sol,1, …, Q_sol,n]

    ZOH discretisation (exact for piecewise-constant inputs):
      A_d = expm(F dt),   B_d = F⁻¹(A_d − I) G_u,   E_d = F⁻¹(A_d − I) G_d

    On-off sources are modelled with a duty-cycle relaxation: the MPC
    optimises the continuous fraction u ∈ [0, 1], representing the
    fraction of dt for which the source is active.  The coordinator
    maps this fraction to on/off commands for switch-type entities.

    Parameters
    ----------
    model   : HouseModel
    sources : list of HeatSource (index j → source j)
    dt      : sampling interval [s]
    """

    def __init__(
        self,
        model: HouseModel,
        sources: List[HeatSource],
        dt: float,
    ) -> None:
        self._model = model
        self._sources = sources
        self._dt = dt
        self._room_list: List[str] = model.room_names
        self._room_idx: Dict[str, int] = {
            name: i for i, name in enumerate(self._room_list)
        }
        n = len(self._room_list)

        # Capacitance vector C_cap[i] = Cᵢ  [J/K]
        self._C_cap = np.array(
            [model.rooms[name].thermal_mass for name in self._room_list]
        )

        # Continuous state matrix F = C_cap⁻¹ Aᶜ
        self._F: np.ndarray = model._A / self._C_cap[:, np.newaxis]

        # Continuous disturbance matrix G_d = C_cap⁻¹ [b_ext | Iₙ]
        self._G_d: np.ndarray = np.zeros((n, 1 + n))
        self._G_d[:, 0] = model._B_ext / self._C_cap          # outdoor temp
        for i in range(n):
            self._G_d[i, 1 + i] = 1.0 / self._C_cap[i]       # solar gain room i

        # Full-state observation: C = Iₙ
        self._C_mat: np.ndarray = np.eye(n)

    # ── LinearDiscreteModel interface ────────────────────────────────────

    @property
    def n_x(self) -> int:
        return len(self._room_list)

    @property
    def n_u(self) -> int:
        return len(self._sources)

    @property
    def n_d(self) -> int:
        return 1 + len(self._room_list)   # T_out + n solar gains

    @property
    def C(self) -> np.ndarray:
        return self._C_mat

    @property
    def x(self) -> np.ndarray:
        return np.array(
            [self._model.rooms[name].temperature for name in self._room_list]
        )

    @x.setter
    def x(self, val: np.ndarray) -> None:
        for i, name in enumerate(self._room_list):
            self._model.rooms[name].temperature = float(val[i])

    @property
    def x_ref(self) -> np.ndarray:
        return np.array(
            [self._model.rooms[name].setpoint for name in self._room_list]
        )

    @property
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        return np.zeros(self.n_u), np.ones(self.n_u)

    def discretize(self, d: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        ZOH-discretise the RC model at outdoor temperature d[0].

        The input matrix G_u depends on T_out because heat-pump COP is
        temperature-dependent (LPV update at each controller step).
        """
        outdoor_temp = float(d[0])
        n, m = self.n_x, self.n_u

        G_u = np.zeros((n, m))
        for j, src in enumerate(self._sources):
            i = self._room_idx[src.room]
            # Maximum thermal power per unit fraction at current T_out
            G_u[i, j] = src.thermal_power(1.0, outdoor_temp) / self._C_cap[i]

        A_d, B_d = _zoh(self._F, G_u, self._dt)
        _, E_d    = _zoh(self._F, self._G_d, self._dt)
        return A_d, B_d, E_d

    # ── Convenience helpers for the application layer ────────────────────

    def disturbance_vector(
        self,
        outdoor_temp: float,
        solar_gains: Dict[str, float],
    ) -> np.ndarray:
        """Pack outdoor temperature and per-room solar gains into d ∈ ℝᵖ."""
        d = np.zeros(self.n_d)
        d[0] = outdoor_temp
        for name, gain in solar_gains.items():
            if name in self._room_idx:
                d[1 + self._room_idx[name]] = gain
        return d

    def heating_powers(
        self,
        u: np.ndarray,
        outdoor_temp: float,
    ) -> Dict[str, float]:
        """Convert fractions u to per-room total thermal power [W]."""
        powers: Dict[str, float] = {name: 0.0 for name in self._room_list}
        for j, src in enumerate(self._sources):
            powers[src.room] += src.thermal_power(float(u[j]), outdoor_temp)
        return powers


class HeatingMPCController:
    """
    Application facade for house-heating MPC.

    Builds a HouseThermalSystem, KalmanFilter, OptimalControlProblem, and
    the generic MPCController, then provides the coordinator-facing API:

      actions = controller.compute(outdoor_temp, solar_gains, now)

    Forecasts
    ---------
    Outdoor temperature: persistence (constant at the current measurement).
    Solar gains: computed from the solar geometry model for each horizon step.

    Parameters
    ----------
    model         : HouseModel
    heat_sources  : list of HeatSource
    horizon       : prediction horizon N (number of time steps)
    dt            : sampling interval [s]
    latitude      : site latitude [°]
    longitude     : site longitude [°]
    energy_weight : scalar weight on ‖u‖² in the cost (favours energy saving)
    smoothing_weight : scalar weight on ‖Δu‖² in the cost (penalises rapid
                       input changes for smoother actuator behaviour).
                       Set to 0.0 to disable.
    """

    def __init__(
        self,
        model: HouseModel,
        heat_sources: List[HeatSource],
        horizon: int = 6,
        dt: float = 900.0,
        latitude: float = 55.0,
        longitude: float = 12.0,
        energy_weight: float = 0.01,
        smoothing_weight: float = 0.1,
    ) -> None:
        self._sources = heat_sources
        self._horizon = horizon
        self._dt = dt
        self._latitude = latitude
        self._longitude = longitude

        # Build the linear discrete-time model
        self._system = HouseThermalSystem(model, heat_sources, dt)

        # Cost matrices: Q = I (tracking), R = energy_weight·I, P = Q (terminal)
        # S = smoothing_weight·I (rate-of-change penalty on Δu)
        n_x, n_u = self._system.n_x, self._system.n_u
        Q = np.eye(n_x)
        R = energy_weight * np.eye(n_u)
        S = smoothing_weight * np.eye(n_u) if smoothing_weight > 0.0 else None

        # Assemble generic MPC
        estimator = KalmanFilter(self._system)
        ocp       = OptimalControlProblem(self._system, horizon, Q, R, S=S)
        self._mpc = MPCController(self._system, estimator, ocp)

        # Visualisation data (populated after each compute())
        self._predictions:      List[Dict[str, float]] = []
        self._outdoor_forecast: List[float]            = []
        self._solar_forecast:   List[Dict[str, float]] = []
        self._heating_schedule: List[Dict[str, float]] = []

    # ── Visualisation properties ─────────────────────────────────────────

    @property
    def predictions(self) -> List[Dict[str, float]]:
        """Latest predicted temperature trajectory [{room: °C}, …]."""
        return self._predictions

    @property
    def outdoor_forecast(self) -> List[float]:
        """Outdoor temperature forecast used in the last compute()."""
        return self._outdoor_forecast

    @property
    def solar_forecast(self) -> List[Dict[str, float]]:
        """Solar gain forecast used in the last compute()."""
        return self._solar_forecast

    @property
    def heating_schedule(self) -> List[Dict[str, float]]:
        """Planned heating power schedule from the last compute()."""
        return self._heating_schedule

    # ── Main entry point ─────────────────────────────────────────────────

    def compute(
        self,
        outdoor_temp: float,
        solar_gains: Optional[Dict[str, float]] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """
        Compute optimal control actions for the current time step.

        Parameters
        ----------
        outdoor_temp : float
            Current outdoor temperature [°C].
        solar_gains  : dict, optional
            Pre-computed solar gains {room: W}.  If None, computed from
            the solar model using ``now`` and the stored lat/lon.
        now : datetime, optional
            Current time (UTC).  Required when solar_gains is None.

        Returns
        -------
        dict
            ``{source_name: setpoint_fraction}`` where fraction ∈ [0, 1].
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)
        if solar_gains is None:
            solar_gains = self._current_solar(now)

        # ── Build disturbance forecast D ∈ ℝ^(N × p) ────────────────────
        outdoor_seq = self._forecast_outdoor(outdoor_temp)
        solar_seq   = self._forecast_solar(now)

        D = np.vstack([
            self._system.disturbance_vector(outdoor_seq[k], solar_seq[k])
            for k in range(self._horizon)
        ])                                              # (N, n_d)

        # Store forecasts for visualisation
        self._outdoor_forecast = list(outdoor_seq)
        self._solar_forecast   = [dict(s) for s in solar_seq]

        # ── Current measurement y = room temperatures ─────────────────────
        y = self._system.x                              # (n_x,)

        # ── MPC step ──────────────────────────────────────────────────────
        u_opt, U_seq, X_seq = self._mpc.step(y, D)

        # ── Apply actions to heat sources ─────────────────────────────────
        actions: Dict[str, float] = {}
        for j, src in enumerate(self._sources):
            frac = float(np.clip(u_opt[j], 0.0, 1.0))
            actions[src.name] = frac
            src.set_power(frac, outdoor_temp)

        # ── Store visualisation data ──────────────────────────────────────
        room_list = self._system._room_list
        self._predictions = [
            {name: float(X_seq[k, i]) for i, name in enumerate(room_list)}
            for k in range(self._horizon)
        ]
        self._heating_schedule = [
            self._system.heating_powers(U_seq[k], outdoor_seq[k])
            for k in range(self._horizon)
        ]

        return actions

    # ── Disturbance forecasts ─────────────────────────────────────────────

    def _forecast_outdoor(self, current: float) -> List[float]:
        """Persistence forecast: outdoor temperature constant over horizon."""
        return [current] * self._horizon

    def _forecast_solar(self, now: datetime) -> List[Dict[str, float]]:
        """Solar gain forecast using the geometric solar model."""
        schedules = []
        for k in range(self._horizon):
            t = now + timedelta(seconds=self._dt * k)
            schedules.append({
                name: room_solar_gains(
                    self._system._model.rooms[name].windows,
                    t,
                    self._latitude,
                    self._longitude,
                )
                for name in self._system._room_list
            })
        return schedules

    def _current_solar(self, now: datetime) -> Dict[str, float]:
        """Current-step solar gains for all rooms."""
        return {
            name: room_solar_gains(
                self._system._model.rooms[name].windows,
                now,
                self._latitude,
                self._longitude,
            )
            for name in self._system._room_list
        }
