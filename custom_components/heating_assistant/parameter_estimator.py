"""
Maximum-likelihood thermal parameter estimation.

Estimates per-room ``thermal_mass`` (C_i, J/K) and ``r_external`` (R_i, K/W)
by maximising the prediction-error decomposition (PED) form of the Gaussian
log-likelihood:

    log L(θ) = -½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

where νₖ = yₖ − Ax̂ₖ₋₁ − Bu_{k-1} − Ed_{k-1}  (innovations) and
      Sₖ = Pₖ⁻ + R  (innovation covariance; since C = I).

The Kalman filter is evaluated forward through the accumulated data history
for each candidate parameter set.  Parameters are log-transformed
(φ = log θ_physical) to guarantee positivity and improve numerical
conditioning.  A Gaussian regularisation term shrinks the solution toward the
current configured values when data quality is poor.

The optimisation is performed with a pure-NumPy Nelder-Mead implementation
so no additional runtime dependencies beyond numpy are required.
"""

from __future__ import annotations

import copy
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .controller import HouseThermalSystem, _np_to_cvx, _cvx_to_np
from .heat_sources import HeatSource
from .thermal_model import HouseModel, Room

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

#: Minimum std of inter-room temperature difference for R_ij identifiability.
_MIN_TEMP_DIFF_STD = 0.3  # °C


# ── Nelder–Mead (pure NumPy) ─────────────────────────────────────────────────

def _nelder_mead(
    objective,
    x0: np.ndarray,
    tol: float = 1e-4,
    max_iter: Optional[int] = None,
) -> Tuple[np.ndarray, float, bool]:
    """
    Minimise *objective* starting from *x0* using the Nelder–Mead simplex
    method (pure NumPy, no external dependencies).

    Parameters
    ----------
    objective : callable  f(x) → float
    x0        : (n,) initial parameter vector
    tol       : convergence tolerance on both function-value spread and
                simplex diameter
    max_iter  : maximum number of iterations; defaults to 200 × n

    Returns
    -------
    x_best : (n,) best parameter vector found
    f_best : float, objective value at x_best
    converged : bool
    """
    n = len(x0)
    if max_iter is None:
        max_iter = 200 * n

    # Nelder–Mead coefficients (standard values)
    alpha = 1.0   # reflection
    gamma = 2.0   # expansion
    rho = 0.5     # contraction
    sigma = 0.5   # shrink

    # Initialise simplex: x0 ± 5% perturbation along each axis
    simplex = np.empty((n + 1, n))
    simplex[0] = x0.copy()
    for i in range(n):
        s = x0.copy()
        delta = 0.05 * abs(x0[i]) if x0[i] != 0.0 else 0.025
        s[i] += delta
        simplex[i + 1] = s

    fvals = np.array([objective(s) for s in simplex])

    converged = False
    for _ in range(max_iter):
        # Sort ascending
        order = np.argsort(fvals)
        simplex = simplex[order]
        fvals = fvals[order]

        # Convergence checks
        f_spread = abs(fvals[-1] - fvals[0])
        x_spread = np.max(np.abs(simplex[1:] - simplex[0]))
        if f_spread < tol and x_spread < tol:
            converged = True
            break

        # Centroid of all but the worst vertex
        x_bar = simplex[:-1].mean(axis=0)

        # Reflection
        x_r = x_bar + alpha * (x_bar - simplex[-1])
        f_r = objective(x_r)

        if fvals[0] <= f_r < fvals[-2]:
            simplex[-1] = x_r
            fvals[-1] = f_r
        elif f_r < fvals[0]:
            # Expansion
            x_e = x_bar + gamma * (x_r - x_bar)
            f_e = objective(x_e)
            if f_e < f_r:
                simplex[-1] = x_e
                fvals[-1] = f_e
            else:
                simplex[-1] = x_r
                fvals[-1] = f_r
        else:
            # Contraction
            if f_r < fvals[-1]:
                x_c = x_bar + rho * (x_r - x_bar)
                f_c = objective(x_c)
                if f_c <= f_r:
                    simplex[-1] = x_c
                    fvals[-1] = f_c
                    continue
            else:
                x_c = x_bar + rho * (simplex[-1] - x_bar)
                f_c = objective(x_c)
                if f_c <= fvals[-1]:
                    simplex[-1] = x_c
                    fvals[-1] = f_c
                    continue
            # Shrink
            simplex[1:] = simplex[0] + sigma * (simplex[1:] - simplex[0])
            fvals[1:] = np.array([objective(s) for s in simplex[1:]])

    return simplex[0], fvals[0], converged


