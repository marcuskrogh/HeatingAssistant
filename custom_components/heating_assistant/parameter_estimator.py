"""
Maximum-likelihood thermal parameter estimation.

Estimates a *complete* set of grey-box parameters jointly:

    Per room  i: thermal mass C_i [J/K], external resistance R_i,ext [K/W],
                 constant internal heat gain Q_int,i [W]
    Per source s: power-scale α_s (heater-power miscalibration correction)
    Per pair (i,j): inter-room resistance R_ij [K/W] (when identifiable)

The estimator maximises the prediction-error decomposition (PED) Gaussian
log-likelihood

    log L(θ) = -½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

where νₖ = yₖ − A x̂ₖ₋₁ − B (α ⊙ u_{k-1}) − E (d_{k-1} + Q_int)
      Sₖ = Pₖ⁻ + R   (since C = I)

The Kalman filter is evaluated forward through the accumulated data history
for each candidate parameter set.  All positive parameters are log-transformed
to guarantee positivity and improve numerical conditioning; the unbounded
internal-gain parameters Q_int are kept in linear space.  A Gaussian
regularisation term shrinks each parameter toward its prior (the configured
value or zero) when data quality is poor.

Identifiability gates
---------------------
The optimiser only includes a parameter when the data actually identify it:

* α_s     — included only when std(u_s) ≥ _MIN_HEATER_USAGE_STD
* R_ij    — included only when std(T_i − T_j) ≥ _MIN_TEMP_DIFF_STD
* Q_int,i — always included (always identifiable from the steady-state
            energy balance jointly with C, R_ext)

A multi-start IPOPT run is started from the prior plus a few small random
perturbations; the run with the best (lowest) negative log-likelihood is
returned.  Analytical gradients of the CD-EKF PED log-likelihood are
supplied via a forward-sensitivity pass, giving IPOPT exact first-order
information and dramatically reducing the number of function evaluations
compared to Nelder–Mead.  The IPOPT call is routed through mbc's
``IpoptNLPBackend`` — the same backend the MPC controller uses for OCP
solves — so identification and control share a single IPOPT integration.
"""

from __future__ import annotations

import copy
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .controller import HouseThermalSDE as HouseThermalSystem
from .heat_sources import HeatSource
from .thermal_model import HouseModel, Room
from .mbc.control import IpoptNLPBackend, NLPProblem, ScipyNLPBackend
from .mbc.identification import cd_ped_neg_log_likelihood as _cd_ped_neg_ll
from .mbc.identification import nelder_mead as _nelder_mead  # for test compatibility

_LOGGER = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

#: Minimum number of history steps before attempting estimation.
#: 60 steps ≈ 1 hour at the default 60 s sampling interval — the
#: minimum needed to see meaningful thermal dynamics.
MIN_HISTORY_STEPS = 60

#: Log-space parameter bounds (hard limits).
_LOG_MASS_LO = math.log(1e4)    # ~10 kJ/K
_LOG_MASS_HI = math.log(5e8)    # ~500 MJ/K
_LOG_R_LO = math.log(1e-5)      # 0.00001 K/W
_LOG_R_HI = math.log(10.0)      # 10 K/W

#: Log-space bounds for inter-room resistances.
_LOG_R_IJ_LO = math.log(1e-4)   # 0.0001 K/W (very thin interior partition)
_LOG_R_IJ_HI = math.log(5.0)    # 5 K/W (well-insulated internal wall)

#: Linear bounds for per-room internal heat gain [W].
_Q_INT_LO = -2_000.0   # allow small negative to absorb model bias
_Q_INT_HI =  5_000.0   # large internal source (server room, sauna…)

#: Log-space bounds for heater power-scale α (multiplicative on max_power).
_LOG_ALPHA_LO = math.log(0.3)   # 30 % of rated
_LOG_ALPHA_HI = math.log(3.0)   # 300 % of rated

#: Minimum std of inter-room temperature difference for R_ij identifiability.
_MIN_TEMP_DIFF_STD = 0.3   # °C

#: Minimum std of source duty-cycle for α_s identifiability.
_MIN_HEATER_USAGE_STD = 0.05

#: Number of random restarts in multistart Nelder–Mead.
_N_RESTARTS = 3

#: Standard deviation of the random log-space perturbation between restarts.
_RESTART_PERT = 0.5


# ── Nelder–Mead — now provided by mbc.identification ─────────────────────────
# _nelder_mead is re-exported above from mbc.identification._nelder_mead for
# backward compatibility with callers that import it from this module.


# ── Identifiability gates ────────────────────────────────────────────────────


def _check_identifiable_connections(
    history: List[Dict[str, Any]],
    room_names: List[str],
    connections: List[Tuple[int, int]],
    min_std: float = _MIN_TEMP_DIFF_STD,
) -> List[Tuple[int, int]]:
    """
    Return the subset of room-index pairs (i, j) for which the inter-room
    temperature difference has sufficient variance for R_ij to be identifiable.
    """
    if len(history) < MIN_HISTORY_STEPS:
        return []

    identifiable = []
    for i, j in connections:
        diffs = []
        for record in history:
            y = record.get("y", [])
            if i < len(y) and j < len(y):
                diffs.append(float(y[i]) - float(y[j]))
        if len(diffs) >= MIN_HISTORY_STEPS and float(np.std(diffs)) > min_std:
            identifiable.append((i, j))
    return identifiable


