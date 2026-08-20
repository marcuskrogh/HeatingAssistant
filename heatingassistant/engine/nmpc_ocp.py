"""Certainty-equivalent mean ODE OCP for the slow NMPC rate.

Single shooting in the slow input sequence ``U``.  Each slow interval holds
``u_n`` constant across ``M`` fast implicit-Euler ticks.  Analytic ``dJ/dU``
chains production ``dfdx`` / ``dfdu`` through the implicit-Euler map.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Optional

import numpy as np
from scipy.optimize import minimize

from .heat_sources import HeatSource
from .integrator import implicit_euler_substeps
from .nmpc_accept import accept_plan
from .nmpc_timing import NmpcTiming

NMPC_MAXITER = 200
NMPC_TIMEOUT_S = 60.0
NMPC_FTOL = 1e-6
# Installed |U| below this is treated as idle (off) for re-solve scheduling.
NMPC_IDLE_U_ABS = 1e-6


def electrical_w(sources: Sequence[HeatSource], u: np.ndarray) -> float:
    """Electrical draw [W] for a control vector."""

    power = 0.0
    for j, src in enumerate(sources):
        uj = float(u[j])
        if uj >= 0.0:
            power += float(src.elec_per_unit_heat) * uj
        else:
            power += float(src.elec_per_unit_cool) * (-uj)
    return power


def electrical_dPdu(sources: Sequence[HeatSource], u: np.ndarray) -> np.ndarray:
    """``dP_elec / du`` [W per unit] for the electrical-draw map."""

    grad = np.zeros(len(sources), dtype=float)
    for j, src in enumerate(sources):
        uj = float(u[j])
        if uj > 0.0:
            grad[j] = float(src.elec_per_unit_heat)
        elif uj < 0.0:
            grad[j] = -float(src.elec_per_unit_cool)
    return grad


def mean_price_slow(price_fast: np.ndarray, m: int, n_slow: int) -> np.ndarray:
    """Average fast-rate prices onto each slow interval (persistence pad)."""

    raw = np.asarray(price_fast, dtype=float).reshape(-1)
    out = np.zeros(n_slow, dtype=float)
    if raw.size == 0:
        return out
    for n in range(n_slow):
        start = n * m
        stop = start + m
        if start >= raw.size:
            out[n] = float(raw[-1])
            continue
        chunk = raw[start:min(stop, raw.size)]
        if chunk.size < m:
            chunk = np.concatenate([chunk, np.full(m - chunk.size, chunk[-1])])
        out[n] = float(np.mean(chunk))
    return out


class MeanOcp:
    """Slow-rate mean OCP: comfort zone + ROM + electricity price."""

    def __init__(
        self,
        sde: Any,
        sources: Sequence[HeatSource],
        timing: NmpcTiming,
        x0: np.ndarray,
        u_prev: np.ndarray,
        d_fast: Sequence[np.ndarray],
        *,
        t_min: np.ndarray,
        t_max: np.ndarray,
        rho: float,
        s_rom: float,
        energy_price_weight: float,
        price_slow: Optional[np.ndarray] = None,
        u_min: Optional[np.ndarray] = None,
        u_max: Optional[np.ndarray] = None,
        n_int_steps: Optional[int] = None,
    ) -> None:
        self.sde = sde
        self.sources = list(sources)
        self.timing = timing
        self.m = int(timing.fast_substeps)
        self.n = int(timing.n_slow)
        self.dt_s = float(timing.dt_s)
        self.nu = int(sde.nu)
        self.n_rooms = int(sde._n_rooms)
        self.x0 = np.asarray(x0, dtype=float).copy()
        self.u_prev = np.asarray(u_prev, dtype=float).reshape(self.nu).copy()
        self.p = np.array([], dtype=float)
        self.rho = float(rho)
        self.s_rom = float(s_rom)
        self.energy_price_weight = float(energy_price_weight)
        self.dt_h_slow = float(timing.period_s) / 3600.0
        self.n_int = int(
            n_int_steps if n_int_steps is not None else getattr(sde, "_n_int_steps", 10)
        )
        self._h_sub = self.dt_s / float(self.n_int)

        t_lo = np.asarray(t_min, dtype=float)
        t_hi = np.asarray(t_max, dtype=float)
        n_fast = self.n * self.m
        if t_lo.ndim == 1:
            t_lo = np.tile(t_lo.reshape(1, -1), (n_fast, 1))
        if t_hi.ndim == 1:
            t_hi = np.tile(t_hi.reshape(1, -1), (n_fast, 1))
        if t_lo.shape[0] < n_fast:
            pad = np.tile(t_lo[-1:], (n_fast - t_lo.shape[0], 1))
            t_lo = np.vstack([t_lo, pad])
        if t_hi.shape[0] < n_fast:
            pad = np.tile(t_hi[-1:], (n_fast - t_hi.shape[0], 1))
            t_hi = np.vstack([t_hi, pad])
        self.t_min = t_lo[:n_fast]
        self.t_max = t_hi[:n_fast]

        d_list = [np.asarray(d, dtype=float) for d in d_fast]
        if len(d_list) < n_fast:
            if not d_list:
                raise ValueError("d_fast must contain at least one disturbance")
            last = d_list[-1]
            d_list.extend([last.copy() for _ in range(n_fast - len(d_list))])
        self._d_fast = d_list[:n_fast]

        if price_slow is None:
            self._price_slow = np.zeros(self.n, dtype=float)
        else:
            ps = np.asarray(price_slow, dtype=float).reshape(-1)
            if ps.size < self.n:
                fill = float(ps[-1]) if ps.size else 0.0
                ps = np.concatenate([ps, np.full(self.n - ps.size, fill)])
            self._price_slow = ps[: self.n]

        u_lo_src, u_hi_src = sde.u_bounds
        if u_min is None:
            u_min = np.tile(np.asarray(u_lo_src, dtype=float).reshape(1, -1), (self.n, 1))
        if u_max is None:
            u_max = np.tile(np.asarray(u_hi_src, dtype=float).reshape(1, -1), (self.n, 1))
        self.u_min = np.asarray(u_min, dtype=float).reshape(self.n, self.nu)
        self.u_max = np.asarray(u_max, dtype=float).reshape(self.n, self.nu)
        self.bounds = [
            (float(self.u_min[n, j]), float(self.u_max[n, j]))
            for n in range(self.n)
            for j in range(self.nu)
        ]

        self.nfev = 0
        self.njev = 0
        self.deadline: float | None = None
        self.timed_out = False
        self.best_u: np.ndarray | None = None
        self.best_j = float("inf")
        self._M = np.eye(sde.nx)

    def _refresh_M(self, x: np.ndarray, u: np.ndarray, d: np.ndarray) -> None:
        a0 = self.sde.dfdx(x, u, d, self.p, 0.0)
        eye = np.eye(self.sde.nx)
        self._M = np.linalg.inv(eye - self._h_sub * a0)

    def _maybe_timeout(self) -> None:
        if self.deadline is not None and time.perf_counter() > self.deadline:
            self.timed_out = True
            raise TimeoutError("NMPC wall-clock timeout")

    def _record_best(self, U: np.ndarray, cost: float) -> None:
        if np.isfinite(cost) and cost < self.best_j:
            self.best_j = float(cost)
            self.best_u = np.asarray(U, dtype=float).reshape(self.n, self.nu).copy()

    def _roll_maybe_jac(
        self, U: np.ndarray, *, with_jac: bool
    ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray | None]:
        self._maybe_timeout()
        self.nfev += 1
        U = np.asarray(U, dtype=float).reshape(self.n, self.nu)
        x = self.x0.copy()
        air_path = []
        cost = 0.0
        u_prev = self.u_prev
        n_dec = self.n * self.nu
        sx = np.zeros((self.sde.nx, n_dec)) if with_jac else None
        g = np.zeros(n_dec) if with_jac else None
        for n in range(self.n):
            u_n = U[n]
            col = n * self.nu
            for m in range(self.m):
                k = n * self.m + m
                d_k = self._d_fast[k]
                rhs = lambda xx, u=u_n, d=d_k: self.sde.f(xx, u, d, self.p, 0.0)
                jacx = lambda xx, u=u_n, d=d_k: self.sde.dfdx(xx, u, d, self.p, 0.0)
                x = implicit_euler_substeps(rhs, jacx, x, self.dt_s, self.n_int)
                ta = x[: self.n_rooms]
                air_path.append(ta.copy())
                lo = self.t_min[k]
                hi = self.t_max[k]
                viol = np.maximum(0.0, lo - ta) + np.maximum(0.0, ta - hi)
                cost += self.rho * float(np.dot(viol, viol))
                if with_jac:
                    self._refresh_M(x, u_n, d_k)
                    b_u = self.sde.dfdu(x, u_n, d_k, self.p, 0.0)
                    hmb = self._h_sub * (self._M @ b_u)
                    for _ in range(self.n_int):
                        sx = self._M @ sx
                        sx[:, col : col + self.nu] += hmb
                    sta = sx[: self.n_rooms]
                    for i in range(self.n_rooms):
                        if ta[i] < lo[i]:
                            viol_i = lo[i] - ta[i]
                            g += self.rho * 2.0 * viol_i * (-1.0) * sta[i]
                        elif ta[i] > hi[i]:
                            viol_i = ta[i] - hi[i]
                            g += self.rho * 2.0 * viol_i * (1.0) * sta[i]
            du = u_n - u_prev
            cost += self.s_rom * float(np.dot(du, du))
            if self.energy_price_weight > 0.0:
                cost += (
                    self.energy_price_weight
                    * self._price_slow[n]
                    * electrical_w(self.sources, u_n)
                    * 1e-3
                    * self.dt_h_slow
                )
            if with_jac:
                g[col : col + self.nu] += 2.0 * self.s_rom * du
                if n > 0:
                    g[col - self.nu : col] -= 2.0 * self.s_rom * du
                if self.energy_price_weight > 0.0:
                    g[col : col + self.nu] += (
                        self.energy_price_weight
                        * self._price_slow[n]
                        * 1e-3
                        * self.dt_h_slow
                        * electrical_dPdu(self.sources, u_n)
                    )
            u_prev = u_n
        self._record_best(U, cost)
        return cost, U, np.asarray(air_path), g

    def cost(self, u_flat: np.ndarray) -> float:
        j, _, _air = self._roll_maybe_jac(u_flat, with_jac=False)[:3]
        return j

    def jac(self, u_flat: np.ndarray) -> np.ndarray:
        self.njev += 1
        _, _, _, g = self._roll_maybe_jac(u_flat, with_jac=True)
        assert g is not None
        return g

    def roll(self, u_flat: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        j, U, air, _ = self._roll_maybe_jac(u_flat, with_jac=False)
        return j, U, air


def _clip_to_bounds(u: np.ndarray, ocp: MeanOcp) -> np.ndarray:
    """Clip a flat or shaped decision vector to the OCP box bounds."""

    flat = np.asarray(u, dtype=float).reshape(-1)
    expected = ocp.n * ocp.nu
    if flat.size != expected:
        flat = np.zeros(expected, dtype=float)
    lo = np.array([b[0] for b in ocp.bounds], dtype=float)
    hi = np.array([b[1] for b in ocp.bounds], dtype=float)
    return np.clip(flat, lo, hi)


def _cauchy_warm_start(
    ocp: MeanOcp, u0: np.ndarray, j_ref: float
) -> tuple[np.ndarray, float]:
    """Take a feasible steepest-descent step so SLSQP is not stuck at u = 0.

    Signed heat-pump bounds include 0 in the interior.  From a zero start,
    SLSQP can report success without searching u < 0.  One projected
    gradient step (when it lowers J) puts the iterate in the cooling or
    heating region before SLSQP runs.
    """

    if not np.isfinite(j_ref):
        return u0, j_ref
    try:
        grad = np.asarray(ocp.jac(u0), dtype=float).reshape(-1)
    except TimeoutError:
        raise
    if grad.size != u0.size or not np.isfinite(grad).all():
        return u0, j_ref
    gmax = float(np.max(np.abs(grad)))
    if gmax <= 1e-12:
        return u0, j_ref
    direction = -grad / gmax
    best_u = u0
    best_j = j_ref
    for alpha in (1.0, 0.3, 0.1):
        cand = _clip_to_bounds(u0 + alpha * direction, ocp)
        if np.allclose(cand, u0, atol=1e-12):
            continue
        try:
            j_cand = float(ocp.cost(cand))
        except TimeoutError:
            raise
        if np.isfinite(j_cand) and j_cand < best_j:
            best_u = cand
            best_j = j_cand
            break
    return best_u, best_j


def _signed_probe_if_idle(
    ocp: MeanOcp, u_mat: np.ndarray, fun: float
) -> tuple[np.ndarray, float]:
    """If SLSQP stayed at ~0, try a mid-bound signed probe (cooling or heat)."""

    u_mat = np.asarray(u_mat, dtype=float).reshape(ocp.n, ocp.nu)
    if float(np.max(np.abs(u_mat))) >= 0.05:
        return u_mat, fun
    lo = np.asarray(ocp.u_min, dtype=float).reshape(ocp.n, ocp.nu)
    hi = np.asarray(ocp.u_max, dtype=float).reshape(ocp.n, ocp.nu)
    probe = np.zeros_like(u_mat)
    for j in range(ocp.nu):
        lo_j = float(np.min(lo[:, j]))
        hi_j = float(np.max(hi[:, j]))
        if lo_j < -1e-9:
            probe[:, j] = 0.5 * lo_j
        elif hi_j > 1e-9:
            probe[:, j] = 0.5 * hi_j
    probe = np.clip(probe, lo, hi)
    if float(np.max(np.abs(probe))) < 0.05:
        return u_mat, fun
    try:
        j_probe = float(ocp.cost(probe.reshape(-1)))
    except TimeoutError:
        return u_mat, fun
    if np.isfinite(j_probe) and (not np.isfinite(fun) or j_probe < fun):
        return probe, j_probe
    return u_mat, fun


def solve_mean_ocp(
    ocp: MeanOcp,
    u0: np.ndarray,
    *,
    maxiter: int = NMPC_MAXITER,
    timeout_s: float = NMPC_TIMEOUT_S,
    minimize_fn: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Run SLSQP on ``ocp`` and return the candidate plan (accept is separate)."""

    ocp.nfev = 0
    ocp.njev = 0
    ocp.timed_out = False
    ocp.best_u = None
    ocp.best_j = float("inf")
    ocp.deadline = time.perf_counter() + float(timeout_s)
    t0 = time.perf_counter()
    status = "ok"
    message = ""
    u0 = np.asarray(u0, dtype=float).reshape(-1)
    expected = ocp.n * ocp.nu
    if u0.size != expected:
        u0 = np.zeros(expected, dtype=float)
    u_star = u0.copy()
    nit = 0
    success = False
    fun = float("nan")
    # SLSQP from a zero warm-start does not search u < 0 when |J| and |∇J|
    # are ~1e5 (comfort ρ). Scale the NLP so box bounds below zero are used.
    # Accept still compares unscaled J.  ocp.cost / ocp.jac stay unscaled.
    try:
        j_ref = float(ocp.cost(u0))
    except TimeoutError as exc:
        ocp.deadline = None
        elapsed = time.perf_counter() - t0
        return {
            "status": "timeout",
            "success": False,
            "elapsed_s": elapsed,
            "nfev": ocp.nfev,
            "njev": ocp.njev,
            "nit": 0,
            "fun": float(ocp.best_j) if ocp.best_u is not None else float("nan"),
            "message": str(exc),
            "u_star": (
                ocp.best_u.reshape(ocp.n, ocp.nu)
                if ocp.best_u is not None
                else u0.reshape(ocp.n, ocp.nu)
            ),
            "n_decisions": ocp.n * ocp.nu,
            "N": ocp.n,
            "M": ocp.m,
            "timed_out": ocp.timed_out,
        }
    try:
        u0, j_ref = _cauchy_warm_start(ocp, u0, j_ref)
    except TimeoutError:
        pass
    j_scale = abs(j_ref) if np.isfinite(j_ref) and abs(j_ref) > 1.0 else 1.0

    def fun_scaled(x: np.ndarray) -> float:
        return float(ocp.cost(x)) / j_scale

    def jac_scaled(x: np.ndarray) -> np.ndarray:
        return np.asarray(ocp.jac(x), dtype=float) / j_scale

    solver = minimize_fn or minimize
    kwargs: dict[str, Any] = {
        "fun": fun_scaled,
        "x0": u0,
        "method": "SLSQP",
        "jac": jac_scaled,
        "bounds": ocp.bounds,
        "options": {"maxiter": int(maxiter), "ftol": NMPC_FTOL, "disp": False},
    }
    try:
        res = solver(**kwargs)
        u_star = np.asarray(getattr(res, "x", u0), dtype=float)
        nit = int(getattr(res, "nit", 0) or 0)
        success = bool(getattr(res, "success", False))
        fun = float(getattr(res, "fun", float("nan")))
        if np.isfinite(fun):
            fun *= j_scale
        message = str(getattr(res, "message", ""))
        if not success:
            status = "nonconverged"
    except TimeoutError as exc:
        status = "timeout"
        message = str(exc)
        success = False
        if ocp.best_u is not None:
            u_star = ocp.best_u.reshape(-1)
            fun = float(ocp.best_j)
    finally:
        ocp.deadline = None
    elapsed = time.perf_counter() - t0
    u_mat = np.asarray(u_star, dtype=float).reshape(ocp.n, ocp.nu)
    if status != "timeout":
        u_mat, fun = _signed_probe_if_idle(ocp, u_mat, fun)
    return {
        "status": status,
        "success": success,
        "elapsed_s": elapsed,
        "nfev": ocp.nfev,
        "njev": ocp.njev,
        "nit": nit,
        "fun": fun,
        "message": message,
        "u_star": u_mat,
        "n_decisions": ocp.n * ocp.nu,
        "N": ocp.n,
        "M": ocp.m,
        "timed_out": ocp.timed_out,
    }


