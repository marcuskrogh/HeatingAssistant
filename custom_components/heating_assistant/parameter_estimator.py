"""
Grey-box thermal parameter estimation.

Estimates a *complete* set of grey-box parameters jointly:

    Per room  i: thermal mass C_i [J/K], external resistance R_i,ext [K/W],
                 constant internal heat gain Q_int,i [W]
    Per source s: power-scale α_s (heater-power miscalibration correction)
    Per pair (i,j): inter-room resistance R_ij [K/W] (when identifiable)

The production objective minimises the mean squared error of multi-step
open-loop simulations over short contiguous windows of the history buffer.
For each candidate parameter set the grey-box model is integrated forward
from measured initial conditions **without** Kalman corrections; the squared
temperature error is averaged over rooms and steps.  A deliberately light
Gaussian MAP prior shrinks each parameter toward its configured value (or
toward zero for unconstrained directions) only where the data cannot
constrain it; the prior is kept weak so the configured data window — not the
prior — drives the estimate.

All positive parameters are log-transformed to guarantee positivity and
improve numerical conditioning; the unbounded internal-gain parameters Q_int
are kept in linear space.

Identifiability gates
---------------------
The optimiser only includes a parameter when the data actually identify it:

* α_s     — included only when std(u_s) ≥ _MIN_HEATER_USAGE_STD
* R_ij    — included only when std(T_i − T_j) ≥ _MIN_TEMP_DIFF_STD
* Q_int,i — always included (always identifiable from the steady-state
            energy balance jointly with C, R_ext)

A multi-start IPOPT run is started from physically-anchored points (a coarse
physics-informed least-squares fit and the configured prior); the run with
the lowest simulation MSE is returned.  Analytical gradients of the
open-loop MSE are supplied via a forward-sensitivity pass, giving IPOPT
exact first-order information.  The IPOPT call is routed through mbc's
``IpoptNLPBackend`` — the same backend the MPC controller uses for OCP
solves — so identification and control share a single IPOPT integration.

CD-EKF PED log-likelihood evaluation is retained in
:meth:`KalmanMLEstimator.compute_log_likelihood` and
:meth:`KalmanMLEstimator.compute_loglik_slice` for diagnostics and
visualisation, but is no longer the production optimisation objective.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .controller import HouseThermalSDE as HouseThermalSystem
from .estimation.constants import (
    MIN_HISTORY_STEPS,
    _ALPHA_PRIOR_WEIGHT,
    _ALPHA_PRIOR_WEIGHT_EXCITED,
    _C_AIR_HI,
    _C_AIR_LO,
    _EMPTY_IDX,
    _LOG_ALPHA_HI,
    _LOG_ALPHA_LO,
    _LOG_MASS_HI,
    _LOG_MASS_LO,
    _LOG_R_HI,
    _LOG_R_IJ_HI,
    _LOG_R_IJ_LO,
    _LOG_R_LO,
    _LOG_SOLAR_HI,
    _LOG_SOLAR_LO,
    _MIN_HISTORY_TIME_S,
    _MIN_SEGMENT_TIME_S,
    _Q_INT_HI,
    _Q_INT_LO,
    _R_AW_HI,
    _R_AW_LO,
    _T_WALL_HI,
    _T_WALL_LO,
    _T_WALL_MIN_LAM,
    _T_WALL_PRIOR_STD,
    _nelder_mead,
)
from .estimation.history_std import convert_history_std
from .estimation.identifiability import (
    _adaptive_alpha_prior_weight,
    _check_identifiable_connections,
    _check_identifiable_sources,
    _check_identifiable_solar,
    _identifiable_split_rooms,
)
from .estimation.model_build import (
    _build_parametric_system,
    _build_rooms_from_theta,
    _build_system,
    _theta_model_quantities,
)
from .estimation.regularization import (
    _compute_regularization,
    _compute_regularization_gradient,
    _compute_regularization_theta,
)
from .estimation.theta_layout import _ThetaLayout
from .estimation.warmstart import (
    _initial_state_and_covariance,
    _physics_informed_theta,
    _pin_locked_params,
    _update_wall_init_prior_from_history,
)
from .heat_sources import HeatSource
from .thermal_model import Room
from mbc.control import IpoptNLPBackend, NLPProblem, ScipyNLPBackend
from mbc.identification import cd_ped_neg_log_likelihood as _cd_ped_neg_ll

_LOGGER = logging.getLogger(__name__)


class KalmanMLEstimator:
    """
    Simulation-MSE estimator for grey-box thermal model parameters.

    Performs a single joint optimisation over the identifiable subset of:

    * per-room thermal mass ``thermal_mass`` (J/K)
    * per-room external resistance ``r_external`` (K/W)
    * per-room internal heat gain ``internal_gain`` (W)
    * per-source heater power-scale ``α_s`` (multiplicative correction
      on ``max_power × efficiency``)
    * inter-room thermal resistance ``r_value`` for connections with enough
      observed temperature-difference variance.

    Identifiability gates exclude parameters that the data cannot constrain:

    * α_s    – std(u_s) over the history ≥ ``_MIN_HEATER_USAGE_STD``
    * R_ij   – std(T_i − T_j) over the history ≥ ``_MIN_TEMP_DIFF_STD``

    The objective is the multi-step open-loop simulation error (a free-run of
    the grey-box model over the history window), minimised by IPOPT
    (L-BFGS-B fallback) with analytical gradients from a forward-sensitivity
    pass.  The forward model is integrated with L-stable backward Euler so the
    stiff 2R2C air node stays stable at the coarse sampling interval.  The
    optimiser is run from two physically-anchored starts — a coarse
    physics-informed least-squares fit and the configured prior — and the
    lower-objective finisher is kept; a MAP prior (tight on the heater scale,
    whose rated power is known a priori) regularises the otherwise-degenerate
    C / R / α ridge.

    Parameters
    ----------
    rooms : list of Room
        Current room configuration.  Structural fields (name, connections,
        windows, setpoint) are reused; ``thermal_mass``, ``r_external`` and
        ``internal_gain`` are replaced during the search.
    sources : list of HeatSource
        Heat sources.  ``power_scale`` may be replaced during the search.
    dt : float
        Sampling interval [s].  **Must match the history buffer sampling
        interval** (typically 60 s).
    Q_var : float
        Diagonal process-noise variance [°C²].  Used only by the diagnostic
        CD-EKF PED methods (:meth:`compute_log_likelihood`,
        :meth:`compute_loglik_slice`); not used in the production
        simulation-MSE objective.
    R_var : float
        Diagonal measurement-noise variance [°C²].  Used only by the
        diagnostic CD-EKF PED methods; not used in the production
        simulation-MSE objective.
    regularization : float
        Weight of the Gaussian prior shrinking the solution toward the
        current configured values (and toward zero for ``q_int`` /
        unit-scale α).  Set to 0.0 to disable.  The default is deliberately
        very light (0.01) so the data — not the configured prior — drives
        the estimate.  The identification window is typically short (the
        configured data horizon, e.g. 6 h ≈ 24 steps at 900 s), so the
        summed data-misfit term is modest in magnitude; a heavier prior
        then pins every parameter near its starting value and makes the
        result look unresponsive to the data even under strong heater
        excitation.  The prior is kept just large enough to stabilise the
        directions the data genuinely cannot constrain (unexcited rooms /
        sources) without out-voting the directions it can.
    """

    def __init__(
        self,
        rooms: List[Room],
        sources: List[HeatSource],
        dt: float,
        Q_var: float = 0.01,
        R_var: float = 0.25,
        regularization: float = 0.01,
        max_window_steps: int = 48,
    ) -> None:
        self._rooms = rooms
        self._sources = sources
        self._dt = dt
        self._Q_var = Q_var
        self._R_var = R_var
        self._regularization = regularization
        # Length [steps] of each open-loop simulation window in the
        # identification objective.  Buildings have thermal time constants
        # of many hours, so the window must be long enough for the open-loop
        # trajectory to diverge meaningfully on a wrong R_ext / Q_int —
        # otherwise those slow parameters are unconstrained and collapse onto
        # the (possibly poor) prior.  The window is still bounded so a single
        # bad data stretch can't dominate the gradient.  Default 48 steps
        # default 72 steps (= 18 h at the 900 s sampling interval).
        self._max_window_steps = int(max(20, max_window_steps))

        # Compute dt-aware step thresholds so the estimator works correctly
        # at any sampling interval, not only at the 60 s interval for which
        # the module-level MIN_HISTORY_STEPS = 60 was calibrated.
        #
        # At the default 900 s/step (15-min MPC cycle):
        #   _min_history_steps = max(10, ceil(3600 / 900)) = max(10, 4) = 10 steps
        #   _min_segment_steps = max(4,  ceil(1200 / 900)) = max(4,  2) = 4  steps
        #
        # At 60 s/step these recover the original constants:
        #   _min_history_steps = max(10, ceil(3600 / 60)) = 60 (unchanged)
        #   _min_segment_steps = max(4,  ceil(1200 / 60)) = 20 (unchanged)
        self._min_history_steps: int = max(10, int(math.ceil(_MIN_HISTORY_TIME_S / dt)))
        self._min_segment_steps: int = max(4, int(math.ceil(_MIN_SEGMENT_TIME_S / dt)))

        self._room_names: List[str] = [r.name for r in rooms]
        self._n = len(rooms)
        self._n_u = len(sources)

        # Log-space prior = current configured values
        self._log_mass_prior = np.array(
            [math.log(max(r.thermal_mass, 1.0)) for r in rooms]
        )
        self._log_r_prior = np.array(
            [math.log(max(r.r_external, 1e-9)) for r in rooms]
        )
        # Internal-gain prior: configured value (default 0.0)
        self._q_int_prior = np.array(
            [float(getattr(r, "internal_gain", 0.0)) for r in rooms]
        )
        # Heater-scale prior: configured value (default 1.0 → log = 0)
        self._log_alpha_prior_full = np.array([
            math.log(max(getattr(s, "power_scale", 1.0), 1e-3))
            for s in sources
        ])
        # Solar-scale prior: configured/previously-identified value
        # (default 1.0 → log = 0).
        self._log_solar_prior_full = np.array([
            math.log(max(float(getattr(r, "solar_scale", 1.0)), 1e-3))
            for r in rooms
        ])
        # 2R2C split priors: configured/typology values.
        self._c_air_prior_full = np.array([
            float(getattr(r, "c_air_fraction", 0.05)) for r in rooms
        ])
        self._r_aw_prior_full = np.array([
            float(getattr(r, "r_aw_fraction", 0.05)) for r in rooms
        ])
        # Wall initial temperature prior: seeded at 20 °C; estimate() updates
        # it to a physics-informed value from the first record.
        self._t_wall_init_prior = np.full(len(rooms), 20.0)
        # Adaptive heater-scale prior weight; updated per estimate() call.
        self._alpha_prior_weight: float = _ALPHA_PRIOR_WEIGHT

        # Build unique inter-room connection pairs (i, j) with i < j.
        room_idx = {r.name: k for k, r in enumerate(rooms)}
        seen: set = set()
        self._connection_pairs: List[Tuple[int, int]] = []
        self._connection_r_priors: List[float] = []
        for room in rooms:
            i = room_idx[room.name]
            for conn in room.connections:
                j = room_idx.get(conn.connected_room)
                if j is None:
                    continue
                pair = (min(i, j), max(i, j))
                if pair not in seen:
                    seen.add(pair)
                    self._connection_pairs.append(pair)
                    self._connection_r_priors.append(
                        math.log(max(conn.r_value, 1e-9))
                    )

    # ── Public API ────────────────────────────────────────────────────────

    def compute_log_likelihood(self, history: List[Dict[str, Any]]) -> Optional[float]:
        """
        Evaluate the CD-EKF PED log-likelihood at the *current* configured
        parameter values (without regularisation).

        Uses CDParameterEstimator with the fully nonlinear continuous-discrete
        approach via CD-EKF.
        """
        if len(history) < self._min_history_steps:
            return None

        # Build a minimal layout with no identifiable sources/pairs for the prior evaluation
        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=[],
            identifiable_pairs=[],
        )

        # Theta is [log_mass, log_r, q_int, t_wall_init] at the prior
        theta_prior = np.concatenate([
            self._log_mass_prior,
            self._log_r_prior,
            self._q_int_prior,
            self._t_wall_init_prior,
        ])

        # Convert history to CD-EKF format (use "ym" key)
        std_history = self._convert_history_std(history, use_ym=True)

        # Build model factory for CDParameterEstimator
        def _model_factory(theta: np.ndarray):
            return self._build_parametric_system(layout, theta)

        # Initial state estimate/covariance (supports augmented states, e.g. [T, b])
        system0 = _model_factory(theta_prior)
        if system0 is None:
            return None
        x0, P0 = self._initial_state_and_covariance(
            system0, std_history[0]["ym"],
            u=std_history[0].get("u"), d=std_history[0].get("d"),
        )

        try:
            # Evaluate negative log-likelihood using CD-EKF
            neg_ll = _cd_ped_neg_ll(
                model_factory=_model_factory,
                theta=theta_prior,
                history=std_history,
                x0=x0,
                P0=P0,
                dt=self._dt,
                n_steps=10,
            )
            if not np.isfinite(neg_ll):
                return None
            return float(-neg_ll)
        except Exception as exc:
            _LOGGER.debug("compute_log_likelihood failed: %s", exc, exc_info=True)
            return None

    def compute_loglik_slice(
        self,
        history: List[Dict[str, Any]],
        room_name: str,
        n_grid: int = 11,
        span_log: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate the log-likelihood on a ``log(C)`` × ``log(R_ext)`` grid.

        The grid is centred on the current estimated ``thermal_mass`` /
        ``r_external`` for *room_name* and spans ``±span_log`` log-units in
        each direction. Used by the Diagnostics dashboard to render a
        2-D log-likelihood landscape (mirrors ``plots/fig6_ll_surface.png``).

        Parameters
        ----------
        history
            Observation buffer (same format as :meth:`compute_log_likelihood`).
        room_name
            Room to slice the likelihood over.
        n_grid
            Number of grid points per axis. Defaults to 11 (centre + 5 on
            each side); odd values keep the centre on a grid line.
        span_log
            Half-width of the slice in log-units. ``1.0`` ≈ factor 2.7 either
            side, which is wide enough to see the local curvature.

        Returns
        -------
        dict | None
            ``None`` when history is too short or the room is unknown.
            Otherwise a dict with ``log_mass_grid``, ``log_r_grid``,
            ``log_likelihood`` (2-D list, ``None`` on per-cell failures),
            and the ``center`` values.
        """
        if len(history) < self._min_history_steps:
            return None
        if room_name not in self._room_names:
            return None

        room_idx = self._room_names.index(room_name)

        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=[],
            identifiable_pairs=[],
        )
        theta_prior = np.concatenate([
            self._log_mass_prior,
            self._log_r_prior,
            self._q_int_prior,
            self._t_wall_init_prior,
        ])

        center_log_mass = float(self._log_mass_prior[room_idx])
        center_log_r = float(self._log_r_prior[room_idx])

        def _model_factory(theta: np.ndarray):
            return self._build_parametric_system(layout, theta)

        std_history = self._convert_history_std(history, use_ym=True)
        system0 = _model_factory(theta_prior)
        if system0 is None:
            return None
        x0, P0 = self._initial_state_and_covariance(
            system0, std_history[0]["ym"],
            u=std_history[0].get("u"), d=std_history[0].get("d"),
        )

        n_grid = max(3, int(n_grid))
        span = float(abs(span_log))
        log_mass_grid = np.linspace(
            center_log_mass - span, center_log_mass + span, n_grid
        )
        log_r_grid = np.linspace(
            center_log_r - span, center_log_r + span, n_grid
        )

        log_lik: List[List[Optional[float]]] = []
        for log_mass in log_mass_grid:
            row: List[Optional[float]] = []
            for log_r in log_r_grid:
                theta = theta_prior.copy()
                theta[room_idx] = log_mass
                theta[self._n + room_idx] = log_r
                try:
                    neg_ll = _cd_ped_neg_ll(
                        model_factory=_model_factory,
                        theta=theta,
                        history=std_history,
                        x0=x0,
                        P0=P0,
                        dt=self._dt,
                        n_steps=10,
                    )
                    if not np.isfinite(neg_ll):
                        row.append(None)
                    else:
                        row.append(float(-neg_ll))
                except Exception:
                    row.append(None)
            log_lik.append(row)

        return {
            "room": room_name,
            "param_x": "log_thermal_mass",
            "param_y": "log_r_external",
            "log_mass_grid": [float(v) for v in log_mass_grid],
            "log_r_grid": [float(v) for v in log_r_grid],
            "log_likelihood": log_lik,
            "center": {
                "log_mass": center_log_mass,
                "log_r_external": center_log_r,
                "thermal_mass": float(math.exp(center_log_mass)),
                "r_external": float(math.exp(center_log_r)),
            },
        }

    def estimate(
        self,
        history: List[Dict[str, Any]],
        locked_params: Optional[Dict[str, Any]] = None,
        dataset_start_timestamps: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """
        Estimate all identifiable thermal parameters from the history buffer
        using a single joint optimisation with multistart IPOPT and analytical
        gradients.

        Parameters
        ----------
        locked_params : dict, optional
            Parameters to hold fixed (not decision variables) during
            optimisation.  Format::
        dataset_start_timestamps : list of float, optional
            UNIX timestamps of the first record in each stored dataset being
            identified jointly.  When provided, one independent
            ``t_wall_initial`` block per dataset is added to the parameter
            vector so each dataset's initial envelope temperature is estimated
            separately.  When ``None`` (single-dataset or live-window mode),
            a single shared block is used as before.

                {
                    "thermal_mass":    {"room_name": value, ...},
                    "r_external":      {"room_name": value, ...},
                    "internal_gain":   {"room_name": value, ...},
                    "solar_scale":     {"room_name": value, ...},
                    "c_air_fraction":  {"room_name": value, ...},
                    "r_aw_fraction":   {"room_name": value, ...},
                    "t_wall_initial":  {"room_name": value, ...},
                    "heater_scales":   {"source_name": value, ...},
                }

            Locked parameters are pinned to the given value by setting their
            lower and upper bounds equal (lb = ub = fixed_value), so the
            optimiser never moves them while leaving all other parameters free.

        Returns
        -------
        dict
            Estimation result.  Key fields include ``estimated_params``,
            ``estimated_internal_gains``, ``estimated_heater_scales``, etc.

            ``log_likelihood`` — negative normalised open-loop simulation MSE
            at the optimal solution (higher is better).  Kept under this name
            for dashboard / sensor backward compatibility; it is **not** a true
            Gaussian log-likelihood.

            ``neg_normalized_mse`` — alias for ``log_likelihood`` (same value).
        """
        n_steps = len(history)

        # Update wall-initial-temperature prior from the first measured air
        # temperatures and outdoor temperature.  A pure air-temperature prior
        # is a poor guess after long heating/cooling transients; the
        # (T_a, T_out) steady-state blend is a better MAP anchor.
        self._update_wall_init_prior_from_history(history)
        self._alpha_prior_weight = _adaptive_alpha_prior_weight(
            history, self._n_u, self._min_history_steps,
        )

        current = {
            r.name: {
                "thermal_mass": r.thermal_mass,
                "r_external": r.r_external,
                "internal_gain": float(getattr(r, "internal_gain", 0.0)),
            }
            for r in self._rooms
        }

        if n_steps < self._min_history_steps:
            return {
                "success": False,
                "estimated_params": {
                    name: {"thermal_mass": p["thermal_mass"],
                           "r_external": p["r_external"]}
                    for name, p in current.items()
                },
                "current_params": {
                    name: {"thermal_mass": p["thermal_mass"],
                           "r_external": p["r_external"]}
                    for name, p in current.items()
                },
                "estimated_internal_gains": {
                    name: p["internal_gain"] for name, p in current.items()
                },
                "estimated_heater_scales": {
                    s.name: float(getattr(s, "power_scale", 1.0))
                    for s in self._sources
                },
                "estimated_inter_room_r": {},
                "identifiable_connections": [],
                "identifiable_sources": [],
                "stage2_converged": False,
                "n_steps": n_steps,
                "log_likelihood": None,
                "neg_normalized_mse": None,
                "message": (
                    f"Insufficient data: {n_steps} steps available, "
                    f"need ≥ {self._min_history_steps}.  Keep the system running "
                    "and try again when more observations have been collected."
                ),
            }

        # ── Identifiability gates ───────────────────────────────────────────
        identifiable_pairs = _check_identifiable_connections(
            history, self._room_names, self._connection_pairs,
            min_history_steps=self._min_history_steps,
        )
        # Heater power-scale α is treated like every other parameter (thermal
        # mass, R_ext, internal gain): it is *always* part of the joint vector
        # rather than being switched on/off by an excitation gate.  A source
        # that never runs contributes no heat, so ∂f/∂α ≡ 0 for it and the
        # optimiser leaves its scale at the unit prior — i.e. unexcited sources
        # self-regularise without a special gate, while any source that does
        # vary gets its scale identified from the data.
        identifiable_sources = list(range(self._n_u))
        # The duty-cycle excitation check is still used to decide which rooms'
        # 2R2C envelope splits are identifiable (those need a heater step to be
        # seen), so it is computed separately from the always-on α block.
        excited_sources = _check_identifiable_sources(
            history, self._n_u,
            min_history_steps=self._min_history_steps,
        )
        identifiable_solar = _check_identifiable_solar(
            history, self._room_names,
            min_history_steps=self._min_history_steps,
        )
        identifiable_splits = _identifiable_split_rooms(
            excited_sources, self._sources, self._room_names,
        )

        # One t_wall_init block per distinct dataset start timestamp (minimum 1).
        n_wall_segs = max(1, len(dataset_start_timestamps)) if dataset_start_timestamps else 1

        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=identifiable_sources,
            identifiable_pairs=identifiable_pairs,
            identifiable_solar=identifiable_solar,
            identifiable_splits=identifiable_splits,
            n_wall_segs=n_wall_segs,
        )

        # ── Build initial point and priors for the joint vector ────────────
        log_alpha_prior = np.array([
            self._log_alpha_prior_full[s] for s in identifiable_sources
        ])
        log_r_ij_prior = np.array([
            self._connection_r_priors[self._connection_pairs.index(p)]
            for p in identifiable_pairs
        ])
        log_solar_prior = np.array([
            self._log_solar_prior_full[i] for i in identifiable_solar
        ])
        c_air_prior = np.array([
            self._c_air_prior_full[i] for i in identifiable_splits
        ])
        r_aw_prior = np.array([
            self._r_aw_prior_full[i] for i in identifiable_splits
        ])
        # t_wall_init prior: one block per wall segment (tiled from the single prior).
        t_wall_prior_all = np.tile(self._t_wall_init_prior, n_wall_segs)
        theta_prior = np.concatenate([
            self._log_mass_prior,
            self._log_r_prior,
            self._q_int_prior,
            t_wall_prior_all,
            log_alpha_prior,
            log_r_ij_prior,
            log_solar_prior,
            c_air_prior,
            r_aw_prior,
        ])

        # ── Build per-parameter bounds ─────────────────────────────────────
        n = self._n
        bounds: List[Tuple[float, float]] = (
            [(_LOG_MASS_LO, _LOG_MASS_HI)] * n
            + [(_LOG_R_LO, _LOG_R_HI)] * n
            + [(_Q_INT_LO, _Q_INT_HI)] * n
            + [(_T_WALL_LO, _T_WALL_HI)] * (n * n_wall_segs)
            + [(_LOG_ALPHA_LO, _LOG_ALPHA_HI)] * len(identifiable_sources)
            + [(_LOG_R_IJ_LO, _LOG_R_IJ_HI)] * len(identifiable_pairs)
            + [(_LOG_SOLAR_LO, _LOG_SOLAR_HI)] * len(identifiable_solar)
            + [(_C_AIR_LO, _C_AIR_HI)] * len(identifiable_splits)
            + [(_R_AW_LO, _R_AW_HI)] * len(identifiable_splits)
        )

        # ── Apply parameter locks (equality constraints via lb = ub) ──────
        if locked_params:
            self._pin_locked_params(
                bounds, theta_prior, locked_params,
                layout, identifiable_sources,
                identifiable_solar, identifiable_splits,
            )

        # ── Convert history (carries timestamps for gap detection) ────────
        std_history = self._convert_history_std(history, use_ym=True)

        # ── Build regularisation function ──────────────────────────────────
        def _regularization_fn(theta: np.ndarray) -> float:
            return self._compute_regularization_theta(theta, layout)

        # ── IPOPT optimisation with analytical gradients ──────────────────────
        # Objective: multi-step open-loop simulation MSE (see
        # _simulation_mse_and_grad).  The EKF PED likelihood is retained
        # for diagnostics (compute_log_likelihood / compute_loglik_slice)
        # but is no longer used here because it diverges on unevenly-spaced
        # data from controller restarts.
        lb = np.array([lo for lo, _ in bounds])
        ub = np.array([hi for _, hi in bounds])

        # Cache the last (theta, obj, grad) triple so separate fun/jac calls
        # at the same point do not trigger a second forward-sensitivity pass.
        _cache: List[Optional[object]] = [None, None, None]  # [theta, val, grad]

        def _eval(theta: np.ndarray) -> None:
            if _cache[0] is None or not np.array_equal(theta, _cache[0]):
                mse, g_mse = self._simulation_mse_and_grad(
                    theta, layout, std_history, nominal_dt=self._dt,
                    max_window_steps=self._max_window_steps,
                    min_segment_steps=self._min_segment_steps,
                    dataset_start_ts=dataset_start_timestamps,
                )
                reg = _regularization_fn(theta)
                reg_grad = self._compute_regularization_gradient(
                    theta, layout, identifiable_pairs
                )
                _cache[0] = theta.copy()
                _cache[1] = mse + reg
                _cache[2] = g_mse + reg_grad

        def _fun(theta: np.ndarray) -> float:
            _eval(theta)
            return float(_cache[1])  # type: ignore[arg-type]

        def _jac(theta: np.ndarray) -> np.ndarray:
            _eval(theta)
            return np.asarray(_cache[2], dtype=float)  # type: ignore[arg-type]

        ipopt_backend = IpoptNLPBackend(
            options={"print_level": 0, "max_iter": 300, "tol": 1e-6}
        )
        scipy_backend = ScipyNLPBackend(
            # L-BFGS-B builds a quasi-Newton Hessian approximation that
            # handles the large scale differences between parameters (e.g.
            # log_mass gradient is O(1) while q_int gradient is O(1e-4)),
            # giving much better convergence than pure-gradient SLSQP.
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-6},
        )
        _active_backend = ipopt_backend

        best_theta = theta_prior.copy()
        best_f = float("inf")
        best_converged = False

        # ── Multistart from physically-anchored points ─────────────────────
        # The open-loop identification landscape is non-convex with a
        # documented degenerate ridge (the "C huge / R huge" basin, README
        # §17.5).  We run the optimiser from the physics-informed start (a
        # coarse least-squares 1R1C fit) and from the configured prior, then
        # keep whichever finisher has the lower regularised objective.  The
        # MAP prior (see _compute_regularization_theta) — tight on the heater
        # scale, whose rated power is genuinely known a priori — is what keeps
        # the chosen optimum off the degenerate ridge; the second start simply
        # guards against the physics-informed seed landing in a bad basin.
        phys_theta = self._physics_informed_theta(
            std_history, layout, theta_prior, lb, ub,
        )
        starts: List[np.ndarray] = []
        if phys_theta is not None:
            starts.append(phys_theta)
        starts.append(theta_prior.copy())

        def _solve_from(theta_start: np.ndarray) -> Optional[Tuple[float, np.ndarray, bool]]:
            """Run the active backend from one start; return (f, theta, ok)."""
            nonlocal _active_backend
            _cache[0] = None
            problem = NLPProblem(
                objective=_fun,
                objective_jac=_jac,
                x0=theta_start,
                lb=lb,
                ub=ub,
                constraints=(),
            )
            try:
                res = _active_backend.solve(problem)
            except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
                _LOGGER.warning(
                    "IPOPT backend unavailable for parameter estimation (%s); "
                    "falling back to L-BFGS-B.", exc,
                )
                _active_backend = scipy_backend
                _cache[0] = None
                try:
                    res = _active_backend.solve(problem)
                except Exception as exc2:
                    _LOGGER.debug("Optimiser fallback failed: %s", exc2)
                    return None
            except Exception as exc:
                _LOGGER.debug("Optimiser failed: %s", exc)
                return None
            if not np.isfinite(res.fun):
                return None
            return float(res.fun), np.asarray(res.x, dtype=float), bool(res.success)

        for theta_start in starts:
            out = _solve_from(theta_start)
            if out is None:
                continue
            f_cand, theta_c, converged = out
            if f_cand < best_f:
                best_f = f_cand
                best_theta = theta_c
                best_converged = converged

        # ── Unpack and clip the best solution ──────────────────────────────
        (log_mass, log_r, q_int, t_wall_init, log_alpha, log_r_ij,
         log_solar, c_air, r_aw) = layout.unpack(best_theta)
        log_mass = np.clip(log_mass, _LOG_MASS_LO, _LOG_MASS_HI)
        log_r = np.clip(log_r, _LOG_R_LO, _LOG_R_HI)
        q_int = np.clip(q_int, _Q_INT_LO, _Q_INT_HI)
        t_wall_init = np.clip(t_wall_init, _T_WALL_LO, _T_WALL_HI)
        log_alpha = np.clip(log_alpha, _LOG_ALPHA_LO, _LOG_ALPHA_HI)
        log_r_ij = np.clip(log_r_ij, _LOG_R_IJ_LO, _LOG_R_IJ_HI)
        log_solar = np.clip(log_solar, _LOG_SOLAR_LO, _LOG_SOLAR_HI)
        c_air = np.clip(c_air, _C_AIR_LO, _C_AIR_HI)
        r_aw = np.clip(r_aw, _R_AW_LO, _R_AW_HI)

        # Build result dict ------------------------------------------------
        estimated_params: Dict[str, Dict[str, Any]] = {}
        estimated_internal_gains: Dict[str, float] = {}
        for i, name in enumerate(self._room_names):
            estimated_params[name] = {
                "thermal_mass": round(float(math.exp(log_mass[i])), 0),
                "r_external": round(float(math.exp(log_r[i])), 6),
            }
            estimated_internal_gains[name] = round(float(q_int[i]), 2)

        estimated_heater_scales: Dict[str, float] = {
            s.name: float(getattr(s, "power_scale", 1.0))
            for s in self._sources
        }
        for k, s_idx in enumerate(identifiable_sources):
            estimated_heater_scales[self._sources[s_idx].name] = round(
                float(math.exp(log_alpha[k])), 4
            )

        estimated_r_ij: Dict[str, float] = {}
        identifiable_names: List[str] = []
        for k, (pi, pj) in enumerate(identifiable_pairs):
            key = f"{self._room_names[pi]}:{self._room_names[pj]}"
            estimated_r_ij[key] = round(float(math.exp(log_r_ij[k])), 6)
            identifiable_names.append(key)

        # Per-room solar scales / envelope splits: identified value for
        # gated rooms, configured value otherwise.
        estimated_solar_scales: Dict[str, float] = {
            self._room_names[i]: float(math.exp(self._log_solar_prior_full[i]))
            for i in range(self._n)
        }
        for k, i in enumerate(identifiable_solar):
            estimated_solar_scales[self._room_names[i]] = round(
                float(math.exp(log_solar[k])), 4
            )
        estimated_splits: Dict[str, Dict[str, float]] = {
            self._room_names[i]: {
                "c_air_fraction": float(self._c_air_prior_full[i]),
                "r_aw_fraction": float(self._r_aw_prior_full[i]),
            }
            for i in range(self._n)
        }
        for k, i in enumerate(identifiable_splits):
            estimated_splits[self._room_names[i]] = {
                "c_air_fraction": round(float(c_air[k]), 4),
                "r_aw_fraction": round(float(r_aw[k]), 4),
            }

        # Per-room wall initial temperatures — first dataset segment for backward compat.
        estimated_t_wall_initial: Dict[str, float] = {
            self._room_names[i]: round(float(t_wall_init[i]), 2)
            for i in range(self._n)
        }
        # All dataset-segment wall temperatures (populated when n_wall_segs > 1).
        if n_wall_segs > 1:
            estimated_t_wall_per_dataset: Optional[List[Dict[str, float]]] = [
                {
                    self._room_names[i]: round(float(layout.get_t_wall_seg(best_theta, seg)[i]), 2)
                    for i in range(self._n)
                }
                for seg in range(n_wall_segs)
            ]
        else:
            estimated_t_wall_per_dataset = None

        # Report negative normalised MSE (higher → better fit).
        # Stored in the same "log_likelihood" field for dashboard compatibility;
        # the value is -MSE/room/step (not a true log-likelihood).
        try:
            if not np.isfinite(best_f):
                log_ll_val: Optional[float] = None
            else:
                reg = self._compute_regularization_theta(best_theta, layout)
                log_ll_val = round(float(-(best_f - reg)), 6)
        except Exception:
            log_ll_val = None

        msg_parts = []
        if best_converged:
            msg_parts.append("Joint optimisation converged.")
        else:
            msg_parts.append(
                "Joint optimisation reached iteration limit "
                "(result may be approximate)."
            )
        if identifiable_sources:
            n_excited = len(excited_sources)
            msg_parts.append(
                f"Heater scale estimated for {len(identifiable_sources)} "
                f"source(s) ({n_excited} with clear duty-cycle excitation; "
                "the rest stay near unit scale)."
            )
        if identifiable_pairs:
            msg_parts.append(
                f"Inter-room R estimated for {len(identifiable_pairs)} "
                "connection(s)."
            )
        if identifiable_solar:
            msg_parts.append(
                f"Solar scale estimated for {len(identifiable_solar)} room(s)."
            )
        else:
            msg_parts.append(
                "Solar scale not identifiable — no room saw enough solar "
                "variation during the window."
            )
        if identifiable_splits:
            msg_parts.append(
                f"Envelope split estimated for {len(identifiable_splits)} room(s)."
            )

        identifiable_source_names = [
            self._sources[s].name for s in identifiable_sources
        ]

        return {
            "success": True,
            "estimated_params": estimated_params,
            "current_params": {
                name: {"thermal_mass": p["thermal_mass"],
                       "r_external": p["r_external"]}
                for name, p in current.items()
            },
            "estimated_internal_gains": estimated_internal_gains,
            "estimated_heater_scales": estimated_heater_scales,
            "estimated_inter_room_r": estimated_r_ij,
            "estimated_solar_scales": estimated_solar_scales,
            "estimated_envelope_splits": estimated_splits,
            "estimated_t_wall_initial": estimated_t_wall_initial,
            "estimated_t_wall_per_dataset": estimated_t_wall_per_dataset,
            "identifiable_connections": identifiable_names,
            "identifiable_sources": identifiable_source_names,
            "identifiable_solar_rooms": [
                self._room_names[i] for i in identifiable_solar
            ],
            "identifiable_split_rooms": [
                self._room_names[i] for i in identifiable_splits
            ],
            "stage2_converged": best_converged,
            "n_steps": n_steps,
            "log_likelihood": log_ll_val,
            "neg_normalized_mse": log_ll_val,
            "message": "  ".join(msg_parts),
        }

    def estimate_wall_initial_only(
        self,
        history: List[Dict[str, Any]],
        room_params: Optional[Dict[str, Dict[str, Any]]] = None,
        calibration_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, float]:
        """Quickly estimate only the wall-envelope initial temperature.

        All structural parameters are held fixed at their configured /
        overridden values.  Only ``t_wall_init`` (one value per room) is a
        free decision variable.  A single L-BFGS-B pass is used — no
        multistart, no OLS warm-start — so this is fast enough to run
        before every open-loop simulation.

        When ``calibration_history`` is supplied it is used for the fit
        instead of ``history``.  This lets callers estimate the wall state
        on a leading calibration window while applying the result at the
        start of a separate simulation window.

        Returns a dict ``{room_name: t_wall_initial_float}``.  Falls back to
        the physics-informed prior when history is too short or the
        optimiser fails.
        """
        fit_history = (
            calibration_history if calibration_history is not None else history
        )
        n = self._n

        # Seed the prior from the anchor air / outdoor temperatures.
        self._update_wall_init_prior_from_history(
            fit_history if fit_history else history
        )

        fallback = {
            self._room_names[i]: round(float(self._t_wall_init_prior[i]), 2)
            for i in range(n)
        }

        if len(fit_history) < self._min_history_steps:
            return fallback

        # Minimal layout: only the 4n core parameters; no heater / R_ij /
        # solar / split blocks — those are all locked via equal bounds.
        layout = _ThetaLayout(
            n_rooms=n,
            identifiable_sources=[],
            identifiable_pairs=[],
            identifiable_solar=[],
            identifiable_splits=[],
        )

        # Start from configured/overridden structural params.
        log_mass_init = self._log_mass_prior.copy()
        log_r_init = self._log_r_prior.copy()
        q_int_init = self._q_int_prior.copy()
        if room_params:
            for room_name, overrides in room_params.items():
                if room_name not in self._room_names:
                    continue
                i = self._room_names.index(room_name)
                if "thermal_mass" in overrides:
                    log_mass_init[i] = math.log(
                        max(float(overrides["thermal_mass"]), 1.0)
                    )
                if "r_external" in overrides:
                    log_r_init[i] = math.log(
                        max(float(overrides["r_external"]), 1e-9)
                    )
                if "internal_gain" in overrides:
                    q_int_init[i] = float(overrides["internal_gain"])

        theta_prior = np.concatenate([
            log_mass_init,
            log_r_init,
            q_int_init,
            self._t_wall_init_prior.copy(),
        ])

        # Lock everything except the t_wall_init block (3n … 4n).
        bounds: List[Tuple[float, float]] = (
            [(float(theta_prior[i]), float(theta_prior[i])) for i in range(n)]
            + [(float(theta_prior[n + i]), float(theta_prior[n + i])) for i in range(n)]
            + [(float(theta_prior[2 * n + i]), float(theta_prior[2 * n + i])) for i in range(n)]
            + [(_T_WALL_LO, _T_WALL_HI)] * n
        )

        std_history = self._convert_history_std(fit_history, use_ym=False)

        _cache: List[Optional[object]] = [None, None, None]

        def _eval(theta: np.ndarray) -> None:
            if _cache[0] is None or not np.array_equal(theta, _cache[0]):
                mse, g_mse = self._simulation_mse_and_grad(
                    theta, layout, std_history, nominal_dt=self._dt,
                    max_window_steps=self._max_window_steps,
                    min_segment_steps=self._min_segment_steps,
                )
                a_tw, b_tw = layout.idx_t_wall_init
                t_wall = theta[a_tw:b_tw]
                lam_tw = max(self._regularization, _T_WALL_MIN_LAM)
                reg = lam_tw * float(
                    np.sum((t_wall - self._t_wall_init_prior) ** 2)
                ) / (_T_WALL_PRIOR_STD ** 2)
                reg_grad = np.zeros(len(theta))
                reg_grad[a_tw:b_tw] = (
                    2.0 * lam_tw * (t_wall - self._t_wall_init_prior)
                    / (_T_WALL_PRIOR_STD ** 2)
                )
                _cache[0] = theta.copy()
                _cache[1] = mse + reg
                _cache[2] = g_mse + reg_grad

        def _fun(theta: np.ndarray) -> float:
            _eval(theta)
            return float(_cache[1])  # type: ignore[arg-type]

        def _jac(theta: np.ndarray) -> np.ndarray:
            _eval(theta)
            return np.asarray(_cache[2], dtype=float)  # type: ignore[arg-type]

        lb = np.array([lo for lo, _ in bounds])
        ub = np.array([hi for _, hi in bounds])

        try:
            from scipy.optimize import minimize as _sp_minimize
            res = _sp_minimize(
                _fun, theta_prior,
                jac=_jac,
                bounds=list(zip(lb, ub)),
                method="L-BFGS-B",
                options={"maxiter": 200, "ftol": 1e-10, "gtol": 1e-5},
            )
            if np.isfinite(res.fun):
                a_tw, b_tw = layout.idx_t_wall_init
                t_wall = np.clip(res.x[a_tw:b_tw], _T_WALL_LO, _T_WALL_HI)
                return {
                    self._room_names[i]: round(float(t_wall[i]), 2)
                    for i in range(n)
                }
        except Exception as exc:
            _LOGGER.debug("Fast t_wall_init estimation failed: %s", exc)

        return fallback

    # ── Internal helpers ──────────────────────────────────────────────────

    def _update_wall_init_prior_from_history(
        self,
        history: List[Dict[str, Any]],
    ) -> None:
        _update_wall_init_prior_from_history(self, history)

    def _pin_locked_params(
        self,
        bounds: List[Tuple[float, float]],
        theta_prior: np.ndarray,
        locked_params: Dict[str, Any],
        layout: "_ThetaLayout",
        identifiable_sources: List[int],
        identifiable_solar: List[int],
        identifiable_splits: List[int],
    ) -> None:
        _pin_locked_params(
            self, bounds, theta_prior, locked_params, layout,
            identifiable_sources, identifiable_solar, identifiable_splits,
        )

    def _physics_informed_theta(
        self,
        std_history: List[Dict[str, np.ndarray]],
        layout: "_ThetaLayout",
        theta_prior: np.ndarray,
        lb: np.ndarray,
        ub: np.ndarray,
        nominal_dt: Optional[float] = None,
    ) -> Optional[np.ndarray]:
        return _physics_informed_theta(
            self, std_history, layout, theta_prior, lb, ub, nominal_dt,
        )

    def _theta_model_quantities(
        self,
        layout: "_ThetaLayout",
        theta: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        return _theta_model_quantities(self, layout, theta)

    def _dfdtheta_step(
        self,
        q: Dict[str, np.ndarray],
        layout: "_ThetaLayout",
        model: HouseThermalSystem,
        f_val: np.ndarray,
        x: np.ndarray,
        u_k: np.ndarray,
        d_k: np.ndarray,
        ntheta: int,
        nx: int,
    ) -> np.ndarray:
        """``∂f/∂θ`` at fixed state, shape (ntheta, nx), for the 2R2C drift.

        Only the physical rows (air 0…n−1, wall n…2n−1) are non-zero; the
        emitter-filter drift carries no θ-dependence.  Two parameter
        families use the exact whole-row scaling shortcut (every term of a
        room's drift row is proportional to 1/C):

        * ``log_mass_i`` scales both of room i's rows by −1, and
        * ``c_air_i`` scales the air row by −1/fc and the wall row by
          +1/(1−fc).

        The remaining families are written out from the conductance
        structure.  The wind overlay is inactive during estimation, and
        sky/bridge conductances (default 0) are treated as
        r-independent.
        """
        n = self._n
        T_a = x[:n]
        T_w = x[n: 2 * n]
        T_out = float(d_k[0])
        d_sol = np.array([
            float(d_k[1 + i]) if 1 + i < len(d_k) else 0.0 for i in range(n)
        ])
        C_a, C_w = q["C_a"], q["C_w"]
        g_inf, g_aw, g_we = q["g_inf"], q["g_aw"], q["g_we"]
        fc, rf, s = q["fc"], q["rf"], q["s"]
        w = q["wall_frac"]
        facade = q["facade"]

        D = np.zeros((ntheta, nx))
        j_idx = np.arange(n)

        # log_mass: rows scale with 1/C_tot → −f per row.
        D[j_idx, j_idx] = -f_val[:n]
        D[j_idx, n + j_idx] = -f_val[n: 2 * n]

        # log_r: all three conductances scale with 1/r_ext.
        D[n + j_idx, j_idx] = -(
            (g_aw / C_a) * (T_w - T_a) + (g_inf / C_a) * (T_out - T_a)
        )
        D[n + j_idx, n + j_idx] = -(
            (g_aw / C_w) * (T_a - T_w) + (g_we / C_w) * (T_out - T_w)
        )

        # q_int: direct heat on the air node.
        D[2 * n + j_idx, j_idx] = 1.0 / C_a

        # Heater scales α (air node).
        for k_la, s_idx in enumerate(layout.identifiable_sources):
            src = self._sources[s_idx]
            i_src = model._room_idx[src.room]
            u_s = float(u_k[s_idx]) if s_idx < len(u_k) else 0.0
            u_scaled_s = q["heater_scales"][s_idx] * u_s
            a0, _ = layout.idx_log_alpha
            D[a0 + k_la, i_src] = (
                src.thermal_power(max(0.0, u_scaled_s), T_out) / C_a[i_src]
            )

        # Inter-room resistances (wall-to-wall).
        r0, _ = layout.idx_log_r_ij
        for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
            g_ij = float(q["g_ij"][k_rij])
            D[r0 + k_rij, n + pi] = (g_ij / C_w[pi]) * (T_w[pi] - T_w[pj])
            D[r0 + k_rij, n + pj] = (g_ij / C_w[pj]) * (T_w[pj] - T_w[pi])

        # Solar scales (split between air and wall like G_d).
        s0, _ = layout.idx_log_solar
        for k_s, i in enumerate(layout.identifiable_solar):
            D[s0 + k_s, i] = (1.0 - w) * s[i] * d_sol[i] / C_a[i]
            D[s0 + k_s, n + i] = (w + facade[i]) * s[i] * d_sol[i] / C_w[i]

        # Envelope splits.
        ca0, _ = layout.idx_c_air
        ra0, _ = layout.idx_r_aw
        for k_sp, i in enumerate(layout.identifiable_splits):
            # c_air: air row ∝ 1/fc, wall row ∝ 1/(1−fc).
            D[ca0 + k_sp, i] = -f_val[i] / fc[i]
            D[ca0 + k_sp, n + i] = f_val[n + i] / (1.0 - fc[i])
            # r_aw: ∂g_aw/∂rf = −g_aw/rf, ∂g_we/∂rf = +g_we/(1−rf).
            D[ra0 + k_sp, i] = -(g_aw[i] / (rf[i] * C_a[i])) * (T_w[i] - T_a[i])
            D[ra0 + k_sp, n + i] = (
                -(g_aw[i] / (rf[i] * C_w[i])) * (T_a[i] - T_w[i])
                + (g_we[i] / ((1.0 - rf[i]) * C_w[i])) * (T_out - T_w[i])
            )
        return D

    def _dFdtheta_const(
        self,
        q: Dict[str, np.ndarray],
        layout: "_ThetaLayout",
        model: HouseThermalSystem,
        ntheta: int,
        nx: int,
    ) -> np.ndarray:
        """``∂(∂f/∂x)/∂θ``, shape (ntheta, nx, nx), for the 2R2C drift.

        Used by the EKF-likelihood sensitivity pass to propagate ∂P/∂θ.
        Only the physical 2n × 2n block carries θ-dependence (the
        filter-column coupling through the heater scales is neglected,
        as in the previous 1R1C implementation).
        """
        n = self._n
        C_a, C_w = q["C_a"], q["C_w"]
        g_inf, g_aw, g_we = q["g_inf"], q["g_aw"], q["g_we"]
        fc, rf = q["fc"], q["rf"]
        F_phys = model._F  # (2n, 2n), already divided by C

        D = np.zeros((ntheta, nx, nx))
        for j in range(n):
            # log_mass: both of room j's rows scale with 1/C_tot.
            D[j, j, :2 * n] = -F_phys[j, :]
            D[j, n + j, :2 * n] = -F_phys[n + j, :]
            # log_r: g_inf, g_aw, g_we all scale with 1/r_ext.
            D[n + j, j, j] = (g_inf[j] + g_aw[j]) / C_a[j]
            D[n + j, j, n + j] = -g_aw[j] / C_a[j]
            D[n + j, n + j, j] = -g_aw[j] / C_w[j]
            D[n + j, n + j, n + j] = (g_aw[j] + g_we[j]) / C_w[j]

        r0, _ = layout.idx_log_r_ij
        for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
            g_ij = float(q["g_ij"][k_rij])
            t = r0 + k_rij
            D[t, n + pi, n + pi] = g_ij / C_w[pi]
            D[t, n + pj, n + pj] = g_ij / C_w[pj]
            D[t, n + pi, n + pj] = -g_ij / C_w[pi]
            D[t, n + pj, n + pi] = -g_ij / C_w[pj]

        ca0, _ = layout.idx_c_air
        ra0, _ = layout.idx_r_aw
        for k_sp, i in enumerate(layout.identifiable_splits):
            t = ca0 + k_sp
            D[t, i, :2 * n] = -F_phys[i, :] / fc[i]
            D[t, n + i, :2 * n] = F_phys[n + i, :] / (1.0 - fc[i])
            t = ra0 + k_sp
            D[t, i, i] = g_aw[i] / (rf[i] * C_a[i])
            D[t, i, n + i] = -g_aw[i] / (rf[i] * C_a[i])
            D[t, n + i, i] = -g_aw[i] / (rf[i] * C_w[i])
            D[t, n + i, n + i] = (
                g_aw[i] / (rf[i] * C_w[i])
                - g_we[i] / ((1.0 - rf[i]) * C_w[i])
            )
        return D

    def _simulation_mse_and_grad(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
        std_history: List[Dict[str, Any]],
        nominal_dt: float,
        max_gap_factor: float = 1.5,
        min_segment_steps: int = 20,
        max_window_steps: int = 60,
        dataset_start_ts: Optional[List[float]] = None,
    ) -> Tuple[float, np.ndarray]:
        """
        Multi-step open-loop simulation MSE and its gradient w.r.t. ``theta``.

        The history is split into contiguous segments wherever the timestamp
        gap between consecutive records exceeds ``max_gap_factor * nominal_dt``
        (controller restarts, HA pauses).  Each segment is further divided
        into windows of at most ``max_window_steps`` steps; each window is
        simulated forward from its own first measured temperature.  Short
        windows keep the simulation horizon well-conditioned and prevent
        the gradient for slowly-varying parameters (q_int) from being
        swamped by the accumulated error of very long open-loop runs.

        Gradient is computed via a forward-sensitivity pass propagating
        ``sx = ∂x/∂θ`` alongside the state.  This is strictly simpler than
        the EKF sensitivity pass because there is no Kalman update step —
        the sensitivity resets at every window boundary rather than
        accumulating across the full dataset.

        **Wall-seed contract** (see
        :meth:`HouseThermalSDE.initial_state_from_measurement`): the first
        window of each *dataset* segment seeds the envelope from the
        corresponding per-dataset ``θ[t_wall_init_seg_k]`` block (air seed
        then override); every later window — including the first window of
        within-dataset timestamp-gap segments — uses the ``"steady_state"``
        warm start at the local (T_a, T_out) equilibrium.  When
        ``dataset_start_ts`` is ``None`` (backward-compat / single-dataset
        path) the very first segment receives the single ``θ[t_wall_init]``
        block and all remaining segments use steady-state.

        Returns
        -------
        mse : float
            Sum of squared errors normalised per room per step.  Returns
            ``1e10`` (sentinel) on any numerical failure so the optimiser
            treats the point as infeasible.
        grad : (ntheta,) ndarray
            Gradient of ``mse`` w.r.t. ``theta`` (zero on failure).
        """
        _SENTINEL = 1e10
        ntheta = len(theta)
        _zero_grad = np.zeros(ntheta)

        if not np.all(np.isfinite(theta)):
            return _SENTINEL, _zero_grad.copy()

        model = self._build_parametric_system(layout, theta)
        if model is None:
            return _SENTINEL, _zero_grad.copy()

        n = self._n
        nx = int(model.nx)
        _I = np.eye(nx)
        # Sub-steps per measurement interval for the implicit-Euler propagation.
        # Backward Euler is L-stable, so this is purely for accuracy: at the
        # 900 s sampling interval the 2R2C air node (~300 s) needs a sub-step
        # below its time constant for the heating/cooling transient that
        # separates heater-scale from thermal-mass to be resolved.  ~300 s
        # sub-steps (3 per 900 s interval) keep the truncation error small while
        # keeping each objective evaluation affordable for the joint multi-room
        # optimisation.
        n_sub = max(1, int(math.ceil(self._dt / 300.0)))

        quants = self._theta_model_quantities(layout, theta)
        _p0 = model.params

        # ── Segment history at timestamp gaps ────────────────────────────
        N = len(std_history)
        seg_starts: List[int] = [0]
        for idx in range(N - 1):
            t_a = std_history[idx].get("t")
            t_b = std_history[idx + 1].get("t")
            if t_a is not None and t_b is not None:
                if (float(t_b) - float(t_a)) > max_gap_factor * nominal_dt:
                    seg_starts.append(idx + 1)
        seg_starts.append(N)

        total_sse = 0.0
        total_grad = np.zeros(ntheta)
        n_steps_used = 0

        ra0, _ = layout.idx_r_aw
        tw0, _ = layout.idx_t_wall_init
        n_wall_segs = layout.n_wall_segs

        # Pre-process dataset start timestamps for O(1) segment-to-dataset
        # lookup.  Each detected contiguous segment whose first record's
        # timestamp falls within half a nominal step of a known dataset start
        # will be assigned the corresponding per-dataset θ[t_wall_init_seg]
        # block.  Segments that arise from *within-dataset* gaps (controller
        # restarts) will not match any dataset start and will use steady-state.
        _ts_tol = 0.5 * nominal_dt
        if dataset_start_ts is not None and len(dataset_start_ts) > 0:
            _ds_ts_arr: Optional[np.ndarray] = np.array(
                dataset_start_ts, dtype=float
            )
        else:
            _ds_ts_arr = None

        for seg_i in range(len(seg_starts) - 1):
            seg_begin = seg_starts[seg_i]
            seg_end = seg_starts[seg_i + 1]
            if (seg_end - seg_begin) < min_segment_steps:
                continue

            seg = std_history[seg_begin:seg_end]

            # Determine which (if any) per-dataset wall-temp block applies to
            # the first window of this segment.
            #   _wall_seg_idx is None  → steady-state seed (no injection)
            #   _wall_seg_idx == k     → inject θ[t_wall_init_seg_k]
            _wall_seg_idx: Optional[int]
            if _ds_ts_arr is not None:
                t_seg_start = seg[0].get("t")
                if t_seg_start is not None:
                    diffs = np.abs(_ds_ts_arr - float(t_seg_start))
                    best_k = int(np.argmin(diffs))
                    _wall_seg_idx = (
                        best_k if diffs[best_k] <= _ts_tol else None
                    )
                else:
                    _wall_seg_idx = None
            else:
                # Backward-compat / single-dataset: seed only the very first
                # segment with the single θ[t_wall_init] block (seg index 0).
                _wall_seg_idx = 0 if seg_i == 0 else None

            # Split the contiguous segment into windows of at most
            # max_window_steps.  Each window is simulated independently
            # from its own first measurement so that the open-loop horizon
            # never grows so long that the gradient for slow parameters
            # (q_int, large C) is swamped by accumulated simulation error.
            is_first_window = True
            for win_start in range(0, len(seg), max_window_steps):
                win_end = min(win_start + max_window_steps, len(seg))
                if (win_end - win_start) < min_segment_steps:
                    is_first_window = False
                    continue
                win = seg[win_start:win_end]

                ym0 = np.asarray(win[0]["ym"], dtype=float)
                u0 = np.asarray(win[0].get("u", []), dtype=float)
                d0 = np.asarray(win[0].get("d", []), dtype=float)
                # Match diagnostics: air seed for the first window of a
                # dataset segment (wall override applied next); interior
                # windows use the parameter-dependent steady-state directly.
                _inject_wall = is_first_window and _wall_seg_idx is not None
                _wall_seed = "air" if _inject_wall else "steady_state"
                try:
                    x = np.asarray(
                        model.initial_state_from_measurement(
                            ym0, u0, d0, wall_seed=_wall_seed
                        ),
                        dtype=float,
                    )
                except TypeError:
                    x = np.asarray(
                        model.initial_state_from_measurement(ym0, u0, d0),
                        dtype=float,
                    )
                sx = np.zeros((ntheta, nx))  # ∂x/∂θ — reset per window
                is_first_window = False

                if _inject_wall:
                    # Identified per-dataset t_wall_init from θ;
                    # ∂x₀[n+i]/∂θ[tw_base+i] = 1.
                    tw_base = tw0 + _wall_seg_idx * n  # type: ignore[operator]
                    for i in range(n):
                        if n + i < nx:
                            x[n + i] = float(
                                np.clip(theta[tw_base + i], _T_WALL_LO, _T_WALL_HI)
                            )
                            sx[tw_base + i, n + i] = 1.0
                    # From the second window onward the remaining segment
                    # windows use steady-state, so clear the dataset index to
                    # avoid accidental re-injection.
                    _wall_seg_idx = None
                else:
                    # steady_state wall seed already applied; propagate
                    # r_aw sensitivity for that warmstart.
                    if len(d0) > 0 and len(layout.identifiable_splits):
                        T_out0 = float(d0[0])
                        for k_sp, i in enumerate(layout.identifiable_splits):
                            sx[ra0 + k_sp, n + i] = T_out0 - float(x[i])

                for step in range(len(win) - 1):
                    rec_k = win[step]
                    rec_next = win[step + 1]

                    try:
                        u_k = np.asarray(rec_k["u"], dtype=float)
                        d_k = np.asarray(rec_k["d"], dtype=float)
                        ym_k = np.asarray(rec_k["ym"], dtype=float)
                        ym_next = np.asarray(rec_next["ym"], dtype=float)
                    except (KeyError, TypeError, ValueError):
                        break

                    # ── Per-room open-window exclusion ───────────────────────
                    # A room whose window is open carries unmodelled
                    # air-exchange loss.  Rather than dropping the whole step
                    # (which would discard every *other* room too), we:
                    #   (1) pin the open room's air node to its measurement for
                    #       the duration of the interval (so the coupled
                    #       neighbours see the *true* boundary temperature, not a
                    #       model free-run that cools too slowly), with zero
                    #       parameter sensitivity since a pinned value is data,
                    #       not a function of θ; and
                    #   (2) drop that room's residual from the objective so its
                    #       open-window dynamics never bias C / R / α / R_ij.
                    # A room is excluded at step k→k+1 if its window was open at
                    # either endpoint (the prediction is then built from a pinned
                    # state and/or scored against an open-window measurement).
                    open_k = rec_k.get("window_open")
                    open_next = rec_next.get("window_open")
                    pin_idx = (
                        np.where(open_k)[0]
                        if open_k is not None and open_k.any()
                        else _EMPTY_IDX
                    )
                    if open_k is None and open_next is None:
                        drop_idx = _EMPTY_IDX
                    else:
                        drop_mask = np.zeros(n, dtype=bool)
                        if open_k is not None:
                            drop_mask |= open_k
                        if open_next is not None:
                            drop_mask |= open_next
                        drop_idx = np.where(drop_mask)[0]

                    t_k = rec_k.get("t")
                    t_next = rec_next.get("t")
                    if t_k is not None and t_next is not None:
                        actual_dt = float(t_next) - float(t_k)
                        if actual_dt <= 0.0:
                            actual_dt = nominal_dt
                    else:
                        actual_dt = nominal_dt
                    h_sub = actual_dt / n_sub

                    valid = True
                    for _ in range(n_sub):
                        try:
                            f_val = model.f(x, u_k, d_k, _p0, 0.0)
                            F_full = model.dfdx(x, u_k, d_k, _p0, 0.0)
                        except Exception:
                            valid = False
                            break

                        # Linearly-implicit (backward) Euler step.  The 2R2C
                        # air node has a ~300 s time constant, so an *explicit*
                        # Euler step at the 900 s sampling interval (h/τ ≈ 3)
                        # sits past the |1+hλ|<1 stability limit and amplifies
                        # the air mode by ~2× per step — the trajectory (and its
                        # sensitivity) diverged, corrupting the objective and
                        # gradient that drive the optimiser.  Backward Euler is
                        # L-stable, matching the live CD-EKF's implicit-Euler
                        # propagation.  The heat input does not depend on the
                        # state, so f is affine in x within the ZOH step
                        # (f(y)=F·y+c, F constant in x) and one linear solve is
                        # exact:
                        #     (I − hF) x⁺ = x + h·c,      c = f(x) − F·x
                        M = _I - h_sub * F_full
                        try:
                            c_aff = f_val - F_full @ x
                            x_new = np.linalg.solve(M, x + h_sub * c_aff)
                            # Forward-sensitivity for backward Euler:
                            #     (I − hF) sx⁺ = sx + h·∂f/∂θ|_{x⁺}
                            # ∂f/∂θ must be evaluated at the *post-step* state so
                            # the analytic sensitivity matches the exact
                            # derivative of the implicit trajectory (it depends
                            # on x through f ∝ 1/C and the matrix reshaping by
                            # the envelope-split fractions).
                            f_new = model.f(x_new, u_k, d_k, _p0, 0.0)
                            dfdtheta_val = self._dfdtheta_step(
                                quants, layout, model, f_new, x_new, u_k, d_k,
                                ntheta, nx,
                            )
                            sx = np.linalg.solve(
                                M, (sx + h_sub * dfdtheta_val).T
                            ).T
                            x = x_new
                            # Hold each open room's air node at its measurement
                            # across the ZOH interval so the coupled neighbours
                            # integrate against the real (cooling) boundary
                            # temperature.  The pinned value is data ⇒ its
                            # parameter sensitivity is zero.  The wall node is left
                            # to evolve, driven by the pinned air via R_aw.
                            if pin_idx.size:
                                x[pin_idx] = ym_k[pin_idx]
                                sx[:, pin_idx] = 0.0
                        except np.linalg.LinAlgError:
                            valid = False
                            break

                    if (not valid or not np.all(np.isfinite(x))
                            or not np.all(np.isfinite(sx))):
                        # Overflow/divergence at an extreme trial point (e.g.
                        # during a line search).  Mark the whole evaluation
                        # infeasible so the optimiser backs off rather than
                        # ingesting a non-finite gradient.
                        return _SENTINEL, _zero_grad.copy()

                    residual = ym_next - x[:n]  # shape (n,)
                    # Drop open-window rooms: zeroing the residual removes the
                    # room from both the SSE and the gradient (which is
                    # sx[:, :n] @ residual) at this step.
                    if drop_idx.size:
                        residual[drop_idx] = 0.0
                    total_sse += float(np.dot(residual, residual))
                    # ∂||residual||²/∂θ_i = -2 * sx[i, :n] · residual
                    total_grad -= 2.0 * (sx[:, :n] @ residual)
                    n_steps_used += 1

        if n_steps_used == 0:
            return _SENTINEL, _zero_grad.copy()

        # Noise-weighted *sum* of squared residuals (a proper Gaussian
        # data-misfit term), divided only by the number of rooms.
        #
        # We deliberately do NOT average over the number of steps.  Averaging
        # made the objective's curvature independent of the dataset length,
        # so the data could never out-vote the O(1) Gaussian prior no matter
        # how many observations were collected — the estimates stayed pinned
        # to the configured prior (manifesting as a poorly-fit open-loop with
        # the wrong gains).  Summing over steps and weighting by the
        # measurement variance ``R_var`` puts the data term on the same
        # footing as the prior in :meth:`_compute_regularization`, so the
        # data correctly dominates once enough informative samples exist
        # while the prior still stabilises directions the data cannot
        # constrain.
        scale = float(n * self._R_var)
        mse = total_sse / scale
        grad = total_grad / scale
        if not (np.isfinite(mse) and np.all(np.isfinite(grad))):
            return _SENTINEL, _zero_grad.copy()
        return mse, grad

    def _cd_ped_neg_ll_and_grad(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
        std_history: List[Dict[str, np.ndarray]],
        x0: np.ndarray,
        P0: np.ndarray,
    ) -> Tuple[float, np.ndarray]:
        """
        Compute the CD-EKF PED negative log-likelihood **and** its gradient
        w.r.t. ``theta`` in a single forward-sensitivity pass.

        The likelihood is

            neg_ll(θ) = ½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

        Sensitivities  sx_i = ∂x̂/∂θ_i  and  sP_i = ∂P/∂θ_i  are propagated
        alongside the state and covariance through the Euler prediction steps.
        At each measurement the innovation contributes to both the objective and
        the gradient via

            ∂(neg_ll_k)/∂θ_i = ½ tr(M sP_i) − q · sx_i − ½ q · sP_i q

        where  M = Hᵀ S⁻¹ H  and  q = Hᵀ S⁻¹ ν.

        Returns
        -------
        neg_ll : float
        grad   : (ntheta,) ndarray – gradient of neg_ll only (regularisation
                 is added by the caller).
        """
        _SENTINEL = 1e10
        ntheta = len(theta)
        _zero_grad = np.zeros(ntheta)

        if not np.all(np.isfinite(theta)):
            return _SENTINEL, _zero_grad.copy()

        model = self._build_parametric_system(layout, theta)
        if model is None:
            return _SENTINEL, _zero_grad.copy()

        n = self._n
        nx = int(model.nx)     # 2n + m for 2R2C (no augmented offset states)
        n_sub = 1
        h_sub = self._dt / n_sub

        quants = self._theta_model_quantities(layout, theta)

        # ── Precompute dFdtheta (constant – doesn't depend on x/u/d) ───────
        dFdtheta = self._dFdtheta_const(quants, layout, model, ntheta, nx)

        # ── Measurement Jacobian H (constant for this model) ────────────────
        _u0 = np.zeros(model.nu)
        _d0 = np.zeros(model.nd)
        _p0 = model.params
        H = model.dhmdx(x0, _u0, _d0, _p0, 0.0)   # (nym, nx)
        Rm_mat = model.Rm                            # (nym, nym)

        # ── Initialise state and sensitivity arrays ──────────────────────────
        x = np.asarray(x0, dtype=float).copy()
        P = np.asarray(P0, dtype=float).copy()
        sx = np.zeros((ntheta, nx))      # ∂x̂/∂θ_i
        sP = np.zeros((ntheta, nx, nx))  # ∂P/∂θ_i (symmetric)

        neg_ll = 0.0
        grad = np.zeros(ntheta)

        for k in range(len(std_history) - 1):
            rec_k = std_history[k]
            rec_next = std_history[k + 1]

            try:
                u_k = np.asarray(rec_k["u"], dtype=float)
                d_k = np.asarray(rec_k["d"], dtype=float)
                ym_next = np.asarray(rec_next["ym"], dtype=float)
            except (KeyError, TypeError, ValueError):
                return _SENTINEL, _zero_grad.copy()

            # ── Euler prediction sub-steps ───────────────────────────────────
            for _ in range(n_sub):
                try:
                    f_val = model.f(x, u_k, d_k, _p0, 0.0)        # (nx,)
                    F_full = model.dfdx(x, u_k, d_k, _p0, 0.0)    # (nx, nx)
                    G_sig = model.sigma(x, u_k, d_k, _p0, 0.0)    # (nx, nw)
                except Exception:
                    return _SENTINEL, _zero_grad.copy()

                dfdtheta_val = self._dfdtheta_step(
                    quants, layout, model, f_val, x, u_k, d_k, ntheta, nx,
                )

                # ── Propagate state and covariance (Euler) ──────────────────
                P_dot = F_full @ P + P @ F_full.T + G_sig @ G_sig.T

                # ── Propagate sensitivities (Euler) ─────────────────────────
                # sx_dot[i] = F_full @ sx[i] + dfdtheta[i]
                # Vectorised: sx @ F_full.T gives (F_full @ sx[i])^T per row
                sx_dot = sx @ F_full.T + dfdtheta_val  # (ntheta, nx)

                # sP_dot[i] = F sP[i] + sP[i] Fᵀ + dFdtheta[i] P + P dFdtheta[i]ᵀ
                # Since sP[i] and P are symmetric:
                #   sP[i] Fᵀ = (F sP[i])ᵀ   →  FsP + FsP.T(axes)
                #   P dFdtheta[i]ᵀ = (dFdtheta[i] P)ᵀ  →  dFP + dFP.T(axes)
                FsP = np.einsum("ab,ibc->iac", F_full, sP)   # F @ sP[i]
                dFP = np.einsum("iab,bc->iac", dFdtheta, P)  # dFdtheta[i] @ P
                sP_dot = FsP + FsP.transpose(0, 2, 1) + dFP + dFP.transpose(0, 2, 1)

                x = x + h_sub * f_val
                P = P + h_sub * P_dot
                P = (P + P.T) * 0.5
                sx = sx + h_sub * sx_dot
                sP = sP + h_sub * sP_dot
                sP = (sP + sP.transpose(0, 2, 1)) * 0.5

            if not (np.all(np.isfinite(x)) and np.all(np.isfinite(P))):
                return _SENTINEL, _zero_grad.copy()

            # ── Innovation step ──────────────────────────────────────────────
            try:
                y_hat = model.hm(x, u_k, d_k, _p0, 0.0)
            except Exception:
                return _SENTINEL, _zero_grad.copy()

            nu = ym_next - y_hat
            S = H @ P @ H.T + Rm_mat

            try:
                sign, logdet = np.linalg.slogdet(S)
                if sign <= 0:
                    return _SENTINEL, _zero_grad.copy()
                S_inv = np.linalg.inv(S)
            except np.linalg.LinAlgError:
                return _SENTINEL, _zero_grad.copy()

            S_inv_nu = S_inv @ nu
            neg_ll += 0.5 * (logdet + float(nu @ S_inv_nu))
            if not np.isfinite(neg_ll):
                return _SENTINEL, _zero_grad.copy()

            # ── Gradient contributions ───────────────────────────────────────
            # q = Hᵀ S⁻¹ ν   (nx,)
            # M = Hᵀ S⁻¹ H   (nx, nx)
            # ∂(neg_ll_k)/∂θ_i = ½ tr(M sP_i) − q·sx_i − ½ q·sP_i·q
            q = H.T @ S_inv_nu   # (nx,)
            M = H.T @ S_inv @ H  # (nx, nx)

            # Vectorised over all parameters:
            #   tr(M sP_i) = sum(M * sP_i)   [element-wise then sum]
            #   q · sx_i = sx @ q
            #   q · sP_i · q = einsum('i,iab,b', q, sP, q) == q @ sP[i] @ q per i
            grad += (
                0.5 * np.einsum("ab,iab->i", M, sP)
                - sx @ q
                - 0.5 * np.einsum("a,iab,b->i", q, sP, q)
            )

            # ── Kalman update ────────────────────────────────────────────────
            K = P @ H.T @ S_inv          # (nx, nym)
            Phi = np.eye(nx) - K @ H     # (nx, nx)
            P_minus = P.copy()

            x = x + K @ nu
            P = Phi @ P_minus @ Phi.T + K @ Rm_mat @ K.T
            P = (P + P.T) * 0.5

            # ── Sensitivity update through Kalman correction ─────────────────
            # sx[i]⁺ = Φ (sx[i] + sP[i] Hᵀ S⁻¹ ν) = Φ (sx[i] + sP[i] q)
            sx_aug = sx + np.einsum("iab,b->ia", sP, q)   # (ntheta, nx)
            sx = np.einsum("ab,ib->ia", Phi, sx_aug)       # Φ @ sx_aug[i]

            # J_i = Φ sP[i] Hᵀ S⁻¹ = ∂K/∂θ_i   (nx, nym) per i
            Phi_sP = np.einsum("ab,ibc->iac", Phi, sP)              # (ntheta, nx, nx)
            Phi_sP_Ht = np.einsum("iac,dc->iad", Phi_sP, H)         # (ntheta, nx, nym)
            J = np.einsum("iad,de->iae", Phi_sP_Ht, S_inv)          # (ntheta, nx, nym)

            # sP[i]⁺ = Φ sP[i] Φᵀ − J_i H P⁻ Φᵀ − Φ P⁻ Hᵀ J_iᵀ + J_i Rm Kᵀ + K Rm J_iᵀ
            HP_m = H @ P_minus                                        # (nym, nx)
            JHP = np.einsum("iad,db->iab", J, HP_m)                  # (ntheta, nx, nx)
            term2 = np.einsum("iab,cb->iac", JHP, Phi)               # J H P⁻ Φᵀ
            JRm = np.einsum("iad,de->iae", J, Rm_mat)                # (ntheta, nx, nym)
            term4 = np.einsum("iad,bd->iab", JRm, K)                 # J Rm Kᵀ
            sP = (
                np.einsum("iab,cb->iac", Phi_sP, Phi)  # Φ sP[i] Φᵀ
                - term2                                  # − J H P⁻ Φᵀ
                - term2.transpose(0, 2, 1)               # − Φ P⁻ Hᵀ Jᵀ
                + term4                                  # + J Rm Kᵀ
                + term4.transpose(0, 2, 1)               # + K Rm Jᵀ
            )
            sP = (sP + sP.transpose(0, 2, 1)) * 0.5

        return neg_ll, grad

    def _compute_regularization_gradient(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
        identifiable_pairs: List[Tuple[int, int]],
    ) -> np.ndarray:
        return _compute_regularization_gradient(
            self, theta, layout, identifiable_pairs,
        )

    def _build_rooms_from_theta(
        self,
        layout: _ThetaLayout,
        log_mass: np.ndarray,
        log_r: np.ndarray,
        log_r_ij_map: Optional[Dict[Tuple[int, int], float]],
        log_solar: Optional[np.ndarray] = None,
        c_air: Optional[np.ndarray] = None,
        r_aw: Optional[np.ndarray] = None,
    ) -> List[Room]:
        return _build_rooms_from_theta(
            self, layout, log_mass, log_r, log_r_ij_map,
            log_solar=log_solar, c_air=c_air, r_aw=r_aw,
        )

    def _build_system(
        self,
        log_masses: np.ndarray,
        log_rs: np.ndarray,
        log_r_ij: Optional[Dict[Tuple[int, int], float]] = None,
    ) -> Optional[HouseThermalSystem]:
        return _build_system(self, log_masses, log_rs, log_r_ij)

    def _build_parametric_system(
        self,
        layout: _ThetaLayout,
        theta: np.ndarray,
    ) -> Optional[HouseThermalSystem]:
        return _build_parametric_system(self, layout, theta)

    def _convert_history_std(
        self,
        history: List[Dict[str, Any]],
        use_ym: bool = False,
    ) -> List[Dict[str, np.ndarray]]:
        return convert_history_std(
            history,
            self._n,
            self._n_u,
            self._room_names,
            use_ym=use_ym,
        )

    def _compute_regularization(
        self,
        log_mass: np.ndarray,
        log_r: np.ndarray,
        q_int: np.ndarray,
        log_alpha: np.ndarray,
        log_r_ij: np.ndarray,
        identifiable_pairs: List[Tuple[int, int]],
        identifiable_sources: Optional[List[int]] = None,
    ) -> float:
        return _compute_regularization(
            self, log_mass, log_r, q_int, log_alpha, log_r_ij,
            identifiable_pairs, identifiable_sources,
        )

    def _compute_regularization_theta(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
    ) -> float:
        return _compute_regularization_theta(self, theta, layout)

    def _initial_state_and_covariance(
        self,
        system: HouseThermalSystem,
        first_measurement: np.ndarray,
        u: Optional[np.ndarray] = None,
        d: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        return _initial_state_and_covariance(
            self, system, first_measurement, u=u, d=d,
        )