def _check_identifiable_sources(
    history: List[Dict[str, Any]],
    n_sources: int,
    min_std: float = _MIN_HEATER_USAGE_STD,
) -> List[int]:
    """
    Return the indices of heat sources whose duty-cycle ``u`` shows enough
    variation for the power-scale parameter α_s to be identifiable.
    """
    if len(history) < MIN_HISTORY_STEPS:
        return []

    identifiable = []
    for s in range(n_sources):
        u_vals = []
        for record in history:
            u = record.get("u", [])
            if s < len(u):
                u_vals.append(float(u[s]))
        if len(u_vals) >= MIN_HISTORY_STEPS and float(np.std(u_vals)) > min_std:
            identifiable.append(s)
    return identifiable


# ── Main estimator class ─────────────────────────────────────────────────────


class _ThetaLayout:
    """
    Index layout of the packed parameter vector ``θ``:

        [ log_mass_1..n
          log_r_ext_1..n
          q_int_1..n
          log_alpha_{s_k} for s_k in identifiable_sources
          log_r_ij_{p_k} for p_k in identifiable_pairs ]
    """

    def __init__(
        self,
        n_rooms: int,
        identifiable_sources: List[int],
        identifiable_pairs: List[Tuple[int, int]],
    ) -> None:
        self.n_rooms = n_rooms
        self.identifiable_sources = list(identifiable_sources)
        self.identifiable_pairs = list(identifiable_pairs)

        n = n_rooms
        self.idx_log_mass = (0, n)
        self.idx_log_r = (n, 2 * n)
        self.idx_q_int = (2 * n, 3 * n)

        off = 3 * n
        self.idx_log_alpha = (off, off + len(identifiable_sources))
        off = self.idx_log_alpha[1]
        self.idx_log_r_ij = (off, off + len(identifiable_pairs))

        self.size = self.idx_log_r_ij[1]

    def unpack(self, theta: np.ndarray):
        a, b = self.idx_log_mass
        log_mass = theta[a:b]
        a, b = self.idx_log_r
        log_r = theta[a:b]
        a, b = self.idx_q_int
        q_int = theta[a:b]
        a, b = self.idx_log_alpha
        log_alpha = theta[a:b]
        a, b = self.idx_log_r_ij
        log_r_ij = theta[a:b]
        return log_mass, log_r, q_int, log_alpha, log_r_ij


