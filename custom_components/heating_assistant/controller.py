"""
Model Predictive Controller — House-Heating Application (Linearised CD-MPC).

The house thermal model is formulated as a nonlinear continuous-discrete SDE.
At each control interval the model is linearised around the current operating
point (estimated by a CD-EKF), the local linear model is ZOH-discretised, and
a convex QP is solved via CDLinearizedMPCController from the ``mbc`` toolbox.

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
    Application facade: builds HouseThermalSDE + _InnovationEKF (CD-EKF) +
    CDLinearizedMPCController, adds solar/outdoor forecasting, applies
    source set-points, and exposes the visualisation properties consumed by
    the coordinator.

    At each control interval the controller:
      1. Runs the CD-EKF to fuse the current temperature measurement.
      2. Linearises the SDE model around (x̂, u_prev, d_now) using analytic
         Jacobians and ZOH-discretises the result.
      3. Solves a convex QP (via cvxopt) in deviation coordinates.
      4. Applies the first optimal action to all heat sources.

    Public API:
        controller = HeatingMPCController(model, heat_sources, ...)
        actions    = controller.compute(outdoor_temp, solar_gains, now)
        # controller.predictions, .outdoor_forecast, .solar_forecast,
        # .heating_schedule
"""

from __future__ import annotations


import logging
import math
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .thermal_model import HouseModel
from .heat_sources import HeatSource
from .solar_model import room_solar_gains


def _select_cloud_for_step(
    cloud_forecast: Optional[List[float]], k: int,
    fallback: Optional[float] = None,
) -> Optional[float]:
    """Pick the cloud-cover fraction for horizon step k from a forecast list.

    Returns ``cloud_forecast[k]`` when in range, the last entry when ``k``
    runs past the forecast (persistence), or ``fallback`` when no forecast
    was given (typically the current measured cloud cover).
    """
    if not cloud_forecast:
        return fallback
    if k < len(cloud_forecast):
        return cloud_forecast[k]
    return cloud_forecast[-1]
from .const import MPC_STATS_BUFFER_SIZE

# ── Import model-based control components from mbc ────────────────────────────
from cvxopt import matrix as _cvxmat, solvers as _cvxsolvers
from mbc.models import ContinuousDiscreteModel
from mbc.estimation import ContinuousDiscreteEKF
from mbc.control import (
    CDLinearizedMPCController,
    linearize_cd_model,
    discretize_cd_linearization,
)
from mbc.control.ocp import (
    OptimalControlProblem,
    _block_diag,
    _block_diag_terminal,
    _tile_column,
    _build_D_diff,
)
from mbc._utils import _eye, _zeros, _np_to_cvx

