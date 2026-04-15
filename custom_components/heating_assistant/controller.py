"""
Model Predictive Controller.

Generic Framework
-----------------
LinearDiscreteModel (abstract)
    x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k],   y[k] = C x[k]

    x ∈ ℝⁿ  state,  u ∈ ℝᵐ  input (ZOH over each dt),
    d ∈ ℝᵖ  disturbance,    y ∈ ℝˡ  output.

    Matrices A, B, E may depend on d (LPV); C is time-invariant.

KalmanFilter  (see state_estimator.py)
    Discrete-time Kalman filter with Joseph stabilised covariance update.

OptimalControlProblem  (see optimal_control.py)
    Receding-horizon QP with setpoint tracking, Δu penalty, hard input
    box-constraints, and soft output box-constraints.  Solved via
    cvxopt.solvers.qp.

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
from cvxopt import matrix

from .thermal_model import HouseModel
from .heat_sources import HeatSource
from .solar_model import room_solar_gains
from .state_estimator import KalmanFilter
from .optimal_control import OptimalControlProblem


# ── Backward-compatible alias ────────────────────────────────────────────
StateEstimator = KalmanFilter


# ============================================================
# Helpers
# ============================================================

def _np_to_cvx(a: np.ndarray) -> matrix:
    """Convert a numpy array to a cvxopt dense matrix (column-major)."""
    if a.ndim == 1:
        return matrix(a.tolist(), (len(a), 1), tc="d")
    rows, cols = a.shape
    return matrix(a.tolist(), (rows, cols), tc="d")


def _cvx_to_np(m: matrix) -> np.ndarray:
    """Convert a cvxopt dense matrix to a 2-D numpy array."""
    rows, cols = m.size
    # cvxopt stores matrices in column-major (Fortran) order
    return np.array(list(m), dtype=float).reshape((rows, cols), order="F")


def _cvx_col_to_np(m: matrix) -> np.ndarray:
    """Convert a cvxopt column vector to a 1-D numpy array."""
    return np.array(list(m), dtype=float)


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


# ============================================================
# Generic framework
# ============================================================

class LinearDiscreteModel(ABC):
    """
    Abstract interface for a linear discrete-time system:

      x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k],   y[k] = C x[k]

    Matrices A, B, E may depend on the current disturbance d (LPV model);
    C is time-invariant.  Subclasses implement ``discretize`` to return
    the ZOH-discretised matrices (as cvxopt dense matrices) for a given d.
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
    def C(self) -> matrix:
        """Output matrix C ∈ ℝˡˣⁿ (cvxopt dense)."""

    @property
    @abstractmethod
    def x(self) -> list[float]:
        """Current state x as a plain list of floats."""

    @x.setter
    @abstractmethod
    def x(self, val: list[float]) -> None:
        ...

    @property
    @abstractmethod
    def x_ref(self) -> matrix:
        """Reference / setpoint x_ref ∈ ℝⁿ (cvxopt column vector)."""

    @property
    @abstractmethod
    def u_bounds(self) -> Tuple[matrix, matrix]:
        """Box constraint on inputs (u_min, u_max), each (m, 1) cvxopt."""

    @abstractmethod
    def discretize(self, d: matrix) -> Tuple[matrix, matrix, matrix]:
        """
        Return ZOH-discretised matrices (A_d, B_d, E_d) as cvxopt dense
        matrices for disturbance column vector d.
        """


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
        model: LinearDiscreteModel,
        estimator: KalmanFilter,
        ocp: OptimalControlProblem,
    ) -> None:
        self._model = model
        self._estimator = estimator
        self._ocp = ocp
        self._u_prev: matrix = matrix(0.0, (model.n_u, 1))

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
        n_u = self._model.n_u
        n_d = self._model.n_d
        d0 = D[:n_d]
        x_hat = self._estimator.update(y, d0)
        U_seq, X_seq = self._ocp.solve(
            x_hat, D, self._model.x_ref, u_prev=self._u_prev,
        )
        u = U_seq[:n_u]
        self._u_prev = matrix(u)
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
        self._G_d[:, 0] = model._B_ext / self._C_cap
        for i in range(n):
            self._G_d[i, 1 + i] = 1.0 / self._C_cap[i]

        # Full-state observation: C = Iₙ (cvxopt)
        self._C_mat: matrix = matrix(0.0, (n, n))
        for i in range(n):
            self._C_mat[i, i] = 1.0

    # ── LinearDiscreteModel interface ────────────────────────────────────

    @property
    def n_x(self) -> int:
        return len(self._room_list)

    @property
    def n_u(self) -> int:
        return len(self._sources)

    @property
    def n_d(self) -> int:
        return 1 + len(self._room_list)

    @property
    def C(self) -> matrix:
        return self._C_mat

    @property
    def x(self) -> list[float]:
        return [self._model.rooms[name].temperature
                for name in self._room_list]

    @x.setter
    def x(self, val: list[float]) -> None:
        for i, name in enumerate(self._room_list):
            self._model.rooms[name].temperature = float(val[i])

    @property
    def x_ref(self) -> matrix:
        n = self.n_x
        ref = matrix(0.0, (n, 1))
        for i, name in enumerate(self._room_list):
            ref[i] = self._model.rooms[name].setpoint
        return ref

    @property
    def u_bounds(self) -> Tuple[matrix, matrix]:
        m = self.n_u
        return matrix(0.0, (m, 1)), matrix(1.0, (m, 1))

    def discretize(self, d: matrix) -> Tuple[matrix, matrix, matrix]:
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
            G_u[i, j] = src.thermal_power(1.0, outdoor_temp) / self._C_cap[i]

        A_d_np, B_d_np = _zoh(self._F, G_u, self._dt)
        _, E_d_np = _zoh(self._F, self._G_d, self._dt)
        return _np_to_cvx(A_d_np), _np_to_cvx(B_d_np), _np_to_cvx(E_d_np)

    # ── Convenience helpers for the application layer ────────────────────

    def disturbance_vector(
        self,
        outdoor_temp: float,
        solar_gains: Dict[str, float],
    ) -> matrix:
        """Pack outdoor temperature and per-room solar gains into d ∈ ℝᵖ."""
        p = self.n_d
        d = matrix(0.0, (p, 1))
        d[0] = outdoor_temp
        for name, gain in solar_gains.items():
            if name in self._room_idx:
                d[1 + self._room_idx[name]] = gain
        return d

    def heating_powers(
        self,
        u_vec: matrix,
        outdoor_temp: float,
    ) -> Dict[str, float]:
        """Convert fractions u to per-room total thermal power [W]."""
        powers: Dict[str, float] = {name: 0.0 for name in self._room_list}
        for j, src in enumerate(self._sources):
            powers[src.room] += src.thermal_power(float(u_vec[j]), outdoor_temp)
        return powers