class KalmanMLEstimator:
    """
    Maximum-likelihood estimator for grey-box thermal model parameters.

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

    A multi-start IPOPT run is started from the prior and a few random
    perturbations; the lowest-objective restart is returned.  Analytical
    gradients of the CD-EKF PED log-likelihood are computed via a
    forward-sensitivity pass and supplied directly to IPOPT.

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
        Diagonal process-noise variance used in the Kalman filter [°C²].
    R_var : float
        Diagonal measurement-noise variance [°C²].
    regularization : float
        Weight of the Gaussian prior shrinking the solution toward the
        current configured values (and toward zero for ``q_int`` /
        unit-scale α).  Set to 0.0 to disable.
    """

    def __init__(
        self,
        rooms: List[Room],
        sources: List[HeatSource],
        dt: float,
        Q_var: float = 0.01,
        R_var: float = 0.25,
        regularization: float = 1.0,
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
        # (= 12 h at the 900 s sampling interval).
        self._max_window_steps = int(max(20, max_window_steps))

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
        if len(history) < MIN_HISTORY_STEPS:
            return None

        # Build a minimal layout with no identifiable sources/pairs for the prior evaluation
        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=[],
            identifiable_pairs=[],
        )

        # Theta is just [log_mass, log_r, q_int] at the prior
        theta_prior = np.concatenate([
            self._log_mass_prior,
            self._log_r_prior,
            self._q_int_prior,
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
        x0, P0 = self._initial_state_and_covariance(system0, std_history[0]["ym"])

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
        if len(history) < MIN_HISTORY_STEPS:
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
        ])

        center_log_mass = float(self._log_mass_prior[room_idx])
        center_log_r = float(self._log_r_prior[room_idx])

        def _model_factory(theta: np.ndarray):
            return self._build_parametric_system(layout, theta)

        std_history = self._convert_history_std(history, use_ym=True)
        system0 = _model_factory(theta_prior)
        if system0 is None:
            return None
        x0, P0 = self._initial_state_and_covariance(system0, std_history[0]["ym"])

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

    def estimate(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Estimate all identifiable thermal parameters from the history buffer
        using a single joint optimisation with multistart IPOPT and analytical
        gradients.
        """
        n_steps = len(history)
        current = {
            r.name: {
                "thermal_mass": r.thermal_mass,
                "r_external": r.r_external,
                "internal_gain": float(getattr(r, "internal_gain", 0.0)),
            }
            for r in self._rooms
        }

        if n_steps < MIN_HISTORY_STEPS:
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
                "message": (
                    f"Insufficient data: {n_steps} steps available, "
                    f"need ≥ {MIN_HISTORY_STEPS}.  Keep the system running "
                    "and try again when more observations have been collected."
                ),
            }

        # ── Identifiability gates ───────────────────────────────────────────
        identifiable_pairs = _check_identifiable_connections(
            history, self._room_names, self._connection_pairs
        )
        identifiable_sources = _check_identifiable_sources(
            history, self._n_u
        )

        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=identifiable_sources,
            identifiable_pairs=identifiable_pairs,
        )

        # ── Build initial point and priors for the joint vector ────────────
        log_alpha_prior = np.array([
            self._log_alpha_prior_full[s] for s in identifiable_sources
        ])
        log_r_ij_prior = np.array([
            self._connection_r_priors[self._connection_pairs.index(p)]
            for p in identifiable_pairs
        ])
        theta_prior = np.concatenate([
            self._log_mass_prior,
            self._log_r_prior,
            self._q_int_prior,
            log_alpha_prior,
            log_r_ij_prior,
        ])

        # ── Build per-parameter bounds ─────────────────────────────────────
        n = self._n
        bounds: List[Tuple[float, float]] = (
            [(_LOG_MASS_LO, _LOG_MASS_HI)] * n
            + [(_LOG_R_LO, _LOG_R_HI)] * n
            + [(_Q_INT_LO, _Q_INT_HI)] * n
            + [(_LOG_ALPHA_LO, _LOG_ALPHA_HI)] * len(identifiable_sources)
            + [(_LOG_R_IJ_LO, _LOG_R_IJ_HI)] * len(identifiable_pairs)
        )

        # ── Convert history (carries timestamps for gap detection) ────────
        std_history = self._convert_history_std(history, use_ym=True)

        # ── Build regularisation function ──────────────────────────────────
        def _regularization_fn(theta: np.ndarray) -> float:
            lm, lr, qi, la, lrij = layout.unpack(theta)
            return self._compute_regularization(
                lm, lr, qi, la, lrij, layout.identifiable_pairs,
                layout.identifiable_sources,
            )

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

        # ── Choose a single starting point ─────────────────────────────────
        # Prefer the physics-informed start (a coarse least-squares fit of the
        # per-room 1R1C energy balance) — it places the search in a basin
        # consistent with the data even when the configured prior is far off.
        # Fall back to the prior when the LS fit fails or the data are too
        # sparse to constrain it.
        phys_theta = self._physics_informed_theta(
            std_history, layout, theta_prior, lb, ub,
        )
        theta_start = phys_theta if phys_theta is not None else theta_prior.copy()

        _cache[0] = None

        try:
            problem = NLPProblem(
                objective=_fun,
                objective_jac=_jac,
                x0=theta_start,
                lb=lb,
                ub=ub,
                constraints=(),
            )
            res = _active_backend.solve(problem)
            if np.isfinite(res.fun):
                best_f = float(res.fun)
                best_theta = np.asarray(res.x, dtype=float)
                best_converged = bool(res.success)
        except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
            _LOGGER.warning(
                "IPOPT backend unavailable for parameter estimation (%s); "
                "falling back to L-BFGS-B.",
                exc,
            )
            _active_backend = scipy_backend
            _cache[0] = None
            try:
                res = _active_backend.solve(problem)
                if np.isfinite(res.fun):
                    best_f = float(res.fun)
                    best_theta = np.asarray(res.x, dtype=float)
                    best_converged = bool(res.success)
            except Exception as exc2:
                _LOGGER.debug("Optimiser fallback failed: %s", exc2)
        except Exception as exc:
            _LOGGER.debug("Optimiser failed: %s", exc)

        # ── Unpack and clip the best solution ──────────────────────────────
        log_mass, log_r, q_int, log_alpha, log_r_ij = layout.unpack(best_theta)
        log_mass = np.clip(log_mass, _LOG_MASS_LO, _LOG_MASS_HI)
        log_r = np.clip(log_r, _LOG_R_LO, _LOG_R_HI)
        q_int = np.clip(q_int, _Q_INT_LO, _Q_INT_HI)
        log_alpha = np.clip(log_alpha, _LOG_ALPHA_LO, _LOG_ALPHA_HI)
        log_r_ij = np.clip(log_r_ij, _LOG_R_IJ_LO, _LOG_R_IJ_HI)

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

        # Report negative normalised MSE (higher → better fit).
        # Stored in the same "log_likelihood" field for dashboard compatibility;
        # the value is -MSE/room/step (not a true log-likelihood).
        try:
            if not np.isfinite(best_f):
                log_ll_val: Optional[float] = None
            else:
                reg = self._compute_regularization(
                    log_mass, log_r, q_int, log_alpha, log_r_ij,
                    identifiable_pairs, identifiable_sources,
                )
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
            msg_parts.append(
                f"Heater scale estimated for {len(identifiable_sources)} "
                "active source(s)."
            )
        else:
            msg_parts.append(
                "Heater scale not identifiable — heaters did not vary "
                "enough during the window."
            )
        if identifiable_pairs:
            msg_parts.append(
                f"Inter-room R estimated for {len(identifiable_pairs)} "
                "connection(s)."
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
            "identifiable_connections": identifiable_names,
            "identifiable_sources": identifiable_source_names,
            "stage2_converged": best_converged,
            "n_steps": n_steps,
            "log_likelihood": log_ll_val,
            "message": "  ".join(msg_parts),
        }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _physics_informed_theta(
        self,
        std_history: List[Dict[str, np.ndarray]],
        layout: "_ThetaLayout",
        theta_prior: np.ndarray,
        lb: np.ndarray,
        ub: np.ndarray,
        nominal_dt: Optional[float] = None,
    ) -> Optional[np.ndarray]:
        """Coarse, data-driven starting point for the multi-start optimiser.

        Performs an independent ordinary-least-squares fit of the lumped
        1R1C energy balance for each room

            C_i (dT_i/dt) = g_ext_i (T_out − T_i) + Q_i + q_int_i

        where ``Q_i`` is the known heat injection (heaters at the prior
        power-scale + solar) for room *i*.  Treating the inter-room coupling
        as part of the residual, each room yields a linear system in the
        unknowns ``[C_i, g_ext_i, q_int_i]`` solved by least squares over all
        consecutive sample pairs with a near-nominal time step.

        Only rooms with a well-conditioned fit and physically sensible
        (positive, in-bounds) ``C`` and ``g_ext`` adopt the data-driven
        values; every other room — and the heater-scale / inter-room-R
        blocks — keeps its prior.  Returns ``None`` when no room could be
        fit, so the caller simply falls back to the prior start.

        The result is a *starting point* only; the full nonlinear multi-step
        objective still refines it, so the coarseness of this fit (no
        inter-room term, explicit-Euler derivative) is acceptable.
        """
        n = self._n
        if len(std_history) < MIN_HISTORY_STEPS:
            return None
        dt_nom = float(nominal_dt) if nominal_dt else float(self._dt)

        # Per-room accumulators for the design matrix rows.
        rows: List[List[List[float]]] = [[] for _ in range(n)]
        targets: List[List[float]] = [[] for _ in range(n)]

        # Map each source to its room index and a thermal-power callable.
        src_room = [self._room_names.index(s.room) if s.room in self._room_names
                    else -1 for s in self._sources]

        for k in range(len(std_history) - 1):
            rec = std_history[k]
            rec_next = std_history[k + 1]
            try:
                T_k = np.asarray(rec["ym"], dtype=float)
                T_next = np.asarray(rec_next["ym"], dtype=float)
                u_k = np.asarray(rec["u"], dtype=float)
                d_k = np.asarray(rec["d"], dtype=float)
            except (KeyError, TypeError, ValueError):
                continue
            if T_k.size < n or T_next.size < n:
                continue

            t_k = rec.get("t")
            t_next = rec_next.get("t")
            if t_k is not None and t_next is not None:
                dt = float(t_next) - float(t_k)
                # Skip pairs straddling a gap (controller restart / pause).
                if not (0.5 * dt_nom <= dt <= 1.5 * dt_nom):
                    continue
            else:
                dt = dt_nom

            T_out = float(d_k[0]) if d_k.size > 0 else 0.0

            # Known heat injection per room: heaters (prior scale) + solar.
            Q = np.zeros(n)
            for j, src in enumerate(self._sources):
                i = src_room[j]
                if i < 0:
                    continue
                u_s = float(u_k[j]) if j < u_k.size else 0.0
                try:
                    Q[i] += float(src.thermal_power(max(0.0, u_s), T_out))
                except Exception:
                    pass
            for i in range(n):
                if 1 + i < d_k.size:
                    Q[i] += float(d_k[1 + i])  # solar gain slot

            for i in range(n):
                dTdt = (float(T_next[i]) - float(T_k[i])) / dt
                # C·dTdt − g·(T_out − T) − q_int = Q   →  unknown [C, g, q_int]
                rows[i].append([dTdt, -(T_out - float(T_k[i])), -1.0])
                targets[i].append(float(Q[i]))

        theta = theta_prior.copy()
        any_fit = False
        for i in range(n):
            A = np.asarray(rows[i], dtype=float)
            b = np.asarray(targets[i], dtype=float)
            if A.shape[0] < max(MIN_HISTORY_STEPS // 2, 10):
                continue
            # Require the regressors to actually vary, else the fit is
            # ill-posed (e.g. constant temperature / no excitation).
            if np.all(np.std(A, axis=0) < 1e-9):
                continue
            try:
                sol, _res, rank, _sv = np.linalg.lstsq(A, b, rcond=None)
            except np.linalg.LinAlgError:
                continue
            if rank < 3 or not np.all(np.isfinite(sol)):
                continue
            C_i, g_i, q_i = float(sol[0]), float(sol[1]), float(sol[2])
            if not (C_i > 0.0 and g_i > 0.0):
                continue  # unphysical — keep the prior for this room
            log_mass = float(np.clip(math.log(C_i), _LOG_MASS_LO, _LOG_MASS_HI))
            log_r = float(np.clip(math.log(1.0 / g_i), _LOG_R_LO, _LOG_R_HI))
            q_int = float(np.clip(q_i, _Q_INT_LO, _Q_INT_HI))
            theta[i] = log_mass
            theta[n + i] = log_r
            theta[2 * n + i] = q_int
            any_fit = True

        if not any_fit:
            return None
        return np.clip(theta, lb, ub)

    def _simulation_mse_and_grad(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
        std_history: List[Dict[str, Any]],
        nominal_dt: float,
        max_gap_factor: float = 1.5,
        min_segment_steps: int = 20,
        max_window_steps: int = 60,
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
        the sensitivity resets to zero at every segment boundary rather than
        accumulating across the full dataset.

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
        n_sub = 1

        _skip_analytic_grad = (model._F.shape[0] > n)

        log_mass, log_r, _q_int, log_alpha, log_r_ij_vec = layout.unpack(theta)
        C_cap = np.exp(log_mass)
        g_ext = np.exp(-log_r)

        heater_scales = np.ones(self._n_u)
        for k_la, s_idx in enumerate(layout.identifiable_sources):
            heater_scales[s_idx] = float(np.exp(log_alpha[k_la]))

        g_ij_vec = np.exp(-log_r_ij_vec) if len(log_r_ij_vec) else np.array([])

        F_n = model._F
        G_d_mat = model._G_d
        n_alpha = len(layout.identifiable_sources)

        _p0 = model.params

        # Precompute index arrays and constant diagonal for dfdtheta.
        _j_idx = np.arange(n)
        _Gd_diag = G_d_mat[_j_idx, 1 + _j_idx]  # ∂f_j/∂q_int_j = 1/C_j

        # Precompute dFdtheta (constant — independent of state/input)
        dFdtheta = np.zeros((ntheta, nx, nx))
        if not _skip_analytic_grad:
            for j in range(n):
                dFdtheta[j, j, :n] = -F_n[j, :]
            for j in range(n):
                dFdtheta[n + j, j, j] = g_ext[j] / C_cap[j]
            for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
                g_ij = float(g_ij_vec[k_rij])
                t_idx = 3 * n + n_alpha + k_rij
                dFdtheta[t_idx, pi, pi] = g_ij / C_cap[pi]
                dFdtheta[t_idx, pj, pj] = g_ij / C_cap[pj]
                dFdtheta[t_idx, pi, pj] = -g_ij / C_cap[pi]
                dFdtheta[t_idx, pj, pi] = -g_ij / C_cap[pj]

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

        for seg_i in range(len(seg_starts) - 1):
            seg_begin = seg_starts[seg_i]
            seg_end = seg_starts[seg_i + 1]
            if (seg_end - seg_begin) < min_segment_steps:
                continue

            seg = std_history[seg_begin:seg_end]

            # Split the contiguous segment into windows of at most
            # max_window_steps.  Each window is simulated independently
            # from its own first measurement so that the open-loop horizon
            # never grows so long that the gradient for slow parameters
            # (q_int, large C) is swamped by accumulated simulation error.
            for win_start in range(0, len(seg), max_window_steps):
                win_end = min(win_start + max_window_steps, len(seg))
                if (win_end - win_start) < min_segment_steps:
                    continue
                win = seg[win_start:win_end]

                # Initialise from first measurement of this window, starting
                # the free-run at the *same* state the data is in: room temps
                # from the measurement and emitter-lag states warm-started to
                # the commanded fraction (steady state of the lag filter).
                # This mirrors the open-loop diagnostic and the live EKF, so
                # the parameters we identify reflect the trajectory the user
                # actually sees.  The warm-start carries no θ-dependence, so
                # the per-window sensitivity correctly resets to zero.
                ym0 = np.asarray(win[0]["ym"], dtype=float)
                u0 = np.asarray(win[0].get("u", []), dtype=float)
                x = np.asarray(
                    model.initial_state_from_measurement(ym0, u0), dtype=float
                )
                sx = np.zeros((ntheta, nx))  # ∂x/∂θ — reset per window

                for step in range(len(win) - 1):
                    rec_k = win[step]
                    rec_next = win[step + 1]

                    try:
                        u_k = np.asarray(rec_k["u"], dtype=float)
                        d_k = np.asarray(rec_k["d"], dtype=float)
                        ym_next = np.asarray(rec_next["ym"], dtype=float)
                    except (KeyError, TypeError, ValueError):
                        break

                    t_k = rec_k.get("t")
                    t_next = rec_next.get("t")
                    if t_k is not None and t_next is not None:
                        actual_dt = float(t_next) - float(t_k)
                        if actual_dt <= 0.0:
                            actual_dt = nominal_dt
                    else:
                        actual_dt = nominal_dt
                    h_sub = actual_dt / n_sub
                    T_out_k = float(d_k[0])

                    valid = True
                    for _ in range(n_sub):
                        T = x[:n]
                        try:
                            f_val = model.f(x, u_k, d_k, _p0, 0.0)
                            F_full = model.dfdx(x, u_k, d_k, _p0, 0.0)
                        except Exception:
                            valid = False
                            break

                        dfdtheta_val = np.zeros((ntheta, nx))
                        if not _skip_analytic_grad:
                            dfdtheta_val[_j_idx, _j_idx] = -f_val[:n]
                            dfdtheta_val[n + _j_idx, _j_idx] = (g_ext / C_cap) * (T - T_out_k)
                            dfdtheta_val[2 * n + _j_idx, _j_idx] = _Gd_diag
                            for k_la, s_idx in enumerate(layout.identifiable_sources):
                                src = self._sources[s_idx]
                                i_src = model._room_idx[src.room]
                                u_s = float(u_k[s_idx]) if s_idx < len(u_k) else 0.0
                                u_scaled_s = heater_scales[s_idx] * u_s
                                heat_c = (
                                    src.thermal_power(max(0.0, u_scaled_s), T_out_k)
                                    / C_cap[i_src]
                                )
                                dfdtheta_val[3 * n + k_la, i_src] = heat_c
                            for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
                                g_ij = float(g_ij_vec[k_rij])
                                t_idx = 3 * n + n_alpha + k_rij
                                dfdtheta_val[t_idx, pi] = (
                                    (g_ij / C_cap[pi]) * (T[pi] - T[pj])
                                )
                                dfdtheta_val[t_idx, pj] = (
                                    (g_ij / C_cap[pj]) * (T[pj] - T[pi])
                                )

                        # Euler step for state and sensitivity
                        sx = sx + h_sub * (sx @ F_full.T + dfdtheta_val)
                        x = x + h_sub * f_val

                    if (not valid or not np.all(np.isfinite(x))
                            or not np.all(np.isfinite(sx))):
                        # Overflow/divergence at an extreme trial point (e.g.
                        # during a line search).  Mark the whole evaluation
                        # infeasible so the optimiser backs off rather than
                        # ingesting a non-finite gradient.
                        return _SENTINEL, _zero_grad.copy()

                    residual = ym_next - x[:n]  # shape (n,)
                    total_sse += float(np.dot(residual, residual))
                    # ∂||residual||²/∂θ_i = -2 * sx[i, :n] · residual
                    if not _skip_analytic_grad:
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
        nx = int(model.nx)     # n for 1R1C (no augmented offset states)
        n_sub = 1
        h_sub = self._dt / n_sub

        # Analytical gradient is valid when the drift Jacobian _F has the
        # expected (n, n) shape for the 1R1C model.  If the model ever
        # reverts to a larger state (e.g. 2R2C), the sensitivity writes
        # below would broadcast incorrectly, so we gate on shape equality.
        _skip_analytic_grad = (model._F.shape[0] > n)

        # ── Unpack theta ────────────────────────────────────────────────────
        log_mass, log_r, q_int, log_alpha, log_r_ij_vec = layout.unpack(theta)
        C_cap = np.exp(log_mass)          # (n,) thermal masses
        g_ext = np.exp(-log_r)            # (n,) external conductances 1/R_ext

        # Per-source heater scales
        heater_scales = np.ones(self._n_u)
        for k_la, s_idx in enumerate(layout.identifiable_sources):
            heater_scales[s_idx] = float(np.exp(log_alpha[k_la]))

        # Per-pair inter-room conductances
        g_ij_vec = (np.exp(-log_r_ij_vec)
                    if len(log_r_ij_vec) else np.array([]))  # 1/R_ij

        # Structural matrices baked into this model instance
        F_n = model._F       # (n, n) room-temperature block of drift Jacobian
        G_d_mat = model._G_d  # (n, 1+n) disturbance gain matrix

        n_alpha = len(layout.identifiable_sources)
        n_pairs = len(layout.identifiable_pairs)

        # Precompute index arrays and constant diagonal for dfdtheta.
        _j_idx = np.arange(n)
        _Gd_diag = G_d_mat[_j_idx, 1 + _j_idx]  # ∂f_j/∂q_int_j = 1/C_j

        # ── Precompute dFdtheta (constant – doesn't depend on x/u/d) ───────
        dFdtheta = np.zeros((ntheta, nx, nx))

        if not _skip_analytic_grad:
            # log_mass[j]  → (∂F_n/∂log_mass_j)[j, :] = −F_n[j, :]
            for j in range(n):
                dFdtheta[j, j, :n] = -F_n[j, :]

            # log_r[j]  → (∂F_n/∂log_r_j)[j, j] = g_ext[j] / C_cap[j]
            for j in range(n):
                dFdtheta[n + j, j, j] = g_ext[j] / C_cap[j]

            # q_int[j], log_alpha[k] → dFdtheta = 0  (already zero)

            # log_r_ij[k] for pair (pi, pj):
            for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
                g_ij = float(g_ij_vec[k_rij])
                t_idx = 3 * n + n_alpha + k_rij
                dFdtheta[t_idx, pi, pi] = g_ij / C_cap[pi]
                dFdtheta[t_idx, pj, pj] = g_ij / C_cap[pj]
                dFdtheta[t_idx, pi, pj] = -g_ij / C_cap[pi]
                dFdtheta[t_idx, pj, pi] = -g_ij / C_cap[pj]

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

            T_out_k = float(d_k[0])

            # ── Euler prediction sub-steps ───────────────────────────────────
            for _ in range(n_sub):
                T = x[:n]

                try:
                    f_val = model.f(x, u_k, d_k, _p0, 0.0)        # (nx,)
                    F_full = model.dfdx(x, u_k, d_k, _p0, 0.0)    # (nx, nx)
                    G_sig = model.sigma(x, u_k, d_k, _p0, 0.0)    # (nx, nw)
                except Exception:
                    return _SENTINEL, _zero_grad.copy()

                # ── Compute dfdtheta (state-dependent) ─────────────────────
                # Shape (ntheta, nx).  Disabled under 2R2C (Phase 1 A1) for
                # the same reasons as dFdtheta above; Phase 4 will restore
                # analytic gradients.
                dfdtheta_val = np.zeros((ntheta, nx))

                if not _skip_analytic_grad:
                    dfdtheta_val[_j_idx, _j_idx] = -f_val[:n]
                    dfdtheta_val[n + _j_idx, _j_idx] = (g_ext / C_cap) * (T - T_out_k)
                    dfdtheta_val[2 * n + _j_idx, _j_idx] = _Gd_diag

                    # log_alpha[k]: ∂f_{i_src}/∂log_alpha_k = heat_contrib of src k
                    for k_la, s_idx in enumerate(layout.identifiable_sources):
                        src = self._sources[s_idx]
                        i_src = model._room_idx[src.room]
                        u_s = float(u_k[s_idx]) if s_idx < len(u_k) else 0.0
                        u_scaled_s = heater_scales[s_idx] * u_s
                        heat_c = (
                            src.thermal_power(max(0.0, u_scaled_s), T_out_k)
                            / C_cap[i_src]
                        )
                        dfdtheta_val[3 * n + k_la, i_src] = heat_c

                    # log_r_ij[k]: ∂f_{pi}/∂log_r_ij_k = (g_ij/C_pi)(T_pi−T_pj)
                    for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
                        g_ij = float(g_ij_vec[k_rij])
                        t_idx = 3 * n + n_alpha + k_rij
                        dfdtheta_val[t_idx, pi] = (g_ij / C_cap[pi]) * (
                            T[pi] - T[pj]
                        )
                        dfdtheta_val[t_idx, pj] = (g_ij / C_cap[pj]) * (
                            T[pj] - T[pi]
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
        """
        Return ∂reg/∂θ where reg(θ) is the Gaussian regularisation term
        from :meth:`_compute_regularization`.
        """
        log_mass, log_r, q_int, log_alpha, log_r_ij = layout.unpack(theta)
        lam = self._regularization
        grad = np.zeros_like(theta)

        a, b = layout.idx_log_mass
        grad[a:b] = 2.0 * lam * (log_mass - self._log_mass_prior)

        a, b = layout.idx_log_r
        grad[a:b] = 2.0 * lam * (log_r - self._log_r_prior)

        a, b = layout.idx_q_int
        grad[a:b] = 2.0 * lam * (q_int - self._q_int_prior) / (100.0 ** 2)

        a, b = layout.idx_log_alpha
        if a < b:
            la_prior = np.array(
                [self._log_alpha_prior_full[s] for s in layout.identifiable_sources]
            )
            grad[a:b] = 2.0 * lam * (log_alpha - la_prior)

        a, b = layout.idx_log_r_ij
        if a < b:
            r_priors = np.array([
                self._connection_r_priors[self._connection_pairs.index(p)]
                for p in identifiable_pairs
            ])
            grad[a:b] = 2.0 * lam * (log_r_ij - r_priors)

        return grad

    def _build_system(
        self,
        log_masses: np.ndarray,
        log_rs: np.ndarray,
        log_r_ij: Optional[Dict[Tuple[int, int], float]] = None,
    ) -> Optional[HouseThermalSystem]:
        """Build a :class:`HouseThermalSystem` for the supplied parameters.

        Internal-gain and heater-scale parameters are *not* baked into the
        rebuilt system; they are applied externally during the Kalman
        forward pass.  This keeps the discretised matrices identical
        between candidate parameter sets that share (C, R_ext, R_ij).
        """
        try:
            room_idx = {r.name: k for k, r in enumerate(self._rooms)}
            new_rooms = []
            for i, r in enumerate(self._rooms):
                new_conns = copy.deepcopy(r.connections)
                if log_r_ij:
                    for conn in new_conns:
                        j = room_idx.get(conn.connected_room)
                        if j is not None:
                            pair = (min(i, j), max(i, j))
                            if pair in log_r_ij:
                                conn.r_value = float(math.exp(
                                    float(np.clip(
                                        log_r_ij[pair],
                                        _LOG_R_IJ_LO, _LOG_R_IJ_HI,
                                    ))
                                ))
                new_rooms.append(Room(
                    name=r.name,
                    thermal_mass=float(math.exp(log_masses[i])),
                    r_external=float(math.exp(log_rs[i])),
                    connections=new_conns,
                    windows=r.windows,
                    temperature=r.temperature,
                    setpoint=r.setpoint,
                    # Internal gain is applied as an extra disturbance during
                    # the Kalman pass; keep the rebuilt model neutral.
                    internal_gain=0.0,
                    # Phase 1 C1: preserve the configured infiltration share
                    # so the rebuilt model's leakage-area derivation uses
                    # the same typology as the original.  Not identified
                    # in the current ML estimator — the fraction is a
                    # configured preset.
                    infiltration_fraction=r.infiltration_fraction,
                    # Phase 1 C3 / C4 / C5 — finishing-pass envelope
                    # corrections.  Carried as configured typology
                    # presets (not identified in the v1 estimator; the
                    # Phase 4 system-ID rework will revisit this).
                    sky_radiative_ua=r.sky_radiative_ua,
                    facade_absorptance=r.facade_absorptance,
                    facade_solar_share=r.facade_solar_share,
                    thermal_bridge_psi_l=r.thermal_bridge_psi_l,
                ))
            model = HouseModel(new_rooms)
            return HouseThermalSystem(model, self._sources, self._dt)
        except Exception as exc:
            _LOGGER.debug("Failed to build thermal system: %s", exc, exc_info=True)
            return None

    def _build_parametric_system(
        self,
        layout: _ThetaLayout,
        theta: np.ndarray,
    ) -> Optional[HouseThermalSystem]:
        """
        Build a parametric HouseThermalSDE from theta for CD-EKF estimation.

        Unpacks theta to get thermal parameters, heater scales, and internal gains,
        then constructs a HouseThermalSDE with these values.
        """
        try:
            log_mass, log_r, q_int, log_alpha, log_r_ij = layout.unpack(theta)

            # Build inter-room resistance map
            log_r_ij_map: Optional[Dict[Tuple[int, int], float]] = None
            if len(log_r_ij):
                log_r_ij_map = {
                    pair: float(log_r_ij[k])
                    for k, pair in enumerate(layout.identifiable_pairs)
                }

            # Build the underlying system with theta-dependent thermal parameters
            system = self._build_system(log_mass, log_r, log_r_ij_map)
            if system is None:
                return None

            # Construct heater scales vector (alpha)
            heater_scales = np.ones(self._n_u)
            for k, s_idx in enumerate(layout.identifiable_sources):
                heater_scales[s_idx] = float(math.exp(log_alpha[k]))

            # Create a new parametric instance with heater_scales and internal_gains
            # We need to rebuild to pass the params, heater_scales, and internal_gains
            room_idx = {r.name: k for k, r in enumerate(self._rooms)}
            new_rooms = []
            for i, r in enumerate(self._rooms):
                new_conns = copy.deepcopy(r.connections)
                if log_r_ij_map:
                    for conn in new_conns:
                        j = room_idx.get(conn.connected_room)
                        if j is not None:
                            pair = (min(i, j), max(i, j))
                            if pair in log_r_ij_map:
                                conn.r_value = float(math.exp(
                                    float(np.clip(
                                        log_r_ij_map[pair],
                                        _LOG_R_IJ_LO, _LOG_R_IJ_HI,
                                    ))
                                ))
                new_rooms.append(Room(
                    name=r.name,
                    thermal_mass=float(math.exp(log_mass[i])),
                    r_external=float(math.exp(log_r[i])),
                    connections=new_conns,
                    windows=r.windows,
                    temperature=r.temperature,
                    setpoint=r.setpoint,
                    internal_gain=0.0,  # Applied via theta parameter in f()
                    # Preserve Phase 1 C1 typology fields — not identified in v1.
                    infiltration_fraction=r.infiltration_fraction,
                    # Phase 1 C3 / C4 / C5 — finishing-pass envelope
                    # corrections.  Carried as configured typology
                    # presets (not identified in the v1 estimator; the
                    # Phase 4 system-ID rework will revisit this).
                    sky_radiative_ua=r.sky_radiative_ua,
                    facade_absorptance=r.facade_absorptance,
                    facade_solar_share=r.facade_solar_share,
                    thermal_bridge_psi_l=r.thermal_bridge_psi_l,
                ))
            model = HouseModel(new_rooms)
            return HouseThermalSystem(
                model, self._sources, self._dt,
                sigma_w=math.sqrt(self._Q_var),
                sigma_v=math.sqrt(self._R_var),
                augment_offsets=False,
                n_int_steps=10,
                identifiable_sources=layout.identifiable_sources,
                theta=theta,
            )
        except Exception as exc:
            _LOGGER.debug("Failed to build parametric system: %s", exc, exc_info=True)
            return None

    def _make_d_np(
        self,
        system: HouseThermalSystem,
        d_outdoor: float,
        d_solar: Dict[str, float],
        q_int: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Build a numpy disturbance vector [T_out, Q_sol_1+Q_int_1, …].

        Solar gain and (optional) per-room ``q_int`` are folded into the
        same disturbance slot — they map through the same column of E_d.
        """
        p = system.n_d
        d = np.zeros(p)
        d[0] = d_outdoor
        for name, gain in d_solar.items():
            if name in system._room_idx:
                d[1 + system._room_idx[name]] = gain
        if q_int is not None:
            for i in range(self._n):
                d[1 + i] += float(q_int[i])
        return d

    def _convert_history_std(
        self,
        history: List[Dict[str, Any]],
        use_ym: bool = False,
    ) -> List[Dict[str, np.ndarray]]:
        """Convert the HA history buffer to the standardised mbc format.

        Each record is converted to ``{"y": ndarray, "u": ndarray, "d": ndarray}``
        for discrete-time estimator, or ``{"ym": ndarray, "u": ndarray, "d": ndarray}``
        for continuous-discrete estimator.

        where ``d = [T_out, solar_1, …, solar_n]`` (raw, without q_int).
        The q_int contribution is absorbed by the parametric model through
        the ``internal_gains`` parameter in HouseThermalSDE.

        Parameters
        ----------
        history : list of dicts
            HA history buffer records.
        use_ym : bool, optional
            If True, use "ym" key for measurements (CD-EKF convention).
            If False, use "y" key (discrete-time convention). Default: False.
        """
        n = self._n
        n_u = self._n_u
        room_idx = {name: i for i, name in enumerate(self._room_names)}
        std: List[Dict[str, Any]] = []
        meas_key = "ym" if use_ym else "y"
        for record in history:
            y = np.array(record["y"][:n], dtype=float)
            u = np.zeros(n_u)
            for k, val in enumerate(record.get("u", [])):
                if k < n_u:
                    u[k] = float(val)
            d = np.zeros(1 + n)
            d[0] = float(record["d_outdoor"])
            for name, gain in record.get("d_solar", {}).items():
                if name in room_idx:
                    d[1 + room_idx[name]] = float(gain)
            # Carry the Unix timestamp so _simulation_mse_and_grad can detect
            # gaps from controller restarts and treat them as segment boundaries.
            t_val = record.get("timestamp")
            std.append({
                meas_key: y,
                "u": u,
                "d": d,
                "t": float(t_val) if t_val is not None else None,
            })
        return std

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
        """Gaussian regularisation toward priors for all parameters.

        Internal-gain and α priors use unit-scale weights; the linear-space
        q_int penalty is divided by 100² so the prior std corresponds to
        ~100 W rather than 1 W.
        """
        if identifiable_sources is not None and len(log_alpha):
            log_alpha_prior = np.array(
                [self._log_alpha_prior_full[s] for s in identifiable_sources]
            )
        else:
            log_alpha_prior = np.array([]) if not len(log_alpha) else np.array(
                [self._log_alpha_prior_full[s] for s in range(len(log_alpha))]
            )

        r_ij_priors = np.array([
            self._connection_r_priors[self._connection_pairs.index(p)]
            for p in identifiable_pairs
        ]) if identifiable_pairs else np.array([])

        reg = self._regularization * (
            float(np.sum((log_mass - self._log_mass_prior) ** 2))
            + float(np.sum((log_r - self._log_r_prior) ** 2))
            + float(np.sum((q_int - self._q_int_prior) ** 2)) / (100.0 ** 2)
        )
        if len(log_alpha):
            reg += self._regularization * float(
                np.sum((log_alpha - log_alpha_prior) ** 2)
            )
        if len(log_r_ij):
            reg += self._regularization * float(
                np.sum((log_r_ij - r_ij_priors) ** 2)
            )
        return reg

    def _initial_state_and_covariance(
        self,
        system: HouseThermalSystem,
        first_measurement: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Build x0/P0 matching system dimensions (supports augmented states)."""
        nx = int(system.nx)
        nym = int(system.nym)
        n = self._n
        x0 = np.zeros(nx, dtype=float)
        n_copy = min(nym, len(first_measurement), nx)
        x0[:n_copy] = np.array(first_measurement[:n_copy], dtype=float)

        if nx >= 2 * n and n_copy >= n:
            x0[n: 2 * n] = x0[:n]          # warm-start latent block ← air
        if nx >= 3 * n and n_copy >= n:
            x0[2 * n: 3 * n] = x0[:n]      # warm-start second latent block ← air

        P0 = np.eye(nx, dtype=float) * self._R_var * 10.0
        if nx > nym:
            # Augmented latent states (e.g. temperature offsets) are not
            # directly observed at startup, so start them with higher
            # uncertainty than measured temperature components.
            P0[nym:, nym:] *= 4.0
        return x0, P0
