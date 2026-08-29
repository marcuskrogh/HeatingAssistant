"""House thermal model as a nonlinear continuous-discrete SDE."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
from mbc.models import ContinuousDiscreteSDE

from ..const import (
    AIR_RHO_CP,
    SHERMAN_GRIMSRUD_STACK_COEF,
    SHERMAN_GRIMSRUD_WIND_COEF,
    SOLAR_WALL_FRACTION,
)
from ..heat_sources import HeatSource
from ..thermal_model import HouseModel, _SG_FACTOR_TYPICAL


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

    def _effective_source_fraction(
        self,
        j: int,
        x: np.ndarray,
        u_scaled: np.ndarray,
        heater_scales,
        nx_phys: int,
    ) -> float:
        """Commanded or filtered fraction for source ``j``."""
        k_filter = self._filter_idx_for_source[j]
        if k_filter >= 0:
            scale = heater_scales[j] if heater_scales is not None else 1.0
            return scale * x[nx_phys + k_filter]
        return float(u_scaled[j])

    def _accumulate_source_heat(
        self,
        heat_contrib: np.ndarray,
        j: int,
        eff_u: float,
        outdoor_temp: float,
    ) -> None:
        """Add source ``j`` thermal power onto the air-node heat vector."""
        i = self._src_room_idx[j]
        if self._src_can_cool[j]:
            p_w = self._sources[j].smooth_thermal_power(
                eff_u, outdoor_temp, self._k_sigmoid
            )
            heat_contrib[i] += p_w / self._src_C_cap[j]
        elif self._src_use_linear_gain[j]:
            heat_contrib[i] += self._src_linear_gain_per_C[j] * max(0.0, eff_u)
        else:
            p_w = self._sources[j].thermal_power(max(0.0, eff_u), outdoor_temp)
            heat_contrib[i] += p_w / self._src_C_cap[j]

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
        for j in range(len(self._sources)):
            eff_u = self._effective_source_fraction(
                j, x, u_scaled, heater_scales, nx_phys
            )
            self._accumulate_source_heat(heat_contrib, j, eff_u, outdoor_temp)

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
            k_filter = self._filter_idx_for_source[j]
            if k_filter >= 0:
                # Filtered source: dφ_k/dt = (u_j − φ_k)/τ_k  →  ∂/∂u_j = 1/τ_k
                J[nx_phys + k_filter, j] = self._inv_emitter_taus[k_filter]
            else:
                scale = heater_scale_factors[j] if heater_scale_factors is not None else 1.0
                J[self._src_room_idx[j], j] = self._unfiltered_dfdu(
                    src, j, u[j] * scale, scale, outdoor_temp
                )
        return J

    def _unfiltered_dfdu(self, src, j: int, eff_u: float, scale: float, outdoor_temp: float) -> float:
        """∂f_air/∂u for an unfiltered source at the current effective fraction."""
        i_cap = self._C_cap[self._src_room_idx[j]]
        if not self._src_can_cool[j]:
            return src.thermal_power(1.0, outdoor_temp) * scale / i_cap
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
            return slope * scale / i_cap
        if eff_u >= 0.0:
            return src.thermal_power(1.0, outdoor_temp) * scale / i_cap
        return 0.0

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
