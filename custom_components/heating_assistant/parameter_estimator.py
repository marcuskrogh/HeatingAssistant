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

A multi-start Nelder–Mead is run from the prior plus a few small random
perturbations; the run with the best (lowest) negative log-likelihood is
returned.  This reduces the chance of being trapped in the flat valleys
that are typical of RC-network identification problems.

The optimisation uses a pure-NumPy Nelder–Mead so no additional runtime
dependencies beyond numpy are required.
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
from mbc.identification import CDParameterEstimator as _CDParameterEstimator
from mbc.identification import cd_ped_neg_log_likelihood as _cd_ped_neg_ll
from mbc.identification import nelder_mead as _nelder_mead  # for test compatibility

_LOGGER = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

#: Minimum number of history steps before attempting estimation.
MIN_HISTORY_STEPS = 30

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

    A multi-start Nelder–Mead is run from the prior and a few random
    perturbations; the lowest-objective restart is returned.

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
    ) -> None:
        self._rooms = rooms
        self._sources = sources
        self._dt = dt
        self._Q_var = Q_var
        self._R_var = R_var
        self._regularization = regularization

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

    def estimate(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Estimate all identifiable thermal parameters from the history buffer
        using a single joint optimisation with multistart Nelder–Mead.
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

        # ── Convert history to CD-EKF format (use "ym" key) ────────────────
        std_history = self._convert_history_std(history, use_ym=True)

        # ── Build model factory for CDParameterEstimator ───────────────────
        def _model_factory(theta: np.ndarray):
            return self._build_parametric_system(layout, theta)

        # ── Build regularisation function ──────────────────────────────────
        def _regularization_fn(theta: np.ndarray) -> float:
            lm, lr, qi, la, lrij = layout.unpack(theta)
            return self._compute_regularization(
                lm, lr, qi, la, lrij, layout.identifiable_pairs
            )

        # ── Custom perturbation: scale q_int by 200 for physical restarts ──
        def _perturb(theta0: np.ndarray,
                     rng: np.random.Generator,
                     _restart_idx: int) -> np.ndarray:
            pert = rng.normal(0.0, _RESTART_PERT, size=theta0.size)
            a, b = layout.idx_q_int
            pert[a:b] *= 200.0   # std ≈ 100 W in linear space
            return theta0 + pert

        # ── Initial state estimate for CD-EKF (supports augmented states) ──
        system0 = _model_factory(theta_prior)
        if system0 is None:
            return {
                "success": False,
                "estimated_params": {
                    name: {"thermal_mass": p["thermal_mass"], "r_external": p["r_external"]}
                    for name, p in current.items()
                },
                "current_params": {
                    name: {"thermal_mass": p["thermal_mass"], "r_external": p["r_external"]}
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
                "message": "Failed to initialize parametric model for estimation.",
            }
        x0, P0 = self._initial_state_and_covariance(system0, std_history[0]["ym"])

        # ── Delegate optimisation to CDParameterEstimator ──────────────────
        mbc_est = _CDParameterEstimator(
            model_factory=_model_factory,
            theta0=theta_prior,
            bounds=bounds,
            x0=x0,
            P0=P0,
            dt=self._dt,
            n_steps=10,
            regularization_fn=_regularization_fn,
            n_restarts=_N_RESTARTS,
            restart_perturbation=_RESTART_PERT,
            perturbation_fn=_perturb,
        )

        try:
            mbc_result = mbc_est.estimate(std_history)
        except Exception as exc:
            _LOGGER.error("Parameter estimation failed: %s", exc)
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
                "message": f"Estimation error: {exc}",
            }

        best_theta = mbc_result.theta_best
        best_f = mbc_result.neg_log_likelihood
        best_converged = mbc_result.converged

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

        # Compute reportable log-likelihood (without regularisation)
        try:
            reg = self._compute_regularization(
                log_mass, log_r, q_int, log_alpha, log_r_ij,
                identifiable_pairs,
            )
            log_ll_val: Optional[float] = round(float(-(best_f - reg)), 3)
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
                ))
            model = HouseModel(new_rooms)
            return HouseThermalSystem(
                model, self._sources, self._dt,
                sigma_w=math.sqrt(self._Q_var),
                sigma_v=math.sqrt(self._R_var),
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
        std: List[Dict[str, np.ndarray]] = []
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
            std.append({meas_key: y, "u": u, "d": d})
        return std

    def _compute_regularization(
        self,
        log_mass: np.ndarray,
        log_r: np.ndarray,
        q_int: np.ndarray,
        log_alpha: np.ndarray,
        log_r_ij: np.ndarray,
        identifiable_pairs: List[Tuple[int, int]],
    ) -> float:
        """Gaussian regularisation toward priors for all parameters.

        Internal-gain and α priors use unit-scale weights; the linear-space
        q_int penalty is divided by 100² so the prior std corresponds to
        ~100 W rather than 1 W.
        """
        log_alpha_prior = np.array(
            [self._log_alpha_prior_full[s]
             for s in range(self._n_u)
             if s < self._n_u and len(log_alpha) > 0]
        )[: len(log_alpha)] if len(log_alpha) else np.array([])

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
        x0 = np.zeros(nx, dtype=float)
        n_copy = min(nym, len(first_measurement), nx)
        x0[:n_copy] = np.array(first_measurement[:n_copy], dtype=float)

        P0 = np.eye(nx, dtype=float) * self._R_var * 10.0
        if nx > nym:
            # Augmented latent states (e.g. temperature offsets) are not
            # directly observed at startup, so start them with higher
            # uncertainty than measured temperature components.
            P0[nym:, nym:] *= 4.0
        return x0, P0
