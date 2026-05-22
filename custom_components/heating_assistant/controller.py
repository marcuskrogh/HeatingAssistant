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


import logging
import math
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
from mbc.control import CDTrackingOptimalControlProblem, CDNMPCController, NLPScalingPolicy

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
    soft_constraint_weight : multiplier for soft constraint penalty:
                        ρ_z = energy_weight × soft_constraint_weight.
                        Controls how strictly the comfort region bounds are enforced.
                        Default: 1000.0.
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
        setpoint_pull_weight: float = DEFAULT_SETPOINT_PULL_WEIGHT,
        smoothing_weight: float = 0.1,
        soft_constraint_weight: float = 1000.0,
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
        self._energy_weight = float(energy_weight)
        self._soft_constraint_weight = float(soft_constraint_weight)
        self._rho_z = self._energy_weight * self._soft_constraint_weight
        self._setpoint_pull_weight = float(setpoint_pull_weight)
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
        # Runtime controller path uses the un-augmented state to keep the EKF/OCP
        # dimension as small as possible for predictable solve times.
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

        # ── EKF: initialise from current room temperatures ──────────────
        #
        # 1R1C state layout: [T (n), b (n)] (augmented).  The
        # temperature block is initialised from the current room
        # readings; the offset block starts at zero.
        x0 = np.array(self._system.x)
        n_rooms = self._system.nym
        m_filter = self._system._n_filtered
        P0 = np.eye(n_x)  # initial state uncertainty [K²]
        if self._system._augment_offsets:
            # Temperature block (0..n_rooms-1) starts tight — we trust
            # the current measurement.  Filter block (n..n+m, Phase 1 B2)
            # starts at zero with moderate uncertainty.  Offset block
            # (n+m..2n+m) starts moderately uncertain.
            if m_filter > 0:
                P0[
                    n_rooms: n_rooms + m_filter,
                    n_rooms: n_rooms + m_filter,
                ] *= 4.0                                            # filter
            P0[
                n_rooms + m_filter:, n_rooms + m_filter:,
            ] *= 4.0                                                # offsets
        # Implicit-Euler scheme is L-stable on the stiff envelope dynamics
        # introduced by the 2R2C + slab work (Phase 1 A1/A2); see
        # README §3.3.  On the un-augmented 1R1C dynamics in production
        # before A1, the difference vs explicit Euler was below the
        # EKF's measurement-noise floor (verified by the bit-equivalence
        # test in tests/test_integrator.py).
        self._ekf = ContinuousDiscreteEKF(
            self._system, x0, P0, ekf_dt,
            n_steps=n_int_steps, scheme="implicit-euler",
        )

        # ── OCP cost matrices ───────────────────────────────────────────
        Q = self._setpoint_pull_weight * np.eye(n_z)  # weak stage setpoint pull
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
        OCP_MAX_INTEGRATION_STEPS = 1
        ocp_n_steps = min(n_int_steps, OCP_MAX_INTEGRATION_STEPS)
        self._ocp_n_steps = ocp_n_steps
        self._Q = Q
        self._R = R
        self._S = S
        self._P = P

        # Reference and bounds for the OCP
        z_ref = self._control_system.x_ref.copy()
        z_min, z_max = self._control_system.comfort_corridor_bounds()
        u_min, u_max = self._control_system.u_bounds

        # ── CDTrackingOptimalControlProblem ─────────────────────────────
        self._ocp = self._build_ocp_with_fallback(
            horizon=horizon,
            Q=Q,
            R=R,
            P=P,
            S=S,
            z_ref=z_ref,
            z_min=z_min,
            z_max=z_max,
            rho_z=self._rho_z,
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
        z_min: np.ndarray,
        z_max: np.ndarray,
        rho_z: float,
        u_min: np.ndarray,
        u_max: np.ndarray,
        n_steps: int,
        dt: float,
        use_analytic_derivatives: bool = True,
    ) -> CDTrackingOptimalControlProblem:
        solver_options = self._solver_options_for(solver)
        kwargs: Dict[str, Any] = {
            "N": horizon,
            "Q": Q,
            "R": R,
            "P": P,
            "S": S,
            "z_ref": z_ref,
            "z_min": z_min,
            "z_max": z_max,
            "rho_z": rho_z,
            "u_min": u_min,
            "u_max": u_max,
            "n_steps": n_steps,
            "dt": dt,
            "solver": solver,
        }
        if solver_options:
            kwargs["solver_options"] = solver_options

        # For IPOPT scale the NLP objective by 1/rho_z so that the dominant
        # soft-penalty term O(rho_z) is normalised to O(1).  Without this the
        # objective can reach ~10^6–10^7 while IPOPT's absolute dual-
        # infeasibility tolerance is 1e-6, a gap that prevents convergence
        # within the iteration budget.  SLSQP uses different tolerance logic
        # and does not benefit from this scaling.
        if solver.lower() in {"ipopt", "cyipopt"}:
            kwargs["solver_scaling"] = NLPScalingPolicy(objective_scale=1.0 / rho_z)

        # Note: mbc.CDTrackingOptimalControlProblem automatically detects and uses
        # analytical Jacobians if model.dfdu, model.dgmdx, and model.dgmdu are
        # defined. The use_analytic_derivatives flag is stored in the controller
        # for diagnostic/introspection purposes but doesn't need to be passed to OCP.

        ocp = CDTrackingOptimalControlProblem(self._control_system, **kwargs)

        return ocp

    def _build_ocp_with_fallback(
        self,
        *,
        horizon: int,
        Q: np.ndarray,
        R: np.ndarray,
        P: np.ndarray,
        S: Optional[np.ndarray],
        z_ref: np.ndarray,
        z_min: np.ndarray,
        z_max: np.ndarray,
        rho_z: float,
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
                z_min=z_min,
                z_max=z_max,
                rho_z=rho_z,
                u_min=u_min,
                u_max=u_max,
                n_steps=n_steps,
                dt=dt,
                use_analytic_derivatives=self._use_analytic_derivatives,
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
                z_min=z_min,
                z_max=z_max,
                rho_z=rho_z,
                u_min=u_min,
                u_max=u_max,
                n_steps=n_steps,
                dt=dt,
                use_analytic_derivatives=self._use_analytic_derivatives,
            )

    def _solver_options_for(self, solver: str) -> Dict[str, Any]:
        opts = dict(self._solver_options)
        key = solver.lower()
        if key in {"ipopt", "cyipopt"}:
            opts.setdefault("max_iter", 200)
            opts.setdefault("tol", 1e-6)
            # Use mbc's user-scaling to properly normalise the soft-output
            # penalty term (O(1e4)) relative to the energy term (O(1e3)).
            # This provides better numerical conditioning for L-BFGS Hessian.
            opts.setdefault("nlp_scaling_method", "user-scaling")
            # Dual infeasibility tolerance is the bottleneck when the objective
            # has orders-of-magnitude imbalance; relax it slightly to allow
            # faster convergence without sacrificing solution quality.
            opts.setdefault("dual_inf_tol", 1e-4)
        else:
            opts.setdefault("maxiter", 200)
            opts.setdefault("ftol", 1e-6)
        return opts

    # ── Visualisation properties ─────────────────────────────────────────


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

    def set_wind_speed(self, wind_speed: Optional[float]) -> None:
        """Apply a new wind speed [m/s] to the Sherman–Grimsrud
        infiltration overlay (Phase 1 C1).  ``None`` disables the
        overlay so the external conductance falls back to the
        typical-conditions baseline.

        Pushed to both the EKF system (used for state estimation) and
        the OCP system (used for prediction over the horizon) so the
        same wind value drives both halves of the cycle.
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
        """Estimated per-room integrated mismatch offsets ``b`` after EKF update.

        1R1C state layout: ``[T (n), φ (m), b (n)]``.  Offsets live
        immediately after the filter block — at indices ``n+m..2n+m``.
        """
        x_hat = self._ekf.x_hat
        n = self._system.nym
        if not self._system._augment_offsets:
            return {
                name: 0.0
                for name in self._system._room_list[:n]
            }
        b_start = self._system._offset_block_start
        return {
            name: float(x_hat[b_start + i])
            for i, name in enumerate(self._system._room_list[:n])
        }

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room filtered temperatures from the EKF."""
        x_hat = self._ekf.x_hat
        n = self._system.nym
        y_hat = self._effective_room_temperatures(self._system, x_hat)
        return {
            name: float(y_hat[i])
            for i, name in enumerate(self._system._room_list[:n])
        }

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
        """Map a state vector to the user-visible room (air) temperatures.

        Phase 1 A2 state layout: ``[T_a (n), T_w (n), T_s (n), b (n)]``.
        The visualisation surface tracks what users perceive — the air
        node augmented with the slow measurement bias — so the
        returned vector is ``T_a + b`` when augmented, otherwise just
        the air block.  Wall and slab nodes are exposed separately as
        diagnostics.
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
        z_min, z_max = self._control_system.comfort_corridor_bounds()
        # NOTE: mbc's CDTrackingOptimalControlProblem has no public API for
        # updating the reference trajectory after construction, so we reach
        # into the internal EconomicOptimalControlProblem.  This is technical
        # debt — if the mbc internals change, this line must be updated.
        _eocp = self._ocp._eocp
        _M = _eocp._N * _eocp._n_steps
        _eocp._z_ref = np.tile(z_ref, (_M + 1, 1))
        _eocp._z_min = np.asarray(z_min, dtype=float)
        _eocp._z_max = np.asarray(z_max, dtype=float)
        _eocp._has_soft_z = True
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
        m_filter = self._system._n_filtered
        # Phase 1 A2 + B2 state layout:
        # ``[T_a (n), T_w (n), T_s (n), φ (m), b (n)]``.
        # 1R1C state layout: [T (n), φ (m), b (n)].
        # Filter block starts at n_rooms; offset block at n_rooms + m_filter.
        # Write the EKF reconstructions for filter and offset blocks back
        # to the system so subsequent diagnostics and the next cycle's
        # predict step see a consistent state.
        b_start = n_rooms + m_filter
        if self._system._augment_offsets:
            self._system._offset_state = np.array(x_hat[b_start:], dtype=float)
        else:
            self._system._offset_state = np.zeros(n_rooms, dtype=float)
        if m_filter > 0:
            self._system._filter_state = np.array(
                x_hat[n_rooms: n_rooms + m_filter], dtype=float,
            )
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
                z_min=z_min,
                z_max=z_max,
                rho_z=self._rho_z,
                u_min=self._control_system.u_bounds[0],
                u_max=self._control_system.u_bounds[1],
                n_steps=self._ocp_n_steps,
                dt=self._dt,
                use_analytic_derivatives=self._use_analytic_derivatives,
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
                p_smooth = src.smooth_thermal_power(
                    frac, outdoor_temp, self._system._k_sigmoid,
                )
                # Apply the source's min_power clamp consistently with the
                # heating-only branch's call to ``set_power``: a positive
                # output below ``min_power`` is reported as zero so the
                # current-power surface matches what the hardware can
                # actually deliver.  Cooling power (negative) is allowed to
                # remain unclamped — min_power is a heating-side
                # specification.
                min_power = float(getattr(src, "min_power", 0.0) or 0.0)
                if 0.0 < p_smooth < min_power:
                    p_smooth = 0.0
                src._current_power = p_smooth
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
