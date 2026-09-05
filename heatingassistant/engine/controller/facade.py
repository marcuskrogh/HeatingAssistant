"""
Application facade for house-heating NMPC + P tracking.

HeatingMPCController builds HouseThermalSDE + _InnovationEKF (CD-EKF) +
a two-rate NMPC + P tracker. The linearised QP is kept only as EKF glue
and is not solved on the happy path.

Public API:
    controller = HeatingMPCController(model, heat_sources, ...)
    actions    = controller.compute(outdoor_temp, solar_gains, now)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from mbc.estimation import (
    ContinuousDiscreteEKFParams,
    IntegrationScheme,
)

from ..const import (
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_P_DEADBAND,
    DEFAULT_SETPOINT_PULL_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_U_REF_GATE,
    MPC_STATS_BUFFER_SIZE,
    NMPC_WATCHDOG_S,
    SOLAR_GAIN_SMOOTHING_TAU_S,
)
from ..heat_sources import HeatSource
from ..nmpc_ocp import (
    NMPC_IDLE_U_ABS,
    NMPC_MAXITER,
    NMPC_TIMEOUT_S,
    MeanOcp,
    evaluate_zero_heat_cost,
    mean_price_slow,
    plan_from_solve,
    roll_fast_air_path,
    shift_slow_plan,
    solve_mean_ocp,
)
from ..nmpc_p import comfort_fallback_command, p_command, require_non_negative_p_gating
from ..nmpc_timing import (
    NmpcTiming,
    derive_nmpc_timing,
    grid_slot_index,
    timing_from_dt_horizon,
)
from ..solar_forecast import select_cloud_for_step, select_ghi_for_step
from ..solar_model import (
    room_solar_gains,
    room_solar_gains_from_exposure,
    smooth_solar_gain_schedule,
)
from ..thermal_model import HouseModel
from .ekf import _InnovationEKF
from .linearised import HeatingLinearisedMPC
from .sde import HouseThermalSDE


def _diag_np(n: int, v: float) -> np.ndarray:
    """Return an n×n diagonal numpy matrix with v on the diagonal."""
    return np.eye(n) * v


def _pad_plan_tail(
    arr: Optional[np.ndarray],
    start: int,
    n_fast: int,
    n_cols: int,
) -> Optional[np.ndarray]:
    """Remaining plan rows from ``start``, padded to ``n_fast`` with the last row."""
    if arr is None:
        return None
    M = np.asarray(arr, dtype=float)
    if M.ndim == 1:
        M = M.reshape(-1, max(int(n_cols), 1))
    if M.shape[0] == 0:
        return None
    i = min(max(int(start), 0), M.shape[0])
    if i >= M.shape[0]:
        return np.tile(M[-1:], (n_fast, 1))
    tail = M[i:]
    if tail.shape[0] >= n_fast:
        return tail[:n_fast].copy()
    pad = np.tile(tail[-1:], (n_fast - tail.shape[0], 1))
    return np.vstack([tail, pad])


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
         last accepted plan, or ``u = 0`` while NMPC is near zero and air
         is inside the P temperature deadband.  With no plan, P toward
         the setpoint while air is outside the comfort band (watchdog
         still forces ``u = 0``).
         ``T_ref`` is the OCP air trajectory on the fast grid (``m`` samples
         per slow ``U*`` hold from integrator substeps). It is not a
         two-hour constant. ``u_ref`` is the zero-order hold of ``U*``.
         That accept-time path stays for the whole slow interval: new
         disturbances move ``T_hat``, not the reference. Room-view
         Forecast is leftover ``T_ref``.
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
    p_deadband        : temperature deadband [K] around T_ref while NMPC is off.
    u_ref_gate        : |u_ref| below this (heater fraction) is NMPC-off.
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
        p_deadband: float = DEFAULT_P_DEADBAND,
        u_ref_gate: float = DEFAULT_U_REF_GATE,
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
        require_non_negative_p_gating(p_deadband, u_ref_gate)
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
        self._solar_gain_filt: Dict[str, float] = {}
        self._solar_gain_tau_s: float = float(SOLAR_GAIN_SMOOTHING_TAU_S)
        self._wind_forecast: List[float] = []
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
        self._nmpc_plan_epoch: Optional[float] = None
        self._nmpc_warm: Optional[np.ndarray] = None
        self._rho = float(rho)
        self._smoothing_weight = float(smoothing_weight)
        self._p_deadband = float(p_deadband)
        self._u_ref_gate = float(u_ref_gate)
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
        """True when a slow solve should start (no path or idle zeros).

        The two-hour cadence is a wall-clock grid on the runtime, not
        ``_nmpc_k >= M``. Fast-step count only indexes the installed plan.
        """
        if self._nmpc_busy:
            return False
        with self._nmpc_lock:
            if self._nmpc_U is None:
                return True
            if float(np.max(np.abs(self._nmpc_U))) < NMPC_IDLE_U_ABS:
                return True
            return False

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
        plan_epoch: Optional[float] = None,
    ) -> None:
        """Install a slow plan for the fast P-law (tests and worker).

        Copies ``U*`` and the OCP air trajectory ``T_ref`` so later solver
        arrays cannot retarget P or room-view Forecast during the slow
        interval. ``T_ref`` stays the fast-grid solution path (not a
        two-hour constant).
        """
        U = np.asarray(u_star, dtype=float).reshape(self._timing.n_slow, self._system.nu).copy()
        T = np.asarray(t_ref, dtype=float)
        n_fast = self._timing.n_fast
        n_rooms = self._system._n_rooms
        stamp = time.time() if now is None else float(now)
        if T.ndim == 1:
            T = np.tile(T.reshape(1, -1), (n_fast, 1))
        if T.shape[0] < n_fast:
            pad = np.tile(T[-1:], (n_fast - T.shape[0], 1))
            T = np.vstack([T, pad])
        T = np.array(T[:n_fast, :n_rooms], dtype=float, copy=True)
        with self._nmpc_lock:
            self._nmpc_U = U
            self._nmpc_T_ref = T
            self._nmpc_U_fast = np.repeat(U, self._timing.m, axis=0)
            if plan_epoch is None:
                self._nmpc_plan_epoch = None
                self._nmpc_k = 0
            else:
                origin = float(plan_epoch)
                self._nmpc_plan_epoch = origin
                self._nmpc_k = self._fast_index_for(origin, stamp)
            self._nmpc_warm = shift_slow_plan(U).reshape(-1).copy()
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
                self._nmpc_plan_epoch = None

    def _fast_index_for(self, origin: float, now: float) -> int:
        n_fast = max(int(self._timing.n_fast), 1)
        k = grid_slot_index(float(origin), float(self._timing.dt_s), float(now))
        return min(max(k, 0), n_fast - 1)

    def sync_fast_index(
        self,
        now: float,
        fallback_epoch: Optional[float] = None,
    ) -> None:
        """Point `_nmpc_k` at the wall-clock substep of the installed plan."""

        with self._nmpc_lock:
            origin = self._nmpc_plan_epoch
            if origin is None:
                origin = fallback_epoch
            if origin is None:
                return
            self._nmpc_k = self._fast_index_for(float(origin), float(now))

    def apply_nmpc_result(
        self,
        result: Dict[str, Any],
        *,
        now: Optional[float] = None,
        plan_epoch: Optional[float] = None,
    ) -> bool:
        """Accept or reject a worker result.  Returns True when the path updated."""
        self._nmpc_busy = False
        elapsed = result.get("elapsed_s")
        if elapsed is not None:
            self._solve_times.append(float(elapsed))
        if result.get("accepted"):
            self.set_accepted_path(
                result["u_star"], result["t_ref"], now=now, plan_epoch=plan_epoch,
            )
            self.refresh_p_command()
            self.rebuild_forecast_from_plan()
            return True
        self._record_nmpc_reject(now)
        return False

    def _pad_outdoor_forecast(self, n: int) -> Optional[List[float]]:
        outdoor_seq = list(getattr(self, "_outdoor_forecast", []) or [])
        if not outdoor_seq:
            return None
        if len(outdoor_seq) < n:
            outdoor_seq = outdoor_seq + [float(outdoor_seq[-1])] * (n - len(outdoor_seq))
        return [float(v) for v in outdoor_seq[:n]]

    def _pad_solar_forecast(self, n: int) -> Optional[List[Dict[str, float]]]:
        solar_seq = list(getattr(self, "_solar_forecast", []) or [])
        if not solar_seq:
            return None
        if len(solar_seq) < n:
            last = dict(solar_seq[-1])
            solar_seq = solar_seq + [dict(last) for _ in range(n - len(solar_seq))]
        return [dict(step) for step in solar_seq[:n]]

    def _pad_wind_forecast(self, n: int) -> Optional[List[float]]:
        wind_seq = list(getattr(self, "_wind_forecast", []) or [])
        if not wind_seq:
            return None
        if len(wind_seq) < n:
            wind_seq = wind_seq + [float(wind_seq[-1])] * (n - len(wind_seq))
        return [float(v) for v in wind_seq[:n]]

    def _heating_schedule_from_u(
        self,
        U_abs: np.ndarray,
        outdoor_seq: List[float],
    ) -> List[Dict[str, float]]:
        """Convert remaining U* to watts with one outdoor sample per 2 h hold."""
        m = max(int(self._timing.m), 1)
        with self._nmpc_lock:
            k0 = int(self._nmpc_k)
        n = min(int(self._horizon), len(U_abs), len(outdoor_seq))
        schedule: List[Dict[str, float]] = []
        for i in range(n):
            j = i - ((k0 + i) % m)
            if j < 0:
                j = 0
            schedule.append(
                self._system.display_heating_powers(U_abs[i], outdoor_seq[j])
            )
        return schedule

    def _predictions_from_air(
        self,
        air: np.ndarray,
        room_list: List[str],
        n_rooms: int,
    ) -> List[Dict[str, float]]:
        """Map an air path ``(n, n_rooms)`` to per-step room temperature dicts."""
        T = np.asarray(air, dtype=float)
        if T.ndim == 1:
            T = T.reshape(-1, max(n_rooms, 1))
        n = int(T.shape[0])
        cols = min(int(T.shape[1]), n_rooms)
        return [
            {
                name: float(T[k, i]) if i < cols else 0.0
                for i, name in enumerate(room_list[:n_rooms])
            }
            for k in range(n)
        ]

    def _publish_plan_rollout(
        self,
        U_abs: np.ndarray,
        outdoor_seq: List[float],
        solar_seq: List[Dict[str, float]],
        wind_seq: Optional[List[float]] = None,
    ) -> None:
        """Publish leftover U* (Planned Power) and accept-time T_ref (Forecast).

        Temperature is the remaining NMPC air trajectory on the fast grid.
        That path already has OCP substep fidelity under two-hour ``U*``
        holds — do not collapse it to a constant, and do not retarget it
        when outdoor/solar/wind update. P tracks the same series.
        Planned Power is leftover ``U*`` with one outdoor sample per 2 h
        hold. With no accepted path, Forecast falls back to an open-loop
        roll of ``U_abs``.
        """
        self._heating_schedule = self._heating_schedule_from_u(U_abs, outdoor_seq)
        room_list = self._system._room_list
        n_rooms = self._system._n_rooms
        n = max(int(self._horizon), 1)
        air = self._forecast_T(n)
        if air is not None:
            self._predictions = self._predictions_from_air(air, room_list, n_rooms)
        else:
            self._predictions = self._compute_nonlinear_predictions(
                U_abs, outdoor_seq, solar_seq, room_list, n_rooms, wind_seq=wind_seq,
            )
        self._linearised_predictions = [dict(step) for step in self._predictions]

    def rebuild_forecast_from_plan(self) -> bool:
        """Publish remaining accepted U* and T_ref from the current plan index.

        Forecast temperature is the leftover accept-time air trajectory
        (fast-grid OCP path, not a two-hour constant). Do not re-roll leftover
        two-hour holds when outdoor/solar/wind forecasts change. Display
        power uses leftover ``U*`` with one outdoor sample per hold so COP
        does not invent 15-minute watt steps. Do not replay U* from slow
        index 0 against a later state.

        Does not write ``_nmpc_T_ref`` / ``_nmpc_U``.
        """
        with self._nmpc_lock:
            has_plan = self._nmpc_U_fast is not None
        if not has_plan:
            return False
        n = int(self._horizon)
        outdoor_seq = self._pad_outdoor_forecast(n)
        if outdoor_seq is None:
            return False
        U_abs = self._forecast_U(n)
        solar_seq = self._pad_solar_forecast(n) or []
        self._publish_plan_rollout(
            U_abs, outdoor_seq, solar_seq, wind_seq=self._pad_wind_forecast(n),
        )
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
            persist=True,
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
                # Accept-time OCP air trajectory on the fast grid (same T_ref as Forecast).
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
                        u_ref_gate=self._u_ref_gate,
                        p_deadband=self._p_deadband,
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
        if u_min_seq is not None and u_max_seq is not None and clamp_mask is not None:
            for j, src in enumerate(self._sources):
                if clamp_mask[0, j]:
                    u_abs[j] = float(u_min_seq[0, j])
        return u_abs

    def refresh_p_command(self) -> Dict[str, float]:
        """Apply P with the installed ``u_ref`` without advancing the EKF.

        Used when a slow plan is accepted mid-interval so the feedforward
        bias takes effect immediately. The next ``compute()`` still runs the
        EKF over elapsed ``T_s``.
        """

        u_abs = self._p_command_vector(None, None, None)
        outdoor = 0.0
        seq = getattr(self, "_outdoor_forecast", None) or []
        if seq:
            try:
                outdoor = float(seq[0])
            except (TypeError, ValueError):
                outdoor = 0.0
        actions: Dict[str, float] = {}
        for j, src in enumerate(self._sources):
            frac = float(np.clip(u_abs[j], src.u_min, src.u_max))
            actions[src.name] = frac
            if src.can_cool:
                src._current_power = src.display_smooth_thermal_power(
                    frac, outdoor, self._system._k_sigmoid,
                )
            else:
                src.set_power(frac, outdoor)
        self._u_prev = u_abs.copy()
        self._mpc._u_prev = u_abs.copy()
        return actions

    def _forecast_U(self, n_fast: int) -> np.ndarray:
        """Remaining accepted U* from the current plan index, padded to n_fast."""
        n_u = self._system.nu
        with self._nmpc_lock:
            remaining = _pad_plan_tail(
                self._nmpc_U_fast, int(self._nmpc_k), n_fast, n_u
            )
        if remaining is None:
            return np.zeros((n_fast, n_u), dtype=float)
        return remaining

    def _forecast_T(self, n_fast: int) -> Optional[np.ndarray]:
        """Remaining accept-time T_ref from the current plan index, padded.

        Returns None when no air path is installed so callers can fall back.
        """
        n_rooms = self._system._n_rooms
        with self._nmpc_lock:
            return _pad_plan_tail(
                self._nmpc_T_ref, int(self._nmpc_k), n_fast, n_rooms
            )

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
            persist=True,
        )
        if solar_gains is None:
            # Same k=0 cloud/GHI path as the plotted history/NOW sample.
            solar_gains = dict(solar_seq[0])
        else:
            solar_seq[0] = dict(solar_gains)

        # Store forecasts for visualisation
        self._outdoor_forecast = list(outdoor_seq)
        self._solar_forecast = [dict(s) for s in solar_seq]
        self._price_forecast = list(price_forecast) if price_forecast is not None else []
        self._wind_forecast = []

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
            self._wind_forecast = list(wind_seq)

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

        with self._nmpc_lock:
            plan_identity = self._nmpc_U_fast
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

        self._publish_plan_rollout(
            U_abs, outdoor_seq, solar_seq, wind_seq=wind_seq,
        )
        # The NLP worker may install a path while this tick rolled out U=0.
        # Do not refresh when zeros come from disabled_sources — that would
        # republish the unmasked remaining plan as Planned Power.
        disabled = bool(disabled_sources)
        if float(np.max(np.abs(U_abs))) < 1e-9 and not disabled:
            self.rebuild_forecast_from_plan()
        plan_replaced = False
        with self._nmpc_lock:
            if self._nmpc_U_fast is plan_identity:
                self._nmpc_k += 1
            else:
                plan_replaced = True
        if plan_replaced and not disabled:
            self.rebuild_forecast_from_plan()

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
        persist: bool = False,
    ) -> List[Dict[str, float]]:
        """Solar gain forecast using the geometric solar model.

        Returns N+1 entries where solar_seq[k] = solar gains computed with
        sun position at now + k * dt, for k = 0, ..., N.

        * k = 0 uses current measured GHI when present, and current cloud
          cover.  When ``cloud_cover_now`` is missing, k = 0 still takes the
          first cloud-forecast step so history/NOW is not an unattenuated
          clear-sky spike beside a cloudy horizon.
        * k >= 1 uses ghi_forecast[k-1] / cloud_forecast[k-1], which are
          interpolated at now + k*dt, matching the sun position time.
        * k = N is one step beyond the OCP horizon for visualisation.

        Intensity per step follows a precedence: forecast GHI [W/m²] (decomposed
        geometrically) when available, else the clear-sky model attenuated by the
        Kasten-Czeplak cloud factor, else clear sky.  GHI steps outside the
        forecast's coverage fall back to the cloud/clear path; cloud cover beyond
        its forecast holds the last value (persistence).

        After intensity, each room's watts pass a first-order EMA so cloud
        and GHI steps do not jump in one sample.  Isolated calls
        (``persist=False``) seed k = 0 from the instantaneous value and do
        not update live filter state.  Live ``compute`` / ``solve_nmpc``
        persist k = 0 so the next cycle continues from NOW.
        """
        rooms = list(self._system._room_list)
        schedules = []
        for k in range(self._horizon + 1):  # N+1 entries: k = 0 ... N
            t = now + timedelta(seconds=self._dt * k)
            if k == 0:
                g = ghi_now
                cc = cloud_cover_now
                if cc is None:
                    cc = select_cloud_for_step(cloud_forecast, 0)
            else:
                g = select_ghi_for_step(ghi_forecast, k - 1)
                cc = select_cloud_for_step(cloud_forecast, k - 1, fallback=cloud_cover_now)
            schedules.append({
                name: self._room_gain(name, t, cc, g)
                for name in rooms
            })
        prev = self._solar_gain_filt if persist else None
        filtered, k0 = smooth_solar_gain_schedule(
            schedules,
            prev,
            self._dt,
            self._solar_gain_tau_s,
            rooms,
        )
        if persist:
            self._solar_gain_filt = dict(k0)
        return filtered

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
        """Resimulate remaining U* with the OCP plant step.

        Same map as ``MeanOcp``: ``roll_fast_air_path`` / ``step_hold``,
        implicit Euler, ``n_int`` substeps, U and d held on ``_control_system``.
        Outdoor and solar are the current forecasts. Wind is the horizon mean
        (the NLP does not vary wind inside the horizon).
        """
        plant = self._control_system
        N = len(outdoor_seq)
        U = np.asarray(U_abs, dtype=float)
        if U.ndim == 1:
            U = U.reshape(-1, max(int(self._system.nu), 1))
        if U.shape[0] == 0 or N == 0:
            return []
        if U.shape[0] < N:
            U = np.vstack([U, np.tile(U[-1:], (N - U.shape[0], 1))])
        U = U[:N]
        last_solar: Dict[str, float] = {}
        d_fast = []
        for k in range(N):
            if k < len(solar_seq):
                last_solar = solar_seq[k]
            d_fast.append(
                plant.disturbance_vector(float(outdoor_seq[k]), last_solar)
            )
        wind_restore = plant._wind_speed
        try:
            if wind_seq:
                finite = [float(w) for w in wind_seq if np.isfinite(w)]
                if finite:
                    plant.set_wind_speed(float(np.mean(finite)))
            air = roll_fast_air_path(
                plant,
                self._ekf.x_hat,
                U,
                d_fast,
                dt_s=float(self._timing.dt_s),
                n_int=int(self._n_int_steps),
                n_rooms=n_rooms,
            )
        finally:
            plant.set_wind_speed(wind_restore)
        return self._predictions_from_air(air, room_list, n_rooms)