class HeatingMPCController:
    """
    Application facade for house-heating MPC.

    Builds a HouseThermalSystem, KalmanFilter, OptimalControlProblem, and
    the generic MPCController, then provides the coordinator-facing API:

      actions = controller.compute(outdoor_temp, solar_gains, now, outdoor_forecast)

    Forecasts
    ---------
    Outdoor temperature: uses weather forecast if provided by the coordinator;
        otherwise falls back to persistence (constant at the current measurement).
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
    constraint_offset : symmetric offset δ around the setpoint for soft
                        output constraints:  SP − δ ≤ y ≤ SP + δ.
                        Default: 2.0 °C.
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
        constraint_offset: float = 2.0,
    ) -> None:
        self._sources = heat_sources
        self._horizon = horizon
        self._dt = dt
        self._latitude = latitude
        self._longitude = longitude

        if smoothing_weight < 0.0:
            raise ValueError(
                f"smoothing_weight must be >= 0; got {smoothing_weight}"
            )

        # Build the linear discrete-time model
        self._system = HouseThermalSystem(model, heat_sources, dt)

        # Cost matrices (cvxopt dense)
        n_x, n_u = self._system.n_x, self._system.n_u

        Q = matrix(0.0, (n_x, n_x))
        for i in range(n_x):
            Q[i, i] = 1.0

        R = matrix(0.0, (n_u, n_u))
        for i in range(n_u):
            R[i, i] = energy_weight

        S: matrix | None = None
        if smoothing_weight > 0.0:
            S = matrix(0.0, (n_u, n_u))
            for i in range(n_u):
                S[i, i] = smoothing_weight

        self._constraint_offset = constraint_offset

        # Assemble generic MPC
        estimator = KalmanFilter(self._system)
        ocp = OptimalControlProblem(
            self._system, horizon, Q, R, S=S,
            y_offset=constraint_offset,
        )
        self._mpc = MPCController(self._system, estimator, ocp)

        # Visualisation data (populated after each compute())
        self._predictions:      List[Dict[str, float]] = []
        self._outdoor_forecast: List[float]            = []
        self._solar_forecast:   List[Dict[str, float]] = []
        self._heating_schedule: List[Dict[str, float]] = []

    # ── Visualisation properties ─────────────────────────────────────────

    @property
    def constraint_offset(self) -> float:
        """Symmetric offset δ around the setpoint for soft output constraints [°C]."""
        return self._constraint_offset

    @property
    def predictions(self) -> List[Dict[str, float]]:
        """Latest predicted temperature trajectory [{room: °C}, …]."""
        return self._predictions

    @property
    def outdoor_forecast(self) -> List[float]:
        """Outdoor temperature forecast used in the last compute() (weather or persistence)."""
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
        outdoor_forecast: Optional[List[float]] = None,
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
        outdoor_forecast : list of float, optional
            External outdoor temperature forecast for each horizon step
            (e.g. from a weather integration).  If provided, replaces
            the default persistence forecast.  Must have length >= horizon.

        Returns
        -------
        dict
            ``{source_name: setpoint_fraction}`` where fraction ∈ [0, 1].
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)
        if solar_gains is None:
            solar_gains = self._current_solar(now)

        N = self._horizon
        n_x = self._system.n_x
        n_u = self._system.n_u
        n_d = self._system.n_d

        # ── Build disturbance forecast D ∈ ℝ^{N·p × 1} ──────────────────
        if outdoor_forecast is not None and len(outdoor_forecast) >= N:
            outdoor_seq = list(outdoor_forecast[:N])
        else:
            outdoor_seq = self._forecast_outdoor(outdoor_temp)
        solar_seq = self._forecast_solar(now)

        D = matrix(0.0, (N * n_d, 1))
        for k in range(N):
            dk = self._system.disturbance_vector(outdoor_seq[k], solar_seq[k])
            D[k * n_d:(k + 1) * n_d] = dk

        # Store forecasts for visualisation
        self._outdoor_forecast = list(outdoor_seq)
        self._solar_forecast = [dict(s) for s in solar_seq]

        # ── Current measurement y = room temperatures ────────────────────
        x_vals = self._system.x
        y = matrix(x_vals, (n_x, 1))

        # ── MPC step ─────────────────────────────────────────────────────
        u_opt, U_seq, X_seq = self._mpc.step(y, D)

        # ── Apply actions to heat sources ────────────────────────────────
        actions: Dict[str, float] = {}
        for j, src in enumerate(self._sources):
            frac = max(0.0, min(1.0, float(u_opt[j])))
            actions[src.name] = frac
            src.set_power(frac, outdoor_temp)

        # ── Store visualisation data ─────────────────────────────────────
        room_list = self._system._room_list
        self._predictions = []
        for k in range(N):
            step_pred: Dict[str, float] = {}
            for i, name in enumerate(room_list):
                step_pred[name] = float(X_seq[k * n_x + i])
            self._predictions.append(step_pred)

        self._heating_schedule = []
        for k in range(N):
            u_k = U_seq[k * n_u:(k + 1) * n_u]
            self._heating_schedule.append(
                self._system.heating_powers(u_k, outdoor_seq[k])
            )

        return actions

    # ── Disturbance forecasts ────────────────────────────────────────────

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
