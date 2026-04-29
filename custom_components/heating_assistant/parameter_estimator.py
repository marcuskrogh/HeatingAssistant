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

from .controller import HouseThermalSystem, _np_to_cvx, _cvx_to_np
from .heat_sources import HeatSource
from .thermal_model import HouseModel, Room
from mbc.identification import ParameterEstimator as _MbcEstimator
from mbc.identification import ped_neg_log_likelihood as _ped_neg_ll
from mbc.identification import nelder_mead as _nelder_mead  # backward compat

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


# ── Augmented model wrapping HouseThermalSystem ───────────────────────────────


class _AugmentedHouseModel:
    """
    Wraps :class:`~controller.HouseThermalSystem` to absorb two
    theta-dependent effects that are not captured in the ZOH matrices:

    *  **Heater power-scale** (``alpha_full``) — scales each column of B_d
       so that ``B_d_aug @ u_raw = B_d @ (alpha ⊙ u_raw)``.
    *  **Internal heat gain** (``q_int``) — a per-room constant additive
       heat source applied via :meth:`predict_offset`.

    The internal gain is pre-computed as ``E_d[:, 1:n+1] @ q_int_vec``
    (a constant vector, since E_d is time-invariant in the RC model).

    The wrapped model conforms to the duck-type interface expected by
    :func:`mbc.identification.ped_neg_log_likelihood`.
    """

    def __init__(
        self,
        system: HouseThermalSystem,
        alpha_full: np.ndarray,
        q_int_vec: np.ndarray,
    ) -> None:
        self._system = system
        self._alpha = alpha_full

        # Precompute E_d @ q_int correction (E_d is time-invariant)
        from cvxopt import matrix as _cvx_mat
        n_d = system.n_d
        d_nominal = _cvx_mat([0.0] * n_d, (n_d, 1), tc="d")
        _, _, E_cvx = system.discretize(d_nominal)
        E_np = _cvx_to_np(E_cvx)
        n = system.n_x
        # q_int maps through columns 1..n of E_d (solar/internal gain slots)
        self._q_int_bias: np.ndarray = E_np[:, 1:1 + n] @ q_int_vec

    # ── LinearDiscreteModel duck-type interface ───────────────────────────

    @property
    def n_x(self) -> int:
        return self._system.n_x

    @property
    def n_u(self) -> int:
        return self._system.n_u

    @property
    def n_d(self) -> int:
        return self._system.n_d

    @property
    def C(self):
        return self._system.C

    @property
    def x(self):
        return self._system.x

    @x.setter
    def x(self, val) -> None:
        self._system.x = val

    @property
    def x_ref(self):
        return self._system.x_ref

    @property
    def u_bounds(self):
        return self._system.u_bounds

    def discretize(self, d):
        """Return (A, B_alpha, E) where B_alpha columns are scaled by alpha."""
        A, B, E = self._system.discretize(d)
        B_np = _cvx_to_np(B)
        for j, a in enumerate(self._alpha):
            B_np[:, j] *= float(a)
        return A, _np_to_cvx(B_np), E

    def predict_offset(self, d_np: np.ndarray) -> np.ndarray:
        """Constant q_int contribution: E_d[:, 1:n+1] @ q_int_vec."""
        return self._q_int_bias


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
        Evaluate the Kalman PED log-likelihood at the *current* configured
        parameter values (without regularisation).
        """
        x0 = np.concatenate([self._log_mass_prior, self._log_r_prior])
        neg_ll = self._neg_log_likelihood(x0, history)
        if not math.isfinite(neg_ll) or neg_ll >= 1e9:
            return None
        return float(-neg_ll)

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

        # ── Convert history to standardised format (fixed, theta-agnostic) ─
        std_history = self._convert_history_std(history)

        # ── Build model factory for mbc ────────────────────────────────────
        def _model_factory(theta: np.ndarray):
            lm, lr, qi, la, lrij = layout.unpack(theta)
            lrij_map: Optional[Dict[Tuple[int, int], float]] = None
            if len(lrij):
                lrij_map = {
                    pair: float(lrij[k])
                    for k, pair in enumerate(layout.identifiable_pairs)
                }
            sys_ = self._build_system(lm, lr, log_r_ij=lrij_map)
            if sys_ is None:
                raise ValueError("Failed to build thermal system")
            alpha_full = np.ones(self._n_u)
            for k, s_idx in enumerate(layout.identifiable_sources):
                alpha_full[s_idx] = float(math.exp(la[k]))
            return _AugmentedHouseModel(sys_, alpha_full, qi)

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

        # ── Delegate optimisation to mbc.ParameterEstimator ───────────────
        Q_np = np.eye(n) * self._Q_var
        R_np = np.eye(n) * self._R_var

        mbc_est = _MbcEstimator(
            model_factory=_model_factory,
            theta0=theta_prior,
            bounds=bounds,
            Q=Q_np,
            R=R_np,
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
        except Exception:
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
    ) -> List[Dict[str, np.ndarray]]:
        """Convert the HA history buffer to the standardised mbc format.

        Each record is converted to ``{"y": ndarray, "u": ndarray, "d": ndarray}``
        where ``d = [T_out, solar_1, …, solar_n]`` (raw, without q_int).
        The q_int contribution is absorbed by
        :class:`_AugmentedHouseModel.predict_offset`.
        """
        n = self._n
        n_u = self._n_u
        room_idx = {name: i for i, name in enumerate(self._room_names)}
        std: List[Dict[str, np.ndarray]] = []
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
            std.append({"y": y, "u": u, "d": d})
        return std

    def _kalman_neg_ll_via_mbc(
        self,
        system: HouseThermalSystem,
        history: List[Dict[str, Any]],
        q_int: Optional[np.ndarray],
        alpha_full: Optional[np.ndarray],
    ) -> float:
        """Evaluate the PED neg-log-likelihood via mbc, applying alpha and q_int.

        Replaces the former ``_kalman_neg_ll`` implementation.  Builds an
        :class:`_AugmentedHouseModel` and delegates to
        :func:`mbc.identification.ped_neg_log_likelihood`.
        """
        if alpha_full is None:
            alpha_full = np.ones(self._n_u)
        if q_int is None:
            q_int = np.zeros(self._n)

        aug = _AugmentedHouseModel(system, alpha_full, q_int)
        std_history = self._convert_history_std(history)
        Q = np.eye(self._n) * self._Q_var
        R = np.eye(self._n) * self._R_var
        return _ped_neg_ll(lambda _: aug, np.array([]), std_history, Q, R)

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

    def _neg_log_likelihood_full(
        self,
        theta: np.ndarray,
        layout: _ThetaLayout,
        history: List[Dict[str, Any]],
    ) -> float:
        """Negative PED log-likelihood for the joint parameter vector."""
        if np.any(~np.isfinite(theta)):
            return 1e10

        log_mass, log_r, q_int, log_alpha, log_r_ij = layout.unpack(theta)

        if (np.any(log_mass < _LOG_MASS_LO) or np.any(log_mass > _LOG_MASS_HI)
                or np.any(log_r < _LOG_R_LO) or np.any(log_r > _LOG_R_HI)
                or np.any(q_int < _Q_INT_LO) or np.any(q_int > _Q_INT_HI)):
            return 1e10
        if len(log_alpha) and (np.any(log_alpha < _LOG_ALPHA_LO)
                               or np.any(log_alpha > _LOG_ALPHA_HI)):
            return 1e10
        if len(log_r_ij) and (np.any(log_r_ij < _LOG_R_IJ_LO)
                              or np.any(log_r_ij > _LOG_R_IJ_HI)):
            return 1e10

        # Build the system with current C, R_ext, R_ij
        log_r_ij_map: Optional[Dict[Tuple[int, int], float]] = None
        if len(log_r_ij):
            log_r_ij_map = {
                pair: float(log_r_ij[k])
                for k, pair in enumerate(layout.identifiable_pairs)
            }
        system = self._build_system(log_mass, log_r, log_r_ij=log_r_ij_map)
        if system is None:
            return 1e10

        # Build full alpha vector (unit for non-identifiable sources, exp for
        # identifiable ones)
        alpha_full = np.ones(self._n_u)
        for k, s_idx in enumerate(layout.identifiable_sources):
            alpha_full[s_idx] = float(math.exp(log_alpha[k]))

        neg_ll = self._kalman_neg_ll_via_mbc(
            system, history,
            q_int=q_int,
            alpha_full=alpha_full,
        )
        if neg_ll >= 1e9:
            return neg_ll

        reg = self._compute_regularization(
            log_mass, log_r, q_int, log_alpha, log_r_ij,
            layout.identifiable_pairs,
        )
        return neg_ll + reg

    # ── Backward-compatible 2-parameter likelihood (used by tests) ────────

    def _neg_log_likelihood(
        self,
        log_params: np.ndarray,
        history: List[Dict[str, Any]],
    ) -> float:
        """Compute neg-LL using only (log_mass, log_r) — no Q_int, no α.

        Preserved for backward compatibility with existing unit tests.  The
        full joint likelihood is :meth:`_neg_log_likelihood_full`.
        """
        n = self._n
        log_masses = log_params[:n]
        log_rs = log_params[n:]

        if np.any(~np.isfinite(log_params)):
            return 1e10
        if np.any(log_masses < _LOG_MASS_LO) or np.any(log_masses > _LOG_MASS_HI):
            return 1e10
        if np.any(log_rs < _LOG_R_LO) or np.any(log_rs > _LOG_R_HI):
            return 1e10

        system = self._build_system(log_masses, log_rs)
        if system is None:
            return 1e10

        neg_ll = self._kalman_neg_ll_via_mbc(
            system, history,
            q_int=None,
            alpha_full=None,
        )
        if neg_ll >= 1e9:
            return neg_ll

        reg = self._regularization * float(
            np.sum((log_masses - self._log_mass_prior) ** 2)
            + np.sum((log_rs - self._log_r_prior) ** 2)
        )
        return neg_ll + reg

    def _neg_log_likelihood_with_r_ij(
        self,
        log_masses: np.ndarray,
        log_rs: np.ndarray,
        log_rs_ij: np.ndarray,
        identifiable_pairs: List[Tuple[int, int]],
        history: List[Dict[str, Any]],
    ) -> float:
        """Backward-compatible neg-LL with R_ij but no Q_int / α."""
        n = self._n

        if (np.any(~np.isfinite(log_masses)) or np.any(~np.isfinite(log_rs))
                or np.any(~np.isfinite(log_rs_ij))):
            return 1e10
        if np.any(log_masses < _LOG_MASS_LO) or np.any(log_masses > _LOG_MASS_HI):
            return 1e10
        if np.any(log_rs < _LOG_R_LO) or np.any(log_rs > _LOG_R_HI):
            return 1e10
        if len(log_rs_ij) and (np.any(log_rs_ij < _LOG_R_IJ_LO)
                               or np.any(log_rs_ij > _LOG_R_IJ_HI)):
            return 1e10

        log_r_ij_map = {
            pair: float(log_rs_ij[k])
            for k, pair in enumerate(identifiable_pairs)
        }
        system = self._build_system(log_masses, log_rs, log_r_ij=log_r_ij_map)
        if system is None:
            return 1e10

        neg_ll = self._kalman_neg_ll_via_mbc(
            system, history,
            q_int=None,
            alpha_full=None,
        )
        if neg_ll >= 1e9:
            return neg_ll

        r_ij_priors_arr = np.array([
            self._connection_r_priors[self._connection_pairs.index(pair)]
            for pair in identifiable_pairs
        ])
        reg = self._regularization * float(
            np.sum((log_masses - self._log_mass_prior) ** 2)
            + np.sum((log_rs - self._log_r_prior) ** 2)
            + np.sum((log_rs_ij - r_ij_priors_arr) ** 2)
        )
        return neg_ll + reg