# ── Main estimator class ─────────────────────────────────────────────────────


def _check_identifiable_connections(
    history: List[Dict[str, Any]],
    room_names: List[str],
    connections: List[Tuple[int, int]],
    min_std: float = _MIN_TEMP_DIFF_STD,
) -> List[Tuple[int, int]]:
    """
    Return the subset of room-index pairs (i, j) for which the inter-room
    temperature difference has sufficient variance for R_ij to be identifiable.

    A connection is identifiable when ``std(T_i − T_j)`` over the history
    window exceeds *min_std* [°C].  With fewer than ``MIN_HISTORY_STEPS``
    samples the list is always empty.

    Parameters
    ----------
    history : list of dicts
        Each record must contain ``"y"`` (list of temperatures in room_names
        order).
    room_names : list of str
        Ordered room names matching the ``y`` vector.
    connections : list of (int, int)
        Unique pairs of room indices to test.  Each pair must appear only
        once (i.e. both (i,j) and (j,i) should NOT both be included).
    min_std : float
        Minimum standard deviation threshold [°C].

    Returns
    -------
    list of (int, int) – identifiable connections.
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

class KalmanMLEstimator:
    """
    Maximum-likelihood estimator for house thermal model parameters.

    Uses the Kalman filter prediction-error decomposition to evaluate the
    log-likelihood for any set of (``thermal_mass``, ``r_external``) values,
    then optimises over those parameters using a pure-NumPy Nelder–Mead
    algorithm.

    Parameters
    ----------
    rooms : list of Room
        Current room configuration.  Only the *structural* fields (name,
        connections, windows, setpoint) are reused; ``thermal_mass`` and
        ``r_external`` are replaced during the search.
    sources : list of HeatSource
        Heat sources – kept fixed throughout estimation.
    dt : float
        Sampling interval [s].  **Must match the history buffer sampling
        interval** (``UPDATE_INTERVAL``, typically 60 s), NOT the MPC control
        horizon step (``DEFAULT_DT``).  Using the wrong value causes the
        discrete-time model matrices to be built for the wrong step length,
        making estimated parameters systematically wrong.
    Q_var : float
        Diagonal process-noise variance used in the Kalman filter [°C²].
        A larger value makes the filter trust the model less.
    R_var : float
        Diagonal measurement-noise variance [°C²].
    regularization : float
        Weight of the Gaussian prior shrinking the solution toward the
        current (configured) parameter values.  Set to 0.0 to disable.
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

        # Build unique inter-room connection pairs (i, j) with i < j.
        # We only add each physical connection once even though RoomConnection
        # objects are stored in both directions.
        room_idx = {r.name: k for k, r in enumerate(rooms)}
        seen: set = set()
        self._connection_pairs: List[Tuple[int, int]] = []  # (i, j) with i < j
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
        parameter values.

        Returns
        -------
        float or None if the model cannot be evaluated.
        """
        x0 = np.concatenate([self._log_mass_prior, self._log_r_prior])
        neg_ll = self._neg_log_likelihood(x0, history)
        if not math.isfinite(neg_ll) or neg_ll >= 1e9:
            return None
        # Subtract the regularisation term (which is zero at the prior)
        return float(-neg_ll)

    def estimate(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Estimate thermal parameters from the accumulated history buffer.

        Runs a two-stage optimisation:

        * **Stage 1** (always): estimate per-room ``thermal_mass`` and
          ``r_external`` using a Kalman-PED maximum-likelihood approach with
          Nelder–Mead.

        * **Stage 2** (conditional): if any inter-room connections are
          identifiable (``std(T_i − T_j) > _MIN_TEMP_DIFF_STD``), extend
          the parameter vector with ``log(R_ij)`` for those connections and
          run a second Nelder–Mead warm-started from the Stage 1 result.

        Parameters
        ----------
        history : list of dict, each containing:
            ``y``         – list[float] room temperatures [°C] (room_names order)
            ``u``         – list[float] control fractions [0, 1] (sources order)
            ``d_outdoor`` – float outdoor temperature [°C]
            ``d_solar``   – dict[str, float] solar gains {room_name: W}

        Returns
        -------
        dict with keys:
            success                   : bool
            estimated_params          : {room_name: {thermal_mass, r_external}}
            current_params            : {room_name: {thermal_mass, r_external}}
            n_steps                   : int
            log_likelihood            : float or None
            message                   : str
            estimated_inter_room_r    : {\"room_i:room_j\": K/W, ...}  (may be {})
            identifiable_connections  : [\"room_i:room_j\", ...]
            stage2_converged          : bool
        """
        n_steps = len(history)
        current = {
            r.name: {
                "thermal_mass": r.thermal_mass,
                "r_external": r.r_external,
            }
            for r in self._rooms
        }

        if n_steps < MIN_HISTORY_STEPS:
            return {
                "success": False,
                "estimated_params": current,
                "current_params": current,
                "n_steps": n_steps,
                "log_likelihood": None,
                "message": (
                    f"Insufficient data: {n_steps} steps available, "
                    f"need ≥ {MIN_HISTORY_STEPS}.  Keep the system running "
                    "and try again when more observations have been collected."
                ),
                "estimated_inter_room_r": {},
                "identifiable_connections": [],
                "stage2_converged": False,
            }

        # ── Stage 1: estimate thermal_mass and r_external ────────────────
        x0_s1 = np.concatenate([self._log_mass_prior, self._log_r_prior])

        def objective_s1(x: np.ndarray) -> float:
            return self._neg_log_likelihood(x, history)

        try:
            x_best, f_best, converged = _nelder_mead(
                objective_s1,
                x0_s1,
                tol=1e-4,
                max_iter=300 * len(x0_s1),
            )

            n = self._n
            log_masses = np.clip(x_best[:n], _LOG_MASS_LO, _LOG_MASS_HI)
            log_rs = np.clip(x_best[n:], _LOG_R_LO, _LOG_R_HI)

            estimated: Dict[str, Dict[str, Any]] = {}
            for i, room_name in enumerate(self._room_names):
                estimated[room_name] = {
                    "thermal_mass": round(float(math.exp(log_masses[i])), 0),
                    "r_external": round(float(math.exp(log_rs[i])), 6),
                }

            # Negative log-likelihood (without regularisation) for reporting
            log_ll_val: Optional[float] = None
            try:
                reg_s1 = self._regularization * float(
                    np.sum((log_masses - self._log_mass_prior) ** 2)
                    + np.sum((log_rs - self._log_r_prior) ** 2)
                )
                log_ll_val = round(float(-(f_best - reg_s1)), 3)
            except Exception:
                pass

            msg = (
                "Optimisation converged."
                if converged
                else "Optimisation reached iteration limit (result may be approximate)."
            )

            # ── Stage 2: estimate inter-room resistances ─────────────────
            estimated_r_ij: Dict[str, float] = {}
            identifiable_names: List[str] = []
            stage2_converged = False

            identifiable_pairs = _check_identifiable_connections(
                history, self._room_names, self._connection_pairs
            )

            if identifiable_pairs:
                # Build extended warm-start from Stage 1 result
                r_ij_priors = []
                for k_pair, (pi, pj) in enumerate(self._connection_pairs):
                    if (pi, pj) in identifiable_pairs:
                        r_ij_priors.append(self._connection_r_priors[k_pair])

                x0_s2 = np.concatenate([log_masses, log_rs, np.array(r_ij_priors)])

                def objective_s2(x: np.ndarray) -> float:
                    lm = x[:n]
                    lr = x[n: 2 * n]
                    lr_ij = x[2 * n:]
                    return self._neg_log_likelihood_with_r_ij(
                        lm, lr, lr_ij, identifiable_pairs, history
                    )

                try:
                    x2_best, f2_best, s2_conv = _nelder_mead(
                        objective_s2,
                        x0_s2,
                        tol=1e-4,
                        max_iter=300 * len(x0_s2),
                    )
                    stage2_converged = s2_conv

                    # Accept Stage 2 only if it improves the log-likelihood
                    if f2_best < f_best:
                        log_masses = np.clip(x2_best[:n], _LOG_MASS_LO, _LOG_MASS_HI)
                        log_rs = np.clip(x2_best[n: 2 * n], _LOG_R_LO, _LOG_R_HI)
                        log_rs_ij = x2_best[2 * n:]

                        for i, room_name in enumerate(self._room_names):
                            estimated[room_name] = {
                                "thermal_mass": round(float(math.exp(log_masses[i])), 0),
                                "r_external": round(float(math.exp(log_rs[i])), 6),
                            }

                        for k_ij, (pi, pj) in enumerate(identifiable_pairs):
                            r_val = float(math.exp(
                                float(np.clip(log_rs_ij[k_ij], _LOG_R_IJ_LO, _LOG_R_IJ_HI))
                            ))
                            key = f"{self._room_names[pi]}:{self._room_names[pj]}"
                            estimated_r_ij[key] = round(r_val, 6)
                            identifiable_names.append(key)

                        # Update log-likelihood using Stage 2 result
                        try:
                            reg_s2 = self._regularization * (
                                float(np.sum((log_masses - self._log_mass_prior) ** 2))
                                + float(np.sum((log_rs - self._log_r_prior) ** 2))
                                + float(np.sum(
                                    (log_rs_ij - np.array(r_ij_priors)) ** 2
                                ))
                            )
                            log_ll_val = round(float(-(f2_best - reg_s2)), 3)
                        except Exception:
                            pass

                        msg += (
                            f"  Stage 2 (inter-room R for "
                            f"{len(identifiable_pairs)} connection(s)) "
                            + ("converged." if s2_conv else "reached iteration limit.")
                        )
                    else:
                        msg += "  Stage 2 did not improve fit; Stage 1 result retained."
                except Exception as exc_s2:
                    _LOGGER.debug("Stage 2 estimation failed: %s", exc_s2)
                    msg += "  Stage 2 failed; Stage 1 result retained."

            return {
                "success": True,
                "estimated_params": estimated,
                "current_params": current,
                "n_steps": n_steps,
                "log_likelihood": log_ll_val,
                "message": msg,
                "estimated_inter_room_r": estimated_r_ij,
                "identifiable_connections": identifiable_names,
                "stage2_converged": stage2_converged,
            }

        except Exception as exc:
            _LOGGER.error("Parameter estimation failed: %s", exc)
            return {
                "success": False,
                "estimated_params": current,
                "current_params": current,
                "n_steps": n_steps,
                "log_likelihood": None,
                "message": f"Estimation error: {exc}",
                "estimated_inter_room_r": {},
                "identifiable_connections": [],
                "stage2_converged": False,
            }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _build_system(
        self,
        log_masses: np.ndarray,
        log_rs: np.ndarray,
        log_r_ij: Optional[Dict[Tuple[int, int], float]] = None,
    ) -> Optional[HouseThermalSystem]:
        """Build a :class:`HouseThermalSystem` from log-transformed parameters.

        Parameters
        ----------
        log_masses : (n,) log thermal masses
        log_rs     : (n,) log external resistances
        log_r_ij   : optional {(i, j): log_r_value} for inter-room connections
                     that should override configured values.
        """
        try:
            room_idx = {r.name: k for k, r in enumerate(self._rooms)}
            new_rooms = []
            for i, r in enumerate(self._rooms):
                # Deep-copy connections so we can modify r_value without
                # affecting the original room objects stored in the coordinator
                new_conns = copy.deepcopy(r.connections)
                if log_r_ij:
                    j_idx = room_idx.get
                    for conn in new_conns:
                        j = j_idx(conn.connected_room)
                        if j is not None:
                            pair = (min(i, j), max(i, j))
                            if pair in log_r_ij:
                                conn.r_value = float(math.exp(
                                    float(np.clip(log_r_ij[pair], _LOG_R_IJ_LO, _LOG_R_IJ_HI))
                                ))
                new_rooms.append(Room(
                    name=r.name,
                    thermal_mass=float(math.exp(log_masses[i])),
                    r_external=float(math.exp(log_rs[i])),
                    connections=new_conns,
                    windows=r.windows,
                    temperature=r.temperature,
                    setpoint=r.setpoint,
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
    ) -> np.ndarray:
        """Build a numpy disturbance vector [T_out, Q_sol_1, …, Q_sol_n]."""
        p = system.n_d
        d = np.zeros(p)
        d[0] = d_outdoor
        for name, gain in d_solar.items():
            if name in system._room_idx:
                d[1 + system._room_idx[name]] = gain
        return d

    def _neg_log_likelihood(
        self,
        log_params: np.ndarray,
        history: List[Dict[str, Any]],
    ) -> float:
        """
        Compute the negative PED log-likelihood plus regularisation.

        Returns a large positive sentinel (1e10) for invalid parameter sets.
        """
        n = self._n
        log_masses = log_params[:n]
        log_rs = log_params[n:]

        # Reject out-of-bounds or NaN parameters
        if np.any(~np.isfinite(log_params)):
            return 1e10
        if np.any(log_masses < _LOG_MASS_LO) or np.any(log_masses > _LOG_MASS_HI):
            return 1e10
        if np.any(log_rs < _LOG_R_LO) or np.any(log_rs > _LOG_R_HI):
            return 1e10

        system = self._build_system(log_masses, log_rs)
        if system is None:
            return 1e10

        Q = np.eye(n) * self._Q_var
        R = np.eye(n) * self._R_var

        # Bootstrap the Kalman filter from the first measurement
        first = history[0]
        x_hat = np.array(first["y"][:n], dtype=float)
        P = np.eye(n)
        u_prev = np.zeros(self._n_u)
        raw_u = first.get("u", [])
        for k, val in enumerate(raw_u):
            if k < self._n_u:
                u_prev[k] = float(val)
        d_prev = self._make_d_np(system, first["d_outdoor"], first["d_solar"])

        neg_ll = 0.0

        for record in history[1:]:
            y = np.array(record["y"][:n], dtype=float)
            d = self._make_d_np(system, record["d_outdoor"], record["d_solar"])

            # Discretise model at the *previous* disturbance (ZOH)
            try:
                A_cvx, B_cvx, E_cvx = system.discretize(_np_to_cvx(d_prev))
            except Exception:
                return 1e10
            A = _cvx_to_np(A_cvx)
            B = _cvx_to_np(B_cvx)
            E = _cvx_to_np(E_cvx)

            # Prediction step
            x_pred = A @ x_hat + B @ u_prev + E @ d_prev
            P_pred = A @ P @ A.T + Q

            # Innovation (C = I)
            nu = y - x_pred
            S = P_pred + R

            # Log-likelihood contribution: −½ (log|S| + νᵀ S⁻¹ ν)
            try:
                sign, logdet = np.linalg.slogdet(S)
                if sign <= 0:
                    return 1e10
                S_inv_nu = np.linalg.solve(S, nu)
                neg_ll += 0.5 * (logdet + float(nu @ S_inv_nu))
            except np.linalg.LinAlgError:
                return 1e10

            # Kalman update – Joseph form with C = I
            # K = P_pred S⁻¹
            try:
                K = np.linalg.solve(S.T, P_pred.T).T
            except np.linalg.LinAlgError:
                return 1e10
            IKC = np.eye(n) - K   # since C = I
            x_hat = x_pred + K @ nu
            P = IKC @ P_pred @ IKC.T + K @ R @ K.T
            P = (P + P.T) * 0.5   # enforce symmetry

            # Prepare for next step
            u_prev = np.zeros(self._n_u)
            raw_u = record.get("u", [])
            for k, val in enumerate(raw_u):
                if k < self._n_u:
                    u_prev[k] = float(val)
            d_prev = d

        # Gaussian regularisation: penalise deviation from log-prior
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
        """
        Like :meth:`_neg_log_likelihood` but also includes inter-room
        resistance parameters ``log_rs_ij`` for *identifiable_pairs*.

        The ``log_rs_ij`` vector must be in the same order as
        ``identifiable_pairs``.
        """
        n = self._n

        if np.any(~np.isfinite(log_masses)) or np.any(~np.isfinite(log_rs)):
            return 1e10
        if np.any(~np.isfinite(log_rs_ij)):
            return 1e10
        if np.any(log_masses < _LOG_MASS_LO) or np.any(log_masses > _LOG_MASS_HI):
            return 1e10
        if np.any(log_rs < _LOG_R_LO) or np.any(log_rs > _LOG_R_HI):
            return 1e10

        log_r_ij_map: Dict[Tuple[int, int], float] = {
            pair: float(log_rs_ij[k])
            for k, pair in enumerate(identifiable_pairs)
        }

        system = self._build_system(log_masses, log_rs, log_r_ij=log_r_ij_map)
        if system is None:
            return 1e10

        Q = np.eye(n) * self._Q_var
        R_noise = np.eye(n) * self._R_var

        first = history[0]
        x_hat = np.array(first["y"][:n], dtype=float)
        P = np.eye(n)
        u_prev = np.zeros(self._n_u)
        raw_u = first.get("u", [])
        for k, val in enumerate(raw_u):
            if k < self._n_u:
                u_prev[k] = float(val)
        d_prev = self._make_d_np(system, first["d_outdoor"], first["d_solar"])

        neg_ll = 0.0

        for record in history[1:]:
            y = np.array(record["y"][:n], dtype=float)
            d = self._make_d_np(system, record["d_outdoor"], record["d_solar"])

            try:
                A_cvx, B_cvx, E_cvx = system.discretize(_np_to_cvx(d_prev))
            except Exception:
                return 1e10
            A = _cvx_to_np(A_cvx)
            B = _cvx_to_np(B_cvx)

            x_pred = A @ x_hat + B @ u_prev
            P_pred = A @ P @ A.T + Q

            nu = y - x_pred
            S = P_pred + R_noise

            try:
                sign, logdet = np.linalg.slogdet(S)
                if sign <= 0:
                    return 1e10
                S_inv_nu = np.linalg.solve(S, nu)
                neg_ll += 0.5 * (logdet + float(nu @ S_inv_nu))
            except np.linalg.LinAlgError:
                return 1e10

            try:
                K = np.linalg.solve(S.T, P_pred.T).T
            except np.linalg.LinAlgError:
                return 1e10
            IKC = np.eye(n) - K
            x_hat = x_pred + K @ nu
            P = IKC @ P_pred @ IKC.T + K @ R_noise @ K.T
            P = (P + P.T) * 0.5

            u_prev = np.zeros(self._n_u)
            raw_u = record.get("u", [])
            for k, val in enumerate(raw_u):
                if k < self._n_u:
                    u_prev[k] = float(val)
            d_prev = d

        # Regularisation includes prior on R_ij
        r_ij_priors_arr = np.array([self._connection_r_priors[
            self._connection_pairs.index(pair)
        ] for pair in identifiable_pairs])
        reg = self._regularization * float(
            np.sum((log_masses - self._log_mass_prior) ** 2)
            + np.sum((log_rs - self._log_r_prior) ** 2)
            + np.sum((log_rs_ij - r_ij_priors_arr) ** 2)
        )

        return neg_ll + reg
