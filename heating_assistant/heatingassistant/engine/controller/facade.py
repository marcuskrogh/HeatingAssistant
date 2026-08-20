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
    a two-rate NMPC + P tracker.  The linearised QP is kept only as EKF glue
    and is not solved on the happy path.

    At each fast control interval the controller:
      1. Runs the CD-EKF to fuse the current temperature measurement
         (predict uses the last applied P command).
      2. Applies ``u = clip(u_ref + K_p (T_ref − T_hat), u_min, u_max)``
         from the last accepted slow plan.  With no plan, P toward the
         setpoint while air is outside the comfort band (else ``u = 0``).
      3. Applies the command to all heat sources.

    The slow nonlinear OCP runs on a worker thread (not inline from
    ``compute``).

    Public API:
        controller = HeatingMPCController(model, heat_sources, ...)
        actions    = controller.compute(outdoor_temp, solar_gains, now)
        # controller.predictions, .outdoor_forecast, .solar_forecast,
        # .heating_schedule
"""

from __future__ import annotations


import logging
import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from ..thermal_model import HouseModel
from ..heat_sources import HeatSource
from ..solar_model import room_solar_gains, room_solar_gains_from_exposure
from ..solar_forecast import select_ghi_for_step


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
from ..const import MPC_STATS_BUFFER_SIZE

# ── Import model-based control components from mbc ────────────────────────────
from mbc.models import ContinuousDiscreteSDE
from mbc.estimation import (
    ContinuousDiscreteEKF,
    ContinuousDiscreteEKFParams,
    IntegrationScheme,
)
from mbc.control import StandardLinearisedContinuousMPC

from ..const import (
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_SETPOINT_PULL_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    AIR_RHO_CP,
    NMPC_WATCHDOG_S,
    SHERMAN_GRIMSRUD_STACK_COEF,
    SHERMAN_GRIMSRUD_WIND_COEF,
    SOLAR_WALL_FRACTION,
)
from ..integrator import implicit_euler_substeps
from ..nmpc_ocp import (
    NMPC_IDLE_U_ABS,
    NMPC_MAXITER,
    NMPC_TIMEOUT_S,
    MeanOcp,
    evaluate_zero_heat_cost,
    mean_price_slow,
    plan_from_solve,
    solve_mean_ocp,
)
from ..nmpc_p import comfort_fallback_command, p_command
from ..nmpc_timing import NmpcTiming, derive_nmpc_timing, timing_from_dt_horizon
from ..thermal_model import _SG_FACTOR_TYPICAL

_LOGGER = logging.getLogger(__name__)


# ============================================================
# House-heating SDE model
# ============================================================

class HouseThermalSDE(ContinuousDiscreteSDE):
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
    the piecewise-linear map in ``HeatPump.smooth_thermal_power`` that
    delivers Q_heat·u (u ≥ 0) and Q_cool·u (u < 0).

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
        ts: Optional[float] = None,
    ) -> None:
        self._model = model
        self._sources = sources
        self._dt = dt
        self._ts = float(ts if ts is not None else dt)
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
        self._nx_phys = 2 * n   # 2R2C: [T_a (n), T_w (n)]
        self._offset_state: np.ndarray = np.zeros(n, dtype=float)
        # Per-room covariance scaling for process noise (Phase 3 W1).
        # Values are covariance multipliers; 1.0 means no inflation.
        self._room_q_scales: np.ndarray = np.ones(n, dtype=float)
        self._window_open: np.ndarray = np.zeros(n, dtype=bool)

        # 2R2C state layout: ``x_phys = [T_a (n), T_w (n)]`` with
        # ``nx_phys = 2n`` — air block first so the measured/controlled
        # temperatures stay at ``x[:n]`` exactly as before.
        # Capacitance, drift, and disturbance matrices are all derived
        # from the 2n-state ``HouseModel`` so they share a single source
        # of truth.
        self._C_cap = np.array(model._C, dtype=float)                # (2n,) [C_a, C_w]
        self._inv_C_cap: np.ndarray = 1.0 / self._C_cap             # (2n,)
        self._F: np.ndarray = model._A / self._C_cap[:, np.newaxis]  # (2n, 2n)

        # Continuous disturbance matrix G_d shape (2n, 1 + 2n):
        # column 0:        outdoor temperature coupling via B_ext (both blocks);
        # columns 1..n:    per-room solar gain, scaled by the identified
        #                  ``solar_scale`` and split between the air node
        #                  (1 − SOLAR_WALL_FRACTION) and the wall node
        #                  (SOLAR_WALL_FRACTION + sol-air facade share);
        # columns 1+n..2n: per-room direct air-node heat [W] — the identified
        #                  internal gain q_int.
        self._G_d: np.ndarray = np.zeros((2 * n, 1 + 2 * n))
        self._G_d[:, 0] = model._B_ext * self._inv_C_cap
        for i in range(n):
            room = model.rooms[self._room_list[i]]
            s_i = float(room.solar_scale)
            wall_frac = SOLAR_WALL_FRACTION
            facade = float(room.facade_solar_share) * float(room.facade_absorptance)
            self._G_d[i, 1 + i] = (1.0 - wall_frac) * s_i * self._inv_C_cap[i]
            self._G_d[n + i, 1 + i] = (wall_frac + facade) * s_i * self._inv_C_cap[n + i]
            # Air-node heat channel (q_int / Δg): air row only.
            self._G_d[i, 1 + n + i] = self._inv_C_cap[i]

        # Sky cooling-drift bias (wall rows).  Mirrors the HouseModel-side
        # constant (–sky_ua · ΔT_sky / C_w per room), attenuated at runtime
        # by the cloud clear-sky fraction.  Added directly to the drift in
        # ``f``; doesn't enter ``dfdx``.
        self._sky_offset_phys = np.array(model._B_sky_offset, dtype=float)  # (2n,)
        self._sky_clear_fraction: float = 1.0

        # Sherman–Grimsrud per-room effective leakage area [m²] for the
        # air-node infiltration overlay; single source of truth in HouseModel.
        self._leakage_area = np.array(model._leakage_area, dtype=float)

        # Wall-node equilibrium mixing ratio: at steady state (no solar,
        # no inter-room flow) the wall sits at
        #     T_w = ρ·T_a + (1 − ρ)·T_out,   ρ = g_aw / (g_aw + g_wout).
        # Used to seed wall states for open-loop starts and the QP
        # linearisation point.
        _ratios = []
        for name in self._room_list:
            room = model.rooms[name]
            _g_inf, g_aw, g_we = room.conductances()
            g_wout = g_we + float(room.sky_radiative_ua) + float(room.thermal_bridge_psi_l)
            _ratios.append(g_aw / (g_aw + g_wout))
        self._wall_eq_ratio: np.ndarray = np.array(_ratios, dtype=float)

        # Per-source first-order emitter filter (B2).
        # Each source with ``emitter_time_constant > 0`` gets a filter
        # state ``φ_j`` that lags the commanded fraction ``u_j`` with
        # time constant ``τ_em,j``.  The state-vector layout becomes
        #
        #     [T_a (n), T_w (n), φ (m), b (n)]
        #
        # where ``m`` is the number of filtered sources and the b block
        # is optional (``augment_offsets``).
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
        nx_phys = self._nx_phys

        # Offset block start index (avoids property dispatch in g/gm/hm/dfdx)
        self._offset_block_start: int = nx_phys + m

        # Total state dimension.  Layout:
        #   [T_a (n), T_w (n), φ (m), b (n if augment_offsets)]
        self._nx: int = (nx_phys + n + m) if augment_offsets else (nx_phys + m)

        # Per-source fixed lookups — replace per-call dict/cast overhead.
        # Heat sources land on the air node, so the source rows are < n and
        # ``_src_C_cap`` picks up the air capacitances.
        self._src_room_idx: np.ndarray = np.array(
            [self._room_idx[src.room] for src in self._sources], dtype=int
        )
        self._src_C_cap: np.ndarray = self._C_cap[self._src_room_idx]
        self._src_can_cool: list = [src.can_cool for src in self._sources]

        # Filter block constants
        if m > 0:
            self._neg_inv_taus: np.ndarray = -1.0 / self._emitter_taus
            self._inv_emitter_taus: np.ndarray = 1.0 / self._emitter_taus
            self._filter_diag_idx: np.ndarray = np.arange(nx_phys, nx_phys + m)
        else:
            self._neg_inv_taus = np.zeros(0)
            self._inv_emitter_taus = np.zeros(0)
            self._filter_diag_idx = np.zeros(0, dtype=int)

        # Identifiable sources as numpy array for vectorized scale extraction
        self._n_identifiable: int = len(self._identifiable_sources)
        self._identifiable_sources_arr: np.ndarray = np.array(
            self._identifiable_sources, dtype=int
        )

        # Constant measurement/output Jacobian H = dhm/dx = dgm/dx.
        # Only the air block is measured; the wall, filter, and gain blocks
        # contribute nothing, so their columns stay zero.
        b_start = self._offset_block_start
        _H = np.zeros((n, self._nx))
        _H[:, :n] = np.eye(n)
        if augment_offsets:
            _H[:, b_start:b_start + n] = np.eye(n)
        self._H_const: np.ndarray = _H

        # Sherman–Grimsrud precomputed constants
        self._AIR_RHO_CP_leakage: np.ndarray = AIR_RHO_CP * self._leakage_area
        self._ua_inf_typ: np.ndarray = self._AIR_RHO_CP_leakage * _SG_FACTOR_TYPICAL
        self._zeros_n_rooms: np.ndarray = np.zeros(n)

        # Diagonal index array for in-place F-matrix update in dfdx
        self._diag_n_idx: np.ndarray = np.arange(n)

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

    # ── ContinuousDiscreteSDE abstract dimensions ─────────────────────────

    @property
    def Ts(self) -> float:
        """EKF / measurement sampling interval [s]."""
        return self._ts

    @property
    def nx(self) -> int:
        # 2R2C state layout:
        # * Physical block: ``2n`` states (air + wall node per room).
        # * Filter block: ``m`` states (emitter lags).
        # * With offset augmentation we append per-room measurement
        #   biases (``+n``).
        # * With internal-gain augmentation we append per-room gain
        #   deviation states (``+n``).
        return self._nx

    @property
    def nu(self) -> int:
        return len(self._sources)

    @property
    def nd(self) -> int:
        # d = [T_out, q_solar (n), q_air (n)]: solar gains in slots 1..n,
        # direct air-node heat (internal gain, decayed Δg) in slots n+1..2n.
        return 1 + 2 * len(self._room_list)

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
        """Accepted for API compatibility; no-op (no slab node)."""
        pass

    def set_cloud_cover(self, cloud_cover: Optional[float]) -> None:
        """Attenuate the sky cooling drift by the current cloud cover.

        Mirrors :meth:`HouseModel.set_cloud_cover`: the long-wave sky
        depression collapses under an overcast sky, so the constant drift
        is scaled by ``1 − cloud_cover``.  ``None`` keeps the previous value.
        """
        if cloud_cover is None:
            return
        self._sky_clear_fraction = float(
            min(1.0, max(0.0, 1.0 - cloud_cover))
        )

    def set_room_process_noise_covariance_scales(
        self, scales_by_room: Dict[str, float],
    ) -> None:
        """Update per-room process-noise covariance multipliers.

        ``scales_by_room[room]`` scales Q for that room's physical states
        (air and wall nodes). Values <= 0 are ignored.
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

    def set_window_open(self, flags: Optional[Dict[str, bool]]) -> None:
        """Record which rooms currently have an open window/door contact.

        Used by the contact-gated extra-UA term in :meth:`f` / :meth:`dfdx`.
        Missing names stay closed.
        """
        self._window_open[:] = False
        if not flags:
            return
        for name, is_open in flags.items():
            idx = self._room_idx.get(name)
            if idx is None:
                continue
            self._window_open[idx] = bool(is_open)

    def _contact_ua_watts(self, outdoor_temp: float, T_a: np.ndarray) -> np.ndarray:
        """Air-node extra heat [W] from identified UA_open while contact is open."""
        n = self._n_rooms
        q = np.zeros(n, dtype=float)
        for i, name in enumerate(self._room_list):
            if not bool(self._window_open[i]):
                continue
            ua = float(getattr(self._model.rooms[name], "ua_open", 0.0) or 0.0)
            if ua <= 0.0:
                continue
            q[i] = ua * (outdoor_temp - float(T_a[i]))
        return q

    def _build_sigma_matrix(self) -> np.ndarray:
        """Build (or rebuild) the diagonal diffusion matrix σ from current Q
        scales.

        Block-diagonal layout matching the state vector
        ``[T_a (n), T_w (n), φ (m), b (n if augment_offsets)]``:

        * physical blocks:  ``σ_w · √(q_scale)`` per room, applied to both
          the air and the wall node of the room,
        * filter block:     ``σ_w`` per filtered source,
        * offset block:     ``σ_b`` per room (random-walk bias).
        """
        n = self._n_rooms
        m = self._n_filtered
        physical_std = np.sqrt(np.maximum(self._room_q_scales, 0.0))
        diag_parts = [
            self._sigma_w * physical_std,          # air block
            self._sigma_w * physical_std,          # wall block
            self._sigma_w * np.ones(m, dtype=float),
        ]
        if self._augment_offsets:
            diag_parts.append(self._sigma_b * np.ones(n, dtype=float))
        return np.diag(np.concatenate(diag_parts))

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
        Drift for the 2R2C state ``x = [T_a (n), T_w (n), φ (m), b?, Δg?]``.

        Heat-source power, internal gains, and the Sherman–Grimsrud
        infiltration overlay land on the **air** node; conduction, sky
        radiative cooling, thermal bridges, the sol-air facade share and
        inter-room coupling act on the **wall** node; solar gain splits
        between the two via ``G_d``.  The measurement-offset block ``b``
        has zero drift — it's a slowly-varying random-walk bias state.

        Heat-source dispatch:

        * **Cooling-capable** (``src.can_cool``): piecewise-linear map
          u ∈ [−1, 1] → [−Q_cool_max, +Q_heat_max] via
          ``smooth_thermal_power``.
        * **Heating-only**: linear ``thermal_power(max(0, u), T_out)``.

        Parameter vector ``p`` (or ``self._theta`` when ``p`` is empty)
        layout: ``theta = [log_mass(n), log_r(n), q_int(n), log_alpha(*),
        log_r_ij(*), …]``.  ``q_int`` is folded into the air-node heat
        channel of the disturbance vector (slots ``1+n … 2n``);
        ``log_alpha`` scales each source's commanded power.  Parameters
        beyond the heater scales (inter-room R, solar scale, envelope
        splits) are baked into the model matrices at construction.
        """
        outdoor_temp = float(d[0])
        n = self._n_rooms
        nx_phys = self._nx_phys
        T_phys = x[:nx_phys]

        theta = p if len(p) > 0 else self._theta

        # Extract internal gains and heater scales from parameter vector.
        if len(theta) >= 3 * n:
            q_int = theta[2 * n: 3 * n]
            d_augmented = d.copy()
            d_augmented[1 + n: 1 + 2 * n] += q_int
            heater_scales = self._get_heater_scales(theta)
            u_scaled = u if heater_scales is None else heater_scales * u
        else:
            heater_scales = None
            u_scaled = u
            d_augmented = d

        # Heat-source contribution — all sources reach the air node.
        # Filtered sources use filter state φ_j as effective fraction.
        m = self._n_filtered
        heat_contrib = np.zeros(nx_phys)

        for j, src in enumerate(self._sources):
            k_filter = self._filter_idx_for_source[j]
            if k_filter >= 0:
                eff_u = (heater_scales[j] if heater_scales is not None else 1.0) * x[nx_phys + k_filter]
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

        # Physical 2R2C drift on [T_a, T_w].
        dT_phys = (
            self._F @ T_phys
            + heat_contrib
            + self._G_d @ d_augmented
            + self._sky_offset_phys * self._sky_clear_fraction
        )

        # Sherman–Grimsrud wind-driven infiltration overlay on the air rows
        # (skip when not configured).
        if self._wind_speed is not None:
            delta_ua = self._infiltration_delta_ua(outdoor_temp, T_phys[:n])
            dT_phys[:n] += (delta_ua * self._inv_C_cap[:n]) * (outdoor_temp - T_phys[:n])

        # Identified contact-gated extra UA (simulated air).
        q_ua = self._contact_ua_watts(outdoor_temp, T_phys[:n])
        if np.any(q_ua):
            dT_phys[:n] += q_ua * self._inv_C_cap[:n]

        # Filter-block drift  dφ/dt = (u_cmd - φ) / τ_em.
        if m > 0:
            phi = x[nx_phys: nx_phys + m]
            dphi = (u[self._filtered_source_indices] - phi) / self._emitter_taus
        else:
            dphi = self._zeros_n_rooms[:0]

        blocks = [dT_phys, dphi]
        if self._augment_offsets:
            blocks.append(self._zeros_n_rooms)
        return np.concatenate(blocks)

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
        ``[T_a (n), T_w (n), φ (m), b (n)?, Δg (n)?]``.

        Block structure (rows in same order as state):

        * physical 2n × 2n block: ``self._F`` (from HouseModel),
          augmented on the air-node diagonal by the Sherman–Grimsrud
          overlay (linearly-implicit treatment, frozen at the current
          state).
        * filter→physical: each filtered source contributes
          ``∂Q_j/∂φ_j / C_a,i`` to its room's air-node row.
        * filter→filter: diagonal ``-1/τ_em``.
        * offset block: zero drift, so its rows / columns are zero.
        * gain→physical: diagonal ``1/C_a,i`` (Δg deviation onto the
          air node); gain→gain: diagonal ``-κ`` (OU reversion).

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
        nx_phys = self._nx_phys
        outdoor_temp = float(d[0])
        if self._wind_speed is not None:
            delta_ua = self._infiltration_delta_ua(outdoor_temp, x[:n])
            F_eff = self._F.copy()
            F_eff[self._diag_n_idx, self._diag_n_idx] -= delta_ua * self._inv_C_cap[:n]
        else:
            F_eff = self._F

        if np.any(self._window_open):
            extra = None
            for i, name in enumerate(self._room_list):
                if not bool(self._window_open[i]):
                    continue
                ua = float(getattr(self._model.rooms[name], "ua_open", 0.0) or 0.0)
                if ua <= 0.0:
                    continue
                if extra is None:
                    extra = F_eff if F_eff is not self._F else self._F.copy()
                extra[i, i] -= ua * self._inv_C_cap[i]
            if extra is not None:
                F_eff = extra

        J = np.zeros((self._nx, self._nx))
        J[:nx_phys, :nx_phys] = F_eff

        if m > 0:
            theta = p if len(p) > 0 else self._theta
            heater_scale_factors = self._get_heater_scales(theta)

            eps = 1e-6
            for k, j in enumerate(self._filtered_source_indices):
                src = self._sources[j]
                phi_j = x[nx_phys + k]
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
                J[i_room, nx_phys + k] += dpdphi / self._C_cap[i_room]

            # Filter-block diagonal: -1/τ_em (precomputed).
            J[self._filter_diag_idx, self._filter_diag_idx] = self._neg_inv_taus

        # ``J`` is allocated at the full state dimension ``self._nx`` (which
        # already accounts for the active blocks), so it is returned as-is.
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

        For **cooling-capable** (heat-pump) sources the derivative follows the
        piecewise-linear power curve (with a small blend around u = 0 to avoid
        a discontinuous Jacobian when the equilibrium input crosses zero)::

        For **filtered** sources (emitter lag τ > 0)::

            ∂f[nx_phys + k_filter] / ∂u_j = 1 / τ_em[k_filter]
        """
        outdoor_temp = float(d[0])
        nx_phys = self._nx_phys
        theta = p if len(p) > 0 else self._theta
        heater_scale_factors = self._get_heater_scales(theta)

        J = np.zeros((self._nx, self.nu))
        for j, src in enumerate(self._sources):
            i = self._src_room_idx[j]
            k_filter = self._filter_idx_for_source[j]
            if k_filter >= 0:
                # Filtered source: dφ_k/dt = (u_j − φ_k)/τ_k  →  ∂/∂u_j = 1/τ_k
                J[nx_phys + k_filter, j] = self._inv_emitter_taus[k_filter]
            else:
                scale = heater_scale_factors[j] if heater_scale_factors is not None else 1.0
                eff_u = u[j] * scale
                if self._src_can_cool[j]:
                    q_heat = src.thermal_power(1.0, outdoor_temp)
                    q_cool = src._q_cool_const
                    if q_heat > 0.0 and q_cool > 0.0:
                        # Piecewise-linear curve φ(u) = q_heat·u (u ≥ 0) /
                        # q_cool·u (u < 0) has a kink at u = 0.  A hard switch
                        # in the Jacobian at eff_u = 0 causes the linearisation
                        # point u_ss to flip the B matrix discontinuously when
                        # the equilibrium input crosses zero, producing jitter.
                        # Smooth the transition over ±_KINK_BLEND so the Jacobian
                        # is continuous in u_ss across mild-weather transitions.
                        _KINK_BLEND = 0.025
                        if abs(eff_u) < _KINK_BLEND:
                            t_blend = eff_u / _KINK_BLEND  # in (−1, 1)
                            slope = 0.5 * ((1.0 + t_blend) * q_heat + (1.0 - t_blend) * q_cool)
                        elif eff_u > 0.0:
                            slope = q_heat
                        else:
                            slope = q_cool
                        J[i, j] = slope * scale / self._C_cap[i]
                    elif eff_u >= 0.0:
                        J[i, j] = src.thermal_power(1.0, outdoor_temp) * scale / self._C_cap[i]
                else:
                    J[i, j] = src.thermal_power(1.0, outdoor_temp) * scale / self._C_cap[i]
        return J

    # ── Application-layer helpers ────────────────────────────────────────

    @property
    def x(self) -> list[float]:
        """Current state vector as a list of floats.

        Layout: un-augmented ``[T_a (n), T_w (n), φ (m)]``; augmented
        adds a ``b (n)`` block at the end.  ``m`` is the
        number of filtered heat sources (``self._n_filtered``).  ``φ`` is
        initialised from the filter cache (zero on cold start); the wall
        block round-trips through ``Room.wall_temperature``.
        """
        temps = [self._model.rooms[name].temperature for name in self._room_list]
        walls = [
            self._model.rooms[name].wall_temperature for name in self._room_list
        ]
        phi = self._filter_state.tolist() if self._n_filtered > 0 else []
        out = temps + walls + phi
        if self._augment_offsets:
            out = out + self._offset_state.tolist()
        return out

    @x.setter
    def x(self, val: list[float]) -> None:
        n = self._n_rooms
        m = self._n_filtered
        nx_phys = self._nx_phys
        if len(val) < n:
            raise ValueError(f"Expected at least {n} state values, got {len(val)}")

        for i, name in enumerate(self._room_list):
            self._model.rooms[name].temperature = float(val[i])

        if len(val) >= nx_phys:
            for i, name in enumerate(self._room_list):
                self._model.rooms[name].wall_temperature = float(val[n + i])
        else:
            # Short (air-only) vector: walls follow the air node.
            for i, name in enumerate(self._room_list):
                self._model.rooms[name].wall_temperature = float(val[i])

        if m > 0:
            if len(val) >= nx_phys + m:
                self._filter_state = np.array(
                    val[nx_phys: nx_phys + m], dtype=float,
                )
            else:
                self._filter_state = np.zeros(m, dtype=float)

        if self._augment_offsets and len(val) >= self._offset_block_start + n:
            b_start = self._offset_block_start
            self._offset_state = np.array(
                val[b_start: b_start + n], dtype=float,
            )

    def wall_equilibrium(
        self,
        t_air: np.ndarray,
        t_out: float,
    ) -> np.ndarray:
        """Steady-state wall temperatures for given air and outdoor temps.

        ``T_w = ρ·T_a + (1 − ρ)·T_out`` with the per-room conductance
        ratio ``ρ = g_aw / (g_aw + g_wout)`` (solar and inter-room flows
        neglected).  Used to seed wall states for open-loop simulation
        starts and the QP linearisation point.
        """
        t_air = np.asarray(t_air, dtype=float).ravel()
        return self._wall_eq_ratio * t_air + (1.0 - self._wall_eq_ratio) * float(t_out)

    def initial_state_from_measurement(
        self,
        y: np.ndarray,
        u: Optional[np.ndarray] = None,
        d: Optional[np.ndarray] = None,
        wall_seed: str = "steady_state",
    ) -> np.ndarray:
        """Build a full state vector that is consistent with a measurement.

        This is the single source of truth for initialising an open-loop
        (free-run) simulation or filter from a recorded data point so that
        the model **starts at the same state the data is in**.

        **Wall-seed contract** (shared by the three replay pipelines)::

            Pipeline                          First start           Continuation
            --------------------------------  --------------------  ------------------
            Estimator objective               air → t_wall_initial  steady_state
            Open-loop diagnostics             air → t_wall_initial  steady_state *
            EKF reconstruction (sysid)        air → t_wall_initial  (N/A — filter)

        ``t_wall_initial`` is the identified envelope temperature at the
        **dataset start**; it is applied exactly once per replay.  Later
        estimator windows and diagnostic segment restarts use
        ``"steady_state"`` so the wall warm-start tracks the local
        (T_a, T_out) equilibrium instead of reusing the dataset-start value.
        Continuous open-loop plots (``segment_length=None``) propagate state
        forward and do not re-seed mid-run (* no re-init).

        Common air / emitter / offset rules for every call:

        * air-temperature block ``T_a`` ← measured temperatures ``y`` so
          that ``hm(x0) == y`` exactly (the offset block ``b`` is zeroed,
          so the predicted output equals the measured temperature with no
          initial innovation);
        * wall block ``T_w`` ← depends on ``wall_seed``:

          - ``"steady_state"`` (default): the steady-state value implied by the
            air and outdoor temperatures (:meth:`wall_equilibrium`), falling
            back to the air temperature when no disturbance vector is supplied.
            This starts the unobserved wall close to its true value for the
            estimator's short free-run windows, which improves identifiability.
          - ``"air"``: the measured air temperature, i.e. the envelope starts
            equal to the air node (``T_air = T_envelope``).  This is the
            unbiased seed used by the reconstruction / open-loop **diagnostics**:
            it makes no assumption about the (yet-to-be-identified) parameters,
            so the displayed envelope starts at the air temperature instead of
            jumping to a parameter-dependent steady state near the setpoint;
        * emitter-lag block ``φ`` ← warm-started to the commanded fraction
          ``u`` of each filtered source (the steady state of the first-order
          emitter filter ``dφ/dt = (u − φ)/τ``).  Cold-starting ``φ`` at zero
          would make the simulation under-deliver heat for ~one emitter time
          constant and inject a spurious start-of-window transient/bias;
        * offset block ``b`` ← zero (random-walk bias states are not part of
          the physical open-loop prediction).

        Parameters
        ----------
        y : array-like
            Measured room temperatures (length ≥ ``n_rooms``; extra entries
            ignored).
        u : array-like, optional
            Commanded control fractions for each source.  When omitted the
            emitter-lag states start at zero (cold).
        d : array-like, optional
            Disturbance vector; only ``d[0]`` (outdoor temperature) is used,
            for the ``"steady_state"`` wall warm start.
        wall_seed : str, optional
            ``"steady_state"`` (default) or ``"air"`` — see above.

        Returns
        -------
        np.ndarray
            State vector of length ``self.nx``.
        """
        n = self._n_rooms
        m = self._n_filtered
        nx_phys = self._nx_phys
        x = np.zeros(self._nx, dtype=float)

        y_arr = np.asarray(y, dtype=float).ravel()
        n_copy = min(n, y_arr.size)
        x[:n_copy] = y_arr[:n_copy]

        # Wall warm start.  ``"air"`` (diagnostics) seeds the envelope at the air
        # node; ``"steady_state"`` (estimator) uses the (T_a, T_out) equilibrium.
        if wall_seed == "air":
            x[n: 2 * n] = x[:n]
        else:
            t_out: Optional[float] = None
            if d is not None:
                d_arr = np.asarray(d, dtype=float).ravel()
                if d_arr.size > 0 and np.isfinite(d_arr[0]):
                    t_out = float(d_arr[0])
            if t_out is None:
                x[n: 2 * n] = x[:n]
            else:
                x[n: 2 * n] = self.wall_equilibrium(x[:n], t_out)

        if m > 0 and u is not None:
            u_arr = np.asarray(u, dtype=float).ravel()
            for k, src_idx in enumerate(self._filtered_source_indices):
                if src_idx < u_arr.size:
                    x[nx_phys + k] = float(u_arr[src_idx])

        # Offset block (if augmented) intentionally left at zero so that
        # hm(x0) == y exactly.
        return x

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
        air_gains: Optional[Dict[str, float]] = None,
    ) -> np.ndarray:
        """
        Pack the disturbances into ``d = [T_out, q_solar (n), q_air (n)]``.

        ``solar_gains`` are the *unscaled* modelled solar gains [W] — the
        identified per-room ``solar_scale`` and the air/wall split are
        applied by ``G_d``.  The air-heat channel carries
        ``Room.internal_gain`` plus any caller-supplied per-room extra
        (e.g. the decaying online gain deviation over the control horizon);
        it lands on the air node only.
        """
        n = self._n_rooms
        d = np.zeros(self.nd)
        d[0] = outdoor_temp
        for i, name in enumerate(self._room_list):
            d[1 + i] = float(solar_gains.get(name, 0.0))
            extra = float(air_gains.get(name, 0.0)) if air_gains else 0.0
            d[1 + n + i] = float(self._model.rooms[name].internal_gain) + extra
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

    def display_heating_powers(
        self,
        u_vec: np.ndarray,
        outdoor_temp: float,
    ) -> Dict[str, float]:
        """Convert fractions u to per-room display thermal power [W].

        Uses configured rated capacities and ignores identified ``power_scale``
        so room-view plots and sensors reflect heater configuration.
        """
        powers: Dict[str, float] = {name: 0.0 for name in self._room_list}
        for j, src in enumerate(self._sources):
            u_j = float(u_vec[j])
            if src.can_cool:
                powers[src.room] += src.display_smooth_thermal_power(
                    u_j, outdoor_temp, self._k_sigmoid,
                )
            else:
                powers[src.room] += src.display_thermal_power(u_j, outdoor_temp)
        return powers

    def heating_powers(
        self,
        u_vec: np.ndarray,
        outdoor_temp: float,
    ) -> Dict[str, float]:
        """Convert fractions u to per-room model thermal power [W].

        Includes identified ``power_scale`` — for plots use
        :meth:`display_heating_powers`.

        Cooling-capable sources use ``smooth_thermal_power`` (same as
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
        - Cooling-capable sources: closed-form piecewise-linear inverse.
        - Heating-only sources: linear inverse.

        The result is clipped to each source's [u_min, u_max].

        For filtered sources (τ_em > 0) the equilibrium satisfies φ_ss = u_eq
        (the filter state equals the commanded input at steady state).  The
        same inversion logic is used as for un-filtered sources; the returned
        u_eq[j] is both the commanded input and the equilibrium filter state.
        Callers that build the operating-point state x_ss should set
        x_ss[n + k_filter] = u_eq[j] for each filtered source j.

        This is used as the QP linearisation point so the local power slope
        matches the expected operating region, reducing model mismatch during
        large transients.
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
                    # Invert the piecewise-linear curve: heating (q_req ≥ 0) uses
                    # the q_heat slope, cooling (q_req < 0) uses the q_cool slope.
                    if q_req >= 0.0:
                        eff_u_eq = q_req / q_heat
                    else:
                        eff_u_eq = q_req / q_cool
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

def _diag_np(n: int, v: float) -> np.ndarray:
    """Return an n×n diagonal numpy matrix with v on the diagonal."""
    return np.eye(n) * v


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
        x_prior = self._x.copy()
        y_hat = self._model.hm(x_prior, u, d, p, 0.0)
        self._last_innovation = (np.asarray(y, dtype=float) - y_hat).tolist()
        return self.update(y, u, d, p, mask=mask)


# ── House-heating successive-linearisation MPC ───────────────────────────────

class HeatingLinearisedMPC(StandardLinearisedContinuousMPC):
    """House-heating wrapper around mbc's :class:`StandardLinearisedContinuousMPC`.

    All optimal control — condensed QP assembly, soft-output corridor slacks,
    absolute-input regularisation, signed-magnitude (bidirectional) price
    slacks, successive linearisation and CD-EKF estimation — is performed by the
    native mbc classes.  This wrapper only adds house-heating glue:

    * **Linearisation point.**  The Jacobians are evaluated at the comfort
      *setpoint* equilibrium — room setpoints, walls at their steady state for
      those setpoints and the current outdoor temperature, and emitter-filter
      states at ``u_eq`` — rather than at the current estimate, so the local
      model stays accurate during transients.  With ``Q = 0`` and the absolute-
      input cost this yields pure zone control (minimum-cost action inside the
      comfort corridor is ``u_abs = 0``).
    * **Horizon translation.**  The per-step / per-room / per-source quantities
      the coordinator produces (disturbance forecast, setpoints, comfort
      offsets, Q/R scales, input bounds and the electricity-price linear cost on
      the absolute draw) are mapped onto the native horizon-profile setters.
    * **Estimate-only.**  :meth:`estimate_only` runs the estimator without
      solving the OCP, for when the controller is stopped.

    The absolute-input quadratic cost (``‖u_abs‖²_R`` rather than ``‖u_dev‖²_R``)
    is supplied natively: :meth:`~StandardLinearisedContinuousMPC.compute` sets
    the OCP ``input_equilibrium`` to ``u_ss`` automatically each solve.
    """

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
        u_min_seq: Optional[np.ndarray] = None,
        u_max_seq: Optional[np.ndarray] = None,
        price_seq: Optional[np.ndarray] = None,
        elec_heat: Optional[np.ndarray] = None,
        elec_cool: Optional[np.ndarray] = None,
        bid_mask: Optional[np.ndarray] = None,
        price_weight: float = 0.0,
        dt_h: float = 0.25,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run one MPC step and return ``(u_abs, U_abs, X_abs)``.

        Parameters mirror the coordinator's trajectory: ``D_forecast`` is the
        absolute disturbance forecast ``(N, nd)``; ``x_ref_abs_seq`` the absolute
        per-room setpoints ``(N, n_rooms)``; ``offset_seq`` the comfort-corridor
        half-widths ``(N, n_rooms)``; ``q_scale_seq``/``r_scale_seq`` per-step
        per-room/per-source weight multipliers; ``u_min_seq``/``u_max_seq`` the
        per-step *absolute* input box; and ``price_seq``/``elec_*``/``bid_mask``/
        ``price_weight``/``dt_h`` the electricity-price linear cost on the
        absolute electrical draw.  ``None`` falls back to the static behaviour
        for that quantity.
        """
        model = self._model
        nz = model.nz
        nu = model.nu
        n = model._n_rooms
        nx_phys = model._nx_phys
        d_now = np.asarray(d, dtype=float).reshape(model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)

        # ── Linearisation point: the comfort-setpoint equilibrium ────────────
        # Air states are set to the room setpoints and wall states to their
        # steady-state values, giving a consistent (x_ss, u_ss) equilibrium
        # pair when the setpoint is feasible.
        x_ss = self._x_ref_abs.copy()
        x_ss[n:nx_phys] = model.wall_equilibrium(x_ss[:n], float(d_now[0]))
        u_ss = model.compute_u_eq(x_ss, d_now, p_, t)
        for j in range(nu):
            k_f = model._filter_idx_for_source[j]
            if k_f >= 0:
                x_ss[nx_phys + k_f] = u_ss[j]

        # ── Infeasibility correction ──────────────────────────────────────────
        # When the HP cannot maintain the setpoint at full output, compute_u_eq
        # clips u_ss to u_max and (x_ss=[setpoint], u_ss=u_max) is NOT a true
        # equilibrium: the room actually cools even at max input.  The mbc
        # deviation model assumes equilibrium and predicts stable temperatures,
        # so the MPC never sees the room drop and suppresses actuation.
        # One Newton step on the air-temperature drift finds the achievable
        # equilibrium temperature, making (x_ss_corrected, u_ss) consistent.
        air_drift = model.f(x_ss, u_ss, d_now, p_, t)[:n]
        if np.any(air_drift < -1e-6):
            J = model.dfdx(x_ss, u_ss, d_now, p_, t)
            rho = model._wall_eq_ratio          # (n,) per-room wall-to-air ratio
            F_eff = J[:n, :n] + J[:n, n:2 * n] * rho.reshape(1, -1)
            try:
                delta_air = np.linalg.solve(F_eff, -air_drift)
                x_ss_air_orig = x_ss[:n].copy()
                # Correction moves setpoint down; clip to [T_outdoor, setpoint].
                x_ss[:n] = np.clip(x_ss[:n] + delta_air, float(d_now[0]), x_ss_air_orig)
                x_ss[n:nx_phys] = model.wall_equilibrium(x_ss[:n], float(d_now[0]))
                u_ss = model.compute_u_eq(x_ss, d_now, p_, t)
                for j in range(nu):
                    k_f = model._filter_idx_for_source[j]
                    if k_f >= 0:
                        x_ss[nx_phys + k_f] = u_ss[j]
            except np.linalg.LinAlgError:
                pass

        d_ss = d_now.copy()

        # ── Configure the native horizon profile for this solve ──────────────
        self.clear_horizon_profile()
        self.set_linearisation_point(x_ss, u_ss, d_ss)

        if D_forecast is not None:
            self.set_disturbance_profile(
                np.asarray(D_forecast, dtype=float).reshape(self._N, model.nd)
            )

        if x_ref_abs_seq is not None:
            # Native tracks z = Cz·x against z_ref + dev.  At the setpoint
            # equilibrium the base z_ref is zero, so dev is the per-step setpoint
            # in deviation coordinates relative to the linearisation point.
            dev = np.asarray(x_ref_abs_seq, dtype=float) - x_ss[:nz].reshape(1, -1)
            self.set_output_reference_profile(dev)

        if offset_seq is not None:
            self.set_soft_output_band_half_width_profile(
                np.asarray(offset_seq, dtype=float)
            )
        else:
            # Always set per-room comfort offsets from the model so each room
            # uses its own configured offset rather than the global maximum
            # that was baked in at construction time.
            room_offsets = np.array(
                [
                    float(
                        getattr(self._model._model.rooms[name], "comfort_offset", 2.0)
                        or 2.0
                    )
                    for name in self._model._room_list
                ],
                dtype=float,
            )
            self.set_soft_output_band_half_width_profile(
                np.tile(room_offsets.reshape(1, -1), (self._N, 1))
            )

        if q_scale_seq is not None:
            self.set_output_tracking_weight_scale_profile(
                np.asarray(q_scale_seq, dtype=float)
            )

        if r_scale_seq is not None:
            self.set_input_regularisation_weight_scale_profile(
                np.asarray(r_scale_seq, dtype=float)
            )

        if u_min_seq is not None and u_max_seq is not None:
            # Absolute input-bound profiles; native shifts them into deviation
            # coordinates by u_ss inside compute().
            self.set_input_bound_profiles(
                np.asarray(u_min_seq, dtype=float),
                np.asarray(u_max_seq, dtype=float),
            )

        self._configure_price(
            price_seq, elec_heat, elec_cool, bid_mask, price_weight, dt_h
        )

        u_abs, U_abs, X_abs = self.compute(y, d, p, t)

        # Safety clip the whole trajectory to the physical actuator range as a
        # guard against tiny solver-tolerance excursions (native clips step 0).
        u_min_abs, u_max_abs = model.u_bounds
        U_abs = np.clip(
            U_abs, u_min_abs.reshape(1, -1), u_max_abs.reshape(1, -1)
        )
        u_abs = U_abs[0].copy()
        self._u_prev = u_abs.copy()
        return u_abs, U_abs, X_abs

    def _configure_price(
        self,
        price_seq: Optional[np.ndarray],
        elec_heat: Optional[np.ndarray],
        elec_cool: Optional[np.ndarray],
        bid_mask: Optional[np.ndarray],
        price_weight: float,
        dt_h: float,
    ) -> None:
        """Map the electricity-price cost onto the native input-linear-cost profile.

        The price charges the *absolute* electrical draw (kW × h × €/kWh).
        Non-bidirectional sources are priced directly on their (signed) input:
        heating-only sources pay ``elec_heat`` per ``+u``, cooling-only sources
        pay ``elec_cool`` per ``|u|`` (a negative gradient).  Bidirectional
        sources use the native signed-magnitude decomposition ``u = s⁺ − s⁻``
        with asymmetric coefficients ``elec_heat`` on ``s⁺`` and ``elec_cool`` on
        ``s⁻``, so the effective cost is ``π·c·|u|`` for the active direction.
        """
        if not (price_seq is not None and price_weight > 0.0 and elec_heat is not None):
            return

        model = self._model
        N = self._N
        nu = model.nu
        price = np.maximum(np.asarray(price_seq, dtype=float).reshape(-1), 0.0)
        n_price = len(price)
        elec_heat = np.asarray(elec_heat, dtype=float).reshape(nu)
        elec_cool = (
            np.asarray(elec_cool, dtype=float).reshape(nu)
            if elec_cool is not None
            else elec_heat
        )
        bid = (
            np.asarray(bid_mask, dtype=bool).reshape(nu)
            if bid_mask is not None
            else np.zeros(nu, dtype=bool)
        )
        _, u_max_abs = model.u_bounds

        coeff = np.zeros((N, nu))
        pos = np.zeros((N, nu))
        neg = np.zeros((N, nu))
        for k in range(N):
            p_k = float(price[min(k, n_price - 1)])
            w = price_weight * p_k * dt_h
            for i in range(nu):
                if bid[i]:
                    pos[k, i] = w * elec_heat[i] * 1e-3
                    neg[k, i] = w * elec_cool[i] * 1e-3
                elif float(u_max_abs[i]) > 0.0:
                    coeff[k, i] = w * elec_heat[i] * 1e-3
                else:
                    coeff[k, i] = -w * elec_cool[i] * 1e-3

        bid_idx = np.flatnonzero(bid)
        if bid_idx.size > 0:
            self.set_input_linear_cost_profile(
                coeff,
                signed_magnitude_input_indices=bid_idx,
                positive_slack_coefficient_profile=pos[:, bid_idx],
                negative_slack_coefficient_profile=neg[:, bid_idx],
            )
        else:
            self.set_input_linear_cost_profile(coeff)

    def estimate_only(
        self,
        y: np.ndarray,
        d: np.ndarray,
        p: Optional[np.ndarray] = None,
        t: float = 0.0,
    ) -> np.ndarray:
        """Run the CD-EKF predict+update without solving the OCP.

        Used when the controller is stopped: state estimation and innovation
        logging must keep running so the filtered temperatures stay grounded in
        reality, but the (expensive) MPC optimisation is skipped.

        Calls the estimator's combined step (predict for the nominal Ts then
        measurement update) directly rather than delegating to
        :meth:`~StandardLinearisedContinuousMPC.propagate`.  ``propagate``
        gates the predict phase on ``t - t_last > 0``, but because the full
        optimization path always passes ``t=0.0`` the stored ``_t_last`` is
        permanently 0 — making ``dt`` identically zero and silently dropping
        every predict call.  Without predict, process noise is never added to
        the covariance ``P``: over many cycles ``P → 0``, the Kalman gain
        collapses to zero, and the filter stops tracking the actual
        temperature.  When the system is later re-enabled the first real
        predict step inflates ``P`` and the subsequent update snaps the frozen
        estimate to the current measurement, producing the large discrete jump
        reported by the user.

        Calling ``_estimator.step`` directly guarantees predict always runs
        for exactly ``Ts``, keeping ``P`` at its healthy steady-state value
        and the gain at a level that lets the filter track temperature changes
        throughout the stopped period.

        ``_d_prev`` is advanced to the current disturbance (matching
        ``propagate``'s bookkeeping) so the next cycle's predict uses the
        right disturbance; ``_u_prev`` is left untouched so the coordinator
        can correct it to the actually-delivered input via
        :meth:`HeatingMPCController.notify_applied_u` before the next cycle.

        Returns the filtered state estimate ``x_hat``.
        """
        d_now = np.asarray(d, dtype=float).reshape(self._model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)
        y_arr = np.asarray(y, dtype=float).reshape(self._model.nym)
        x_hat, _ = self._estimator.step(y_arr, self._u_prev, self._d_prev, p_, t)
        self._d_prev = d_now.copy()
        return np.asarray(x_hat, dtype=float)


