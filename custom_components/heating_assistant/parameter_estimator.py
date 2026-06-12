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
internal-gain parameters Q_int are kept in linear space.  A deliberately
light Gaussian regularisation term shrinks each parameter toward its prior
(the configured value or zero) only in the directions the data cannot
constrain; it is kept weak so the configured data window — not the prior —
drives the estimate.

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
#: NOTE: This constant was calibrated for 60 s sampling.  At the default
#: 900 s sampling interval the effective bar is 15 hours, which exceeds
#: any realistic identification window.  Instance methods therefore use
#: ``self._min_history_steps`` (computed from ``dt`` in the constructor)
#: instead of this module-level constant.  It is kept for backward
#: compatibility with external importers (e.g. test suites).
MIN_HISTORY_STEPS = 60

#: Minimum wall-clock time [s] of data required before attempting estimation.
#: Corresponds to ≈ 1 hour — enough to observe meaningful thermal dynamics
#: for rooms with time constants of 2–6 hours.
_MIN_HISTORY_TIME_S: float = 3600.0

#: Minimum wall-clock time [s] for a contiguous simulation window to be used
#: in the open-loop MSE objective.  Short windows cannot constrain slow
#: parameters (R_ext, q_int); 20 minutes is a practical lower bound.
_MIN_SEGMENT_TIME_S: float = 1200.0

#: Shared empty integer index array, returned by the per-room open-window
#: bookkeeping when no room is excluded at a step (avoids per-step allocation).
_EMPTY_IDX: np.ndarray = np.array([], dtype=int)

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

#: Relative MAP-prior weight for the heater power-scale α, multiplying the base
#: regularisation.  Unlike thermal mass / R_external (which are genuinely
#: unknown a priori and get the light base weight so the data drives them), a
#: heater's rated power is usually known to within ~±20 %, so α carries real
#: prior information: log-space σ ≈ 0.2 ⇒ a much tighter penalty.  This is the
#: lever that keeps the joint optimum off the documented "C huge / R huge"
#: degenerate ridge, where C, R and α trade off against one another (README
#: §17.5): pinning α near its physical value resolves that degeneracy so the
#: data identifies C and R instead of sliding α to a bound.
_ALPHA_PRIOR_WEIGHT = 25.0

#: Minimum std of inter-room temperature difference for R_ij identifiability.
_MIN_TEMP_DIFF_STD = 0.3   # °C

#: Minimum std of source duty-cycle for α_s identifiability.
_MIN_HEATER_USAGE_STD = 0.05

#: Minimum std of a room's recorded solar gain [W] for the per-room solar
#: scale s_i to be identifiable.  A room with no windows / aperture (or a
#: window covering only the night hours) carries no solar information.
_MIN_SOLAR_STD = 30.0

#: Log-space bounds for the per-room solar-gain scale s_i.
_LOG_SOLAR_LO = math.log(0.2)   # heavy unmodelled shading
_LOG_SOLAR_HI = math.log(3.0)   # preset badly underestimates the aperture

#: Linear-space bounds for the 2R2C envelope split fractions.  Kept inside
#: the hard clips in ``thermal_model.Room`` so the estimator can never
#: construct a degenerate room.
_C_AIR_LO, _C_AIR_HI = 0.02, 0.60
_R_AW_LO, _R_AW_HI = 0.02, 0.90

#: Prior standard deviation for the split fractions (linear space).  The
#: splits are deliberately held on a tight leash: they are only weakly
#: identified, and the typology defaults are decent.  The data must carry
#: real multi-hour excitation to move them.
_SPLIT_PRIOR_STD = 0.1

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
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> List[Tuple[int, int]]:
    """
    Return the subset of room-index pairs (i, j) for which the inter-room
    temperature difference has sufficient variance for R_ij to be identifiable.
    """
    if len(history) < min_history_steps:
        return []

    identifiable = []
    for i, j in connections:
        diffs = []
        for record in history:
            y = record.get("y", [])
            if i < len(y) and j < len(y):
                diffs.append(float(y[i]) - float(y[j]))
        if len(diffs) >= min_history_steps and float(np.std(diffs)) > min_std:
            identifiable.append((i, j))
    return identifiable


