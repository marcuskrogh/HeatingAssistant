"""
Model Predictive Controller — House-Heating Application (Nonlinear CD-NMPC).

The house thermal model is formulated as a nonlinear continuous-discrete SDE
and solved using a CD-EKF + CDTrackingOptimalControlProblem from the ``mbc``
toolbox.  No discretisation, no LPV, no linear approximations.

House-Heating Application
--------------------------
HouseThermalSDE(ContinuousDiscreteModel)
    Wraps HouseModel + list of HeatSource objects as a nonlinear
    continuous-discrete SDE.

    State        x = [T₁, …, Tₙ, b₁, …, bₙ]       (room temperatures + integrated model-mismatch offsets, °C)
    Input        u = [f₁, …, fₘ]                   (setpoint fractions, ∈ [0, 1])
    Disturbance  d = [T_out, Q_sol,1, …, Q_sol,n]  (°C and W)
    Output       z = [T₁, …, Tₙ]                   (controlled output, physical temperatures)
                 ym = [T₁+b₁, …, Tₙ+bₙ]            (effective output incl. offset state)

    Itô SDE:
        dx(t) = f(x, u, d, p, t) dt + σ_w I dw(t),  dw ~ N(0, I dt)
        ym(tₖ) = x(tₖ) + v(tₖ),                      v ~ N(0, σ_v² I)

    Drift:
        f(x, u, d, p, t) = F x + G_u(d₀) u + G_d d
        F        = C_cap⁻¹ Aᶜ              (n × n, structural)
        G_u[:,j] = φⱼ(d₀) / C_cap[rⱼ]    (n × m, nonlinear via heat-pump COP)
        G_d      = C_cap⁻¹ [b_ext | Iₙ]   (n × (1+n), structural)
        d₀       = d[0] = T_out

    The nonlinearity arises from the heat-pump COP which depends on the
    outdoor temperature d₀.  The drift is affine in (x, u) for a fixed d,
    but the coefficient G_u depends on d nonlinearly.

HeatingMPCController
    Application facade: builds HouseThermalSDE + ContinuousDiscreteEKF +
    CDTrackingOptimalControlProblem, adds solar/outdoor forecasting, applies
    source set-points, and exposes the visualisation properties consumed by
    the coordinator.

    Public API:
        controller = HeatingMPCController(model, heat_sources, ...)
        actions    = controller.compute(outdoor_temp, solar_gains, now)
        # controller.predictions, .outdoor_forecast, .solar_forecast,
        # .heating_schedule
"""

from __future__ import annotations

import inspect
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .thermal_model import HouseModel
from .heat_sources import HeatSource
from .solar_model import room_solar_gains


def _select_cloud_for_step(
    cloud_forecast: Optional[List[float]], k: int,
) -> Optional[float]:
    """Pick the cloud-cover fraction for horizon step k from a forecast list.

    Returns ``cloud_forecast[k]`` when in range, the last entry when ``k``
    runs past the forecast (persistence), or None when no forecast was given.
    """
    if not cloud_forecast:
        return None
    if k < len(cloud_forecast):
        return cloud_forecast[k]
    return cloud_forecast[-1]
from .const import MPC_STATS_BUFFER_SIZE

# ── Import nonlinear model-based control components from mbc ─────────────────
from mbc.models import ContinuousDiscreteModel
from mbc.estimation import ContinuousDiscreteEKF
from mbc.control import CDTrackingOptimalControlProblem, CDNMPCController

from .integrator import implicit_euler_substeps

_LOGGER = logging.getLogger(__name__)


# ============================================================
# House-heating SDE model
# ============================================================