# ============================================================
# House-heating linearised MPC facade
# ============================================================

class HeatingMPCController:
    """
    Application facade for house-heating NMPC + P tracking.

    Builds a HouseThermalSDE, _InnovationEKF (CD-EKF), and
    HeatingLinearisedMPC (EKF glue only — the linearised QP is not solved
    on the happy path), then provides the coordinator-facing API:

      actions = controller.compute(outdoor_temp, solar_gains, now, outdoor_forecast)

    The fast loop at each ``T_s``:
      1. CD-EKF predict+update using the last applied P command.
      2. ``u = clip(u_ref + K_p (T_ref − T_hat), u_min, u_max)`` from the
         last accepted plan.  With no plan, P toward the setpoint while
         air is outside the comfort band (watchdog still forces ``u = 0``).
      3. Apply the command to all heat sources.

    The slow OCP (SciPy SLSQP, analytic Jacobian) runs on a worker via
    :meth:`solve_nmpc`.  Cost: soft comfort zone, input ROM, energy-price.
    No extra setpoint pull.

    Forecasts
    ---------
    Outdoor temperature: uses weather forecast if provided by the coordinator;
        otherwise falls back to persistence (constant at the current measurement).
    Solar gains: computed from the solar geometry model for each horizon step.

    Parameters
    ----------
    model             : HouseModel
    heat_sources      : list of HeatSource
    horizon           : fast-step count (tests / legacy).  Ignored when the
                        NMPC triple is given.
    dt                : fast sample ``T_s`` [s] (tests / legacy).
    nmpc_period       : slow NMPC cadence [s].  With the other triple keys,
                        this wins over ``horizon`` / ``dt``.
    nmpc_fast_substeps: fast ticks per slow interval.
    nmpc_horizon_h    : look-ahead [hours].
    measurement_dt    : EKF measurement interval [s].  If None, falls back to dt.
    latitude          : site latitude [deg]
    longitude         : site longitude [deg]
    tracking_weight   : weight on ||z - z_ref||^2_Q (kept for API; NMPC cost
                        does not add setpoint pull).
    energy_weight     : weight on ||u||^2_R (QP glue only).
    smoothing_weight  : ROM weight on ||Delta u||^2 in the slow OCP.
    soft_constraint_weight : quadratic penalty rho on comfort-zone violations.
    soft_constraint_linear_weight : kept for API; unused by the NMPC cost.
    sigma_w           : process-noise std dev for the SDE / EKF [K/sqrt(s)].
    sigma_v           : measurement-noise std dev [K].
    sigma_b           : offset-state process-noise std dev [K/sqrt(s)].
    n_int_steps       : Euler sub-steps per interval in EKF / OCP.
    solver            : accepted for API compatibility, ignored (SLSQP used).
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
        albedo: float = 0.2,
        tracking_weight: float = DEFAULT_SETPOINT_PULL_WEIGHT,
        energy_weight: float = 0.01,
        smoothing_weight: float = 0.1,
        soft_constraint_weight: float = DEFAULT_SOFT_CONSTRAINT_WEIGHT,
        soft_constraint_linear_weight: float = 0.0,
        terminal_weight: float = 100.0,
        sigma_w: float = 0.1,
        sigma_v: float = 0.5,
        sigma_b: float = 0.002,
        n_int_steps: int = 10,
        solver: str = "nmpc",
        solver_options: Optional[Dict[str, Any]] = None,
        use_analytic_derivatives: bool = True,
        energy_price_weight: float = 0.0,
        nmpc_period: Optional[float] = None,
        nmpc_fast_substeps: Optional[int] = None,
        nmpc_horizon_h: Optional[float] = None,
    ) -> None:
        self._sources = heat_sources
        if (
            nmpc_period is not None
            or nmpc_fast_substeps is not None
            or nmpc_horizon_h is not None
        ):
            timing = derive_nmpc_timing(
                DEFAULT_NMPC_PERIOD if nmpc_period is None else float(nmpc_period),
                DEFAULT_NMPC_FAST_SUBSTEPS
                if nmpc_fast_substeps is None
                else int(nmpc_fast_substeps),
                DEFAULT_NMPC_HORIZON_H if nmpc_horizon_h is None else float(nmpc_horizon_h),
            )
        else:
            timing = timing_from_dt_horizon(dt, horizon)
        self._timing: NmpcTiming = timing
        self._horizon = timing.n_fast
        self._dt = timing.dt_s
        self._latitude = latitude
        self._longitude = longitude
        self._albedo = float(albedo)

        # solver/derivative args accepted for API compat; SLSQP is the NLP.
        self._solver_requested = "nmpc"
        self._solver_active = "slsqp"
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
        # compute() calls, NOT the slow OCP period.
        ekf_dt = measurement_dt if measurement_dt is not None else self._dt

        # ── Build SDE models ────────────────────────────────────────────
        # The EKF (estimation) and control (linearisation) models share the
        # same un-augmented state space.  Offsets stay disabled to keep the
        # dimensions small.
        self._system = HouseThermalSDE(
            model, heat_sources, self._dt,
            ts=ekf_dt,
            sigma_w=sigma_w, sigma_v=sigma_v,
            sigma_b=sigma_b,
            augment_offsets=False,
            n_int_steps=n_int_steps,
        )
        self._control_system = HouseThermalSDE(
            model, heat_sources, self._dt,
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
        # The wall states are not measured and start at the air temperature;
        # give them a larger initial variance so the filter knows they are a
        # guess and lets the dynamics pull them to a consistent value.
        for i in range(n_rooms):
            P0[n_rooms + i, n_rooms + i] = 4.0
        self._ekf = _InnovationEKF(
            self._system, x0, P0,
            params=ContinuousDiscreteEKFParams(
                n_steps=n_int_steps,
                scheme=IntegrationScheme.IMPLICIT_EULER,
            ),
        )

        # ── OCP cost matrices ────────────────────────────────────────────
        Q_cv = _diag_np(n_z, float(tracking_weight))
        R_cv = _diag_np(n_u, float(energy_weight))
        P_cv = _diag_np(n_z, float(terminal_weight) * float(tracking_weight))
        S_cv = _diag_np(n_u, float(smoothing_weight)) if smoothing_weight > 0.0 else None

        # Soft-constraint penalties on comfort-corridor violations
        rho = float(soft_constraint_weight)
        rho_lin = float(soft_constraint_linear_weight)

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

        # State reference: setpoints on the room-temperature block, zero
        # elsewhere.  Sized to the *control* model (the MPC does not carry the
        # EKF's internal-gain block), which may be smaller than ``n_x``.
        n_x_ctrl = self._control_system.nx
        x_ref = np.zeros(n_x_ctrl)
        x_ref[:n_rooms] = [model.rooms[name].setpoint for name in room_list]

        # ── MPC controller (native mbc successive-linearisation MPC) ─────
        self._mpc = HeatingLinearisedMPC(
            model=self._control_system,
            estimator=self._ekf,
            N=self._horizon,
            Q=Q_cv,
            R=R_cv,
            dt=self._dt,
            u_min=u_min,
            u_max=u_max,
            x_ref=x_ref,
            P=P_cv,
            S=S_cv,
            rho=rho,
            rho_lin=rho_lin,
            z_offset=y_offset,
        )

        # Store global cost weights so the trajectory builder can use them
        # to convert per-period multipliers to absolute values if needed,
        # and so backward-compatibility checks can detect the static case.
        self._tracking_weight: float = float(tracking_weight)
        self._energy_weight: float = float(energy_weight)

        # ── Price-aware cost term ────────────────────────────────────────
        self._energy_price_weight: float = float(energy_price_weight)
        self._dt_h: float = self._dt / 3600.0
        # Electrical draw per unit of u for each source (recomputed from
        # current power_scale so estimation updates are reflected).
        self._elec_heat: np.ndarray = np.array(
            [src.elec_per_unit_heat for src in heat_sources], dtype=float
        )
        self._elec_cool: np.ndarray = np.array(
            [src.elec_per_unit_cool for src in heat_sources], dtype=float
        )
        # Bidirectional mask: sources that can act in *both* directions (u_min < 0 and u_max > 0).
        # Only these require slack variables (s⁺, s⁻) so that a single u variable can represent
        # either heating or cooling while electricity price is charged on the absolute draw via
        # the slacks. Pure heating-only or cooling-only sources are priced directly on their
        # (signed) input variable and must not appear in the slack price terms.
        self._bid_mask: np.ndarray = np.array(
            [(src.u_min < 0 and src.u_max > 0) for src in heat_sources], dtype=bool
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
        # Unconstrained MPC optimum per source from the last compute(), captured
        # *before* disabled-source zeroing.  Used to resume a force-disabled
        # heater (e.g. open-window override) at the value the MPC kept solving
        # for in the background once the override clears.
        self._mpc_actions: Dict[str, float] = {}

        # ── Slow NMPC path / watchdog ────────────────────────────────────
        self._nmpc_lock = threading.Lock()
        self._nmpc_U: Optional[np.ndarray] = None
        self._nmpc_T_ref: Optional[np.ndarray] = None
        self._nmpc_U_fast: Optional[np.ndarray] = None
        self._nmpc_k: int = 0
        self._nmpc_warm: Optional[np.ndarray] = None
        self._rho = float(rho)
        self._smoothing_weight = float(smoothing_weight)
        self._n_int_steps = int(n_int_steps)
        self._reject_since: Optional[float] = None
        self._watchdog_tripped: bool = False
        self._notify_active: bool = False
        self._watchdog_notification: Optional[str] = None
        self._nmpc_busy: bool = False

    # ── Visualisation / diagnostic properties ────────────────────────────

    @property
    def horizon(self) -> int:
        """NMPC / P prediction horizon (number of fast steps)."""
        return self._horizon

    @property
    def solve_times(self) -> deque:
        """Rolling buffer of recent NMPC solve times [s] (read-only view)."""
        return self._solve_times

    @property
    def terminal_weight(self) -> float:
        """Terminal cost weight (P = terminal_weight * Q) in effect for this controller."""
        return self._terminal_weight

    @property
    def solver_requested(self) -> str:
        """Configured solver backend name (always 'nmpc')."""
        return self._solver_requested

    @property
    def solver_active(self) -> str:
        """Currently active solver backend (always 'slsqp')."""
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

    def set_cloud_cover(self, cloud_cover: Optional[float]) -> None:
        """Attenuate the sky cooling drift by the current cloud cover.

        Pushed to both the EKF system and the control system.  Only
        effective for rooms with ``sky_radiative_ua > 0``.
        """
        self._system.set_cloud_cover(cloud_cover)
        self._control_system.set_cloud_cover(cloud_cover)

    def set_room_process_noise_covariance_scales(
        self, scales_by_room: Dict[str, float],
    ) -> None:
        """Apply per-room EKF/OCP process-noise covariance multipliers."""
        self._system.set_room_process_noise_covariance_scales(scales_by_room)
        self._control_system.set_room_process_noise_covariance_scales(scales_by_room)

    def set_window_open(self, flags: Dict[str, bool]) -> None:
        """Hold current window/door contact on the EKF and MPC predictors."""
        self._system.set_window_open(flags)
        self._control_system.set_window_open(flags)

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
    def gain_estimation_enabled(self) -> bool:
        """Whether online internal-gain estimation is active for this controller.

        Online gain estimation has been removed from the state space, so this is
        always ``False``.  Retained for sensor/coordinator API compatibility.
        """
        return False

    @property
    def estimated_internal_gains(self) -> Dict[str, float]:
        """Per-room internal heat gain [W].

        Equals each room's configured (offline-identified) nominal internal
        gain.  No online deviation is estimated.
        """
        room_list = self._system._room_list
        rooms = self._system._model.rooms
        return {
            name: float(rooms[name].internal_gain)
            for name in room_list
        }

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room filtered temperatures from the EKF (same as filtered_temperatures)."""
        return self.filtered_temperatures

    @property
    def wall_temperatures(self) -> Dict[str, float]:
        """Per-room EKF-reconstructed wall/mass-node temperatures."""
        x_hat = self._ekf.x_hat
        room_list = self._system._room_list
        n = self._system._n_rooms
        if len(x_hat) < 2 * n:
            return self.temperatures
        return {
            name: float(x_hat[n + i]) for i, name in enumerate(room_list)
        }

    @property
    def wall_temperature_stds(self) -> Dict[str, float]:
        """Per-room posterior std [°C] of the EKF wall-state estimate.

        The wall node is not measured, so this is the direct observability
        health signal: it should contract after start-up and stay bounded.
        """
        room_list = self._system._room_list
        n = self._system._n_rooms
        try:
            P = np.asarray(self._ekf.P, dtype=float)
        except Exception:
            return {name: float("nan") for name in room_list}
        if P.shape[0] < 2 * n:
            return {name: float("nan") for name in room_list}
        return {
            name: float(np.sqrt(max(0.0, P[n + i, n + i])))
            for i, name in enumerate(room_list)
        }

    @property
    def slab_temperatures(self) -> Dict[str, float]:
        """Per-room temperatures (no slab node — alias for the air node)."""
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
    def mpc_actions(self) -> Dict[str, float]:
        """Unconstrained MPC optimum per source from the last compute().

        Unlike the dict returned by :meth:`compute`, these values are *not*
        zeroed for ``disabled_sources``: they are the actuation the MPC would
        command if every source were available.  The coordinator uses them to
        bring a heater back online at the right level the instant an
        open-window override settle timer expires.
        """
        return dict(self._mpc_actions)

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

    @property
    def ekf_state(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return (x_hat, P) copies from the EKF estimator."""
        return self._ekf.x_hat, self._ekf.P

    @property
    def ekf_inputs(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return (u_prev, d_prev) last passed to the EKF predict step."""
        return self._mpc._u_prev.copy(), self._mpc._d_prev.copy()

    def restore_ekf_state(self, x_hat: np.ndarray, P: np.ndarray) -> bool:
        """Inject a previously persisted EKF state into the filter.

        Used to preserve state across stop/start sequences without triggering
        a full-variance cold-start.  Silently ignores the restore when the
        array shapes do not match the current model (e.g. after a room was
        added or removed) so the filter falls back to its default warm-up.

        Returns True on success, False if the dimensions were incompatible.
        """
        x_hat = np.asarray(x_hat, dtype=float)
        P = np.asarray(P, dtype=float)
        n_x = self._ekf._x.shape[0]
        if x_hat.shape != (n_x,) or P.shape != (n_x, n_x):
            return False
        self._ekf._x = x_hat.copy()
        self._ekf._P = P.copy()
        return True

    def propagate_ekf(self, u_seq: np.ndarray, d: np.ndarray) -> None:
        """Propagate the EKF forward without measurement updates.

        Called after restoring a persisted EKF state to fill the gap between
        the last save and the current restart time, using the actuator/
        disturbance sequence that best reflects what happened during the gap
        (experiment excitation, schedule off-periods, or last commanded value).

        Parameters
        ----------
        u_seq : (n_steps, nu) array — per-step actuator commands.
        d     : (nd,) array       — disturbance held constant over the gap.
        """
        p_ = np.array([], dtype=float)
        d_arr = np.asarray(d, dtype=float)
        u_arr = np.asarray(u_seq, dtype=float)
        if u_arr.ndim == 1:
            u_arr = u_arr.reshape(1, -1)
        for u_k in u_arr:
            self._ekf.predict(u_k, d_arr, p_, 0.0)
        # Update _mpc's bookkeeping so the next EKF step uses the last
        # gap actuator as its u_prev (consistent with normal operation).
        if len(u_arr) > 0:
            self._mpc._u_prev = u_arr[-1].copy()

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
        """Return the wall-node block ``x[n:2n]`` (air block if too short)."""
        n = system.nym
        if len(x) >= 2 * n:
            return x[n: 2 * n].copy()
        if len(x) < n:
            return np.zeros(n)
        return x[:n].copy()

    def _slab_temperatures_from_state(
        self,
        system: HouseThermalSDE,
        x: np.ndarray,
    ) -> np.ndarray:
        """Return the air-node temperatures (no slab block)."""
        n = system.nym
        if len(x) < n:
            return np.zeros(n)
        return x[:n].copy()

    @property
    def timing(self) -> NmpcTiming:
        """Derived two-rate grid."""
        return self._timing

    @property
    def nmpc_due(self) -> bool:
        """True when a slow solve should start (no path, idle zeros, or one period)."""
        if self._nmpc_busy:
            return False
        with self._nmpc_lock:
            if self._nmpc_U is None:
                return True
            if float(np.max(np.abs(self._nmpc_U))) < NMPC_IDLE_U_ABS:
                return True
            return self._nmpc_k >= self._timing.m

    def nmpc_plan_idle(self) -> bool:
        """True when an installed slow plan is identically off."""
        with self._nmpc_lock:
            U = self._nmpc_U
            if U is None:
                return False
            return float(np.max(np.abs(U))) < NMPC_IDLE_U_ABS

    def consume_watchdog_notification(self) -> Optional[str]:
        """Return ``create`` / ``dismiss`` once, then clear."""
        note = self._watchdog_notification
        self._watchdog_notification = None
        return note

    def set_accepted_path(
        self,
        u_star: np.ndarray,
        t_ref: np.ndarray,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Install a slow plan for the fast P-law (tests and worker)."""
        U = np.asarray(u_star, dtype=float).reshape(self._timing.n_slow, self._system.nu)
        T = np.asarray(t_ref, dtype=float)
        n_fast = self._timing.n_fast
        n_rooms = self._system._n_rooms
        if T.ndim == 1:
            T = np.tile(T.reshape(1, -1), (n_fast, 1))
        if T.shape[0] < n_fast:
            pad = np.tile(T[-1:], (n_fast - T.shape[0], 1))
            T = np.vstack([T, pad])
        T = T[:n_fast, :n_rooms]
        with self._nmpc_lock:
            self._nmpc_U = U
            self._nmpc_T_ref = T
            self._nmpc_U_fast = np.repeat(U, self._timing.m, axis=0)
            self._nmpc_k = 0
            self._nmpc_warm = U.reshape(-1).copy()
            self._reject_since = None
            if self._watchdog_tripped or self._notify_active:
                self._watchdog_notification = "dismiss"
            self._watchdog_tripped = False
            self._notify_active = False

    def _record_nmpc_reject(self, now: Optional[float] = None) -> None:
        stamp = time.time() if now is None else float(now)
        if self._reject_since is None:
            self._reject_since = stamp
        if (stamp - self._reject_since) >= NMPC_WATCHDOG_S:
            self._watchdog_tripped = True
            if not self._notify_active:
                self._watchdog_notification = "create"
                self._notify_active = True
            with self._nmpc_lock:
                self._nmpc_U = None
                self._nmpc_T_ref = None
                self._nmpc_U_fast = None
                self._nmpc_k = 0

    def apply_nmpc_result(
        self,
        result: Dict[str, Any],
        *,
        now: Optional[float] = None,
    ) -> bool:
        """Accept or reject a worker result.  Returns True when the path updated."""
        self._nmpc_busy = False
        elapsed = result.get("elapsed_s")
        if elapsed is not None:
            self._solve_times.append(float(elapsed))
        if result.get("accepted"):
            self.set_accepted_path(result["u_star"], result["t_ref"], now=now)
            self.rebuild_forecast_from_plan()
            return True
        self._record_nmpc_reject(now)
        return False

    def rebuild_forecast_from_plan(self) -> bool:
        """Rebuild plot trajectories from the installed slow plan.

        Ingress Forecast / Planned Power should show the accepted ``U*``
        hold and the planner air path ``T_ref``, not the 15-minute P-grid
        re-rollout. Display power uses the outdoor sample at the start of
        each slow interval so COP does not invent 15-minute watt steps.
        """
        with self._nmpc_lock:
            U_fast = None if self._nmpc_U_fast is None else self._nmpc_U_fast.copy()
            T_ref = None if self._nmpc_T_ref is None else self._nmpc_T_ref.copy()
            m = int(self._timing.m)
        if U_fast is None:
            return False
        outdoor_seq = list(getattr(self, "_outdoor_forecast", []) or [])
        N = int(self._horizon)
        if U_fast.shape[0] < N:
            return False
        if len(outdoor_seq) < N:
            if not outdoor_seq:
                return False
            outdoor_seq = outdoor_seq + [float(outdoor_seq[-1])] * (N - len(outdoor_seq))
        m = max(m, 1)
        self._heating_schedule = []
        for k in range(N):
            outdoor_k = outdoor_seq[(k // m) * m]
            self._heating_schedule.append(
                self._system.display_heating_powers(U_fast[k], outdoor_k)
            )
        if T_ref is not None and T_ref.shape[0] > 0:
            room_list = self._system._room_list
            n_rooms = min(int(T_ref.shape[1]), len(room_list))
            self._predictions = [
                {
                    name: float(T_ref[min(k, T_ref.shape[0] - 1), i])
                    for i, name in enumerate(room_list[:n_rooms])
                }
                for k in range(N)
            ]
            self._linearised_predictions = [dict(step) for step in self._predictions]
        return True

    def _comfort_bounds_fast(
        self,
        control_trajectory: Optional[Any],
        n_fast: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        room_list = self._system._room_list
        n_rooms = self._system._n_rooms
        t_min = np.zeros((n_fast, n_rooms), dtype=float)
        t_max = np.zeros((n_fast, n_rooms), dtype=float)
        for i, name in enumerate(room_list):
            room = self._system._model.rooms[name]
            sp = float(room.setpoint)
            off = float(getattr(room, "comfort_offset", 2.0) or 2.0)
            t_min[:, i] = sp - off
            t_max[:, i] = sp + off
        if control_trajectory is not None:
            for i, name in enumerate(room_list):
                sps = np.asarray(control_trajectory.setpoints[name], dtype=float)
                offs = np.asarray(control_trajectory.comfort_offsets[name], dtype=float)
                n = min(n_fast, sps.shape[0], offs.shape[0])
                t_min[:n, i] = sps[:n] - offs[:n]
                t_max[:n, i] = sps[:n] + offs[:n]
                if n < n_fast:
                    t_min[n:, i] = t_min[n - 1, i]
                    t_max[n:, i] = t_max[n - 1, i]
        return t_min, t_max

    def _slow_input_bounds(
        self,
        u_min_seq: Optional[np.ndarray],
        u_max_seq: Optional[np.ndarray],
        clamp_mask: Optional[np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray]:
        u_min_abs, u_max_abs = self._control_system.u_bounds
        n_slow = self._timing.n_slow
        m = self._timing.m
        lo = np.tile(np.asarray(u_min_abs, dtype=float).reshape(1, -1), (n_slow, 1))
        hi = np.tile(np.asarray(u_max_abs, dtype=float).reshape(1, -1), (n_slow, 1))
        if u_min_seq is None or u_max_seq is None or clamp_mask is None:
            return lo, hi
        for n in range(n_slow):
            start = n * m
            stop = min(start + m, clamp_mask.shape[0])
            for j in range(self._system.nu):
                col = clamp_mask[start:stop, j]
                if col.any():
                    idx = start + int(np.argmax(col))
                    lo[n, j] = float(u_min_seq[idx, j])
                    hi[n, j] = float(u_max_seq[idx, j])
        return lo, hi

    def solve_nmpc(
        self,
        outdoor_temp: float,
        now: Optional[datetime] = None,
        *,
        outdoor_forecast: Optional[List[float]] = None,
        cloud_forecast: Optional[List[float]] = None,
        cloud_cover_now: Optional[float] = None,
        ghi_forecast: Optional[List[Optional[float]]] = None,
        ghi_now: Optional[float] = None,
        price_forecast: Optional[List[float]] = None,
        solar_gains: Optional[Dict[str, float]] = None,
        control_trajectory: Optional[Any] = None,
        input_clamps: Optional[Dict[str, np.ndarray]] = None,
        x0: Optional[np.ndarray] = None,
        u_prev: Optional[np.ndarray] = None,
        minimize_fn: Optional[Any] = None,
        maxiter: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Solve the slow mean OCP (blocking).  Does not run the EKF."""
        if now is None:
            now = datetime.now(tz=timezone.utc)
        N = self._horizon
        if outdoor_forecast is not None and len(outdoor_forecast) >= N:
            outdoor_seq = list(outdoor_forecast[:N])
        else:
            outdoor_seq = self._forecast_outdoor(outdoor_temp)
        solar_seq = self._forecast_solar(
            now,
            cloud_forecast=cloud_forecast,
            cloud_cover_now=cloud_cover_now,
            ghi_forecast=ghi_forecast,
            ghi_now=ghi_now,
        )
        if solar_gains is not None:
            solar_seq[0] = dict(solar_gains)
        d_fast = [
            self._control_system.disturbance_vector(outdoor_seq[k], solar_seq[k])
            for k in range(N)
        ]
        t_min, t_max = self._comfort_bounds_fast(control_trajectory, N)
        u_min_seq = u_max_seq = clamp_mask = None
        if input_clamps:
            u_min_abs, u_max_abs = self._control_system.u_bounds
            u_min_seq = np.tile(np.asarray(u_min_abs, dtype=float).reshape(1, -1), (N, 1))
            u_max_seq = np.tile(np.asarray(u_max_abs, dtype=float).reshape(1, -1), (N, 1))
            clamp_mask = np.zeros((N, len(self._sources)), dtype=bool)
            k_sig = self._system._k_sigmoid
            for j, src in enumerate(self._sources):
                arr = input_clamps.get(src.name)
                if arr is None:
                    continue
                arr = np.asarray(arr, dtype=float).reshape(-1)
                lo, hi = float(src.u_min), float(src.u_max)
                for k in range(min(N, arr.shape[0])):
                    v = arr[k]
                    if np.isnan(v):
                        continue
                    u_val = src.control_for_power_fraction(float(v), outdoor_temp, k_sig)
                    u_val = min(max(float(u_val), lo), hi)
                    u_min_seq[k, j] = u_val
                    u_max_seq[k, j] = u_val
                    clamp_mask[k, j] = True
        slow_lo, slow_hi = self._slow_input_bounds(u_min_seq, u_max_seq, clamp_mask)
        price_slow = None
        if price_forecast is not None and self._energy_price_weight > 0.0:
            price_slow = mean_price_slow(
                np.asarray(price_forecast, dtype=float),
                self._timing.m,
                self._timing.n_slow,
            )
        x_hat = self._ekf.x_hat.copy() if x0 is None else np.asarray(x0, dtype=float)
        u0_prev = self._u_prev.copy() if u_prev is None else np.asarray(u_prev, dtype=float)
        ocp = MeanOcp(
            self._control_system,
            self._sources,
            self._timing,
            x_hat,
            u0_prev,
            d_fast,
            t_min=t_min,
            t_max=t_max,
            rho=self._rho,
            s_rom=self._smoothing_weight,
            energy_price_weight=self._energy_price_weight,
            price_slow=price_slow,
            u_min=slow_lo,
            u_max=slow_hi,
            n_int_steps=self._n_int_steps,
        )
        warm = self._nmpc_warm
        if warm is None or np.asarray(warm).size != ocp.n * ocp.nu:
            warm = np.zeros(ocp.n * ocp.nu, dtype=float)
        _t0 = time.perf_counter()
        raw = solve_mean_ocp(
            ocp,
            warm,
            maxiter=NMPC_MAXITER if maxiter is None else int(maxiter),
            timeout_s=NMPC_TIMEOUT_S if timeout_s is None else float(timeout_s),
            minimize_fn=minimize_fn,
        )
        cost_zero = evaluate_zero_heat_cost(ocp)
        plan = plan_from_solve(ocp, raw, cost_zero)
        plan["elapsed_s"] = time.perf_counter() - _t0
        self._total_computes += 1
        return plan

    def _p_command_vector(
        self,
        clamp_mask: Optional[np.ndarray],
        u_min_seq: Optional[np.ndarray],
        u_max_seq: Optional[np.ndarray],
    ) -> np.ndarray:
        n_u = self._system.nu
        room_list = self._system._room_list
        room_index = {name: i for i, name in enumerate(room_list)}
        x_hat = self._ekf.x_hat
        u_abs = np.zeros(n_u, dtype=float)
        watchdog = self._watchdog_tripped
        with self._nmpc_lock:
            U = self._nmpc_U
            T_ref = self._nmpc_T_ref
            k = self._nmpc_k
            n_fast = self._timing.n_fast
            m = self._timing.m
            n_slow = self._timing.n_slow
            if not watchdog and U is not None and T_ref is not None:
                idx = min(max(k, 0), n_fast - 1)
                n = min(idx // m, n_slow - 1)
                u_ref = U[n]
                t_ref_row = T_ref[idx]
                for j, src in enumerate(self._sources):
                    ri = room_index.get(src.room, 0)
                    kp = float(getattr(src, "p_gain", 0.1))
                    t_hat = float(x_hat[ri])
                    u_abs[j] = p_command(
                        float(u_ref[j]),
                        float(t_ref_row[ri]),
                        t_hat,
                        kp,
                        float(src.u_min),
                        float(src.u_max),
                    )
            elif not watchdog:
                rooms = self._system._model.rooms
                for j, src in enumerate(self._sources):
                    ri = room_index.get(src.room, 0)
                    room = rooms.get(src.room)
                    if room is None:
                        continue
                    kp = float(getattr(src, "p_gain", 0.1))
                    u_abs[j] = comfort_fallback_command(
                        float(x_hat[ri]),
                        float(room.setpoint),
                        float(getattr(room, "comfort_offset", 2.0) or 2.0),
                        kp,
                        float(src.u_min),
                        float(src.u_max),
                    )
            self._nmpc_k = k + 1
        if u_min_seq is not None and u_max_seq is not None and clamp_mask is not None:
            for j, src in enumerate(self._sources):
                if clamp_mask[0, j]:
                    u_abs[j] = float(u_min_seq[0, j])
        return u_abs

    def _forecast_U(self, n_fast: int) -> np.ndarray:
        n_u = self._system.nu
        with self._nmpc_lock:
            U_fast = self._nmpc_U_fast
            if U_fast is None:
                return np.zeros((n_fast, n_u), dtype=float)
            if U_fast.shape[0] >= n_fast:
                return U_fast[:n_fast].copy()
            pad = np.tile(U_fast[-1:], (n_fast - U_fast.shape[0], 1))
            return np.vstack([U_fast, pad])

    # ── Main entry point ─────────────────────────────────────────────────

    def compute(
        self,
        outdoor_temp: float,
        solar_gains: Optional[Dict[str, float]] = None,
        now: Optional[datetime] = None,
        outdoor_forecast: Optional[List[float]] = None,
        cloud_forecast: Optional[List[float]] = None,
        cloud_cover_now: Optional[float] = None,
        ghi_forecast: Optional[List[Optional[float]]] = None,
        ghi_now: Optional[float] = None,
        wind_forecast: Optional[List[float]] = None,
        disabled_sources: Optional[Set[str]] = None,
        control_trajectory: Optional[Any] = None,
        price_forecast: Optional[List[float]] = None,
        input_clamps: Optional[Dict[str, "np.ndarray"]] = None,
        run_optimization: bool = True,
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
        ghi_forecast : list of float, optional
            Forecast Global Horizontal Irradiance [W/m²] per horizon step (from
            a solar-forecast sensor).  When present for a step it drives the
            intensity (decomposed geometrically) and takes precedence over the
            cloud-cover attenuation; ``None`` entries fall back per-step.
        ghi_now : float, optional
            Current GHI [W/m²].  Used for the k=0 entry / current gains when
            ``solar_gains`` was not pre-computed.
        wind_forecast : list of float, optional
            Wind speed [m/s] per horizon step.  The QP linearisation uses
            the horizon mean (the wind enters through the conductance, not
            the disturbance vector); the nonlinear prediction rollout
            applies the per-step values.  ``None`` keeps the current wind
            for the whole horizon.
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
        input_clamps : dict, optional
            ``{source_name: ndarray (N,)}`` of signed *power* fractions
            (``+`` heat / ``-`` cool, as a fraction of capacity) the source must
            deliver at each horizon step (``NaN`` = unclamped at that step).  Each
            value is converted to the control input that delivers it (via
            :meth:`HeatSource.control_for_power_fraction`, inverting the heat
            pump's smooth sigmoid so the step is linear in delivered power), then
            the QP's input box bounds for that source are pinned to it over the
            horizon.  The MPC plans the rest of the house around the prescribed
            signal and the planned trajectory (and thus the actuator forecast
            plot) already reflects it.  Used to drive identification experiments.
        run_optimization : bool
            When ``True`` (default) the full MPC optimisation runs.  When
            ``False`` — i.e. the system is stopped — only the CD-EKF state
            estimation runs (so filtered temperatures and innovation logging
            stay live); the QP solve and prediction rollout are skipped and
            the forecast / heating-schedule fields are cleared.

        Returns
        -------
        dict
            {source_name: setpoint_fraction} where fraction is in [-1, 1]
            for cooling-capable sources and [0, 1] for heating-only.
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)
        if solar_gains is None:
            solar_gains = self._current_solar(
                now, cloud_cover=cloud_cover_now, ghi=ghi_now
            )

        N = self._horizon
        p = np.array([], dtype=float)  # no estimated parameters

        # ── Disturbance forecast for visualisation ───────────────────────
        if outdoor_forecast is not None and len(outdoor_forecast) >= N:
            outdoor_seq = list(outdoor_forecast[:N])
        else:
            outdoor_seq = self._forecast_outdoor(outdoor_temp)
        solar_seq = self._forecast_solar(
            now,
            cloud_forecast=cloud_forecast,
            cloud_cover_now=cloud_cover_now,
            ghi_forecast=ghi_forecast,
            ghi_now=ghi_now,
        )

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

        # ── Stopped: run the EKF only, skip the MPC optimisation ──────────
        # State estimation and logging continue (the coordinator still records
        # the observation in its history buffer), but no optimal trajectory is
        # produced.  Clearing the prediction / schedule fields makes the
        # dashboard render a visible gap instead of a stale forecast while the
        # system is stopped.
        if not run_optimization:
            self._mpc.estimate_only(y, d, p, 0.0)
            self._last_innovation = self._ekf.last_innovation
            self._predictions = []
            self._linearised_predictions = []
            self._heating_schedule = []
            # Report each source's current commanded fraction; the coordinator
            # overwrites these with the actually-delivered values anyway.
            actions = {
                src.name: float(np.clip(self._mpc._u_prev[j], src.u_min, src.u_max))
                for j, src in enumerate(self._sources)
            }
            self._mpc_actions = dict(actions)
            # Update current_power from emitter state so heating_power_measured
            # reflects actual thermal delivery while the system is stopped.
            _nx_phys = self._system._nx_phys
            _x_hat = self._ekf.x_hat
            _filter_idx = self._system._filter_idx_for_source
            for j, src in enumerate(self._sources):
                k = int(_filter_idx[j])
                if k < 0:
                    continue
                eff_frac = float(np.clip(_x_hat[_nx_phys + k], src.u_min, src.u_max))
                if src.can_cool:
                    src._current_power = src.display_smooth_thermal_power(
                        eff_frac, outdoor_temp, self._system._k_sigmoid,
                    )
                else:
                    src.set_power(eff_frac, outdoor_temp)
            return actions

        # Horizon-mean wind for the infiltration overlay on the forecast rollout.
        wind_seq: Optional[List[float]] = None
        if wind_forecast:
            wind_seq = [
                float(wind_forecast[k]) if k < len(wind_forecast)
                else float(wind_forecast[-1])
                for k in range(N)
            ]
            finite = [w for w in wind_seq if np.isfinite(w)]
            if finite:
                self._control_system.set_wind_speed(float(np.mean(finite)))

        # ── Per-step input clamps (e.g. identification experiment) ───────────
        u_min_seq: Optional[np.ndarray] = None
        u_max_seq: Optional[np.ndarray] = None
        clamp_mask: Optional[np.ndarray] = None
        if input_clamps:
            u_min_abs, u_max_abs = self._control_system.u_bounds
            u_min_seq = np.tile(np.asarray(u_min_abs, dtype=float).reshape(1, -1), (N, 1))
            u_max_seq = np.tile(np.asarray(u_max_abs, dtype=float).reshape(1, -1), (N, 1))
            clamp_mask = np.zeros((N, len(self._sources)), dtype=bool)
            k_sig = self._system._k_sigmoid
            for j, src in enumerate(self._sources):
                arr = input_clamps.get(src.name)
                if arr is None:
                    continue
                arr = np.asarray(arr, dtype=float).reshape(-1)
                lo, hi = float(src.u_min), float(src.u_max)
                for k in range(min(N, arr.shape[0])):
                    v = arr[k]
                    if np.isnan(v):
                        continue
                    u_val = src.control_for_power_fraction(float(v), outdoor_temp, k_sig)
                    u_val = min(max(float(u_val), lo), hi)
                    u_min_seq[k, j] = u_val
                    u_max_seq[k, j] = u_val
                    clamp_mask[k, j] = True
            if not clamp_mask.any():
                u_min_seq = u_max_seq = clamp_mask = None

        # ── EKF then P (no linearised QP on the happy path) ──────────────
        self._mpc.estimate_only(y, d, p, 0.0)
        self._last_innovation = self._ekf.last_innovation
        self._total_computes += 1

        u_abs = self._p_command_vector(clamp_mask, u_min_seq, u_max_seq)
        self._mpc_actions = {
            src.name: float(np.clip(u_abs[j], src.u_min, src.u_max))
            for j, src in enumerate(self._sources)
        }
        if disabled_sources:
            for j, src in enumerate(self._sources):
                if src.name not in disabled_sources:
                    continue
                if clamp_mask is not None and clamp_mask[0, j]:
                    continue
                u_abs[j] = 0.0

        U_abs = self._forecast_U(N)
        if disabled_sources:
            for j, src in enumerate(self._sources):
                if src.name not in disabled_sources:
                    continue
                if clamp_mask is None:
                    U_abs[:, j] = 0.0
                else:
                    col = clamp_mask[:, j]
                    U_abs[~col, j] = 0.0
        if clamp_mask is not None and u_min_seq is not None:
            U_abs[clamp_mask] = u_min_seq[clamp_mask]

        _nx_phys = self._system._nx_phys
        _x_hat = self._ekf.x_hat
        _filter_idx = self._system._filter_idx_for_source

        actions: Dict[str, float] = {}
        for j, src in enumerate(self._sources):
            frac = float(np.clip(u_abs[j], src.u_min, src.u_max))
            actions[src.name] = frac

            k = int(_filter_idx[j])
            eff_frac = (
                float(np.clip(_x_hat[_nx_phys + k], src.u_min, src.u_max))
                if k >= 0
                else frac
            )

            if src.can_cool:
                src._current_power = src.display_smooth_thermal_power(
                    eff_frac, outdoor_temp, self._system._k_sigmoid,
                )
            else:
                src.set_power(eff_frac, outdoor_temp)

        self._u_prev = u_abs.copy()
        self._mpc._u_prev = u_abs.copy()

        # Ingress plots the installed slow plan.  The 15-minute tick still
        # runs EKF then P for actuators; it must not replace Forecast /
        # Planned Power with a fast-grid re-rollout.  Display power uses
        # the (possibly disabled-source-masked) U_abs with one outdoor
        # sample per slow interval so COP does not invent 15-minute watt
        # steps.
        if self.rebuild_forecast_from_plan():
            m = max(int(self._timing.m), 1)
            self._heating_schedule = [
                self._system.display_heating_powers(
                    U_abs[k], outdoor_seq[(k // m) * m]
                )
                for k in range(N)
            ]
        else:
            self._predictions = self._compute_nonlinear_predictions(
                U_abs, outdoor_seq, solar_seq, room_list, n_rooms,
                wind_seq=wind_seq,
            )
            self._linearised_predictions = [dict(step) for step in self._predictions]
            self._heating_schedule = [
                self._system.display_heating_powers(U_abs[k], outdoor_seq[k])
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

    def _room_gain(
        self,
        name: str,
        t: datetime,
        cloud_cover: Optional[float],
        ghi: Optional[float],
    ) -> float:
        """Solar gain [W] for one room, driven by forecast GHI (preferred) or cloud.

        Uses the detailed per-window geometry when the room has any windows
        (primary, higher-fidelity path).  Falls back to the room's single
        solar-exposure aperture when no windows are configured, so a room can
        opt out of per-window entry without losing solar gain entirely.
        """
        room = self._system._model.rooms[name]
        if room.windows:
            return room_solar_gains(
                room.windows,
                t,
                self._latitude,
                self._longitude,
                cloud_cover=cloud_cover,
                ghi=ghi,
                albedo=self._albedo,
            )
        return room_solar_gains_from_exposure(
            room.solar_exposure_aperture,
            room.solar_facing,
            t,
            self._latitude,
            self._longitude,
            cloud_cover=cloud_cover,
            ghi=ghi,
            albedo=self._albedo,
        )

    def _forecast_solar(
        self,
        now: datetime,
        cloud_forecast: Optional[List[float]] = None,
        cloud_cover_now: Optional[float] = None,
        ghi_forecast: Optional[List[Optional[float]]] = None,
        ghi_now: Optional[float] = None,
    ) -> List[Dict[str, float]]:
        """Solar gain forecast using the geometric solar model.

        Returns N+1 entries where solar_seq[k] = solar gains computed with
        sun position at now + k * dt, for k = 0, ..., N.

        * k = 0 uses current measured GHI/cloud (ghi_now, cloud_cover_now).
        * k >= 1 uses ghi_forecast[k-1] / cloud_forecast[k-1], which are
          interpolated at now + k*dt, matching the sun position time.
        * k = N is one step beyond the OCP horizon for visualisation.

        Intensity per step follows a precedence: forecast GHI [W/m²] (decomposed
        geometrically) when available, else the clear-sky model attenuated by the
        Kasten-Czeplak cloud factor, else clear sky.  GHI steps outside the
        forecast's coverage fall back to the cloud/clear path; cloud cover beyond
        its forecast holds the last value (persistence).
        """
        schedules = []
        for k in range(self._horizon + 1):  # N+1 entries: k = 0 ... N
            t = now + timedelta(seconds=self._dt * k)
            if k == 0:
                g = ghi_now
                cc = cloud_cover_now
            else:
                g = select_ghi_for_step(ghi_forecast, k - 1, fallback=ghi_now)
                cc = _select_cloud_for_step(cloud_forecast, k - 1, fallback=cloud_cover_now)
            schedules.append({
                name: self._room_gain(name, t, cc, g)
                for name in self._system._room_list
            })
        return schedules

    def _current_solar(
        self,
        now: datetime,
        cloud_cover: Optional[float] = None,
        ghi: Optional[float] = None,
    ) -> Dict[str, float]:
        """Current-step solar gains for all rooms."""
        return {
            name: self._room_gain(name, now, cloud_cover, ghi)
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
        wind_seq: Optional[List[float]] = None,
    ) -> List[Dict[str, float]]:
        """Compute nonlinear model predictions using the optimal control sequence.

        Simulates the nonlinear thermal model forward over the horizon using the
        optimal control inputs from the MPC and the forecasted disturbances
        (outdoor temperature, solar gains, and — when available — per-step
        wind speed).  The online internal-gain deviation decays with its OU
        rate κ over the rollout, matching the decrement the QP saw in its
        disturbance forecast.

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
        wind_seq : list of float, optional
            Wind speed [m/s] per horizon step for the infiltration overlay.

        Returns
        -------
        list of dict
            Predicted temperatures {room_name: temp_°C} for each horizon step.
        """
        predictions = []
        x_curr = self._ekf.x_hat.copy()
        p = np.array([], dtype=float)

        wind_restore = self._system._wind_speed

        N = len(outdoor_seq)
        try:
            for k in range(N):
                u_k = U_abs[k] if k < len(U_abs) else U_abs[-1]
                outdoor_temp = outdoor_seq[k]
                solar_gains = solar_seq[k] if k < len(solar_seq) else solar_seq[-1]
                if wind_seq is not None and k < len(wind_seq):
                    self._system.set_wind_speed(wind_seq[k])

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
        finally:
            self._system.set_wind_speed(wind_restore)

        return predictions
