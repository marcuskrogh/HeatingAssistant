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
        Sampling interval [s].
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
            success          : bool
            estimated_params : {room_name: {thermal_mass, r_external}}
            current_params   : {room_name: {thermal_mass, r_external}}
            n_steps          : int
            log_likelihood   : float or None
            message          : str
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
            }

        x0 = np.concatenate([self._log_mass_prior, self._log_r_prior])

        def objective(x: np.ndarray) -> float:
            return self._neg_log_likelihood(x, history)

        try:
            x_best, f_best, converged = _nelder_mead(
                objective,
                x0,
                tol=1e-4,
                max_iter=300 * len(x0),
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
                reg = self._regularization * float(
                    np.sum((log_masses - self._log_mass_prior) ** 2)
                    + np.sum((log_rs - self._log_r_prior) ** 2)
                )
                log_ll_val = round(float(-(f_best - reg)), 3)
            except Exception:
                pass

            msg = (
                "Optimisation converged."
                if converged
                else "Optimisation reached iteration limit (result may be approximate)."
            )

            return {
                "success": True,
                "estimated_params": estimated,
                "current_params": current,
                "n_steps": n_steps,
                "log_likelihood": log_ll_val,
                "message": msg,
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
            }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _build_system(
        self,
        log_masses: np.ndarray,
        log_rs: np.ndarray,
    ) -> Optional[HouseThermalSystem]:
        """Build a :class:`HouseThermalSystem` from log-transformed parameters."""
        try:
            new_rooms = [
                Room(
                    name=r.name,
                    thermal_mass=float(math.exp(log_masses[i])),
                    r_external=float(math.exp(log_rs[i])),
                    connections=r.connections,
                    windows=r.windows,
                    temperature=r.temperature,
                    setpoint=r.setpoint,
                )
                for i, r in enumerate(self._rooms)
            ]
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