class HouseThermalSDE(ContinuousDiscreteModel):
    """
    House thermal model as a nonlinear continuous-discrete SDE.

    Continuous-time RC circuit per room i:

        Cᵢ dTᵢ/dt = Σⱼ∈adj(i) (Tⱼ − Tᵢ)/Rᵢⱼ + (T_out − Tᵢ)/Rᵢ,ext
                   + Q_heat,i(u, T_out) + Q_sol,i + Q_int,i

    In Itô SDE form:
        dx(t) = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t)

    where
        f(x, u, d, p, t) = F x + G_u(d[0]) u + G_d d
        sigma             = diag(sigma_w*I_n, sigma_b*I_n)

    and the observation model is:
        ym(tₖ) = hm(x) = T + b
        Rm = sigma_v² * I_n

    The nonlinearity comes from G_u(T_out): for heat pumps the delivered
    thermal power depends on the outdoor temperature through the COP.

    For cooling-capable heat pumps, the thermal contribution is computed via
    a smooth, asymmetric sigmoid (see ``HeatPump.smooth_thermal_power``) that
    maps u ∈ [−1, 1] continuously to the range [−Q_cool_max, +Q_heat_max].
    This eliminates the non-differentiable kink at u = 0 that a piecewise
    model would produce, giving the NLP optimiser smooth gradients.

    Parameters
    ----------
    model      : HouseModel
    sources    : list of HeatSource
    dt         : measurement sampling interval [s]
    sigma_w    : process-noise standard deviation [K/√s].  Default: 0.1.
    sigma_v    : measurement-noise standard deviation [K].  Default: 0.5.
    sigma_b    : offset-state process-noise standard deviation [K/√s].
                 Small values make offset adaptation slow/stable. Default: 0.002.
    n_int_steps: Euler sub-steps per sampling interval for EKF/OCP.
                 Default: 10.
    k_sigmoid  : Base sharpness of the smooth sigmoid activation used for
                 cooling-capable sources.  The effective sharpness is
                 automatically increased for asymmetric heating/cooling
                 capacities.  Default: 5.0.
    identifiable_sources: list of int, optional
        Indices of heat sources whose power-scale factors are being estimated.
        Used to unpack the parameter vector p in f(). If None (default),
        no heater scaling is applied.
    theta: np.ndarray, optional
        Parameter vector for this model instance (fixed for CD-EKF evaluation).
        If provided, f() will extract q_int and heater scales from this vector.
    """

    def __init__(
        self,
        model: HouseModel,
        sources: List[HeatSource],
        dt: float,
        sigma_w: float = 0.1,
        sigma_v: float = 0.5,
        sigma_b: float = 0.002,
        augment_offsets: bool = True,
        n_int_steps: int = 10,
        identifiable_sources: Optional[List[int]] = None,
        theta: Optional[np.ndarray] = None,
        k_sigmoid: float = 5.0,
    ) -> None:
        self._model = model
        self._sources = sources
        self._dt = dt
        self._sigma_w = sigma_w
        self._sigma_v = sigma_v
        self._sigma_b = sigma_b
        self._augment_offsets = augment_offsets
        self._n_int_steps = n_int_steps
        self._k_sigmoid = k_sigmoid

        # Store identifiable source indices and theta for parameter extraction
        self._identifiable_sources = identifiable_sources if identifiable_sources is not None else []
        self._theta = theta if theta is not None else np.array([])

        self._room_list: List[str] = model.room_names
        self._room_idx: Dict[str, int] = {
            name: i for i, name in enumerate(self._room_list)
        }
        n = len(self._room_list)
        self._n_rooms = n
        self._offset_state: np.ndarray = np.zeros(n, dtype=float)

        # Capacitance vector C_cap[i] = Cᵢ  [J/K]
        self._C_cap = np.array(
            [model.rooms[name].thermal_mass for name in self._room_list]
        )

        # Continuous state matrix F = C_cap⁻¹ Aᶜ
        self._F: np.ndarray = model._A / self._C_cap[:, np.newaxis]

        # Continuous disturbance matrix G_d = C_cap⁻¹ [b_ext | Iₙ]
        # d = [T_out, Q_sol,1+Q_int,1, …, Q_sol,n+Q_int,n]
        self._G_d: np.ndarray = np.zeros((n, 1 + n))
        self._G_d[:, 0] = model._B_ext / self._C_cap
        for i in range(n):
            self._G_d[i, 1 + i] = 1.0 / self._C_cap[i]

        # Cached measurement noise covariance
        self._Rm: np.ndarray = (sigma_v ** 2) * np.eye(n)

    # ── ContinuousDiscreteModel abstract dimensions ───────────────────────

    @property
    def nx(self) -> int:
        return 2 * self._n_rooms if self._augment_offsets else self._n_rooms

    @property
    def nu(self) -> int:
        return len(self._sources)

    @property
    def nd(self) -> int:
        return 1 + len(self._room_list)

    @property
    def nw(self) -> int:
        return self.nx

    @property
    def nz(self) -> int:
        return self._n_rooms

    @property
    def nym(self) -> int:
        return self._n_rooms

    @property
    def Rm(self) -> np.ndarray:
        return self._Rm

    # ── ContinuousDiscreteModel abstract functions ────────────────────────

    def _build_G_u(self, outdoor_temp: float) -> np.ndarray:
        """Input gain matrix G_u(T_out) ∈ ℝⁿˣᵐ."""
        n, m = self._n_rooms, self.nu
        G_u = np.zeros((n, m))
        for j, src in enumerate(self._sources):
            i = self._room_idx[src.room]
            G_u[i, j] = src.thermal_power(1.0, outdoor_temp) / self._C_cap[i]
        return G_u

    def f(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Drift f(x, u, d, p, t) = F x + heat_contrib(u, d[0]) + G_d (d + [0, q_int]).

        The thermal contribution of each source is computed as follows:

        * **Cooling-capable sources** (``src.can_cool is True``): a smooth,
          asymmetric shifted-logistic sigmoid (see
          ``HeatPump.smooth_thermal_power``) maps u_j ∈ [−1, 1] continuously
          to [−Q_cool_max, +Q_heat_max].  Positive u contributes heating;
          negative u contributes cooling (negative power).  The function is
          C∞-smooth everywhere, providing well-defined gradients for the
          nonlinear NLP optimiser even at u = 0.

        * **Heating-only sources** (``src.can_cool is False``): the standard
          linear ``thermal_power(max(0, u_j), T_out)`` is used, with a
          zero-floor to prevent spurious cooling from negative inputs.

        For parameter estimation, the parameter vector p (or self._theta if p is empty)
        contains theta with structure:
            theta = [log_mass_1..n, log_r_1..n, q_int_1..n, log_alpha_k, log_r_ij_k]

        We extract q_int and alpha (heater scales) from the parameter vector and apply them.
        """
        outdoor_temp = float(d[0])
        n = self._n_rooms
        T = x[:n]

        # Use p if provided, otherwise fall back to self._theta
        theta = p if len(p) > 0 else self._theta

        # Extract internal gains and heater scales from parameter vector
        if len(theta) >= 3 * n:
            # theta structure: [log_mass (n), log_r (n), q_int (n), log_alpha (...), log_r_ij (...)]
            q_int = theta[2*n : 3*n]

            # Extract heater scales for identifiable sources
            heater_scales = np.ones(self.nu)
            if len(self._identifiable_sources) > 0 and len(theta) >= 3*n + len(self._identifiable_sources):
                log_alpha = theta[3*n : 3*n + len(self._identifiable_sources)]
                for k, s_idx in enumerate(self._identifiable_sources):
                    heater_scales[s_idx] = np.exp(log_alpha[k])

            # Apply heater power-scale factors
            u_scaled = heater_scales * u

            # Apply internal gains to disturbance
            # d = [T_out, Q_sol,1, ..., Q_sol,n]
            # We add q_int to the solar gain channels
            d_augmented = d.copy()
            for i in range(n):
                d_augmented[1 + i] += q_int[i]
        else:
            # No parameter estimation - use defaults (no scaling, no internal gains)
            u_scaled = u
            d_augmented = d

        # Per-room thermal contribution.
        # * Cooling-capable sources (can_cool=True): smooth asymmetric sigmoid
        #   that maps u ∈ [-1, 1] → [-Q_cool_max, +Q_heat_max] with no kink.
        # * Heating-only sources: standard linear thermal_power(u, T_out),
        #   with u clamped to [0, 1] so that negative OCP values (which the
        #   bounds prevent in practice) never produce spurious cooling.
        heat_contrib = np.zeros(n)
        for j, src in enumerate(self._sources):
            i = self._room_idx[src.room]
            u_j = float(u_scaled[j])
            if src.can_cool:
                heat_contrib[i] += (
                    src.smooth_thermal_power(u_j, outdoor_temp, self._k_sigmoid)
                    / self._C_cap[i]
                )
            else:
                heat_contrib[i] += (
                    src.thermal_power(max(0.0, u_j), outdoor_temp) / self._C_cap[i]
                )

        dT = self._F @ T + heat_contrib + self._G_d @ d_augmented
        if not self._augment_offsets:
            return dT
        db = np.zeros(n)
        return np.concatenate([dT, db])

    def sigma(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Diffusion sigma(x, u, d, p, t) = sigma_w * I_n.

        The CD-EKF computes G @ Gᵀ = sigma_w² * I for the continuous
        Riccati equation.
        """
        n = self._n_rooms
        if not self._augment_offsets:
            return self._sigma_w * np.eye(n)
        sig = np.zeros((self.nx, self.nw))
        sig[:n, :n] = self._sigma_w * np.eye(n)
        sig[n:, n:] = self._sigma_b * np.eye(n)
        return sig

    def g(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Controlled output z = T + b for augmented mode, else z = T."""
        n = self._n_rooms
        if not self._augment_offsets or len(x) < 2 * n:
            return x[:n].copy()
        return x[:n] + x[n:2 * n]

    def gm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Continuous-time output gm = T + b for augmented mode, else gm = T."""
        n = self._n_rooms
        if not self._augment_offsets or len(x) < 2 * n:
            return x[:n].copy()
        return x[:n] + x[n:2 * n]

    def hm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Measurement function ym = T + b with integrated mismatch offset state."""
        n = self._n_rooms
        if not self._augment_offsets or len(x) < 2 * n:
            return x[:n].copy()
        return x[:n] + x[n:2 * n]

    # ── Analytic Jacobians (override default FD for efficiency) ──────────

    def dfdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """∂f/∂x for x = [T, b]."""
        n = self._n_rooms
        if not self._augment_offsets:
            return self._F.copy()
        J = np.zeros((2 * n, 2 * n))
        J[:n, :n] = self._F
        return J

    def dhmdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """∂hm/∂x = [I  I] for ym = T + b."""
        n = self._n_rooms
        if not self._augment_offsets:
            return np.eye(n)
        H = np.zeros((n, 2 * n))
        H[:, :n] = np.eye(n)
        H[:, n:] = np.eye(n)
        return H

    # ── Application-layer helpers ────────────────────────────────────────

    @property
    def x(self) -> list[float]:
        """Current augmented state [T, b] as a list of floats."""
        temps = [self._model.rooms[name].temperature for name in self._room_list]
        if not self._augment_offsets:
            return temps
        return temps + self._offset_state.tolist()

    @x.setter
    def x(self, val: list[float]) -> None:
        n = self._n_rooms
        if len(val) < n:
            raise ValueError(f"Expected at least {n} state values, got {len(val)}")
        for i, name in enumerate(self._room_list):
            self._model.rooms[name].temperature = float(val[i])
        if self._augment_offsets and len(val) >= 2 * n:
            self._offset_state = np.array(val[n:2 * n], dtype=float)

    @property
    def x_ref(self) -> np.ndarray:
        """Room setpoints as a (nx,) ndarray."""
        return np.array(
            [self._model.rooms[name].setpoint for name in self._room_list]
        )

    @property
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Input box constraints.

        For cooling-capable sources (``src.can_cool is True``) the lower
        bound is −1 (full cooling); for heating-only sources it is 0.
        The upper bound is always 1 (full heating).
        """
        u_min = np.array([-1.0 if src.can_cool else 0.0 for src in self._sources])
        return u_min, np.ones(self.nu)

    def disturbance_vector(
        self,
        outdoor_temp: float,
        solar_gains: Dict[str, float],
    ) -> np.ndarray:
        """
        Pack outdoor temperature and per-room gains into d ∈ ℝᵖ.

        ``Room.internal_gain`` is folded into the same disturbance slot as
        solar gain — both are constant heat injections [W] mapped through
        the same column of G_d, so the EKF/OCP cannot tell them apart and
        does not need to.
        """
        d = np.zeros(self.nd)
        d[0] = outdoor_temp
        for i, name in enumerate(self._room_list):
            slot = 1 + i
            gain = float(solar_gains.get(name, 0.0))
            gain += float(self._model.rooms[name].internal_gain)
            d[slot] = gain
        return d

    @property
    def room_offsets(self) -> Dict[str, float]:
        """Estimated integrated mismatch offsets b for each room [°C]."""
        return {
            name: float(self._offset_state[i])
            for i, name in enumerate(self._room_list)
        }

    def heating_powers(
        self,
        u_vec: np.ndarray,
        outdoor_temp: float,
    ) -> Dict[str, float]:
        """Convert fractions u to per-room total thermal power [W].

        Cooling-capable sources use the smooth asymmetric sigmoid (same as
        ``f()``); heating-only sources use the linear ``thermal_power``.
        Negative power values represent active heat removal (cooling).
        """
        powers: Dict[str, float] = {name: 0.0 for name in self._room_list}
        for j, src in enumerate(self._sources):
            u_j = float(u_vec[j])
            if src.can_cool:
                powers[src.room] += src.smooth_thermal_power(
                    u_j, outdoor_temp, self._k_sigmoid,
                )
            else:
                powers[src.room] += src.thermal_power(u_j, outdoor_temp)
        return powers


# ============================================================
# House-heating NMPC facade
# ============================================================

class HeatingMPCController:
    """
    Application facade for house-heating NMPC.

    Builds a HouseThermalSDE, ContinuousDiscreteEKF, and
    CDTrackingOptimalControlProblem, then provides the coordinator-facing API:

      actions = controller.compute(outdoor_temp, solar_gains, now, outdoor_forecast)

    The control loop at each step:
      1. EKF predict+update: fuse the current room-temperature measurement.
      2. CDTrackingOCP solve: minimise the NLP over the horizon.
      3. Apply the first optimal action to all heat sources.

    The CDTrackingOptimalControlProblem uses a configurable NLP backend
    (default: IPOPT with deterministic fallback to SLSQP when unavailable) to solve the
    finite-horizon NLP with:
        - Quadratic output-tracking cost  ‖z − z_ref‖²_Q
        - Quadratic input cost            ‖u‖²_R
        - Optional ROM penalty            ‖Δu‖²_S
        - Soft output constraints         z_min ≤ z ≤ z_max  (penalty ρ_z)
        - Hard input bounds               0 ≤ u ≤ 1

    Forecasts
    ---------
    Outdoor temperature: uses weather forecast if provided by the coordinator;
        otherwise falls back to persistence (constant at the current measurement).
    Solar gains: computed from the solar geometry model for each horizon step.

    Parameters
    ----------
    model             : HouseModel
    heat_sources      : list of HeatSource
    horizon           : prediction horizon N (number of time steps)
    dt                : OCP step size — the zero-order-hold duration for
                        each step in the optimisation horizon [s].
    measurement_dt    : EKF measurement interval — the actual wall-clock
                        time between successive ``compute()`` calls [s].
                        Must match the coordinator's update period.  If
                        ``None`` (default) it falls back to ``dt``, which
                        is correct only when compute() is called exactly
                        once every ``dt`` seconds.
    latitude          : site latitude [°]
    longitude         : site longitude [°]
    energy_weight     : weight on ‖u‖²_R (input energy cost)
    smoothing_weight  : weight on ‖Δu‖²_S (ROM penalty; 0 disables)
    constraint_offset : symmetric half-width δ for soft output constraints
                        SP − δ ≤ z ≤ SP + δ.  Default: 2.0 °C.
    sigma_w           : process-noise std dev for the SDE / EKF [K/√s].
    sigma_v           : measurement-noise std dev [K].
    sigma_b           : offset-state process-noise std dev [K/√s].
    n_int_steps       : Euler sub-steps per interval in EKF / OCP.
    """

    def __init__(
        self,
        model: HouseModel,
        heat_sources: List[HeatSource],
        horizon: int = 6,
        dt: float = 900.0,
        measurement_dt: Optional[float] = None,
        latitude: float = 55.0,
        longitude: float = 12.0,
        energy_weight: float = 0.01,
        smoothing_weight: float = 0.1,
        constraint_offset: float = 2.0,
        terminal_weight: float = 100.0,
        sigma_w: float = 0.1,
        sigma_v: float = 0.5,
        sigma_b: float = 0.002,
        n_int_steps: int = 10,
        solver: str = "ipopt",
        solver_options: Optional[Dict[str, Any]] = None,
        use_analytic_derivatives: bool = True,
    ) -> None:
        self._sources = heat_sources
        self._horizon = horizon
        self._dt = dt
        self._latitude = latitude
        self._longitude = longitude
        self._constraint_offset = constraint_offset
        self._solver_requested = solver
        self._solver_active = solver
        self._solver_options = dict(solver_options) if solver_options is not None else {}
        self._use_analytic_derivatives = bool(use_analytic_derivatives)

        # The EKF must integrate over the actual wall-clock interval between
        # compute() calls, NOT the OCP horizon step size.  Using dt (e.g.
        # 900 s) when compute() is called every UPDATE_INTERVAL (e.g. 60 s)
        # causes the EKF predict step to overshoot by dt/UPDATE_INTERVAL and
        # incorrectly accumulates 840 extra seconds of thermal drift and
        # input effect on every call.
        ekf_dt = measurement_dt if measurement_dt is not None else dt

        if smoothing_weight < 0.0:
            raise ValueError(
                f"smoothing_weight must be >= 0; got {smoothing_weight}"
            )
        if terminal_weight < 1.0:
            raise ValueError(
                f"terminal_weight must be at least 1.0; got {terminal_weight}"
            )

        # Build the nonlinear continuous-discrete model
        self._system = HouseThermalSDE(
            model, heat_sources, dt,
            sigma_w=sigma_w, sigma_v=sigma_v,
            sigma_b=sigma_b,
            augment_offsets=True,
            n_int_steps=n_int_steps,
        )
        self._control_system = HouseThermalSDE(
            model, heat_sources, dt,
            sigma_w=sigma_w, sigma_v=sigma_v,
            sigma_b=sigma_b,
            augment_offsets=True,
            n_int_steps=n_int_steps,
        )
        n_x = self._system.nx
        n_u = self._system.nu
        n_z = self._control_system.nz

        # ── EKF: initialise from current room temperatures ──────────────
        x0 = np.array(self._system.x)
        P0 = np.eye(n_x)  # initial state uncertainty [K²]
        n_rooms = self._system.nym
        P0[n_rooms:, n_rooms:] *= 4.0  # offsets start more uncertain than temperatures
        self._ekf = ContinuousDiscreteEKF(
            self._system, x0, P0, ekf_dt, n_steps=n_int_steps
        )

        # ── OCP cost matrices ───────────────────────────────────────────
        Q = np.eye(n_z)                      # stage output tracking
        R = energy_weight * np.eye(n_u)      # input cost
        S = (smoothing_weight * np.eye(n_u)
             if smoothing_weight > 0.0 else None)  # ROM penalty
        # Terminal cost: P = terminal_weight × Q
        # A large terminal_weight strongly incentivises the controller to
        # drive the predicted state to the setpoint by the end of the
        # horizon, improving steady-state tracking without increasing the
        # stage cost (which would sacrifice energy efficiency mid-horizon).
        P = terminal_weight * Q
        # Keep OCP integration steps lower than EKF steps to bound NLP size and
        # preserve controller runtime on larger houses; EKF still uses full
        # n_int_steps for state-estimation fidelity.
        OCP_MAX_INTEGRATION_STEPS = 2
        ocp_n_steps = min(n_int_steps, OCP_MAX_INTEGRATION_STEPS)
        self._ocp_n_steps = ocp_n_steps
        self._Q = Q
        self._R = R
        self._S = S
        self._P = P

        # Reference and bounds for the OCP
        z_ref = self._control_system.x_ref.copy()
        u_min, u_max = self._control_system.u_bounds

        # ── CDTrackingOptimalControlProblem ─────────────────────────────
        self._ocp = self._build_ocp_with_fallback(
            horizon=horizon,
            Q=Q,
            R=R,
            P=P,
            S=S,
            z_ref=z_ref,
            u_min=u_min,
            u_max=u_max,
            n_steps=ocp_n_steps,
            dt=dt,
        )

        # ── Warm-start storage ──────────────────────────────────────────
        self._u_prev: np.ndarray = np.zeros(n_u)
        self._u_seq_prev: Optional[np.ndarray] = None
        self._x_seq_prev: Optional[np.ndarray] = None
        self._y_seq_prev: Optional[np.ndarray] = None

        # ── Solve-time rolling statistics ───────────────────────────────
        self._solve_times: deque = deque(maxlen=MPC_STATS_BUFFER_SIZE)
        # Monotonically increasing counter; never capped by the rolling-window
        # maxlen so it always advances and can be used as a live-sensor state.
        self._total_computes: int = 0

        # ── Terminal weight (stored for sensor access) ───────────────────
        self._terminal_weight: float = terminal_weight

        # ── Kalman innovation (populated after each compute()) ───────────
        # ν = y − hm(x̂⁻)  (measurement residual at the update step)
        self._last_innovation: Optional[List[float]] = None

        # Visualisation data (populated after each compute())
        self._predictions: List[Dict[str, float]] = []
        self._outdoor_forecast: List[float] = []
        self._solar_forecast: List[Dict[str, float]] = []
        self._heating_schedule: List[Dict[str, float]] = []

    def _build_ocp(
        self,
        *,
        solver: str,
        horizon: int,
        Q: np.ndarray,
        R: np.ndarray,
        P: np.ndarray,
        S: Optional[np.ndarray],
        z_ref: np.ndarray,
        u_min: np.ndarray,
        u_max: np.ndarray,
        n_steps: int,
        dt: float,
    ) -> CDTrackingOptimalControlProblem:
        solver_options = self._solver_options_for(solver)
        kwargs: Dict[str, Any] = {
            "N": horizon,
            "Q": Q,
            "R": R,
            "P": P,
            "S": S,
            "z_ref": z_ref,
            "u_min": u_min,
            "u_max": u_max,
            "n_steps": n_steps,
            "dt": dt,
            "solver": solver,
        }
        if solver_options:
            kwargs["solver_options"] = solver_options

        # Forward-compatible analytical-derivative plumbing:
        # pass hooks only when the installed mbc API explicitly supports them.
        if self._use_analytic_derivatives:
            sig = inspect.signature(CDTrackingOptimalControlProblem.__init__).parameters
            if "use_analytic_derivatives" in sig:
                kwargs["use_analytic_derivatives"] = True
            if "derivative_fallback" in sig:
                kwargs["derivative_fallback"] = "numerical"
            if "analytic_derivatives_fallback" in sig:
                kwargs["analytic_derivatives_fallback"] = "numerical"

        return CDTrackingOptimalControlProblem(self._control_system, **kwargs)

    def _build_ocp_with_fallback(
        self,
        *,
        horizon: int,
        Q: np.ndarray,
        R: np.ndarray,
        P: np.ndarray,
        S: Optional[np.ndarray],
        z_ref: np.ndarray,
        u_min: np.ndarray,
        u_max: np.ndarray,
        n_steps: int,
        dt: float,
    ) -> CDTrackingOptimalControlProblem:
        """Build OCP, with deterministic IPOPT→SLSQP fallback when unavailable."""
        try:
            return self._build_ocp(
                solver=self._solver_active,
                horizon=horizon,
                Q=Q,
                R=R,
                P=P,
                S=S,
                z_ref=z_ref,
                u_min=u_min,
                u_max=u_max,
                n_steps=n_steps,
                dt=dt,
            )
        except (ImportError, ModuleNotFoundError, RuntimeError, ValueError) as err:
            if self._solver_active.lower() not in {"ipopt", "cyipopt"}:
                raise
            _LOGGER.warning(
                "IPOPT backend unavailable during OCP build (%s); "
                "falling back to SLSQP for deterministic continuity.",
                err,
            )
            self._solver_active = "SLSQP"
            return self._build_ocp(
                solver=self._solver_active,
                horizon=horizon,
                Q=Q,
                R=R,
                P=P,
                S=S,
                z_ref=z_ref,
                u_min=u_min,
                u_max=u_max,
                n_steps=n_steps,
                dt=dt,
            )

    def _solver_options_for(self, solver: str) -> Dict[str, Any]:
        opts = dict(self._solver_options)
        key = solver.lower()
        if key in {"ipopt", "cyipopt"}:
            opts.setdefault("max_iter", 300)
            opts.setdefault("tol", 1e-6)
        else:
            opts.setdefault("maxiter", 300)
            opts.setdefault("ftol", 1e-6)
        return opts

    # ── Visualisation properties ─────────────────────────────────────────

    @property
    def constraint_offset(self) -> float:
        """Symmetric offset δ around the setpoint for soft output constraints [°C]."""
        return self._constraint_offset

    @property
    def terminal_weight(self) -> float:
        """Terminal cost weight (P = terminal_weight × Q) in effect for this controller."""
        return self._terminal_weight

    @property
    def solver_requested(self) -> str:
        """Configured NLP solver backend name."""
        return self._solver_requested

    @property
    def solver_active(self) -> str:
        """Currently active NLP solver backend (may change after fallback)."""
        return self._solver_active

    @property
    def use_analytic_derivatives(self) -> bool:
        """Whether analytical-derivative plumbing is enabled when supported by mbc."""
        return self._use_analytic_derivatives

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """Kalman filter innovation ν = y − hm(x̂⁻) from the most recent compute() call.

        One value per room (in room_names order).  None if compute() has not
        been called yet.  This is the raw measurement residual at the update
        step — a well-tuned filter should produce zero-mean, low-autocorrelation
        innovations.
        """
        return self._last_innovation

    @property
    def filtered_temperatures(self) -> Dict[str, float]:
        """Kalman-filtered room temperatures y(k|k) after the latest update step.

        Before the first ``compute()`` call this returns the EKF initial
        state (which equals the room temperatures the controller was
        constructed with), so callers always get a usable dict.
        """
        x_hat = self._ekf.x_hat
        n = self._system.nym
        y_hat = self._effective_room_temperatures(self._system, x_hat)
        return {
            name: float(y_hat[i])
            for i, name in enumerate(self._system._room_list[:n])
        }

    @property
    def temperature_offsets(self) -> Dict[str, float]:
        """Estimated per-room integrated mismatch offsets b after EKF update."""
        x_hat = self._ekf.x_hat
        n = self._system.nym
        return {
            name: float(x_hat[n + i])
            for i, name in enumerate(self._system._room_list[:n])
        }

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

    @property
    def last_solve_time(self) -> Optional[float]:
        """Wall-clock time [s] consumed by the most recent OCP solve, or None."""
        return self._solve_times[-1] if self._solve_times else None

    @property
    def mean_solve_time(self) -> Optional[float]:
        """Mean OCP solve time [s] over the rolling history, or None."""
        if not self._solve_times:
            return None
        return float(np.mean(list(self._solve_times)))

    @property
    def max_solve_time(self) -> Optional[float]:
        """Maximum OCP solve time [s] observed in the rolling history, or None."""
        if not self._solve_times:
            return None
        return float(np.max(list(self._solve_times)))

    @property
    def n_solves(self) -> int:
        """Total number of OCP solves recorded in the rolling history."""
        return len(self._solve_times)

    @property
    def total_computes(self) -> int:
        """Monotonically increasing count of all compute() calls (never resets)."""
        return self._total_computes

    def _effective_room_temperatures(
        self,
        system: HouseThermalSDE,
        x: np.ndarray,
    ) -> np.ndarray:
        """Map a state vector to the room temperatures shown in visualisations."""
        n = system.nym
        if len(x) < n:
            return np.zeros(n)
        if system._augment_offsets and len(x) >= 2 * n:
            return x[:n] + x[n:2 * n]
        return x[:n].copy()

    # ── Main entry point ─────────────────────────────────────────────────

    def compute(
        self,
        outdoor_temp: float,
        solar_gains: Optional[Dict[str, float]] = None,
        now: Optional[datetime] = None,
        outdoor_forecast: Optional[List[float]] = None,
        cloud_forecast: Optional[List[float]] = None,
        cloud_cover_now: Optional[float] = None,
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
            External outdoor temperature forecast for each horizon step.
            If provided, must have length >= horizon.
        cloud_forecast : list of float, optional
            Cloud-cover fraction in [0, 1] for each horizon step.  When
            provided, the solar forecast is attenuated by the Kasten–Czeplak
            factor (see :func:`solar_model.cloud_attenuation_factor`).
        cloud_cover_now : float, optional
            Current cloud-cover fraction in [0, 1].  Used for the k=0 entry
            of the solar schedule when ``solar_gains`` was not pre-computed.

        Returns
        -------
        dict
            ``{source_name: setpoint_fraction}`` where fraction ∈ [0, 1].
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)
        if solar_gains is None:
            solar_gains = self._current_solar(now, cloud_cover=cloud_cover_now)

        N = self._horizon
        p = np.array([], dtype=float)  # no estimated parameters

        # ── Disturbance forecast D ∈ ℝ^{N × nd} ─────────────────────────
        if outdoor_forecast is not None and len(outdoor_forecast) >= N:
            outdoor_seq = list(outdoor_forecast[:N])
        else:
            outdoor_seq = self._forecast_outdoor(outdoor_temp)
        solar_seq = self._forecast_solar(now, cloud_forecast=cloud_forecast)

        d_traj = np.zeros((N, self._control_system.nd))
        for k in range(N):
            d_traj[k] = self._control_system.disturbance_vector(
                outdoor_seq[k], solar_seq[k]
            )

        # Store forecasts for visualisation
        self._outdoor_forecast = list(outdoor_seq)
        self._solar_forecast = [dict(s) for s in solar_seq]

        # ── Current measurement y = room temperatures ────────────────────
        y = np.array(
            [
                self._system._model.rooms[name].temperature
                for name in self._system._room_list
            ],
            dtype=float,
        )

        # ── Update setpoint reference in OCP (setpoints may have changed) ──
        z_ref = self._control_system.x_ref
        # NOTE: mbc's CDTrackingOptimalControlProblem has no public API for
        # updating the reference trajectory after construction, so we reach
        # into the internal EconomicOptimalControlProblem.  This is technical
        # debt — if the mbc internals change, this line must be updated.
        _eocp = self._ocp._eocp
        _M = _eocp._N * _eocp._n_steps
        _eocp._z_ref = np.tile(z_ref, (_M + 1, 1))
        # Also update the Mayer (terminal cost) function's captured _zref.
        # CDTrackingOptimalControlProblem defines _mayer as:
        #   def _mayer(x, y, theta, _P=P_arr, _zref=z_ref_arr, _model=model, ...)
        # The _zref default arg (index 1) is the same numpy array that was
        # passed at OCP construction.  Updating it in-place keeps the terminal
        # cost consistent with the stage cost whenever setpoints change.
        if _eocp._mayer is not None:
            # index 1 → _zref (see CDTrackingOptimalControlProblem._mayer signature)
            _eocp._mayer.__defaults__[1][:] = z_ref

        # ── Step 1: EKF predict + innovation capture + update ───────────
        # Split the EKF step() into predict() + update() so we can capture
        # the innovation ν = y − hm(x̂⁻) between the two phases.  This is
        # needed by KalmanInnovationSensor (stored in the history buffer by
        # the coordinator).
        self._ekf.predict(self._u_prev, d_traj[0], p, 0.0)
        x_prior = self._ekf.x_hat.copy()  # x̂⁻ (prior before measurement fusion)
        # Innovation: ν = y − hm(x̂⁻)
        y_hat_prior = self._system.hm(x_prior, self._u_prev, d_traj[0], p, 0.0)
        self._last_innovation = (y - y_hat_prior).tolist()
        x_hat, _ = self._ekf.update(y, self._u_prev, d_traj[0], p)
        n_rooms = self._system.nym
        self._system._offset_state = np.array(x_hat[n_rooms:], dtype=float)
        x_hat_control = x_hat.copy()

        # ── Step 2: OCP solve (timed) ────────────────────────────────────
        _t0 = time.perf_counter()
        try:
            u_opt, _, _info = self._ocp.solve(
                x_hat_control, d_traj,
                u_prev=self._u_seq_prev,
                x_prev=self._x_seq_prev,
                y_prev=self._y_seq_prev,
                p=p, t0=0.0
            )
        except RuntimeError as err:
            if self._solver_active.lower() not in {"ipopt", "cyipopt"}:
                raise
            _LOGGER.warning(
                "IPOPT solve failed (%s); falling back to SLSQP for deterministic continuity.",
                err,
            )
            self._solver_active = "SLSQP"
            self._ocp = self._build_ocp(
                solver=self._solver_active,
                horizon=self._horizon,
                Q=self._Q,
                R=self._R,
                P=self._P,
                S=self._S,
                z_ref=self._control_system.x_ref.copy(),
                u_min=self._control_system.u_bounds[0],
                u_max=self._control_system.u_bounds[1],
                n_steps=self._ocp_n_steps,
                dt=self._dt,
            )
            u_opt, _, _info = self._ocp.solve(
                x_hat_control, d_traj,
                u_prev=self._u_seq_prev,
                x_prev=self._x_seq_prev,
                y_prev=self._y_seq_prev,
                p=p, t0=0.0
            )
        self._solve_times.append(time.perf_counter() - _t0)
        self._total_computes += 1

        # ── Step 3: Apply first action ───────────────────────────────────
        u0 = u_opt[0]

        # Update warm-start for next call
        self._u_seq_prev = u_opt.copy()
        self._x_seq_prev = _info.get("X")
        self._y_seq_prev = _info.get("Y")
        self._u_prev = u0.copy()

        # ── Apply actions to heat sources ────────────────────────────────
        actions: Dict[str, float] = {}
        for j, src in enumerate(self._sources):
            u_lo = -1.0 if src.can_cool else 0.0
            frac = float(np.clip(u0[j], u_lo, 1.0))
            actions[src.name] = frac
            if src.can_cool:
                # Track the smooth-sigmoid power so sensors and the EKF are
                # consistent with the model function f().
                src._current_power = src.smooth_thermal_power(
                    frac, outdoor_temp, self._system._k_sigmoid,
                )
            else:
                src.set_power(frac, outdoor_temp)

        # ── Reconstruct predicted trajectory for visualisation ───────────
        # Implicit-Euler sub-stepping matches the MPC's prediction scheme
        # (the OCP itself already uses implicit Euler upstream) and stays
        # L-stable when later phases introduce stiff 2R2C / slab dynamics.
        room_list = self._control_system._room_list
        n_x = self._control_system.nx
        sys = self._control_system

        self._predictions = []
        x_pred = x_hat_control.copy()
        for k in range(N):
            u_k = u_opt[k]
            d_k = d_traj[k]

            def rhs(state, u=u_k, d=d_k):
                return sys.f(state, u, d, p, 0.0)

            def jac(state, u=u_k, d=d_k):
                return sys.dfdx(state, u, d, p, 0.0)

            x_pred = implicit_euler_substeps(
                rhs, jac, x_pred, self._dt, sys._n_int_steps,
            )
            y_pred = self._effective_room_temperatures(sys, x_pred)
            self._predictions.append(
                {name: float(y_pred[i]) for i, name in enumerate(room_list)}
            )

        self._heating_schedule = [
            self._system.heating_powers(u_opt[k], outdoor_seq[k])
            for k in range(N)
        ]

        return actions

    def notify_applied_u(self, source_name: str, u_applied: float) -> None:
        """
        Notify the controller that a specific control action was applied
        externally (outside of the OCP solve), so the EKF uses the correct
        previous input on the next compute() call.

        This must be called after any out-of-band action is applied to a
        heat source (e.g. passive cooling that bypasses the OCP), otherwise
        the EKF predict step will use the stale OCP output as u_prev and
        produce inaccurate state estimates.

        Parameters
        ----------
        source_name : str
            Name of the heat source whose action was overridden.
        u_applied : float
            The fraction actually applied, in [−1, 1].
        """
        for j, src in enumerate(self._sources):
            if src.name == source_name:
                u_lo = -1.0 if src.can_cool else 0.0
                self._u_prev[j] = float(np.clip(u_applied, u_lo, 1.0))
                break

    # ── Disturbance forecasts ────────────────────────────────────────────

    def _forecast_outdoor(self, current: float) -> List[float]:
        """Persistence forecast: outdoor temperature constant over horizon."""
        return [current] * self._horizon

    def _forecast_solar(
        self,
        now: datetime,
        cloud_forecast: Optional[List[float]] = None,
    ) -> List[Dict[str, float]]:
        """Solar gain forecast using the geometric solar model.

        Returns N+1 entries where ``solar_seq[k]`` = solar at ``now + k * dt``
        for k = 0, …, N.

        * k = 0 maps to the current solar gains — the correct disturbance for
          the EKF predict step (propagating from ``now − dt`` to ``now``) and
          for the OCP's first prediction step (interval ``[now, now + dt]``).
        * k = 1 … N−1 supply the OCP horizon steps 1 … N−1.
        * k = N is one step beyond the OCP horizon, stored so the visualised
          forecast trace can cover the full prediction window from ``now`` to
          ``now + N·dt`` without truncating the final step.

        Only the first N entries (k = 0 … N−1) are used to build ``d_traj``
        for the OCP.  All N+1 entries are stored in ``_solar_forecast`` and
        exposed via the ``solar_forecast`` property for sensor visualisation.

        When ``cloud_forecast`` is provided (one fraction per horizon step),
        the clear-sky irradiance is attenuated per the Kasten–Czeplak factor.
        Step k uses ``cloud_forecast[k]`` for k < len(cloud_forecast); steps
        beyond the supplied forecast hold the last value (persistence).
        """
        schedules = []
        for k in range(self._horizon + 1):  # N+1 entries: k = 0 … N
            t = now + timedelta(seconds=self._dt * k)
            cc = _select_cloud_for_step(cloud_forecast, k)
            schedules.append({
                name: room_solar_gains(
                    self._system._model.rooms[name].windows,
                    t,
                    self._latitude,
                    self._longitude,
                    cloud_cover=cc,
                )
                for name in self._system._room_list
            })
        return schedules

    def _current_solar(
        self,
        now: datetime,
        cloud_cover: Optional[float] = None,
    ) -> Dict[str, float]:
        """Current-step solar gains for all rooms."""
        return {
            name: room_solar_gains(
                self._system._model.rooms[name].windows,
                now,
                self._latitude,
                self._longitude,
                cloud_cover=cloud_cover,
            )
            for name in self._system._room_list
        }