def _check_identifiable_sources(
    history: List[Dict[str, Any]],
    n_sources: int,
    min_std: float = _MIN_HEATER_USAGE_STD,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> List[int]:
    """
    Return the indices of heat sources whose duty-cycle ``u`` shows enough
    variation for the power-scale parameter α_s to be identifiable.
    """
    if len(history) < min_history_steps:
        return []

    identifiable = []
    for s in range(n_sources):
        u_vals = []
        for record in history:
            u = record.get("u", [])
            if s < len(u):
                u_vals.append(float(u[s]))
        if len(u_vals) >= min_history_steps and float(np.std(u_vals)) > min_std:
            identifiable.append(s)
    return identifiable


def _check_identifiable_solar(
    history: List[Dict[str, Any]],
    room_names: List[str],
    min_std: float = _MIN_SOLAR_STD,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> List[int]:
    """
    Return the room indices whose recorded solar-gain disturbance varies
    enough for the per-room solar scale ``s_i`` to be identifiable.
    """
    if len(history) < min_history_steps:
        return []

    identifiable = []
    for i, name in enumerate(room_names):
        gains = [
            float(record.get("d_solar", {}).get(name, 0.0))
            for record in history
        ]
        if len(gains) >= min_history_steps and float(np.std(gains)) > min_std:
            identifiable.append(i)
    return identifiable


def _identifiable_split_rooms(
    identifiable_sources: List[int],
    sources: List[Any],
    room_names: List[str],
) -> List[int]:
    """
    Return the room indices whose 2R2C split fractions are identifiable.

    The fast/slow split is only visible through heater step responses, so a
    room qualifies when at least one of its own heat sources passed the
    duty-cycle excitation gate.  Passive rooms keep their typology defaults.
    """
    rooms_with_excited_source = {
        getattr(sources[s], "room", None) for s in identifiable_sources
    }
    return [
        i for i, name in enumerate(room_names)
        if name in rooms_with_excited_source
    ]


# ── Main estimator class ─────────────────────────────────────────────────────


class _ThetaLayout:
    """
    Index layout of the packed parameter vector ``θ``:

        [ log_mass_1..n
          log_r_ext_1..n
          q_int_1..n
          log_alpha_{s_k} for s_k in identifiable_sources
          log_r_ij_{p_k} for p_k in identifiable_pairs
          log_solar_{i_k} for i_k in identifiable_solar
          c_air_{i_k}    for i_k in identifiable_splits   (linear space)
          r_aw_{i_k}     for i_k in identifiable_splits   (linear space) ]

    The first three blocks always exist (one entry per room); the gated
    blocks are present only for the rooms / sources / pairs that passed
    their identifiability gates, so an old 3n-element θ remains a valid
    layout with all gates closed.
    """

    def __init__(
        self,
        n_rooms: int,
        identifiable_sources: List[int],
        identifiable_pairs: List[Tuple[int, int]],
        identifiable_solar: Optional[List[int]] = None,
        identifiable_splits: Optional[List[int]] = None,
    ) -> None:
        self.n_rooms = n_rooms
        self.identifiable_sources = list(identifiable_sources)
        self.identifiable_pairs = list(identifiable_pairs)
        self.identifiable_solar = list(identifiable_solar or [])
        self.identifiable_splits = list(identifiable_splits or [])

        n = n_rooms
        self.idx_log_mass = (0, n)
        self.idx_log_r = (n, 2 * n)
        self.idx_q_int = (2 * n, 3 * n)

        off = 3 * n
        self.idx_log_alpha = (off, off + len(identifiable_sources))
        off = self.idx_log_alpha[1]
        self.idx_log_r_ij = (off, off + len(identifiable_pairs))
        off = self.idx_log_r_ij[1]
        self.idx_log_solar = (off, off + len(self.identifiable_solar))
        off = self.idx_log_solar[1]
        self.idx_c_air = (off, off + len(self.identifiable_splits))
        off = self.idx_c_air[1]
        self.idx_r_aw = (off, off + len(self.identifiable_splits))

        self.size = self.idx_r_aw[1]

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
        a, b = self.idx_log_solar
        log_solar = theta[a:b]
        a, b = self.idx_c_air
        c_air = theta[a:b]
        a, b = self.idx_r_aw
        r_aw = theta[a:b]
        return (
            log_mass, log_r, q_int, log_alpha, log_r_ij,
            log_solar, c_air, r_aw,
        )


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
        Diagonal process-noise variance used in the Kalman filter [°C²].
    R_var : float
        Diagonal measurement-noise variance [°C²].
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
        # (= 12 h at the 900 s sampling interval).
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

                {
                    "thermal_mass":   {"room_name": value, ...},
                    "r_external":     {"room_name": value, ...},
                    "internal_gain":  {"room_name": value, ...},
                    "solar_scale":    {"room_name": value, ...},
                    "c_air_fraction": {"room_name": value, ...},
                    "r_aw_fraction":  {"room_name": value, ...},
                    "heater_scales":  {"source_name": value, ...},
                }

            Locked parameters are pinned to the given value by setting their
            lower and upper bounds equal (lb = ub = fixed_value), so the
            optimiser never moves them while leaving all other parameters free.
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

        layout = _ThetaLayout(
            n_rooms=self._n,
            identifiable_sources=identifiable_sources,
            identifiable_pairs=identifiable_pairs,
            identifiable_solar=identifiable_solar,
            identifiable_splits=identifiable_splits,
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
        theta_prior = np.concatenate([
            self._log_mass_prior,
            self._log_r_prior,
            self._q_int_prior,
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
        (log_mass, log_r, q_int, log_alpha, log_r_ij,
         log_solar, c_air, r_aw) = layout.unpack(best_theta)
        log_mass = np.clip(log_mass, _LOG_MASS_LO, _LOG_MASS_HI)
        log_r = np.clip(log_r, _LOG_R_LO, _LOG_R_HI)
        q_int = np.clip(q_int, _Q_INT_LO, _Q_INT_HI)
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
            "message": "  ".join(msg_parts),
        }

    # ── Internal helpers ──────────────────────────────────────────────────

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
        """Pin locked parameters to their fixed values by setting lb = ub.

        Modifies *bounds* and *theta_prior* in-place so the optimiser treats
        each locked parameter as a constant rather than a decision variable.
        Values outside the physical bounds are silently clamped.
        """
        for param_key, room_or_src_dict in locked_params.items():
            if not isinstance(room_or_src_dict, dict):
                continue

            if param_key == "heater_scales":
                for src_name, value in room_or_src_dict.items():
                    src_idx = next(
                        (j for j, s in enumerate(self._sources)
                         if s.name == src_name),
                        None,
                    )
                    if src_idx is None or src_idx not in identifiable_sources:
                        continue
                    k = identifiable_sources.index(src_idx)
                    log_val = math.log(max(float(value), 1e-3))
                    log_val = max(_LOG_ALPHA_LO, min(_LOG_ALPHA_HI, log_val))
                    idx = layout.idx_log_alpha[0] + k
                    bounds[idx] = (log_val, log_val)
                    theta_prior[idx] = log_val
                continue

            for room_name, value in room_or_src_dict.items():
                if room_name not in self._room_names:
                    continue
                i = self._room_names.index(room_name)

                if param_key == "thermal_mass":
                    log_val = math.log(max(float(value), 1.0))
                    log_val = max(_LOG_MASS_LO, min(_LOG_MASS_HI, log_val))
                    idx = layout.idx_log_mass[0] + i
                    bounds[idx] = (log_val, log_val)
                    theta_prior[idx] = log_val

                elif param_key == "r_external":
                    log_val = math.log(max(float(value), 1e-9))
                    log_val = max(_LOG_R_LO, min(_LOG_R_HI, log_val))
                    idx = layout.idx_log_r[0] + i
                    bounds[idx] = (log_val, log_val)
                    theta_prior[idx] = log_val

                elif param_key == "internal_gain":
                    val = max(_Q_INT_LO, min(_Q_INT_HI, float(value)))
                    idx = layout.idx_q_int[0] + i
                    bounds[idx] = (val, val)
                    theta_prior[idx] = val

                elif param_key == "solar_scale":
                    if i not in identifiable_solar:
                        continue
                    k = identifiable_solar.index(i)
                    log_val = math.log(max(float(value), 1e-3))
                    log_val = max(_LOG_SOLAR_LO, min(_LOG_SOLAR_HI, log_val))
                    idx = layout.idx_log_solar[0] + k
                    bounds[idx] = (log_val, log_val)
                    theta_prior[idx] = log_val

                elif param_key == "c_air_fraction":
                    if i not in identifiable_splits:
                        continue
                    k = identifiable_splits.index(i)
                    val = max(_C_AIR_LO, min(_C_AIR_HI, float(value)))
                    idx = layout.idx_c_air[0] + k
                    bounds[idx] = (val, val)
                    theta_prior[idx] = val

                elif param_key == "r_aw_fraction":
                    if i not in identifiable_splits:
                        continue
                    k = identifiable_splits.index(i)
                    val = max(_R_AW_LO, min(_R_AW_HI, float(value)))
                    idx = layout.idx_r_aw[0] + k
                    bounds[idx] = (val, val)
                    theta_prior[idx] = val

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
        if len(std_history) < self._min_history_steps:
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
            if A.shape[0] < max(self._min_history_steps // 2, 5):
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

    def _theta_model_quantities(
        self,
        layout: "_ThetaLayout",
        theta: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Per-room physical quantities implied by θ, for the sensitivity pass.

        Mirrors the parametrisation in :meth:`Room.conductances` /
        :meth:`_build_rooms_from_theta`: total mass and UA from the
        log-space θ entries, split fractions and solar scales from the
        gated θ entries (configured values for ungated rooms).
        """
        from .const import (  # noqa: PLC0415
            MAX_INFILTRATION_FRACTION,
            SOLAR_WALL_FRACTION,
        )
        (log_mass, log_r, _q_int, log_alpha, log_r_ij,
         log_solar, c_air, r_aw) = layout.unpack(theta)
        n = self._n

        C_tot = np.exp(log_mass)
        ua = np.exp(-log_r)

        fc = self._c_air_prior_full.copy()
        rf = self._r_aw_prior_full.copy()
        for k, i in enumerate(layout.identifiable_splits):
            fc[i] = float(np.clip(c_air[k], _C_AIR_LO, _C_AIR_HI))
            rf[i] = float(np.clip(r_aw[k], _R_AW_LO, _R_AW_HI))
        s = np.exp(self._log_solar_prior_full).copy()
        for k, i in enumerate(layout.identifiable_solar):
            s[i] = math.exp(float(np.clip(log_solar[k], _LOG_SOLAR_LO, _LOG_SOLAR_HI)))

        f_inf = np.array([
            float(np.clip(getattr(r, "infiltration_fraction", 0.0),
                          0.0, MAX_INFILTRATION_FRACTION))
            for r in self._rooms
        ])
        facade = np.array([
            float(getattr(r, "facade_solar_share", 0.0))
            * float(getattr(r, "facade_absorptance", 0.0))
            for r in self._rooms
        ])

        C_a = fc * C_tot
        C_w = (1.0 - fc) * C_tot
        g_inf = f_inf * ua
        g_cond = (1.0 - f_inf) * ua
        g_aw = g_cond / rf
        g_we = g_cond / (1.0 - rf)

        heater_scales = np.ones(self._n_u)
        for k_la, s_idx in enumerate(layout.identifiable_sources):
            heater_scales[s_idx] = float(np.exp(log_alpha[k_la]))
        g_ij_vec = np.exp(-log_r_ij) if len(log_r_ij) else np.array([])

        return {
            "C_a": C_a, "C_w": C_w, "fc": fc, "rf": rf, "s": s,
            "g_inf": g_inf, "g_aw": g_aw, "g_we": g_we,
            "facade": facade, "wall_frac": float(SOLAR_WALL_FRACTION),
            "heater_scales": heater_scales, "g_ij": g_ij_vec,
        }

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
                # the free-run at the *same* state the data is in: air temps
                # from the measurement, wall states at the steady-state value
                # implied by (T_a, T_out), and emitter-lag states warm-started
                # to the commanded fraction.  The steady-state wall seed starts
                # the unobserved envelope close to its true value over these
                # short windows, keeping the heater-scale / resistance
                # parameters identifiable (the reconstruction / open-loop
                # *diagnostics* instead seed the wall at the air temperature for
                # an unbiased display — see initial_state_from_measurement).
                ym0 = np.asarray(win[0]["ym"], dtype=float)
                u0 = np.asarray(win[0].get("u", []), dtype=float)
                d0 = np.asarray(win[0].get("d", []), dtype=float)
                x = np.asarray(
                    model.initial_state_from_measurement(ym0, u0, d0),
                    dtype=float,
                )
                sx = np.zeros((ntheta, nx))  # ∂x/∂θ — reset per window
                # The wall warm start T_w0 = (1−rf)·T_a + rf·T_out depends
                # on the identified r_aw fraction (sky/bridge neglected):
                # ∂T_w0/∂rf = T_out − T_a.
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
        """
        Return ∂reg/∂θ where reg(θ) is the Gaussian regularisation term
        from :meth:`_compute_regularization_theta`.
        """
        (log_mass, log_r, q_int, log_alpha, log_r_ij,
         log_solar, c_air, r_aw) = layout.unpack(theta)
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
            grad[a:b] = 2.0 * lam * _ALPHA_PRIOR_WEIGHT * (log_alpha - la_prior)

        a, b = layout.idx_log_r_ij
        if a < b:
            r_priors = np.array([
                self._connection_r_priors[self._connection_pairs.index(p)]
                for p in identifiable_pairs
            ])
            grad[a:b] = 2.0 * lam * (log_r_ij - r_priors)

        a, b = layout.idx_log_solar
        if a < b:
            s_prior = np.array([
                self._log_solar_prior_full[i] for i in layout.identifiable_solar
            ])
            grad[a:b] = 2.0 * lam * (log_solar - s_prior)

        a, b = layout.idx_c_air
        if a < b:
            ca_prior = np.array([
                self._c_air_prior_full[i] for i in layout.identifiable_splits
            ])
            grad[a:b] = 2.0 * lam * (c_air - ca_prior) / (_SPLIT_PRIOR_STD ** 2)

        a, b = layout.idx_r_aw
        if a < b:
            ra_prior = np.array([
                self._r_aw_prior_full[i] for i in layout.identifiable_splits
            ])
            grad[a:b] = 2.0 * lam * (r_aw - ra_prior) / (_SPLIT_PRIOR_STD ** 2)

        return grad

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
        """Construct Room objects with θ-dependent parameters baked in.

        Gated parameters (inter-room R, solar scale, envelope splits) take
        their θ values for the identified rooms/pairs and the configured
        values elsewhere.  ``internal_gain`` stays 0 — q_int is applied
        through the θ parameter vector in ``HouseThermalSDE.f``.
        """
        room_idx = {r.name: k for k, r in enumerate(self._rooms)}

        solar_scales = np.exp(self._log_solar_prior_full).copy()
        if log_solar is not None and len(log_solar):
            for k, i in enumerate(layout.identifiable_solar):
                solar_scales[i] = math.exp(float(np.clip(
                    log_solar[k], _LOG_SOLAR_LO, _LOG_SOLAR_HI,
                )))
        c_air_full = self._c_air_prior_full.copy()
        r_aw_full = self._r_aw_prior_full.copy()
        if c_air is not None and len(c_air):
            for k, i in enumerate(layout.identifiable_splits):
                c_air_full[i] = float(np.clip(c_air[k], _C_AIR_LO, _C_AIR_HI))
                r_aw_full[i] = float(np.clip(r_aw[k], _R_AW_LO, _R_AW_HI))

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
                # Internal gain is applied via the θ parameter vector during
                # the forward pass; keep the rebuilt model neutral.
                internal_gain=0.0,
                solar_scale=float(solar_scales[i]),
                c_air_fraction=float(c_air_full[i]),
                r_aw_fraction=float(r_aw_full[i]),
                # Configured typology presets — not identified.
                infiltration_fraction=r.infiltration_fraction,
                sky_radiative_ua=r.sky_radiative_ua,
                facade_absorptance=r.facade_absorptance,
                facade_solar_share=r.facade_solar_share,
                thermal_bridge_psi_l=r.thermal_bridge_psi_l,
            ))
        return new_rooms

    def _build_system(
        self,
        log_masses: np.ndarray,
        log_rs: np.ndarray,
        log_r_ij: Optional[Dict[Tuple[int, int], float]] = None,
    ) -> Optional[HouseThermalSystem]:
        """Build a :class:`HouseThermalSystem` for the supplied parameters.

        Internal-gain and heater-scale parameters are *not* baked into the
        rebuilt system; they are applied externally during the Kalman
        forward pass.  Solar scales and envelope splits stay at their
        configured values (this entry point predates those parameters and
        is kept for callers that only vary C / R / R_ij).
        """
        try:
            layout = _ThetaLayout(self._n, [], [])
            new_rooms = self._build_rooms_from_theta(
                layout, log_masses, log_rs, log_r_ij,
            )
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
        Build a parametric HouseThermalSDE from theta for estimation.

        All structural parameters (C, R_ext, R_ij, solar scale, envelope
        splits) are baked into the model matrices; q_int and the heater
        scales remain in θ and are applied inside ``HouseThermalSDE.f``.
        """
        try:
            (log_mass, log_r, _q_int, _log_alpha, log_r_ij,
             log_solar, c_air, r_aw) = layout.unpack(theta)

            log_r_ij_map: Optional[Dict[Tuple[int, int], float]] = None
            if len(log_r_ij):
                log_r_ij_map = {
                    pair: float(log_r_ij[k])
                    for k, pair in enumerate(layout.identifiable_pairs)
                }

            new_rooms = self._build_rooms_from_theta(
                layout, log_mass, log_r, log_r_ij_map,
                log_solar=log_solar, c_air=c_air, r_aw=r_aw,
            )
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

    def _convert_history_std(
        self,
        history: List[Dict[str, Any]],
        use_ym: bool = False,
    ) -> List[Dict[str, np.ndarray]]:
        """Convert the HA history buffer to the standardised mbc format.

        Each record is converted to ``{"y": ndarray, "u": ndarray, "d": ndarray}``
        for discrete-time estimator, or ``{"ym": ndarray, "u": ndarray, "d": ndarray}``
        for continuous-discrete estimator.

        where ``d = [T_out, solar_1 … solar_n, q_air_1 … q_air_n]`` with the
        recorded (raw, unscaled) solar gains in slots 1…n and zeros in the
        air-heat slots — the q_int contribution is applied parametrically
        through θ inside ``HouseThermalSDE.f``.

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
            d = np.zeros(1 + 2 * n)
            d[0] = float(record["d_outdoor"])
            for name, gain in record.get("d_solar", {}).items():
                if name in room_idx:
                    d[1 + room_idx[name]] = float(gain)
            # Carry the Unix timestamp so _simulation_mse_and_grad can detect
            # gaps from controller restarts and treat them as segment boundaries.
            t_val = record.get("timestamp")
            # Per-room window-open data-quality label.  When a room's window was
            # open at this step its air-exchange loss is unmodelled, so the
            # open-loop objective must neither score that room's residual nor let
            # its (cooling) air node leak into the coupled neighbours.  Missing
            # key (old records / seeded history) ⇒ all-closed.
            wo_map = record.get("window_open") or {}
            window_open = np.array(
                [bool(wo_map.get(name, False)) for name in self._room_names],
                dtype=bool,
            )
            std.append({
                meas_key: y,
                "u": u,
                "d": d,
                "t": float(t_val) if t_val is not None else None,
                "window_open": window_open,
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
            reg += self._regularization * _ALPHA_PRIOR_WEIGHT * float(
                np.sum((log_alpha - log_alpha_prior) ** 2)
            )
        if len(log_r_ij):
            reg += self._regularization * float(
                np.sum((log_r_ij - r_ij_priors) ** 2)
            )
        return reg

    def _compute_regularization_theta(
        self,
        theta: np.ndarray,
        layout: "_ThetaLayout",
    ) -> float:
        """Gaussian regularisation toward priors for the full θ vector.

        Wraps :meth:`_compute_regularization` (the always-present blocks)
        and adds the gated blocks: unit-scale priors for the log solar
        scale, and tight ``_SPLIT_PRIOR_STD`` priors for the linear-space
        envelope split fractions.
        """
        (log_mass, log_r, q_int, log_alpha, log_r_ij,
         log_solar, c_air, r_aw) = layout.unpack(theta)
        reg = self._compute_regularization(
            log_mass, log_r, q_int, log_alpha, log_r_ij,
            layout.identifiable_pairs, layout.identifiable_sources,
        )
        lam = self._regularization
        if len(log_solar):
            prior = np.array([
                self._log_solar_prior_full[i] for i in layout.identifiable_solar
            ])
            reg += lam * float(np.sum((log_solar - prior) ** 2))
        if len(c_air):
            ca_prior = np.array([
                self._c_air_prior_full[i] for i in layout.identifiable_splits
            ])
            ra_prior = np.array([
                self._r_aw_prior_full[i] for i in layout.identifiable_splits
            ])
            reg += lam * float(
                np.sum((c_air - ca_prior) ** 2)
                + np.sum((r_aw - ra_prior) ** 2)
            ) / (_SPLIT_PRIOR_STD ** 2)
        return reg

    def _initial_state_and_covariance(
        self,
        system: HouseThermalSystem,
        first_measurement: np.ndarray,
        u: Optional[np.ndarray] = None,
        d: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Build x0/P0 matching system dimensions (supports augmented states).

        When the first record's control ``u`` / disturbance ``d`` are supplied
        the state is seeded via the system's ``initial_state_from_measurement``
        helper — the single source of truth shared with the open-loop
        diagnostic and the simulation objective — so the wall nodes start at
        the (T_a, T_out) steady state and the emitter-lag states are
        warm-started to the commanded fraction.  Without ``u``/``d`` (or for
        legacy system objects) it falls back to seeding the wall block at the
        air temperature; the latent states never start at 0.
        """
        nx = int(system.nx)
        nym = int(system.nym)
        n = self._n

        x0: Optional[np.ndarray] = None
        init_fn = getattr(system, "initial_state_from_measurement", None)
        if callable(init_fn) and u is not None:
            try:
                x0 = np.asarray(
                    init_fn(
                        np.asarray(first_measurement, dtype=float),
                        np.asarray(u, dtype=float),
                        np.asarray(d, dtype=float) if d is not None else None,
                    ),
                    dtype=float,
                )
                if x0.shape[0] != nx or not np.all(np.isfinite(x0)):
                    x0 = None
            except Exception:
                x0 = None

        if x0 is None:
            x0 = np.zeros(nx, dtype=float)
            n_copy = min(nym, len(first_measurement), nx)
            x0[:n_copy] = np.array(first_measurement[:n_copy], dtype=float)
            # Wall block warm-starts at the air temperature; the emitter-lag
            # block (and any further augmentation) stays at zero.
            if nx >= 2 * n and n_copy >= n:
                x0[n: 2 * n] = x0[:n]

        P0 = np.eye(nx, dtype=float) * self._R_var * 10.0
        if nx > nym:
            # Latent states (wall nodes, emitter lags) are not directly
            # observed at startup, so start them with higher uncertainty
            # than the measured temperature components.
            P0[nym:, nym:] *= 4.0
        return x0, P0
