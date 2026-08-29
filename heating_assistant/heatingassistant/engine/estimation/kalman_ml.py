"""Kalman-filter / simulation-MSE grey-box parameter estimator."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..controller import HouseThermalSDE as HouseThermalSystem
from ..heat_sources import HeatSource
from ..thermal_model import Room
from mbc.control import ScipyNLPBackend
from mbc.identification import cd_ped_neg_log_likelihood as _cd_ped_neg_ll
from .constants import (
    MIN_HISTORY_STEPS,
    _ALPHA_PRIOR_WEIGHT,
    _ALPHA_PRIOR_WEIGHT_EXCITED,
    _C_AIR_HI,
    _C_AIR_LO,
    _LOG_ALPHA_HI,
    _LOG_ALPHA_LO,
    _LOG_R_HI,
    _LOG_R_IJ_HI,
    _LOG_R_IJ_LO,
    _LOG_R_LO,
    _LOG_SOLAR_HI,
    _LOG_SOLAR_LO,
    _MASS_PRIOR_WEIGHT,
    _MIN_HISTORY_TIME_S,
    _MIN_SEGMENT_TIME_S,
    _Q_INT_HI,
    _Q_INT_LO,
    _R_AW_HI,
    _R_AW_LO,
    _T_WALL_HI,
    _T_WALL_LO,
    _UA_OPEN_HI,
    _UA_OPEN_LO,
    _log_mass_bounds,
    _nelder_mead,
)
from .history_std import convert_history_std
from .identifiability import (
    _adaptive_alpha_prior_weight,
    _adaptive_mass_prior_weight,
    _check_identifiable_connections,
    _check_identifiable_open_ua,
    _check_identifiable_sources,
    _check_identifiable_solar,
    _identifiable_split_rooms,
)
from .model_build import (
    _build_parametric_system,
    _build_rooms_from_theta,
    _build_system,
    _theta_model_quantities,
)
from .regularization import (
    _compute_regularization,
    _compute_regularization_gradient,
    _compute_regularization_theta,
)
from .sensitivity import (
    _cd_ped_neg_ll_and_grad,
    _dFdtheta_const,
    _dfdtheta_step,
    _simulation_mse_and_grad,
)
from .nlp_eval import RegularizedMseCache, WallInitMseCache, solve_lbfgs
from .theta_layout import _ThetaLayout
from .warmstart import (
    _initial_state_and_covariance,
    _physics_informed_theta,
    _pin_locked_params,
    _update_wall_init_prior_from_history,
)

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
    the grey-box model over the history window), minimised by SciPy
    L-BFGS-B with analytical gradients from a forward-sensitivity
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
        self._ua_open_prior_full = np.array(
            [max(0.0, float(getattr(r, "ua_open", 0.0))) for r in rooms]
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
        self._mass_prior_weight: float = _MASS_PRIOR_WEIGHT

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
        using a single joint optimisation with multistart SciPy L-BFGS-B and
        analytical gradients.

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
        self._mass_prior_weight = _adaptive_mass_prior_weight(
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
                "estimated_ua_open": {
                    name: 0.0 for name in self._room_names
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
        identifiable_ua = _check_identifiable_open_ua(
            history, self._room_names, self._min_segment_steps,
        )

        # One t_wall_init block per distinct dataset start timestamp (minimum 1).
        n_wall_segs = max(1, len(dataset_start_timestamps)) if dataset_start_timestamps else 1

        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=identifiable_sources,
            identifiable_pairs=identifiable_pairs,
            identifiable_solar=identifiable_solar,
            identifiable_splits=identifiable_splits,
            identifiable_ua=identifiable_ua,
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
        ua_open_prior = np.array([
            self._ua_open_prior_full[i] for i in identifiable_ua
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
            ua_open_prior,
        ])

        # ── Build per-parameter bounds ─────────────────────────────────────
        n = self._n
        bounds: List[Tuple[float, float]] = (
            [_log_mass_bounds(float(self._log_mass_prior[i])) for i in range(n)]
            + [(_LOG_R_LO, _LOG_R_HI)] * n
            + [(_Q_INT_LO, _Q_INT_HI)] * n
            + [(_T_WALL_LO, _T_WALL_HI)] * (n * n_wall_segs)
            + [(_LOG_ALPHA_LO, _LOG_ALPHA_HI)] * len(identifiable_sources)
            + [(_LOG_R_IJ_LO, _LOG_R_IJ_HI)] * len(identifiable_pairs)
            + [(_LOG_SOLAR_LO, _LOG_SOLAR_HI)] * len(identifiable_solar)
            + [(_C_AIR_LO, _C_AIR_HI)] * len(identifiable_splits)
            + [(_R_AW_LO, _R_AW_HI)] * len(identifiable_splits)
            + [(_UA_OPEN_LO, _UA_OPEN_HI)] * len(identifiable_ua)
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

        mse_cache = RegularizedMseCache(
            self,
            layout,
            std_history,
            identifiable_pairs,
            dataset_start_timestamps,
        )
        lb = np.array([lo for lo, _ in bounds])
        ub = np.array([hi for _, hi in bounds])

        scipy_backend = ScipyNLPBackend(
            # L-BFGS-B builds a quasi-Newton Hessian approximation that
            # handles the large scale differences between parameters (e.g.
            # log_mass gradient is O(1) while q_int gradient is O(1e-4)),
            # giving much better convergence than pure-gradient SLSQP.
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-6},
        )

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
        # scale, whose rated power is genuinely known a priori, and on log C
        # toward the selected room size — is what keeps the chosen optimum
        # off the degenerate ridge; the second start simply
        # guards against the physics-informed seed landing in a bad basin.
        phys_theta = self._physics_informed_theta(
            std_history, layout, theta_prior, lb, ub,
        )
        starts: List[np.ndarray] = []
        if phys_theta is not None:
            starts.append(phys_theta)
        starts.append(theta_prior.copy())

        for theta_start in starts:
            out = solve_lbfgs(
                mse_cache.fun,
                mse_cache.jac,
                theta_start,
                lb,
                ub,
                invalidate=mse_cache.invalidate,
                backend=scipy_backend,
            )
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
        log_mass = np.array([
            float(np.clip(log_mass[i], bounds[i][0], bounds[i][1]))
            for i in range(self._n)
        ], dtype=float)
        log_r = np.clip(log_r, _LOG_R_LO, _LOG_R_HI)
        q_int = np.clip(q_int, _Q_INT_LO, _Q_INT_HI)
        t_wall_init = np.clip(t_wall_init, _T_WALL_LO, _T_WALL_HI)
        log_alpha = np.clip(log_alpha, _LOG_ALPHA_LO, _LOG_ALPHA_HI)
        log_r_ij = np.clip(log_r_ij, _LOG_R_IJ_LO, _LOG_R_IJ_HI)
        log_solar = np.clip(log_solar, _LOG_SOLAR_LO, _LOG_SOLAR_HI)
        c_air = np.clip(c_air, _C_AIR_LO, _C_AIR_HI)
        r_aw = np.clip(r_aw, _R_AW_LO, _R_AW_HI)
        ua_open = np.clip(layout.get_ua_open(best_theta), _UA_OPEN_LO, _UA_OPEN_HI)

        # Build result dict ------------------------------------------------
        estimated_params: Dict[str, Dict[str, Any]] = {}
        estimated_internal_gains: Dict[str, float] = {}
        for i, name in enumerate(self._room_names):
            estimated_params[name] = {
                "thermal_mass": round(float(math.exp(log_mass[i])), 0),
                "r_external": round(float(math.exp(log_r[i])), 6),
            }
            estimated_internal_gains[name] = round(float(q_int[i]), 2)

        estimated_ua_open: Dict[str, float] = {
            name: 0.0 for name in self._room_names
        }
        for k, i in enumerate(identifiable_ua):
            estimated_ua_open[self._room_names[i]] = round(float(ua_open[k]), 3)

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
        if identifiable_ua:
            msg_parts.append(
                f"Open-contact UA estimated for {len(identifiable_ua)} room(s)."
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
            "estimated_ua_open": estimated_ua_open,
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
            "identifiable_ua_rooms": [
                self._room_names[i] for i in identifiable_ua
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
        *,
        prior_mean: str = "midpoint",
        min_lam: Optional[float] = None,
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

        ``prior_mean`` ``"air"`` seeds the MAP prior at the first measured
        air temperature (diagnostic simulate).  The default ``"midpoint"``
        keeps the PE prior (air/outdoor blend).

        ``min_lam`` overrides ``_T_WALL_MIN_LAM`` so diagnostic simulate can
        let the window pull Tw0 farther from the prior.

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
        if prior_mean == "air":
            first_y = list((fit_history[0].get("y") if fit_history else []) or [])
            for i in range(n):
                if i >= len(first_y):
                    continue
                try:
                    self._t_wall_init_prior[i] = float(first_y[i])
                except (TypeError, ValueError):
                    pass

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

        wall_cache = WallInitMseCache(self, layout, std_history, min_lam)

        lb = np.array([lo for lo, _ in bounds])
        ub = np.array([hi for _, hi in bounds])

        try:
            from scipy.optimize import minimize as _sp_minimize
            res = _sp_minimize(
                wall_cache.fun, theta_prior,
                jac=wall_cache.jac,
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
        ua_coeff: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        return _dfdtheta_step(
            self, q, layout, model, f_val, x, u_k, d_k, ntheta, nx,
            ua_coeff=ua_coeff,
        )

    def _dFdtheta_const(
        self,
        q: Dict[str, np.ndarray],
        layout: "_ThetaLayout",
        model: HouseThermalSystem,
        ntheta: int,
        nx: int,
    ) -> np.ndarray:
        return _dFdtheta_const(self, q, layout, model, ntheta, nx)

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
        return _simulation_mse_and_grad(
            self, theta, layout, std_history, nominal_dt,
            max_gap_factor=max_gap_factor,
            min_segment_steps=min_segment_steps,
            max_window_steps=max_window_steps,
            dataset_start_ts=dataset_start_ts,
        )

    def _cd_ped_neg_ll_and_grad(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
        std_history: List[Dict[str, np.ndarray]],
        x0: np.ndarray,
        P0: np.ndarray,
    ) -> Tuple[float, np.ndarray]:
        return _cd_ped_neg_ll_and_grad(
            self, theta, layout, std_history, x0, P0,
        )

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