from .const import (
    DEFAULT_SETPOINT_PULL_WEIGHT,
    AIR_RHO_CP,
    SHERMAN_GRIMSRUD_STACK_COEF,
    SHERMAN_GRIMSRUD_WIND_COEF,
)
from .integrator import implicit_euler_substeps
from .thermal_model import _SG_FACTOR_TYPICAL

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
        # Per-room covariance scaling for process noise (Phase 3 W1).
        # Values are covariance multipliers; 1.0 means no inflation.
        self._room_q_scales: np.ndarray = np.ones(n, dtype=float)

        # 1R1C state layout: ``x_phys = [T (n)]`` with ``nx_phys = n``.
        # When ``augment_offsets=True`` an additional block of per-room
        # measurement-bias states is appended, giving ``nx = 2n``.
        # Capacitance, drift, and disturbance matrices are all derived
        # from the n-state ``HouseModel`` so they share a single source
        # of truth.
        self._C_cap = np.array(model._C, dtype=float)                # (n,)
        self._F: np.ndarray = model._A / self._C_cap[:, np.newaxis]  # (n, n)

        # Continuous disturbance matrix G_d shape (n, 1+n):
        # column 0: outdoor temperature coupling on all rows via B_ext.
        # columns 1..n: per-room solar + identified internal gain on
        # the single temperature node.
        # Phase 1 C4 sol-air: the facade sol-air fraction is ADDED to
        # G_d[i, 1+i] (same node as the direct solar gain).
        self._G_d: np.ndarray = np.zeros((n, 1 + n))
        self._G_d[:, 0] = model._B_ext / self._C_cap
        for i in range(n):
            # Direct window-solar gain lands on the single temperature node.
            facade_share = float(
                model.rooms[self._room_list[i]].facade_solar_share
            )
            facade_alpha = float(
                model.rooms[self._room_list[i]].facade_absorptance
            )
            # Base solar channel: 1/C_i (direct window gain)
            # Plus sol-air share: facade_share * facade_alpha / C_i
            total_solar_gain = 1.0 / self._C_cap[i]
            if facade_share > 0.0 and facade_alpha > 0.0:
                total_solar_gain += facade_share * facade_alpha / self._C_cap[i]
            self._G_d[i, 1 + i] = total_solar_gain

        # Phase 1 C3 sky cooling-drift bias.  Mirrors the
        # HouseModel-side constant (–sky_ua · ΔT_sky / C_i per room).
        # Added directly to the drift in ``f`` so the EKF/OCP see the
        # clear-night cooling effect; doesn't enter ``dfdx``.
        self._sky_offset_phys = np.array(model._B_sky_offset, dtype=float)

        # Sherman–Grimsrud per-room effective leakage area [m²].
        # Mirrors HouseModel._leakage_area; cached here so the controller
        # is self-contained even when used outside the live coordinator.
        self._leakage_area = np.array(
            [
                max(0.0, model.rooms[name].infiltration_fraction)
                * (1.0 / model.rooms[name].r_external)
                / (AIR_RHO_CP * _SG_FACTOR_TYPICAL)
                for name in self._room_list
            ],
            dtype=float,
        )

        # Phase 1 B2: per-source first-order emitter filter (pragmatic).
        # Each source with ``emitter_time_constant > 0`` gets a filter
        # state ``φ_j`` that lags the commanded fraction ``u_j`` with
        # time constant ``τ_em,j``.  The state-vector layout becomes
        #
        #     [T_a (n), T_w (n), T_s (n), φ (m), b (n)]
        #
        # where ``m`` is the number of filtered sources.  When
        # ``m = 0`` (all sources τ_em = 0, e.g. pure-electric setups)
        # the state vector is identical to the pre-B2 layout, so
        # nothing changes for those configurations.
        #
        # ``_filtered_source_indices[k]`` is the global source index
        # of the k-th filtered source, and ``_filter_idx_for_source[j]``
        # maps a global source index ``j`` back to its φ-block position
        # (or -1 if the source has no filter).  ``_emitter_taus`` is
        # the parallel-indexed τ_em for each filtered source.
        self._filtered_source_indices: List[int] = [
            j for j, src in enumerate(self._sources)
            if float(getattr(src, "emitter_time_constant", 0.0) or 0.0) > 0.0
        ]
        self._n_filtered: int = len(self._filtered_source_indices)
        self._filter_idx_for_source: np.ndarray = -np.ones(
            len(self._sources), dtype=int,
        )
        for k, j in enumerate(self._filtered_source_indices):
            self._filter_idx_for_source[j] = k
        self._emitter_taus: np.ndarray = np.array(
            [
                float(self._sources[j].emitter_time_constant)
                for j in self._filtered_source_indices
            ],
            dtype=float,
        )
        # Filter-state buffer used by ``x`` / ``x.setter`` to round-trip
        # the φ block when no controller / EKF cycle has run yet.  Cold
        # start: all filters at 0 (no commanded power applied).
        self._filter_state: np.ndarray = np.zeros(self._n_filtered, dtype=float)

        # Wind speed [m/s] applied to the SG overlay; held constant over
        # the OCP horizon and across EKF sub-steps within one coordinator
        # cycle.  ``None`` (default) disables the overlay so the model
        # reduces exactly to its typical-conditions UA.
        self._wind_speed: Optional[float] = None

        # Cached measurement noise covariance.  Measurement size is one
        # per room.
        self._Rm: np.ndarray = (sigma_v ** 2) * np.eye(n)

        # ── Precomputed quantities for hot-path functions ─────────────────

        m = self._n_filtered

        # Offset block start index (avoids property dispatch in g/gm/hm/dfdx)
        self._offset_block_start: int = n + m

        # Per-source fixed lookups — replace per-call dict/cast overhead
        self._src_room_idx: np.ndarray = np.array(
            [self._room_idx[src.room] for src in self._sources], dtype=int
        )
        self._src_C_cap: np.ndarray = self._C_cap[self._src_room_idx]
        self._src_can_cool: list = [src.can_cool for src in self._sources]

        # Filter block constants
        if m > 0:
            self._neg_inv_taus: np.ndarray = -1.0 / self._emitter_taus
            self._inv_emitter_taus: np.ndarray = 1.0 / self._emitter_taus
            self._filter_diag_idx: np.ndarray = np.arange(n, n + m)
        else:
            self._neg_inv_taus = np.zeros(0)
            self._inv_emitter_taus = np.zeros(0)
            self._filter_diag_idx = np.zeros(0, dtype=int)

        # Identifiable sources as numpy array for vectorized scale extraction
        self._n_identifiable: int = len(self._identifiable_sources)
        self._identifiable_sources_arr: np.ndarray = np.array(
            self._identifiable_sources, dtype=int
        )

        # Constant measurement/output Jacobian H = dhm/dx = dgm/dx
        b_start = n + m
        if not augment_offsets:
            _H = np.zeros((n, n + m))
            _H[:, :n] = np.eye(n)
        else:
            _H = np.zeros((n, 2 * n + m))
            _H[:, :n] = np.eye(n)
            _H[:, b_start:b_start + n] = np.eye(n)
        self._H_const: np.ndarray = _H

        # Sherman–Grimsrud precomputed constants
        self._AIR_RHO_CP_leakage: np.ndarray = AIR_RHO_CP * self._leakage_area
        self._ua_inf_typ: np.ndarray = self._AIR_RHO_CP_leakage * _SG_FACTOR_TYPICAL
        self._zeros_n_rooms: np.ndarray = np.zeros(n)

        # Diagonal index array for in-place F-matrix update in dfdx
        self._diag_n_idx: np.ndarray = np.arange(n)

        # Total state dimension (avoids nx property dispatch in g/gm/hm)
        self._nx: int = 2 * n + m if augment_offsets else n + m

        # Per-source fast-path for linear (non-cooling) heat sources in f()
        # Sources with a precomputed '_gain' attr (ElectricHeater) can skip the
        # method call entirely; the gain folded into a per-C scaling constant.
        _nu = len(self._sources)
        self._src_use_linear_gain: list = [
            hasattr(src, "_gain") for src in self._sources
        ]
        self._src_linear_gain_per_C: np.ndarray = np.zeros(_nu, dtype=float)
        for _j, _src in enumerate(self._sources):
            if self._src_use_linear_gain[_j]:
                self._src_linear_gain_per_C[_j] = _src._gain / self._src_C_cap[_j]

        # Precomputed zero Jacobian ∂gm/∂u = 0  (gm is independent of u)
        self._dgmdu_zero: np.ndarray = np.zeros((n, _nu))

        # Precomputed diffusion matrix σ (rebuilt when Q scales change)
        self._sigma_matrix: np.ndarray = self._build_sigma_matrix()

    # ── ContinuousDiscreteModel abstract dimensions ───────────────────────

    @property
    def nx(self) -> int:
        # 1R1C state layout:
        # * Physical block: ``n`` states (single temperature per room).
        # * Filter block: ``m`` states (emitter lags).
        # * With offset augmentation we append per-room measurement
        #   biases, giving ``2n + m``.
        n = self._n_rooms
        m = self._n_filtered
        return 2 * n + m if self._augment_offsets else n + m

    @property
    def nu(self) -> int:
        return len(self._sources)

    @property
    def nd(self) -> int:
        return 1 + len(self._room_list)

    @property
    def nw(self) -> int:
        return self.nx

    # ── Sherman–Grimsrud wind overlay (Phase 1 C1) ────────────────────────

    def set_wind_speed(self, wind_speed: Optional[float]) -> None:
        """Update the SG overlay wind speed.  ``None`` disables the overlay
        so ``f`` / ``dfdx`` reduce to their typical-conditions form."""
        if wind_speed is None or not np.isfinite(wind_speed):
            self._wind_speed = None
        else:
            self._wind_speed = float(max(0.0, wind_speed))

    def set_ground_temp(self, ground_temp: float) -> None:
        """Accepted for API compatibility; no-op in 1R1C (no slab node)."""
        pass

    def set_room_process_noise_covariance_scales(
        self, scales_by_room: Dict[str, float],
    ) -> None:
        """Update per-room process-noise covariance multipliers.

        ``scales_by_room[room]`` scales Q for that room's physical states
        (air/wall/slab). Values <= 0 are ignored.
        """
        scales = np.ones(self._n_rooms, dtype=float)
        for room_name, value in scales_by_room.items():
            if room_name not in self._room_idx:
                continue
            try:
                val = float(value)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(val) or val <= 0.0:
                continue
            scales[self._room_idx[room_name]] = val
        self._room_q_scales = scales
        self._sigma_matrix = self._build_sigma_matrix()

    def _build_sigma_matrix(self) -> np.ndarray:
        """Build (or rebuild) the diffusion matrix from current Q scales."""
        n = self._n_rooms
        m = self._n_filtered
        physical_std = np.sqrt(np.maximum(self._room_q_scales, 0.0))
        if not self._augment_offsets:
            diag = np.concatenate([
                self._sigma_w * physical_std,
                self._sigma_w * np.ones(m, dtype=float),
            ])
            return np.diag(diag)
        sig = np.zeros((self._nx, self._nx))
        diag_nm = np.concatenate([
            self._sigma_w * physical_std,
            self._sigma_w * np.ones(m, dtype=float),
        ])
        sig[:n + m, :n + m] = np.diag(diag_nm)
        sig[n + m:, n + m:] = self._sigma_b * np.eye(n)
        return sig

    def _infiltration_delta_ua(
        self, outdoor_temp: float, room_temps: np.ndarray,
    ) -> np.ndarray:
        """Per-room *delta* on external UA relative to typical conditions.

        Returns the zero vector when no wind speed is configured, so the
        overlay is a no-op until ``set_wind_speed`` is called with a
        finite value.  See :func:`thermal_model.HouseModel.infiltration_delta_ua`
        for the matching reference implementation used by the standalone
        ``HouseModel``.
        """
        if self._wind_speed is None:
            return self._zeros_n_rooms
        v = self._wind_speed
        dT_abs = np.abs(room_temps - outdoor_temp)
        sg = np.sqrt(
            SHERMAN_GRIMSRUD_STACK_COEF * dT_abs
            + SHERMAN_GRIMSRUD_WIND_COEF * v * v
        )
        return self._AIR_RHO_CP_leakage * sg - self._ua_inf_typ

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
        Drift for the 1R1C state ``x = [T (n)]`` (un-augmented)
        or ``x = [T (n), b (n)]`` (augmented).

        Heat-source power and the Sherman–Grimsrud infiltration overlay
        both land on the single temperature node.  The measurement-offset
        block ``b`` has zero drift — it's a slowly-varying random-walk
        bias state.

        Heat-source dispatch:

        * **Cooling-capable** (``src.can_cool``): smooth asymmetric
          shifted-logistic sigmoid mapping u ∈ [−1, 1] → [−Q_cool_max,
          +Q_heat_max].  C∞ everywhere for gradient-friendly NLP solves.
        * **Heating-only**: linear ``thermal_power(max(0, u), T_out)``.

        Parameter vector ``p`` (or ``self._theta`` when ``p`` is empty)
        layout: ``theta = [log_mass(n), log_r(n), q_int(n), log_alpha(*),
        log_r_ij(*)]``.  ``q_int`` is folded into the disturbance channel;
        ``log_alpha`` scales each source's commanded power.
        """
        outdoor_temp = float(d[0])
        n = self._n_rooms
        T_phys = x[:n]

        theta = p if len(p) > 0 else self._theta

        # Extract internal gains and heater scales from parameter vector
        if len(theta) >= 3 * n:
            q_int = theta[2 * n: 3 * n]
            d_augmented = d.copy()
            d_augmented[1: 1 + n] += q_int
            heater_scales = self._get_heater_scales(theta)
            u_scaled = u if heater_scales is None else heater_scales * u
        else:
            heater_scales = None
            u_scaled = u
            d_augmented = d

        # Heat-source contribution (1R1C: all sources reach the single node).
        # Phase 1 B2: filtered sources use filter state φ_j as effective fraction.
        m = self._n_filtered
        heat_contrib = np.zeros(n)

        for j, src in enumerate(self._sources):
            k_filter = self._filter_idx_for_source[j]
            if k_filter >= 0:
                eff_u = (heater_scales[j] if heater_scales is not None else 1.0) * x[n + k_filter]
            else:
                eff_u = u_scaled[j]
            if self._src_can_cool[j]:
                p_w = src.smooth_thermal_power(eff_u, outdoor_temp, self._k_sigmoid)
                heat_contrib[self._src_room_idx[j]] += p_w / self._src_C_cap[j]
            elif self._src_use_linear_gain[j]:
                heat_contrib[self._src_room_idx[j]] += self._src_linear_gain_per_C[j] * max(0.0, eff_u)
            else:
                p_w = src.thermal_power(max(0.0, eff_u), outdoor_temp)
                heat_contrib[self._src_room_idx[j]] += p_w / self._src_C_cap[j]

        # Physical 1R1C drift on T.
        dT_phys = (
            self._F @ T_phys
            + heat_contrib
            + self._G_d @ d_augmented
            + self._sky_offset_phys
        )

        # Sherman–Grimsrud wind-driven infiltration overlay (skip when not configured).
        if self._wind_speed is not None:
            delta_ua = self._infiltration_delta_ua(outdoor_temp, T_phys)
            dT_phys += (delta_ua / self._C_cap) * (outdoor_temp - T_phys)

        # Phase 1 B2: filter-block drift  dφ/dt = (u_cmd - φ) / τ_em.
        if m > 0:
            phi = x[n: n + m]
            dphi = (u[self._filtered_source_indices] - phi) / self._emitter_taus
        else:
            dphi = self._zeros_n_rooms[:0]

        if not self._augment_offsets:
            return np.concatenate([dT_phys, dphi])
        return np.concatenate([dT_phys, dphi, self._zeros_n_rooms])

    def sigma(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Diffusion ``σ(x, u, d, p, t)`` for the augmented state.

        The matrix is state-independent and precomputed in ``__init__``
        (rebuilt by ``set_room_process_noise_covariance_scales``).
        """
        return self._sigma_matrix

    def _get_heater_scales(self, theta: np.ndarray):
        """Extract per-source scale factors from parameter vector.

        Returns ``None`` when no identifiable sources are configured
        (the common case), avoiding an unnecessary ``np.ones`` allocation.
        """
        n = self._n_rooms
        n_id = self._n_identifiable
        if n_id > 0 and len(theta) >= 3 * n + n_id:
            scales = np.ones(self.nu, dtype=float)
            scales[self._identifiable_sources_arr] = np.exp(
                theta[3 * n: 3 * n + n_id]
            )
            return scales
        return None

    def g(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Controlled output ``z = T_a + b`` (or ``z = T_a`` un-augmented).

        Only the air node is exposed for control — that's what users
        perceive and what their setpoints refer to.  Wall, slab and
        filter nodes are internal model states.
        """
        n = self._n_rooms
        if not self._augment_offsets or len(x) < self._nx:
            return x[:n].copy()
        b_start = self._offset_block_start
        return x[:n] + x[b_start: b_start + n]

    def gm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Continuous-time output ``gm = T_a + b`` (matches ``g``)."""
        n = self._n_rooms
        if not self._augment_offsets or len(x) < self._nx:
            return x[:n].copy()
        b_start = self._offset_block_start
        return x[:n] + x[b_start: b_start + n]

    def hm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Measurement function ``ym = T_a + b``.

        Only the air node is observed; wall, slab, and filter nodes
        are reconstructed by the EKF from the dynamics in :meth:`f`.
        """
        n = self._n_rooms
        if not self._augment_offsets or len(x) < self._nx:
            return x[:n].copy()
        b_start = self._offset_block_start
        return x[:n] + x[b_start: b_start + n]

    # ── Analytic Jacobians (override default FD for efficiency) ──────────

    def dfdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """``∂f/∂x`` for the augmented state
        ``[T (n), φ (m), b (n)]``.

        Block structure (rows in same order as state):

        * physical n × n block: ``self._F`` (from HouseModel),
          augmented on the diagonal by the Sherman–Grimsrud
          overlay (linearly-implicit treatment, frozen at the current
          state).
        * filter→physical: each filtered source contributes
          ``∂Q_j/∂φ_j / C_target,i`` to the cell where its room's
          heat lands (air or slab depending on UFH).
        * filter→filter: diagonal ``-1/τ_em``.
        * offset block: zero drift, so its rows / columns are zero.

        The cross-coupling ``∂Q_j/∂φ_j`` is the slope of the heat-
        source power function at the current effective fraction.  For
        electric heaters it's a constant ``P_max · η`` (independent of
        T_out).  For heat pumps the slope depends on T_out (linear in
        u for ``thermal_power``; smooth-sigmoid for cooling) so we
        compute it via a small central finite difference — the cost is
        two extra ``thermal_power`` evaluations per filtered source per
        ``dfdx`` call, which is negligible.
        """
        n = self._n_rooms
        m = self._n_filtered
        T_phys = x[:n]
        outdoor_temp = float(d[0])
        if self._wind_speed is not None:
            delta_ua = self._infiltration_delta_ua(outdoor_temp, T_phys)
            F_eff = self._F.copy()
            F_eff[self._diag_n_idx, self._diag_n_idx] -= delta_ua / self._C_cap
        else:
            F_eff = self._F

        nx_unaug = n + m
        J = np.zeros((self._nx, self._nx))
        J[:n, :n] = F_eff

        if m > 0:
            theta = p if len(p) > 0 else self._theta
            heater_scale_factors = self._get_heater_scales(theta)

            eps = 1e-6
            for k, j in enumerate(self._filtered_source_indices):
                src = self._sources[j]
                phi_j = x[n + k]
                scale_j = heater_scale_factors[j] if heater_scale_factors is not None else 1.0
                eff_plus = scale_j * (phi_j + eps)
                eff_minus = scale_j * (phi_j - eps)
                if self._src_can_cool[j]:
                    p_plus = src.smooth_thermal_power(eff_plus, outdoor_temp, self._k_sigmoid)
                    p_minus = src.smooth_thermal_power(eff_minus, outdoor_temp, self._k_sigmoid)
                else:
                    p_plus = src.thermal_power(max(0.0, eff_plus), outdoor_temp)
                    p_minus = src.thermal_power(max(0.0, eff_minus), outdoor_temp)
                dpdphi = (p_plus - p_minus) / (2.0 * eps)
                i_room = self._src_room_idx[j]
                J[i_room, n + k] += dpdphi / self._C_cap[i_room]

            # Filter-block diagonal: -1/τ_em (precomputed).
            J[self._filter_diag_idx, self._filter_diag_idx] = self._neg_inv_taus

        if not self._augment_offsets:
            return J[:nx_unaug, :nx_unaug]
        return J

    def dhmdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """``∂hm/∂x`` for ``ym = T + b``.

        Identity on the temperature block (rows 0..n−1) and identity on
        the offset block (rows ``b_start..b_start+n``).  The filter
        block contributes nothing to the measurement.
        """
        return self._H_const

    @property
    def dgmdx_const(self) -> np.ndarray:
        """Pre-computed constant output Jacobian H = ∂gm/∂x.

        ``gm`` is linear in ``x`` (it reads the temperature block and,
        in augmented mode, adds the offset block), so this Jacobian
        is constant — it does not depend on the current state.

        Un-augmented (state ``[T, φ]``)::

            H = [I_n | 0_{n, m}]

        Augmented (state ``[T, φ, b]``)::

            H[:, :n]                = I_n   (T block)
            H[:, b_start:b_start+n] = I_n   (offset block)

        Returns shape ``(nz, nx)`` where ``nz = n_rooms``.
        """
        return self._H_const

    def dgmdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytical ``∂gm/∂x``, shape ``(nz, nx)``.

        ``gm`` is a linear function of the state ``x`` (it reads the
        air-temperature and, in augmented mode, the offset block), so the
        Jacobian is constant and equal to :attr:`dgmdx_const`.  This
        method overrides the default finite-difference implementation in
        :class:`~mbc.models.ContinuousDiscreteModel` and is called by the
        mbc EOCP for the Mayer-term gradient and the soft-output
        constraint Jacobian.
        """
        return self.dgmdx_const

    def dgmdu(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytical ``∂gm/∂u``, shape ``(nz, nu)``.

        ``gm`` does not depend on ``u`` — the output is purely a function
        of the state ``x``.  Returns a zero matrix of shape ``(nz, nu)``.
        This overrides the default finite-difference in
        :class:`~mbc.models.ContinuousDiscreteModel`.
        """
        return self._dgmdu_zero

    def dfdu(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytical ``∂f/∂u``, shape ``(nx, nu)``.

        Replaces the default finite-difference implementation in
        :class:`~mbc.models.ContinuousDiscreteModel` with an exact,
        closed-form Jacobian.

        For **heating-only** un-filtered sources the derivative is::

            ∂f[target] / ∂u_j = thermal_power(1, T_out) · scale_j / C_cap[target]

        (u ≥ 0 is guaranteed by the box constraint u_min = 0, so
        max(0, u·scale) is differentiable everywhere in the feasible region.)

        For **cooling-capable** (heat-pump) sources the derivative of the
        smooth sigmoid is::

            ∂f[target] / ∂u_j = (Q_heat + Q_cool) · k · σ(1−σ) · scale_j / C_cap[target]

        For **filtered** sources (emitter lag τ > 0)::

            ∂f[3n+k_filter] / ∂u_j = 1 / τ_em[k_filter]
        """
        outdoor_temp = float(d[0])
        n = self._n_rooms
        theta = p if len(p) > 0 else self._theta
        heater_scale_factors = self._get_heater_scales(theta)

        J = np.zeros((self._nx, self.nu))
        for j, src in enumerate(self._sources):
            i = self._src_room_idx[j]
            k_filter = self._filter_idx_for_source[j]
            if k_filter >= 0:
                # Filtered source: dφ_k/dt = (u_j − φ_k)/τ_k  →  ∂/∂u_j = 1/τ_k
                J[n + k_filter, j] = self._inv_emitter_taus[k_filter]
            else:
                scale = heater_scale_factors[j] if heater_scale_factors is not None else 1.0
                eff_u = u[j] * scale
                if self._src_can_cool[j]:
                    q_heat = src.thermal_power(1.0, outdoor_temp)
                    q_cool = src._q_cool_const
                    if q_heat > 0.0 and q_cool > 0.0:
                        k_sig = self._k_sigmoid + max(0.0, math.log(q_heat / q_cool))
                        offset_sig = math.log(q_cool / q_heat)
                        sig_val = 1.0 / (1.0 + math.exp(-(k_sig * eff_u + offset_sig)))
                        J[i, j] = (
                            (q_heat + q_cool) * k_sig * sig_val * (1.0 - sig_val)
                            * scale / self._C_cap[i]
                        )
                    elif eff_u >= 0.0:
                        J[i, j] = src.thermal_power(1.0, outdoor_temp) * scale / self._C_cap[i]
                else:
                    J[i, j] = src.thermal_power(1.0, outdoor_temp) * scale / self._C_cap[i]
        return J

    # ── Application-layer helpers ────────────────────────────────────────

    @property
    def x(self) -> list[float]:
        """Current state vector as a list of floats.

        Layout (1R1C + B2): un-augmented ``[T (n), φ (m)]``; augmented
        adds a ``b (n)`` block at the end.  ``m`` is the number of
        filtered heat sources (``self._n_filtered``).  ``φ`` is
        initialised from the filter cache (zero on cold start).
        """
        temps = [self._model.rooms[name].temperature for name in self._room_list]
        phi = self._filter_state.tolist() if self._n_filtered > 0 else []
        if not self._augment_offsets:
            return temps + phi
        return temps + phi + self._offset_state.tolist()

    @x.setter
    def x(self, val: list[float]) -> None:
        n = self._n_rooms
        m = self._n_filtered
        if len(val) < n:
            raise ValueError(f"Expected at least {n} state values, got {len(val)}")

        for i, name in enumerate(self._room_list):
            self._model.rooms[name].temperature = float(val[i])

        if m > 0:
            if len(val) >= n + m:
                self._filter_state = np.array(
                    val[n: n + m], dtype=float,
                )
            else:
                self._filter_state = np.zeros(m, dtype=float)

        if self._augment_offsets and len(val) >= self.nx:
            b_start = self._offset_block_start
            self._offset_state = np.array(
                val[b_start: b_start + n], dtype=float,
            )

    @property
    def x_ref(self) -> np.ndarray:
        """Room setpoints as an ``(nz,) = (n_rooms,)`` ndarray.

        Tracking is on the air node (what the user perceives and what
        the setpoint refers to), so the reference has one entry per
        room — not per state.
        """
        return np.array(
            [self._model.rooms[name].setpoint for name in self._room_list]
        )

    def comfort_corridor_bounds(
        self,
        fallback_offset: float = 0.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Per-room comfort corridor bounds used for soft output constraints.

        Each room's comfort region is [setpoint - offset, setpoint + offset]
        where offset is the per-room comfort_offset attribute. The fallback_offset
        parameter is ignored (kept for API compatibility).
        """
        lows: List[float] = []
        highs: List[float] = []
        for name in self._room_list:
            room = self._model.rooms[name]
            sp = float(room.setpoint)
            offset = getattr(room, "comfort_offset", None)
            if offset is None:
                # Fallback should never happen with proper initialization
                offset = 2.0
            low = sp - float(offset)
            high = sp + float(offset)
            lows.append(low)
            highs.append(high)
        return np.array(lows, dtype=float), np.array(highs, dtype=float)

    @property
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """Input box constraints derived from each source's configured hvac_mode."""
        u_min = np.array([src.u_min for src in self._sources])
        u_max = np.array([src.u_max for src in self._sources])
        return u_min, u_max

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
        if not self._augment_offsets or len(self._offset_state) < self._n_rooms:
            return {name: 0.0 for name in self._room_list}
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

    def compute_u_eq(
        self,
        x: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Steady-state input that would maintain the temperature in x.

        Solves f_T(x, u_eq, d) = 0 for the temperature block by inverting
        each source's power function exactly:
        - Cooling-capable sources: closed-form sigmoid inverse.
        - Heating-only sources: linear inverse.

        The result is clipped to each source's [u_min, u_max].

        For filtered sources (τ_em > 0) the equilibrium satisfies φ_ss = u_eq
        (the filter state equals the commanded input at steady state).  The
        same inversion logic is used as for un-filtered sources; the returned
        u_eq[j] is both the commanded input and the equilibrium filter state.
        Callers that build the operating-point state x_ss should set
        x_ss[n + k_filter] = u_eq[j] for each filtered source j.

        This is used as the QP linearisation point so that the sigmoid's
        local gradient matches the expected operating region, reducing model
        mismatch during large transients.
        """
        # Temperature tendency at zero commanded input captures net heat loss
        # (structural exchange + disturbances, no heat-source contribution).
        # For filtered sources the heat contribution in f() uses the filter
        # state φ (from x), not u.  With φ = 0 at the setpoint, drift_zero
        # gives the full q_req each source must supply — correct for both
        # current-state and setpoint-state calls.
        u_zero = np.zeros(self.nu, dtype=float)
        drift_zero = self.f(x, u_zero, d, p, t)

        outdoor_temp = float(d[0])
        n = self._n_rooms
        theta = p if len(p) > 0 else self._theta
        heater_scales = self._get_heater_scales(theta)

        u_eq = np.zeros(self.nu, dtype=float)
        for j, src in enumerate(self._sources):
            i = self._src_room_idx[j]

            scale = heater_scales[j] if heater_scales is not None else 1.0
            # Required power [W] to neutralise the net heat loss in this room.
            # drift_zero[i] < 0 means the room is losing heat → q_req > 0.
            q_req = -drift_zero[i] * self._C_cap[i]

            if self._src_can_cool[j]:
                q_heat = src.thermal_power(1.0, outdoor_temp)
                q_cool = src._q_cool_const
                if q_heat > 0.0 and q_cool > 0.0:
                    k_sig = self._k_sigmoid + max(0.0, math.log(q_heat / q_cool))
                    offset_sig = math.log(q_cool / q_heat)
                    # Clamp to the feasible power range then invert the sigmoid.
                    q_clamped = max(-q_cool, min(q_heat, q_req / scale))
                    sig_target = (q_clamped + q_cool) / (q_heat + q_cool)
                    sig_target = max(1e-9, min(1.0 - 1e-9, sig_target))
                    eff_u_eq = (math.log(sig_target / (1.0 - sig_target)) - offset_sig) / k_sig
                    u_eq[j] = max(-1.0, min(1.0, eff_u_eq / scale))
                else:
                    # Degenerate: fall back to linear inverse.
                    p_rated = src.thermal_power(1.0, outdoor_temp)
                    if p_rated > 0.0:
                        u_eq[j] = max(0.0, min(1.0, q_req / (p_rated * scale)))
            else:
                p_rated = src.thermal_power(1.0, outdoor_temp)
                if p_rated > 0.0:
                    u_eq[j] = max(0.0, min(1.0, q_req / (p_rated * scale)))

        return u_eq


# ── Helper ───────────────────────────────────────────────────────────────────

def _diag_cvx(n: int, v: float) -> _cvxmat:
    """Return an n×n diagonal cvxopt matrix with v on the diagonal."""
    m = _cvxmat(0.0, (n, n))
    for i in range(n):
        m[i, i] = v
    return m


# ── Innovation-capturing EKF wrapper ────────────────────────────────────────

class _InnovationEKF(ContinuousDiscreteEKF):
    """CD-EKF that records the Kalman innovation after each measurement fusion.

    CDLinearizedMPCController calls ``estimator.step(y, u_prev, d_prev, p, t)``
    which combines predict + update.  This subclass intercepts that call to
    compute and store ``ν = y − hm(x̂⁻)`` between the two phases, making the
    innovation available via the ``last_innovation`` property after each step.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_innovation: Optional[List[float]] = None

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """Innovation ν = y − hm(x̂⁻) from the most recent step, or None."""
        return self._last_innovation

    def step(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
        mask: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        self.predict(u, d, p, t)
        x_prior = self._x_np.copy()
        y_hat = self._model.hm(x_prior, u, d, p, 0.0)
        self._last_innovation = (np.asarray(y, dtype=float) - y_hat).tolist()
        return self.update(y, u, d, p, mask=mask)


# ── Absolute-input OCP ───────────────────────────────────────────────────────

class _AbsoluteInputOCP(OptimalControlProblem):
    """OCP that penalises the absolute input ‖u_abs‖²_R instead of ‖u_dev‖²_R.

    In deviation coordinates u_dev = u_abs − u_ss, the cost term
    ‖u_dev + u_ss‖²_R = ‖u_abs‖²_R expands to

        ‖u_dev‖²_R  +  2·u_ss'·R·u_dev  +  ‖u_ss‖²_R

    The Hessian (R_bar) is unchanged.  The linear correction
    ``R_bar · tile(u_ss, N)`` is added to the QP gradient f_u before
    solving so the dynamics linearisation at (x_ss, u_ss, d_ss) is fully
    preserved.  Pass ``u_ss`` to :meth:`solve`; if omitted the call falls
    back to the standard parent behaviour.
    """

    def solve(
        self,
        x0,
        D,
        x_ref,
        u_prev=None,
        u_ss: Optional[np.ndarray] = None,
        x_ref_dev_seq: Optional[np.ndarray] = None,
        offset_seq: Optional[np.ndarray] = None,
        q_scale_seq: Optional[np.ndarray] = None,
        r_scale_seq: Optional[np.ndarray] = None,
        price_seq: Optional[np.ndarray] = None,
        elec_heat: Optional[np.ndarray] = None,
        elec_cool: Optional[np.ndarray] = None,
        bid_mask: Optional[np.ndarray] = None,
        price_weight: float = 0.0,
        dt_h: float = 0.25,
    ):
        has_price = (
            price_seq is not None
            and price_weight > 0.0
            and elec_heat is not None
        )
        # Fast path: no time-varying parameters, no u_ss correction, no price.
        if (
            (u_ss is None or np.allclose(u_ss, 0.0))
            and x_ref_dev_seq is None
            and offset_seq is None
            and q_scale_seq is None
            and r_scale_seq is None
            and not has_price
        ):
            return super().solve(x0, D, x_ref, u_prev)

        N = self._N
        nx = self._model.nx
        nu = self._model.nu
        nd = self._model.nd
        Cz = _np_to_cvx(self._model.Cz)
        nz = Cz.size[0]

        if isinstance(x0, np.ndarray):
            x0 = _np_to_cvx(x0.reshape(-1, 1))
        if isinstance(x_ref, np.ndarray):
            x_ref = _np_to_cvx(x_ref.reshape(-1, 1))

        Ad = _np_to_cvx(self._model.Ad)
        Bd = _np_to_cvx(self._model.Bd)
        Ed = _np_to_cvx(self._model.Ed)

        # State-prediction matrices  X = Ψ x₀ + Γ U + Λ D
        Ad_pow = [_eye(nx)]
        for _ in range(N):
            Ad_pow.append(Ad * Ad_pow[-1])

        Psi    = _zeros(N * nx, nx)
        Gamma  = _zeros(N * nx, N * nu)
        Lambda = _zeros(N * nx, N * nd)

        for k in range(N):
            for i in range(nx):
                for j in range(nx):
                    Psi[k * nx + i, j] = Ad_pow[k + 1][i, j]
            for j_step in range(k + 1):
                Ak_j = Ad_pow[k - j_step]
                AB = Ak_j * Bd
                AE = Ak_j * Ed
                for i in range(nx):
                    for jj in range(nu):
                        Gamma[k * nx + i, j_step * nu + jj] = AB[i, jj]
                    for jj in range(nd):
                        Lambda[k * nx + i, j_step * nd + jj] = AE[i, jj]

        Cz_bar = _block_diag(Cz, N)
        CG = Cz_bar * Gamma
        CP = Cz_bar * Psi
        CL = Cz_bar * Lambda

        # ── Time-varying cost matrices ────────────────────────────────────
        # Build Q_bar, R_bar, z_ref_bar, z_min_tiled, z_max_tiled for the QP.
        # When no time-varying parameters are provided these collapse to the
        # same result as the original static helpers, preserving correctness
        # and backward compatibility.
        has_varying = (
            x_ref_dev_seq is not None
            or offset_seq is not None
            or q_scale_seq is not None
            or r_scale_seq is not None
        )

        if has_varying:
            # Q_bar: block-diagonal with per-step, per-output Q weights.
            # Terminal step uses P = terminal_weight × Q_base per element.
            Q_bar = _zeros(N * nz, N * nz)
            for k in range(N):
                base_diag = self._P if k == N - 1 else self._Q
                for i in range(nz):
                    val = base_diag[i, i]
                    if q_scale_seq is not None:
                        val *= float(q_scale_seq[k, i])
                    Q_bar[k * nz + i, k * nz + i] = val

            # R_bar: block-diagonal with per-step, per-source R weights.
            R_bar = _zeros(N * nu, N * nu)
            for k in range(N):
                for i in range(nu):
                    val = self._R[i, i]
                    if r_scale_seq is not None:
                        val *= float(r_scale_seq[k, i])
                    R_bar[k * nu + i, k * nu + i] = val

            # z_ref_bar: stacked per-step reference in deviation coordinates.
            if x_ref_dev_seq is not None:
                z_ref_bar = _cvxmat(
                    np.asarray(x_ref_dev_seq, dtype=float).reshape(-1).tolist(),
                    (N * nz, 1),
                )
            else:
                z_ref = Cz * x_ref
                z_ref_bar = _tile_column(z_ref, N)
        else:
            Q_bar = _block_diag_terminal(self._Q, self._P, N)
            R_bar = _block_diag(self._R, N)
            z_ref = Cz * x_ref
            z_ref_bar = _tile_column(z_ref, N)

        Z_free   = CP * x0 + CL * D
        e_free   = Z_free - z_ref_bar

        H_uu = CG.T * Q_bar * CG + R_bar
        f_u  = CG.T * Q_bar * e_free

        # Linear correction: penalise ‖u_dev + u_ss‖²_R instead of ‖u_dev‖²_R.
        # Each nu-block of the gradient gets R[i,i] * r_scale[k] * u_ss[i].
        if u_ss is not None and not np.allclose(u_ss, 0.0):
            for k in range(N):
                for i in range(nu):
                    r_val = self._R[i, i]
                    if r_scale_seq is not None:
                        r_val *= float(r_scale_seq[k, i])
                    f_u[k * nu + i] = f_u[k * nu + i] + r_val * u_ss[i]

        if self._S is not None:
            if u_prev is None:
                u_prev = _zeros(nu, 1)
            d0_shift = _zeros(N * nu, 1)
            d0_shift[:nu] = -u_prev
            H_uu += self._D_diff.T * self._S_bar * self._D_diff
            f_u  += self._D_diff.T * self._S_bar * d0_shift

        # Full QP decision variable Z = [U; ε]
        n_U   = N * nu
        n_eps = N * nz
        n_Z   = n_U + n_eps

        H = _zeros(n_Z, n_Z)
        for i in range(n_U):
            for j in range(n_U):
                H[i, j] = H_uu[i, j]
        for i in range(n_eps):
            H[n_U + i, n_U + i] = self._rho

        f = _zeros(n_Z, 1)
        for i in range(n_U):
            f[i] = f_u[i]

        # Input box constraints (unchanged)
        u_min_np, u_max_np = self._model.u_bounds
        u_min_tiled = _tile_column(_np_to_cvx(u_min_np.reshape(-1, 1)), N)
        u_max_tiled = _tile_column(_np_to_cvx(u_max_np.reshape(-1, 1)), N)

        # Soft output constraints: time-varying corridor centred on z_ref[k]
        # with half-width offset[k] per room.  Falls back to static scalar
        # self._y_offset when no offset_seq is provided.
        if has_varying:
            z_min_data: List[float] = []
            z_max_data: List[float] = []
            for k in range(N):
                if x_ref_dev_seq is not None:
                    z_ref_k = np.asarray(x_ref_dev_seq[k], dtype=float)
                else:
                    z_ref_k = np.array(list(Cz * x_ref), dtype=float).flatten()
                if offset_seq is not None:
                    off_k = np.asarray(offset_seq[k], dtype=float)
                else:
                    off_k = np.full(nz, self._y_offset, dtype=float)
                z_min_data.extend((z_ref_k - off_k).tolist())
                z_max_data.extend((z_ref_k + off_k).tolist())
            z_min_tiled = _cvxmat(z_min_data, (N * nz, 1))
            z_max_tiled = _cvxmat(z_max_data, (N * nz, 1))
        else:
            z_min = z_ref - _cvxmat(self._y_offset, (nz, 1))
            z_max = z_ref + _cvxmat(self._y_offset, (nz, 1))
            z_min_tiled = _tile_column(z_min, N)
            z_max_tiled = _tile_column(z_max, N)

        n_ineq = 2 * n_U + 2 * n_eps + n_eps
        G_qp = _zeros(n_ineq, n_Z)
        h_qp = _zeros(n_ineq, 1)

        row = 0
        for i in range(n_U):
            G_qp[row + i, i] = -1.0
            h_qp[row + i] = -u_min_tiled[i]
        row += n_U
        for i in range(n_U):
            G_qp[row + i, i] = 1.0
            h_qp[row + i] = u_max_tiled[i]
        row += n_U
        for i in range(n_eps):
            for j in range(n_U):
                G_qp[row + i, j] = -CG[i, j]
            G_qp[row + i, n_U + i] = -1.0
            h_qp[row + i] = -z_min_tiled[i] + Z_free[i]
        row += n_eps
        for i in range(n_eps):
            for j in range(n_U):
                G_qp[row + i, j] = CG[i, j]
            G_qp[row + i, n_U + i] = -1.0
            h_qp[row + i] = z_max_tiled[i] - Z_free[i]
        row += n_eps
        for i in range(n_eps):
            G_qp[row + i, n_U + i] = -1.0
            h_qp[row + i] = 0.0

        # ── Price-aware linear cost term ──────────────────────────────────────
        # For non-negative sources: add α·pₖ·cᵢ·Δtₕ directly to the gradient.
        # For bidirectional sources: augment the decision vector with slack
        # variables s⁺, s⁻ ≥ 0 satisfying u_abs = s⁺ − s⁻.
        #
        # Complementarity (s⁺·s⁻ = 0) is enforced by the price penalty alone:
        # for any pₖ > 0, the linear cost α·pₖ·(c_heat·s⁺ + c_cool·s⁻) drives
        # whichever slack is not needed to zero.  We do NOT add quadratic or
        # linear penalties on (s⁺+s⁻) — those would be equivalent to penalising
        # |u_abs| directly (an implicit energy cost that distorts the optimisation).
        #
        # Upper bounds s⁺ ≤ u_max, s⁻ ≤ |u_min| bound the QP for pₖ = 0 (no
        # gradient on slacks) without adding any energy-like penalty.
        # Prices are clamped to ≥ 0; negative spot prices yield zero cost, letting
        # comfort/tracking terms drive the decision (the controller can freely use
        # electricity without it artificially increasing the objective).
        if has_price:
            price_arr = np.maximum(np.asarray(price_seq, dtype=float), 0.0)
            n_price = len(price_arr)

            bid_list: List[int] = []
            if bid_mask is not None:
                bid_list = [i for i in range(nu) if bid_mask[i]]
            bid_set = set(bid_list)
            n_bid = len(bid_list)

            # Non-negative sources: direct linear term on u_dev.
            for k in range(N):
                p_k = float(price_arr[min(k, n_price - 1)])
                for i in range(nu):
                    if i not in bid_set:
                        c_h = float(elec_heat[i])
                        f[k * nu + i] = f[k * nu + i] + price_weight * p_k * c_h * dt_h

            if n_bid == 0:
                sol = _cvxsolvers.qp(H, f, G_qp, h_qp)
            else:
                # Bidirectional sources: augment Z = [U; ε; S⁺; S⁻].
                n_S = N * n_bid
                n_Z_aug = n_U + n_eps + 2 * n_S

                # ── Augmented Hessian (no slack quadratic term) ───────────────
                H_aug = _zeros(n_Z_aug, n_Z_aug)
                for i in range(n_U + n_eps):
                    for j in range(n_U + n_eps):
                        H_aug[i, j] = H[i, j]
                # Slack diagonal: zero — price penalty alone enforces complementarity.

                # ── Augmented gradient (price terms only, no slack linear bias) ─
                f_aug = _zeros(n_Z_aug, 1)
                for i in range(n_U + n_eps):
                    f_aug[i] = f[i]
                for k in range(N):
                    p_k = float(price_arr[min(k, n_price - 1)])
                    for jj, src_i in enumerate(bid_list):
                        c_h = float(elec_heat[src_i])
                        c_c = float(elec_cool[src_i]) if elec_cool is not None else c_h
                        sp = n_U + n_eps + k * n_bid + jj
                        sm = n_U + n_eps + n_S + k * n_bid + jj
                        f_aug[sp] = f_aug[sp] + price_weight * p_k * c_h * dt_h
                        f_aug[sm] = f_aug[sm] + price_weight * p_k * c_c * dt_h

                # ── Augmented inequality constraints ──────────────────────────
                # Rows: existing (u box + ε) | s⁺ ≥ 0 | s⁻ ≥ 0 | s⁺ ≤ umax | s⁻ ≤ |umin|
                # The upper bounds are essential when pₖ = 0 to keep the QP bounded.
                u_min_np, u_max_np = self._model.u_bounds
                n_ineq_aug = n_ineq + 4 * n_S
                G_aug = _zeros(n_ineq_aug, n_Z_aug)
                h_aug = _zeros(n_ineq_aug, 1)
                for i in range(n_ineq):
                    for j in range(n_U + n_eps):
                        G_aug[i, j] = G_qp[i, j]
                    h_aug[i] = h_qp[i]
                # s⁺ ≥ 0 and s⁻ ≥ 0 (non-negativity)
                for p in range(2 * n_S):
                    G_aug[n_ineq + p, n_U + n_eps + p] = -1.0
                # s⁺ ≤ u_max_i and s⁻ ≤ |u_min_i| (upper bounds)
                for k in range(N):
                    for jj, src_i in enumerate(bid_list):
                        sp = n_U + n_eps + k * n_bid + jj
                        sm = n_U + n_eps + n_S + k * n_bid + jj
                        ub_row_sp = n_ineq + 2 * n_S + k * n_bid + jj
                        ub_row_sm = n_ineq + 2 * n_S + n_S + k * n_bid + jj
                        G_aug[ub_row_sp, sp] = 1.0
                        h_aug[ub_row_sp] = float(u_max_np[src_i])
                        G_aug[ub_row_sm, sm] = 1.0
                        h_aug[ub_row_sm] = float(abs(u_min_np[src_i]))

                # ── Equality: u_dev[k,i] − s⁺[k,j] + s⁻[k,j] = −u_ss[i] ────
                n_eq = N * n_bid
                A_eq = _zeros(n_eq, n_Z_aug)
                b_eq = _zeros(n_eq, 1)
                for k in range(N):
                    for jj, src_i in enumerate(bid_list):
                        row_eq = k * n_bid + jj
                        A_eq[row_eq, k * nu + src_i] = 1.0
                        A_eq[row_eq, n_U + n_eps + k * n_bid + jj] = -1.0
                        A_eq[row_eq, n_U + n_eps + n_S + k * n_bid + jj] = 1.0
                        b_eq[row_eq] = -(float(u_ss[src_i]) if u_ss is not None
                                         else 0.0)

                sol = _cvxsolvers.qp(H_aug, f_aug, G_aug, h_aug, A_eq, b_eq)
        else:
            sol = _cvxsolvers.qp(H, f, G_qp, h_qp)

        if sol["status"] != "optimal":
            import warnings
            warnings.warn(
                f"_AbsoluteInputOCP.solve: QP solver returned status "
                f"'{sol['status']}'; returning zero inputs as fallback.",
                RuntimeWarning,
                stacklevel=2,
            )
            U_flat = _zeros(n_U, 1)
        else:
            U_flat = sol["x"][:n_U]

        X_flat = Psi * x0 + Gamma * U_flat + Lambda * D
        return U_flat, X_flat


# ── Forecast-aware MPC controller ───────────────────────────────────────────

class _ForecastAwareMPCController(CDLinearizedMPCController):
    """CDLinearizedMPCController extended to accept a time-varying disturbance
    forecast over the prediction horizon.

    When ``D_forecast`` (shape ``(N, nd)``) is passed to ``step()``, the
    deviation disturbance fed to the QP is ``D_dev[k] = D_forecast[k] - d_ss``
    instead of the constant-hold zero vector.

    Uses ``_AbsoluteInputOCP`` so the R-cost penalises the absolute input
    ``‖u_abs‖²_R`` rather than the deviation ``‖u_dev‖²_R``.  With Q = 0 this
    gives pure zone control: the minimum-cost inside the comfort corridor is
    u_abs = 0 (no heating or cooling).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        ocp = self._ocp
        self._ocp = _AbsoluteInputOCP(
            model=self._lin_model,
            N=self._N,
            Q=ocp._Q,
            R=ocp._R,
            P=ocp._P,
            S=ocp._S,
            rho=ocp._rho,
            y_offset=ocp._y_offset,
        )

    def step(
        self,
        y: np.ndarray,
        d: np.ndarray,
        p: Optional[np.ndarray] = None,
        t: float = 0.0,
        D_forecast: Optional[np.ndarray] = None,
        x_ref_abs_seq: Optional[np.ndarray] = None,
        offset_seq: Optional[np.ndarray] = None,
        q_scale_seq: Optional[np.ndarray] = None,
        r_scale_seq: Optional[np.ndarray] = None,
        price_seq: Optional[np.ndarray] = None,
        elec_heat: Optional[np.ndarray] = None,
        elec_cool: Optional[np.ndarray] = None,
        bid_mask: Optional[np.ndarray] = None,
        price_weight: float = 0.0,
        dt_h: float = 0.25,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run one MPC step.

        Parameters
        ----------
        x_ref_abs_seq : (N, n_rooms) ndarray, optional
            Absolute setpoint for each room at each horizon step.  When
            provided the QP uses a time-varying reference instead of the
            static ``x_ref_abs``.  ``None`` falls back to static behaviour.
        offset_seq : (N, n_rooms) ndarray, optional
            Comfort corridor half-width [°C] for each room at each step.
        q_scale_seq : (N, n_rooms) ndarray, optional
            Per-step multiplier applied to the global Q diagonal per room.
        r_scale_seq : (N, n_sources) ndarray, optional
            Per-step multiplier applied to the global R diagonal per source.
        price_seq : (N,) ndarray, optional
            Forecasted electricity price at each horizon step [currency/kWh].
        elec_heat : (nu,) ndarray, optional
            Electrical power drawn per unit of positive input [W/unit] per source.
        elec_cool : (nu,) ndarray, optional
            Electrical power drawn per unit of |negative input| [W/unit] per source.
        bid_mask : (nu,) bool ndarray, optional
            True for sources that can take negative inputs (bidirectional).
        price_weight : float
            Dimensionless scaling factor α for the price term.
        dt_h : float
            OCP time step in hours (dt / 3600).
        """
        y = np.asarray(y, dtype=float).reshape(self._model.nym)
        d_now = np.asarray(d, dtype=float).reshape(self._model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)

        est_out = self._estimator.step(y, self._u_prev, self._d_prev, p_, t)
        x_hat = np.asarray(est_out[0], dtype=float).reshape(self._model.nx)

        # Linearise at the equilibrium (x_ss = setpoint, u_ss = u_eq, d_ss = d_now)
        # so the Jacobians are accurate during transients.  _AbsoluteInputOCP adds
        # the linear correction R·u_ss to the QP gradient so the R-cost penalises
        # ‖u_dev + u_ss‖²_R = ‖u_abs‖²_R instead of ‖u_dev‖²_R (which would
        # drive u_abs → u_ss, not zero).  With Q = 0 this gives pure zone control.
        n = self._model._n_rooms
        x_ss = self._x_ref_abs.copy()          # setpoint temperatures
        u_ss = self._model.compute_u_eq(x_ss, d_now, p_, t)
        # At equilibrium each filtered source's lag state equals u_eq[j].
        for j in range(self._model.nu):
            k_f = self._model._filter_idx_for_source[j]
            if k_f >= 0:
                x_ss[n + k_f] = u_ss[j]
        d_ss = d_now.copy()

        lin = linearize_cd_model(self._model, x_ss, u_ss, d_ss, p_, t)
        disc = discretize_cd_linearization(lin, self._dt)

        # x_ss IS the current setpoint; driving deviation to zero is identical
        # to tracking x_ref_abs in absolute coordinates.
        x_ref_dev = np.zeros(self._model.nx, dtype=float)
        self._lin_model.update(
            Ad=disc["Ad"],
            Bd=disc["Bd"],
            Ed=disc["Ed"],
            Cm=disc["Cm"],
            Cz=disc["Cz"],
            Qd=disc["Qd"],
            Rm=np.asarray(self._model.Rm, dtype=float),
            x_ss=x_ss,
            u_ss=u_ss,
            d_ss=d_ss,
            x_ref=x_ref_dev,
        )

        if D_forecast is not None:
            D_abs = np.asarray(D_forecast, dtype=float).reshape(self._N, self._model.nd)
            D_dev_np = D_abs - d_ss.reshape(1, -1)
        else:
            D_dev_np = np.zeros((self._N, self._model.nd), dtype=float)

        self._last_D_dev = D_dev_np.copy()
        D_dev = _cvxmat(D_dev_np.reshape(-1).tolist(), (self._N * self._model.nd, 1))

        # Non-zero initial condition: the full tracking error from the setpoint.
        # Combined with x_ref_dev = 0, the QP drives x_hat toward x_ss over
        # the horizon.  u_prev_dev = u_prev − u_ss converts the previous
        # absolute action to deviation coordinates for the S-penalty.
        x0_dev = (x_hat - x_ss).astype(float)
        u_prev_dev_np = self._u_prev - u_ss
        u_prev_dev = _cvxmat(u_prev_dev_np.tolist(), (self._model.nu, 1))

        # Convert absolute time-varying setpoints to deviation coordinates so
        # the QP sees them relative to the linearisation point x_ss.
        # x_ref_dev_seq[k] = x_ref_abs_seq[k, :nz] − x_ss[:nz]
        if x_ref_abs_seq is not None:
            nz = self._model.nz
            x_ref_dev_seq = (
                np.asarray(x_ref_abs_seq, dtype=float) - x_ss[:nz].reshape(1, -1)
            )
        else:
            x_ref_dev_seq = None

        U_dev, X_dev = self._ocp.solve(
            x0=x0_dev,
            D=D_dev,
            x_ref=x_ref_dev,
            u_prev=u_prev_dev,
            u_ss=u_ss,
            x_ref_dev_seq=x_ref_dev_seq,
            offset_seq=offset_seq,
            q_scale_seq=q_scale_seq,
            r_scale_seq=r_scale_seq,
            price_seq=price_seq,
            elec_heat=elec_heat,
            elec_cool=elec_cool,
            bid_mask=bid_mask,
            price_weight=price_weight,
            dt_h=dt_h,
        )

        U_dev_np = np.array(list(U_dev), dtype=float).reshape(self._N, self._model.nu)
        X_dev_np = np.array(list(X_dev), dtype=float).reshape(self._N, self._model.nx)

        U_abs = U_dev_np + u_ss.reshape(1, -1)
        X_abs = X_dev_np + x_ss.reshape(1, -1)

        # Safety clip: QP enforces deviation bounds shifted by u_ss; clamp with
        # absolute physical limits as a guard against numerical drift.
        u_min_arr, u_max_arr = self._model.u_bounds
        U_abs = np.clip(U_abs, u_min_arr.reshape(1, -1), u_max_arr.reshape(1, -1))
        u_abs = U_abs[0].copy()

        self._u_prev = u_abs.copy()
        self._d_prev = d_now.copy()

        return u_abs, U_abs, X_abs


# ============================================================
# House-heating linearised MPC facade
# ============================================================

class HeatingMPCController:
    """
    Application facade for house-heating linearised CD-MPC.

    Builds a HouseThermalSDE, _InnovationEKF (CD-EKF), and
    CDLinearizedMPCController, then provides the coordinator-facing API:

      actions = controller.compute(outdoor_temp, solar_gains, now, outdoor_forecast)

    The control loop at each step:
      1. CDLinearizedMPCController.step(): runs EKF predict+update, linearises
         the model around the current operating point, ZOH-discretises, and
         solves a convex QP via cvxopt.
      2. Apply the first optimal action to all heat sources.

    Cost structure (QP):
        - Quadratic output-tracking cost  ||z - z_ref||^2_Q
        - Quadratic input cost            ||u||^2_R
        - Optional ROM penalty            ||Delta u||^2_S
        - Soft output constraints (comfort corridor) with penalty rho
        - Hard input bounds               u_min <= u <= u_max

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
    dt                : OCP step size [s]
    measurement_dt    : EKF measurement interval [s].  If None, falls back to dt.
    latitude          : site latitude [deg]
    longitude         : site longitude [deg]
    tracking_weight   : weight on ||z - z_ref||^2_Q (setpoint tracking; 0 disables tracking)
    energy_weight     : weight on ||u||^2_R (input regularisation)
    smoothing_weight  : weight on ||Delta u||^2_S (input rate-of-movement; 0 disables)
    soft_constraint_weight : direct penalty rho for soft output bound violations.
    sigma_w           : process-noise std dev for the SDE / EKF [K/sqrt(s)].
    sigma_v           : measurement-noise std dev [K].
    sigma_b           : offset-state process-noise std dev [K/sqrt(s)].
    n_int_steps       : Euler sub-steps per interval in EKF.
    solver            : accepted for API compatibility, ignored (QP always used).
    solver_options    : accepted for API compatibility, ignored.
    use_analytic_derivatives : accepted for API compatibility, ignored.
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
        tracking_weight: float = DEFAULT_SETPOINT_PULL_WEIGHT,
        energy_weight: float = 0.01,
        smoothing_weight: float = 0.1,
        soft_constraint_weight: float = 10.0,
        terminal_weight: float = 100.0,
        sigma_w: float = 0.1,
        sigma_v: float = 0.5,
        sigma_b: float = 0.002,
        n_int_steps: int = 10,
        solver: str = "qp",
        solver_options: Optional[Dict[str, Any]] = None,
        use_analytic_derivatives: bool = True,
        energy_price_weight: float = 0.0,
    ) -> None:
        self._sources = heat_sources
        self._horizon = horizon
        self._dt = dt
        self._latitude = latitude
        self._longitude = longitude

        # solver/derivative args accepted for API compat; QP backend always used
        self._solver_requested = "qp"
        self._solver_active = "qp"
        self._use_analytic_derivatives = True

        if tracking_weight < 0.0:
            raise ValueError(
                f"tracking_weight must be >= 0; got {tracking_weight}"
            )
        if smoothing_weight < 0.0:
            raise ValueError(
                f"smoothing_weight must be >= 0; got {smoothing_weight}"
            )
        if terminal_weight < 1.0:
            raise ValueError(
                f"terminal_weight must be at least 1.0; got {terminal_weight}"
            )

        # The EKF must integrate over the actual wall-clock interval between
        # compute() calls, NOT the OCP horizon step size.
        ekf_dt = measurement_dt if measurement_dt is not None else dt

        # ── Build SDE models ────────────────────────────────────────────
        # Both use un-augmented state (augment_offsets=False) to keep the
        # EKF/QP dimension small for predictable solve times.
        self._system = HouseThermalSDE(
            model, heat_sources, dt,
            sigma_w=sigma_w, sigma_v=sigma_v,
            sigma_b=sigma_b,
            augment_offsets=False,
            n_int_steps=n_int_steps,
        )
        self._control_system = HouseThermalSDE(
            model, heat_sources, dt,
            sigma_w=sigma_w, sigma_v=sigma_v,
            sigma_b=sigma_b,
            augment_offsets=False,
            n_int_steps=n_int_steps,
        )

        n_x = self._system.nx
        n_u = self._system.nu
        n_z = self._control_system.nz
        n_rooms = self._system._n_rooms
        room_list = self._system._room_list

        # ── EKF: initialise from current room temperatures ──────────────
        x0 = np.array(self._system.x)
        P0 = np.eye(n_x)  # initial state uncertainty [K^2]
        self._ekf = _InnovationEKF(
            self._system, x0, P0, ekf_dt,
            n_steps=n_int_steps, scheme="implicit-euler",
        )

        # ── OCP cost matrices (cvxopt format) ───────────────────────────
        Q_cv = _diag_cvx(n_z, float(tracking_weight))
        R_cv = _diag_cvx(n_u, float(energy_weight))
        P_cv = _diag_cvx(n_z, float(terminal_weight) * float(tracking_weight))
        S_cv = _diag_cvx(n_u, float(smoothing_weight)) if smoothing_weight > 0.0 else None

        # Soft-constraint penalty: direct weight on comfort-corridor violations
        rho = float(soft_constraint_weight)

        # Comfort corridor half-width: use maximum comfort_offset across all rooms
        y_offset = max(
            (
                float(getattr(model.rooms[name], "comfort_offset", 2.0) or 2.0)
                for name in room_list
            ),
            default=2.0,
        )

        # Input bounds from the SDE model
        u_min, u_max = self._control_system.u_bounds

        # State reference: setpoints on the room-temperature block, zero elsewhere
        x_ref = np.zeros(n_x)
        x_ref[:n_rooms] = [model.rooms[name].setpoint for name in room_list]

        # ── MPC controller (forecast-aware variant) ──────────────────────
        self._mpc = _ForecastAwareMPCController(
            model=self._control_system,
            estimator=self._ekf,
            N=horizon,
            Q=Q_cv,
            R=R_cv,
            dt=dt,
            u_min=u_min,
            u_max=u_max,
            x_ref=x_ref,
            P=P_cv,
            S=S_cv,
            rho=rho,
            y_offset=y_offset,
        )

        # Store global cost weights so the trajectory builder can use them
        # to convert per-period multipliers to absolute values if needed,
        # and so backward-compatibility checks can detect the static case.
        self._tracking_weight: float = float(tracking_weight)
        self._energy_weight: float = float(energy_weight)

        # ── Price-aware cost term ────────────────────────────────────────
        self._energy_price_weight: float = float(energy_price_weight)
        self._dt_h: float = dt / 3600.0
        # Electrical draw per unit of u for each source (recomputed from
        # current power_scale so estimation updates are reflected).
        self._elec_heat: np.ndarray = np.array(
            [src.elec_per_unit_heat for src in heat_sources], dtype=float
        )
        self._elec_cool: np.ndarray = np.array(
            [src.elec_per_unit_cool for src in heat_sources], dtype=float
        )
        # Bidirectional mask: sources whose u_min < 0 need slack vars.
        self._bid_mask: np.ndarray = np.array(
            [src.u_min < 0 for src in heat_sources], dtype=bool
        )

        # ── Warm-start / bookkeeping ─────────────────────────────────────
        self._u_prev: np.ndarray = np.zeros(n_u)
        self._solve_times: deque = deque(maxlen=MPC_STATS_BUFFER_SIZE)
        self._total_computes: int = 0
        self._terminal_weight: float = terminal_weight

        # Kalman innovation (populated after each compute())
        self._last_innovation: Optional[List[float]] = None

        # Visualisation data (populated after each compute())
        self._predictions: List[Dict[str, float]] = []
        self._linearised_predictions: List[Dict[str, float]] = []
        self._outdoor_forecast: List[float] = []
        self._solar_forecast: List[Dict[str, float]] = []
        self._heating_schedule: List[Dict[str, float]] = []
        self._price_forecast: List[float] = []

    # ── Visualisation / diagnostic properties ────────────────────────────

    @property
    def horizon(self) -> int:
        """MPC prediction horizon (number of steps)."""
        return self._horizon

    @property
    def solve_times(self) -> deque:
        """Rolling buffer of recent QP solve times [s] (read-only view)."""
        return self._solve_times

    @property
    def terminal_weight(self) -> float:
        """Terminal cost weight (P = terminal_weight * Q) in effect for this controller."""
        return self._terminal_weight

    @property
    def solver_requested(self) -> str:
        """Configured solver backend name (always 'qp')."""
        return self._solver_requested

    @property
    def solver_active(self) -> str:
        """Currently active solver backend (always 'qp')."""
        return self._solver_active

    @property
    def use_analytic_derivatives(self) -> bool:
        """Whether analytical-derivative plumbing is enabled (always True)."""
        return self._use_analytic_derivatives

    def set_wind_speed(self, wind_speed: Optional[float]) -> None:
        """Apply a new wind speed [m/s] to the Sherman-Grimsrud infiltration overlay.

        None disables the overlay so the external conductance falls back to the
        typical-conditions baseline.  Pushed to both the EKF system and the
        control system so the same wind value drives both halves of the cycle.
        """
        self._system.set_wind_speed(wind_speed)
        self._control_system.set_wind_speed(wind_speed)

    def set_room_process_noise_covariance_scales(
        self, scales_by_room: Dict[str, float],
    ) -> None:
        """Apply per-room EKF/OCP process-noise covariance multipliers."""
        self._system.set_room_process_noise_covariance_scales(scales_by_room)
        self._control_system.set_room_process_noise_covariance_scales(scales_by_room)

    @property
    def last_innovation(self) -> Optional[List[float]]:
        """Kalman filter innovation from the most recent compute() call.

        One value per room (in room_names order).  None if compute() has not
        been called yet.
        """
        return self._last_innovation

    @property
    def filtered_temperatures(self) -> Dict[str, float]:
        """Kalman-filtered room temperatures after the latest EKF update step.

        Before the first compute() call this returns the EKF initial state
        (room temperatures at construction time).
        """
        x_hat = self._ekf.x_hat
        room_list = self._system._room_list
        n_rooms = self._system._n_rooms
        return {
            name: float(x_hat[i])
            for i, name in enumerate(room_list[:n_rooms])
        }

    @property
    def temperature_offsets(self) -> Dict[str, float]:
        """Estimated per-room measurement-bias offsets.

        Always returns zero (un-augmented state — no offset states estimated).
        """
        return {name: 0.0 for name in self._system._room_list}

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room filtered temperatures from the EKF (same as filtered_temperatures)."""
        return self.filtered_temperatures

    @property
    def wall_temperatures(self) -> Dict[str, float]:
        """Per-room temperatures (same as air in 1R1C — no separate wall node)."""
        return self.temperatures

    @property
    def slab_temperatures(self) -> Dict[str, float]:
        """Per-room temperatures (same as air in 1R1C — no separate slab node)."""
        return self.temperatures

    @property
    def predictions(self) -> List[Dict[str, float]]:
        """Latest predicted temperature trajectory [{room: degC}, ...]."""
        return self._predictions

    @property
    def linearised_predictions(self) -> List[Dict[str, float]]:
        """Latest linearised model temperature trajectory [{room: degC}, ...]."""
        return self._linearised_predictions

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
    def price_forecast(self) -> List[float]:
        """Electricity price forecast used in the last compute() [currency/kWh]."""
        return self._price_forecast

    @property
    def last_solve_time(self) -> Optional[float]:
        """Wall-clock time [s] consumed by the most recent QP solve, or None."""
        return self._solve_times[-1] if self._solve_times else None

    @property
    def mean_solve_time(self) -> Optional[float]:
        """Mean QP solve time [s] over the rolling history, or None."""
        if not self._solve_times:
            return None
        return float(np.mean(list(self._solve_times)))

    @property
    def max_solve_time(self) -> Optional[float]:
        """Maximum QP solve time [s] observed in the rolling history, or None."""
        if not self._solve_times:
            return None
        return float(np.max(list(self._solve_times)))

    @property
    def n_solves(self) -> int:
        """Total number of QP solves recorded in the rolling history."""
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
        """Map a state vector to the user-visible room (air) temperatures.

        Un-augmented 1R1C state layout: [T (n), phi (m)].
        Returns the first n_rooms elements (the physical temperature block).
        When augmented, adds the offset block (T + b).
        """
        n = system.nym
        if len(x) < n:
            return np.zeros(n)
        if system._augment_offsets and len(x) >= system.nx:
            b_start = system._offset_block_start
            return x[:n] + x[b_start: b_start + n]
        return x[:n].copy()

    def _wall_temperatures_from_state(
        self,
        system: HouseThermalSDE,
        x: np.ndarray,
    ) -> np.ndarray:
        """Return the single-node temperatures (1R1C has no wall block)."""
        n = system.nym
        if len(x) < n:
            return np.zeros(n)
        return x[:n].copy()

    def _slab_temperatures_from_state(
        self,
        system: HouseThermalSDE,
        x: np.ndarray,
    ) -> np.ndarray:
        """Return the single-node temperatures (1R1C has no slab block)."""
        n = system.nym
        if len(x) < n:
            return np.zeros(n)
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
        disabled_sources: Optional[Set[str]] = None,
        control_trajectory: Optional[Any] = None,
        price_forecast: Optional[List[float]] = None,
    ) -> Dict[str, float]:
        """
        Compute optimal control actions for the current time step.

        Parameters
        ----------
        outdoor_temp : float
            Current outdoor temperature [degC].
        solar_gains  : dict, optional
            Pre-computed solar gains {room: W}.  If None, computed from
            the solar model using now and the stored lat/lon.
        now : datetime, optional
            Current time (UTC).  Required when solar_gains is None.
        outdoor_forecast : list of float, optional
            External outdoor temperature forecast for each horizon step.
            If provided, must have length >= horizon.
        control_trajectory : ControlTrajectory, optional
            Schedule-projected per-step control parameters from the
            coordinator.  When provided the MPC cost uses time-varying
            setpoints, comfort corridors, and cost weights over the
            horizon.  When None the controller falls back to the current
            static setpoint / corridor (identical to pre-schedule-aware
            behaviour).
        cloud_forecast : list of float, optional
            Cloud-cover fraction in [0, 1] for each horizon step.
        cloud_cover_now : float, optional
            Current cloud-cover fraction in [0, 1].  Used for the k=0 entry
            of the solar schedule when solar_gains was not pre-computed.
        disabled_sources : set of str, optional
            Names of heat sources whose rooms are currently off (schedule off,
            user toggle, or window override).  Their QP outputs are zeroed
            out before the actions dict and heating schedule are built, so
            sensors report 0 W for both current and predicted inputs.
        price_forecast : list of float, optional
            Forecasted electricity prices aligned to the prediction horizon
            [currency/kWh].  When provided and energy_price_weight > 0 the
            controller penalises electrical consumption proportional to the
            spot price at each step.

        Returns
        -------
        dict
            {source_name: setpoint_fraction} where fraction is in [-1, 1]
            for cooling-capable sources and [0, 1] for heating-only.
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)
        if solar_gains is None:
            solar_gains = self._current_solar(now, cloud_cover=cloud_cover_now)

        N = self._horizon
        p = np.array([], dtype=float)  # no estimated parameters

        # ── Disturbance forecast for visualisation ───────────────────────
        if outdoor_forecast is not None and len(outdoor_forecast) >= N:
            outdoor_seq = list(outdoor_forecast[:N])
        else:
            outdoor_seq = self._forecast_outdoor(outdoor_temp)
        solar_seq = self._forecast_solar(now, cloud_forecast=cloud_forecast, cloud_cover_now=cloud_cover_now)

        # Store forecasts for visualisation
        self._outdoor_forecast = list(outdoor_seq)
        self._solar_forecast = [dict(s) for s in solar_seq]
        self._price_forecast = list(price_forecast) if price_forecast is not None else []

        # ── Current measurement y = room temperatures ────────────────────
        room_list = self._system._room_list
        n_rooms = self._system._n_rooms
        y = np.array(
            [self._system._model.rooms[name].temperature for name in room_list],
            dtype=float,
        )

        # ── Current disturbance vector ───────────────────────────────────
        d = self._control_system.disturbance_vector(outdoor_temp, solar_gains)

        # ── Disturbance forecast matrix for the OCP ──────────────────────
        # D_forecast[k] = disturbance during horizon step k (k=0..N-1).
        # solar_seq has N+1 entries; solar_seq[k] = solar at now + k*dt,
        # so solar_seq[k] describes the conditions during step k.
        D_forecast = np.array([
            self._control_system.disturbance_vector(outdoor_seq[k], solar_seq[k])
            for k in range(N)
        ], dtype=float)

        # ── Update setpoint reference in MPC (setpoints may have changed) ──
        n_x = self._system.nx
        x_ref_abs = np.zeros(n_x)
        x_ref_abs[:n_rooms] = [
            self._system._model.rooms[name].setpoint for name in room_list
        ]
        self._mpc.x_ref = x_ref_abs

        # ── Build time-varying arrays from schedule trajectory (if provided) ──
        # x_ref_abs_seq : (N, n_rooms)  absolute setpoints per step
        # offset_seq    : (N, n_rooms)  comfort corridor half-widths per step
        # q_scale_seq   : (N, n_rooms)  Q multipliers per step
        # r_scale_seq   : (N, n_sources) R multipliers per step (mapped room → source)
        if control_trajectory is not None:
            x_ref_abs_seq = np.zeros((N, n_rooms), dtype=float)
            offset_seq = np.zeros((N, n_rooms), dtype=float)
            q_scale_seq = np.ones((N, n_rooms), dtype=float)
            r_scale_seq = np.ones((N, len(self._sources)), dtype=float)

            for i, name in enumerate(room_list):
                x_ref_abs_seq[:, i] = control_trajectory.setpoints[name]
                offset_seq[:, i] = control_trajectory.comfort_offsets[name]
                q_scale_seq[:, i] = control_trajectory.q_scales[name]

            for j, src in enumerate(self._sources):
                r_scale_seq[:, j] = control_trajectory.r_scales[src.room]
        else:
            x_ref_abs_seq = None
            offset_seq = None
            q_scale_seq = None
            r_scale_seq = None

        # ── Price forecast aligned to horizon ────────────────────────────
        price_seq_np: Optional[np.ndarray] = None
        if (price_forecast is not None
                and len(price_forecast) > 0
                and self._energy_price_weight > 0.0):
            raw = np.asarray(price_forecast, dtype=float)
            # Clamp to N steps, padding with last value if shorter.
            if len(raw) >= N:
                price_seq_np = raw[:N]
            else:
                price_seq_np = np.concatenate([
                    raw, np.full(N - len(raw), raw[-1])
                ])

        # ── QP solve with disturbance forecast ───────────────────────────
        _t0 = time.perf_counter()
        u_abs, U_abs, X_abs = self._mpc.step(
            y, d, p, 0.0,
            D_forecast=D_forecast,
            x_ref_abs_seq=x_ref_abs_seq,
            offset_seq=offset_seq,
            q_scale_seq=q_scale_seq,
            r_scale_seq=r_scale_seq,
            price_seq=price_seq_np,
            elec_heat=self._elec_heat,
            elec_cool=self._elec_cool,
            bid_mask=self._bid_mask,
            price_weight=self._energy_price_weight,
            dt_h=self._dt_h,
        )
        self._solve_times.append(time.perf_counter() - _t0)
        self._total_computes += 1

        # Capture innovation from the EKF wrapper
        self._last_innovation = self._ekf.last_innovation

        # ── Zero out disabled sources ─────────────────────────────────────
        # Rooms in off-mode (schedule, user toggle, or window override) must
        # always produce 0 W.  Zero both the first-step action and the full
        # horizon trajectory so that the heating schedule sensors show 0 for
        # all future steps as well.  _u_prev is set from u_abs below, so the
        # EKF automatically picks up the correct applied value next cycle.
        if disabled_sources:
            for j, src in enumerate(self._sources):
                if src.name in disabled_sources:
                    u_abs[j] = 0.0
                    U_abs[:, j] = 0.0

        # ── Apply actions to heat sources ────────────────────────────────
        actions: Dict[str, float] = {}
        for j, src in enumerate(self._sources):
            frac = float(np.clip(u_abs[j], src.u_min, src.u_max))
            actions[src.name] = frac
            if src.can_cool:
                # Track the smooth-sigmoid power so sensors and the EKF are
                # consistent with the model function f().
                p_smooth = src.smooth_thermal_power(
                    frac, outdoor_temp, self._system._k_sigmoid,
                )
                # Apply the source's min_power clamp: a positive output below
                # min_power is reported as zero (hardware cannot deliver it).
                # Cooling power (negative) is left unclamped.
                min_power = float(getattr(src, "min_power", 0.0) or 0.0)
                if 0.0 < p_smooth < min_power:
                    p_smooth = 0.0
                src._current_power = p_smooth
            else:
                src.set_power(frac, outdoor_temp)

        self._u_prev = u_abs.copy()

        # ── Build predicted trajectory using nonlinear model simulation ────
        self._predictions = self._compute_nonlinear_predictions(
            U_abs, outdoor_seq, solar_seq, room_list, n_rooms
        )
        self._linearised_predictions = self._extract_linearised_predictions(
            X_abs, room_list, n_rooms
        )

        # ── Heating schedule ─────────────────────────────────────────────
        self._heating_schedule = [
            self._system.heating_powers(U_abs[k], outdoor_seq[k])
            for k in range(N)
        ]

        return actions

    def notify_applied_u(self, source_name: str, u_applied: float) -> None:
        """
        Notify the controller that a specific control action was applied
        externally (outside of the QP solve), so the EKF uses the correct
        previous input on the next compute() call.

        Parameters
        ----------
        source_name : str
            Name of the heat source whose action was overridden.
        u_applied : float
            The fraction actually applied, in [-1, 1].
        """
        for j, src in enumerate(self._sources):
            if src.name == source_name:
                clipped = float(np.clip(u_applied, src.u_min, src.u_max))
                self._u_prev[j] = clipped
                self._mpc._u_prev[j] = clipped
                break

    # ── Disturbance forecasts ────────────────────────────────────────────

    def _forecast_outdoor(self, current: float) -> List[float]:
        """Persistence forecast: outdoor temperature constant over horizon."""
        return [current] * self._horizon

    def _forecast_solar(
        self,
        now: datetime,
        cloud_forecast: Optional[List[float]] = None,
        cloud_cover_now: Optional[float] = None,
    ) -> List[Dict[str, float]]:
        """Solar gain forecast using the geometric solar model.

        Returns N+1 entries where solar_seq[k] = solar at now + k * dt
        for k = 0, ..., N.

        * k = 0 maps to the current solar gains.
        * k = 1 ... N-1 supply the OCP horizon steps 1 ... N-1.
        * k = N is one step beyond the OCP horizon for visualisation.

        When cloud_forecast is provided (one fraction per horizon step),
        the clear-sky irradiance is attenuated per the Kasten-Czeplak factor.
        Step k uses cloud_forecast[k] for k < len(cloud_forecast); steps
        beyond the supplied forecast hold the last value (persistence).

        When cloud_forecast is None but cloud_cover_now is provided, every
        step uses cloud_cover_now (persistence fallback) so the solar
        forecast reflects the currently-observed cloudiness rather than
        assuming a clear sky.
        """
        schedules = []
        for k in range(self._horizon + 1):  # N+1 entries: k = 0 ... N
            t = now + timedelta(seconds=self._dt * k)
            cc = _select_cloud_for_step(cloud_forecast, k, fallback=cloud_cover_now)
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

    def _extract_linearised_predictions(
        self,
        X_abs: np.ndarray,
        room_list: List[str],
        n_rooms: int,
    ) -> List[Dict[str, float]]:
        """Extract room temperature predictions from the linearised QP state trajectory.

        X_abs[k] is the absolute state at horizon step k+1 as predicted by the
        linearised model used inside the MPC solver.  The first n_rooms elements
        of each state vector are the room temperatures.
        """
        predictions = []
        for k in range(len(X_abs)):
            temps_k = X_abs[k, :n_rooms]
            predictions.append(
                {name: float(temps_k[i]) for i, name in enumerate(room_list)}
            )
        return predictions

    def _compute_nonlinear_predictions(
        self,
        U_abs: np.ndarray,
        outdoor_seq: List[float],
        solar_seq: List[Dict[str, float]],
        room_list: List[str],
        n_rooms: int,
    ) -> List[Dict[str, float]]:
        """Compute nonlinear model predictions using the optimal control sequence.

        Simulates the nonlinear thermal model forward over the horizon using the
        optimal control inputs from the MPC and the forecasted disturbances
        (outdoor temperature and solar gains).

        Parameters
        ----------
        U_abs : np.ndarray
            Optimal control sequence [N, m] where N is the horizon length
            and m is the number of control inputs.
        outdoor_seq : list of float
            Outdoor temperature forecast over the horizon [°C].
        solar_seq : list of dict
            Solar gain forecasts per room over the horizon [W].
        room_list : list of str
            Names of the rooms.
        n_rooms : int
            Number of rooms.

        Returns
        -------
        list of dict
            Predicted temperatures {room_name: temp_°C} for each horizon step.
        """
        predictions = []
        x_curr = self._ekf.x_hat.copy()
        p = np.array([], dtype=float)

        N = len(outdoor_seq)
        for k in range(N):
            u_k = U_abs[k] if k < len(U_abs) else U_abs[-1]
            outdoor_temp = outdoor_seq[k]
            solar_gains = solar_seq[k] if k < len(solar_seq) else solar_seq[-1]

            d_k = self._control_system.disturbance_vector(outdoor_temp, solar_gains)

            # Simulate one step forward using the nonlinear model
            # Use default arguments to capture current values in closures
            rhs = lambda x, u=u_k, d=d_k: self._system.f(x, u, d, p, 0.0)
            jacobian = lambda x, u=u_k, d=d_k: self._system.dfdx(x, u, d, p, 0.0)

            x_next = implicit_euler_substeps(
                rhs, jacobian, x_curr, self._dt, self._system._n_int_steps
            )

            # Extract room temperatures (first n_rooms states)
            temps_k = x_next[:n_rooms]
            predictions.append(
                {name: float(temps_k[i]) for i, name in enumerate(room_list)}
            )

            x_curr = x_next

        return predictions