def evaluate_zero_heat_cost(ocp: MeanOcp) -> float:
    """``J(u=0)`` for the in-band accept test (no Jacobian)."""

    saved_deadline = ocp.deadline
    ocp.deadline = None
    try:
        return float(ocp.cost(np.zeros(ocp.n * ocp.nu, dtype=float)))
    finally:
        ocp.deadline = saved_deadline


def plan_from_solve(
    ocp: MeanOcp,
    solve_result: dict[str, Any],
    cost_zero: float,
) -> dict[str, Any]:
    """Attach accept flag and the air-temperature reference path."""

    u_star = np.asarray(solve_result["u_star"], dtype=float).reshape(ocp.n, ocp.nu)
    cost = float(solve_result["fun"])
    saved_deadline = ocp.deadline
    ocp.deadline = None
    try:
        rolled_cost, _, air = ocp.roll(u_star)
        cost = float(rolled_cost)
    except TimeoutError:
        air = np.zeros((ocp.n * ocp.m, ocp.n_rooms), dtype=float)
        cost = float("nan")
    finally:
        ocp.deadline = saved_deadline
    accepted = accept_plan(u_star, cost, cost_zero, ocp.u_min, ocp.u_max)
    return {
        **solve_result,
        "fun": cost,
        "accepted": accepted,
        "cost_zero": float(cost_zero),
        "t_ref": np.asarray(air, dtype=float),
        "u_star": u_star,
    }
