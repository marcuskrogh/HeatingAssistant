#!/usr/bin/env python3
"""SWD-394 measure sandbox: approximate NMPC solve times vs linearised QP.

Isolation tree only. Imports production plant / QP; does not edit them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import approx_fprime, minimize

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heatingassistant.engine.const import (  # noqa: E402
    DEFAULT_ENERGY_PRICE_WEIGHT,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
)
from heatingassistant.engine.controller import (  # noqa: E402
    HeatingMPCController,
    HouseThermalSDE,
)
from heatingassistant.engine.heat_sources import HeatPump  # noqa: E402
from heatingassistant.engine.integrator import implicit_euler_substeps  # noqa: E402
from heatingassistant.engine.thermal_model import (  # noqa: E402
    HouseModel,
    Room,
    RoomConnection,
)

HERE = Path(__file__).resolve().parent
INSPECT = HERE / "inspect"

TS_S = 900.0
TH_H = 36.0
N_FAST = int(TH_H * 3600.0 / TS_S)  # 144
N_INT = 10
RHO = float(DEFAULT_SOFT_CONSTRAINT_WEIGHT)
S_ROM = float(DEFAULT_SMOOTHING_WEIGHT)
ALPHA_PRICE = float(DEFAULT_ENERGY_PRICE_WEIGHT)
TIMEOUT_S = 180.0
FD_EPS = 1e-4
METHODS = ("SLSQP",)

BRACKETS = (
    {"label": "15 min", "t_nmpc_s": 900.0, "maxiter": 8},
    {"label": "1 h", "t_nmpc_s": 3600.0, "maxiter": 20},
    {"label": "2 h", "t_nmpc_s": 7200.0, "maxiter": 20},
)


def build_plant() -> tuple[HouseModel, list[HeatPump], HouseThermalSDE]:
    living = Room(
        name="living",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[RoomConnection("bed", 0.2)],
        temperature=18.0,
        setpoint=21.0,
        comfort_offset=2.0,
    )
    bed = Room(
        name="bed",
        thermal_mass=3_000_000.0,
        r_external=0.08,
        connections=[RoomConnection("living", 0.2)],
        temperature=17.0,
        setpoint=20.0,
        comfort_offset=2.0,
    )
    model = HouseModel([living, bed])
    sources = [
        HeatPump("hp_living", "living", max_power=4000.0, hvac_mode="heat_cool"),
        HeatPump("hp_bed", "bed", max_power=2500.0, hvac_mode="heat_cool"),
    ]
    sde = HouseThermalSDE(
        model, sources, dt=TS_S, augment_offsets=False, n_int_steps=N_INT
    )
    return model, sources, sde


def synthetic_traces(n_fast: int) -> dict[str, np.ndarray]:
    k = np.arange(n_fast)
    hour = (12.0 + k * (TS_S / 3600.0)) % 24.0
    outdoor = 2.0 + 6.0 * np.sin(2.0 * np.pi * (hour - 7.0) / 24.0)
    day = np.clip(np.sin(np.pi * (hour - 7.0) / 12.0), 0.0, None)
    solar_l = 350.0 * day
    solar_b = 180.0 * day
    peak = ((hour >= 7.0) & (hour < 10.0)) | ((hour >= 17.0) & (hour < 20.0))
    price = np.where(peak, 0.42, 0.12)
    return {
        "outdoor": outdoor.astype(float),
        "solar_living": solar_l.astype(float),
        "solar_bed": solar_b.astype(float),
        "price": price.astype(float),
    }


def pack_d(sde: HouseThermalSDE, traces: dict[str, np.ndarray], k: int) -> np.ndarray:
    return sde.disturbance_vector(
        float(traces["outdoor"][k]),
        {"living": float(traces["solar_living"][k]), "bed": float(traces["solar_bed"][k])},
    )


def electrical_w(sources: list[HeatPump], u: np.ndarray) -> float:
    p = 0.0
    for j, src in enumerate(sources):
        uj = float(u[j])
        if uj >= 0.0:
            p += float(src.elec_per_unit_heat) * uj
        else:
            p += float(src.elec_per_unit_cool) * (-uj)
    return p


def electrical_dPdu(sources: list[HeatPump], u: np.ndarray) -> np.ndarray:
    g = np.zeros(len(sources), dtype=float)
    for j, src in enumerate(sources):
        uj = float(u[j])
        if uj > 0.0:
            g[j] = float(src.elec_per_unit_heat)
        elif uj < 0.0:
            g[j] = -float(src.elec_per_unit_cool)
    return g


class MeanOcp:
    def __init__(
        self,
        sde: HouseThermalSDE,
        sources: list[HeatPump],
        traces: dict[str, np.ndarray],
        t_nmpc_s: float,
        x0: np.ndarray,
        u_prev: np.ndarray,
    ) -> None:
        if abs(t_nmpc_s / TS_S - round(t_nmpc_s / TS_S)) > 1e-9:
            raise ValueError("T_NMPC must be an integer multiple of T_s")
        self.sde = sde
        self.sources = sources
        self.traces = traces
        self.m = int(round(t_nmpc_s / TS_S))
        self.n = N_FAST // self.m
        self.nu = sde.nu
        self.n_rooms = sde._n_rooms
        self.x0 = np.asarray(x0, dtype=float).copy()
        self.u_prev = np.asarray(u_prev, dtype=float).copy()
        self.p = np.array([], dtype=float)
        names = sde._room_list
        self.t_min = np.array(
            [sde._model.rooms[nm].setpoint - sde._model.rooms[nm].comfort_offset for nm in names]
        )
        self.t_max = np.array(
            [sde._model.rooms[nm].setpoint + sde._model.rooms[nm].comfort_offset for nm in names]
        )
        u_min, u_max = sde.u_bounds
        self.bounds = [
            (float(u_min[j]), float(u_max[j]))
            for _ in range(self.n)
            for j in range(self.nu)
        ]
        self.dt_h_slow = t_nmpc_s / 3600.0
        self.nfev = 0
        self.deadline: float | None = None
        self.timed_out = False
        self._d_fast = [pack_d(sde, traces, k) for k in range(N_FAST)]
        self._price_slow = np.array(
            [float(np.mean(traces["price"][n * self.m : (n + 1) * self.m])) for n in range(self.n)]
        )
        self.njev = 0
        d0 = self._d_fast[0]
        a0 = sde.dfdx(self.x0, np.zeros(self.nu), d0, self.p, 0.0)
        self._h_sub = TS_S / float(N_INT)
        eye = np.eye(sde.nx)
        self._M = np.linalg.inv(eye - self._h_sub * a0)

    def _roll(self, U: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        j, U, air, _ = self._roll_maybe_jac(U, with_jac=False)
        return j, U, air

    def _roll_maybe_jac(
        self, U: np.ndarray, *, with_jac: bool
    ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray | None]:
        if self.deadline is not None and time.perf_counter() > self.deadline:
            self.timed_out = True
            raise TimeoutError("NMPC wall-clock timeout")
        self.nfev += 1
        U = np.asarray(U, dtype=float).reshape(self.n, self.nu)
        x = self.x0.copy()
        air_path = []
        j = 0.0
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
                x = implicit_euler_substeps(rhs, jacx, x, TS_S, N_INT)
                ta = x[: self.n_rooms]
                air_path.append(ta.copy())
                viol = np.maximum(0.0, self.t_min - ta) + np.maximum(0.0, ta - self.t_max)
                j += RHO * float(np.dot(viol, viol))
                if with_jac:
                    b_u = self.sde.dfdu(x, u_n, d_k, self.p, 0.0)
                    hmb = self._h_sub * (self._M @ b_u)
                    for _ in range(N_INT):
                        sx = self._M @ sx
                        sx[:, col : col + self.nu] += hmb
                    sta = sx[: self.n_rooms]
                    for i in range(self.n_rooms):
                        if ta[i] < self.t_min[i]:
                            viol_i = self.t_min[i] - ta[i]
                            g += RHO * 2.0 * viol_i * (-1.0) * sta[i]
                        elif ta[i] > self.t_max[i]:
                            viol_i = ta[i] - self.t_max[i]
                            g += RHO * 2.0 * viol_i * (1.0) * sta[i]
            du = u_n - u_prev
            j += S_ROM * float(np.dot(du, du))
            j += ALPHA_PRICE * self._price_slow[n] * electrical_w(self.sources, u_n) * 1e-3 * self.dt_h_slow
            if with_jac:
                g[col : col + self.nu] += 2.0 * S_ROM * du
                if n > 0:
                    g[col - self.nu : col] -= 2.0 * S_ROM * du
                g[col : col + self.nu] += (
                    ALPHA_PRICE
                    * self._price_slow[n]
                    * 1e-3
                    * self.dt_h_slow
                    * electrical_dPdu(self.sources, u_n)
                )
            u_prev = u_n
        return j, U, np.asarray(air_path), g

    def cost(self, u_flat: np.ndarray) -> float:
        j, _, _ = self._roll(u_flat)
        return j

    def jac(self, u_flat: np.ndarray) -> np.ndarray:
        self.njev += 1
        _, _, _, g = self._roll_maybe_jac(u_flat, with_jac=True)
        assert g is not None
        return g


def solve_nmpc(
    ocp: MeanOcp,
    u0: np.ndarray,
    maxiter: int,
    method: str,
    *,
    use_explicit_jac: bool = False,
    use_analytic_jac: bool = False,
    timeout_s: float = TIMEOUT_S,
) -> dict:
    ocp.nfev = 0
    ocp.njev = 0
    ocp.timed_out = False
    ocp.deadline = time.perf_counter() + timeout_s
    t0 = time.perf_counter()
    status = "ok"
    message = ""
    u_star = u0.copy()
    nit = 0
    success = False
    fun = float("nan")

    def fd_jac(u_flat: np.ndarray) -> np.ndarray:
        return approx_fprime(u_flat, ocp.cost, FD_EPS)

    kwargs: dict = {
        "fun": ocp.cost,
        "x0": u0,
        "method": method,
        "bounds": ocp.bounds,
        "options": {"maxiter": maxiter, "ftol": 1e-6, "disp": False},
    }
    if use_analytic_jac:
        kwargs["jac"] = ocp.jac
    elif use_explicit_jac:
        kwargs["jac"] = fd_jac
    try:
        res = minimize(**kwargs)
        u_star = np.asarray(res.x, dtype=float)
        nit = int(getattr(res, "nit", 0) or 0)
        success = bool(res.success)
        fun = float(res.fun)
        message = str(res.message)
        if not success:
            status = "nonconverged"
    except TimeoutError as exc:
        status = "timeout"
        message = str(exc)
        success = False
    elapsed = time.perf_counter() - t0
    return {
        "status": status,
        "success": success,
        "elapsed_s": elapsed,
        "nfev": ocp.nfev,
        "njev": ocp.njev,
        "nit": nit,
        "fun": fun,
        "message": message,
        "u_star": u_star,
        "n_decisions": ocp.n * ocp.nu,
        "N": ocp.n,
        "M": ocp.m,
        "timed_out": ocp.timed_out,
    }


def time_qp(model: HouseModel, sources: list[HeatPump], traces: dict[str, np.ndarray], horizon: int) -> dict:
    ctrl = HeatingMPCController(
        model,
        sources,
        horizon=horizon,
        dt=TS_S,
        energy_price_weight=ALPHA_PRICE,
        tracking_weight=0.0,
        smoothing_weight=S_ROM,
        n_int_steps=N_INT,
    )
    now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    outdoor = traces["outdoor"][:horizon].tolist()
    price = traces["price"][:horizon].tolist()
    solar = [
        {"living": float(traces["solar_living"][k]), "bed": float(traces["solar_bed"][k])}
        for k in range(horizon)
    ]
    t0 = time.perf_counter()
    ctrl.compute(
        outdoor_temp=float(outdoor[0]),
        now=now,
        outdoor_forecast=outdoor,
        price_forecast=price,
        solar_gains=solar[0],
    )
    cold = time.perf_counter() - t0
    t1 = time.perf_counter()
    ctrl.compute(
        outdoor_temp=float(outdoor[0]),
        now=now,
        outdoor_forecast=outdoor,
        price_forecast=price,
        solar_gains=solar[0],
    )
    warm = time.perf_counter() - t1
    return {"horizon": horizon, "cold_s": cold, "warm_s": warm}


def plot_times(rows: list[dict], qp_rows: list[dict], path: Path) -> None:
    labels = [r["label"] for r in rows]
    x = np.arange(len(labels))
    width = 0.22
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    ax.bar(x - width, [r["cold_s"] for r in rows], width, label="NMPC cold", color="#1f77b4")
    ax.bar(x, [r["warm_s"] for r in rows], width, label="NMPC warm-start", color="#5fa8d3")
    qp144 = next(q for q in qp_rows if q["horizon"] == N_FAST)
    ax.axhline(qp144["cold_s"], color="#d62728", ls="--", lw=1.2, label=f"QP N={N_FAST} cold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Wall-clock [s]")
    ax.set_title("Approximate OCP solve time (SciPy SLSQP vs linearised QP)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_plan(ocp: MeanOcp, u_star: np.ndarray, path: Path) -> None:
    _, U, air = ocp._roll(u_star)
    t_h = np.arange(air.shape[0]) * (TS_S / 3600.0)
    t_u = np.arange(U.shape[0]) * ocp.m * (TS_S / 3600.0)
    fig, axes = plt.subplots(2, 1, figsize=(8.2, 5.6), sharex=True)
    axes[0].plot(t_h, air[:, 0], label="living air")
    axes[0].plot(t_h, air[:, 1], label="bed air")
    axes[0].axhline(ocp.t_min[0], color="C0", ls=":", lw=1)
    axes[0].axhline(ocp.t_max[0], color="C0", ls=":", lw=1)
    axes[0].axhline(ocp.t_min[1], color="C1", ls=":", lw=1)
    axes[0].axhline(ocp.t_max[1], color="C1", ls=":", lw=1)
    axes[0].set_ylabel("Temperature [°C]")
    axes[0].legend()
    axes[0].grid(alpha=0.3)
    axes[1].step(t_u, U[:, 0], where="post", label="living u")
    axes[1].step(t_u, U[:, 1], where="post", label="bed u")
    axes[1].set_ylabel("u (fraction)")
    axes[1].set_xlabel("Look-ahead [h]")
    axes[1].legend()
    axes[1].grid(alpha=0.3)
    fig.suptitle(f"NMPC plan at T_NMPC = {ocp.m * TS_S / 3600:.2f} h (inspect, not product)")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="SWD-394 NMPC solve-time harness")
    parser.add_argument("--only", action="append", dest="only", default=None)
    parser.add_argument("--maxiter", type=int, default=None)
    parser.add_argument("--tag", default="01")
    parser.add_argument("--explicit-jac", action="store_true")
    parser.add_argument("--analytic", action="store_true", help="analytic dJ/dU via dfdx/dfdu")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    args = parser.parse_args()

    INSPECT.mkdir(parents=True, exist_ok=True)
    model, sources, sde = build_plant()
    traces = synthetic_traces(N_FAST)
    x0 = sde.x.copy()
    u_prev = np.zeros(sde.nu)
    brackets = list(BRACKETS)
    if args.only:
        want = {item.strip() for item in args.only}
        brackets = [br for br in brackets if br["label"] in want]
        if not brackets:
            raise SystemExit(f"no brackets match {sorted(want)}")
    if args.maxiter is not None:
        brackets = [{**br, "maxiter": int(args.maxiter)} for br in brackets]

    t_cost0 = time.perf_counter()
    probe = MeanOcp(sde, sources, traces, 3600.0, x0, u_prev)
    j0 = probe.cost(np.zeros(probe.n * probe.nu))
    t_cost = time.perf_counter() - t_cost0

    qp_rows = [
        time_qp(model, sources, traces, 100),
        time_qp(model, sources, traces, N_FAST),
    ]

    if args.analytic:
        chk = MeanOcp(sde, sources, traces, brackets[0]["t_nmpc_s"], x0, u_prev)
        rng = np.random.default_rng(0)
        u_chk = rng.uniform(0.05, 0.25, size=chk.n * chk.nu)
        g_an = chk.jac(u_chk)
        g_fd = approx_fprime(u_chk, chk.cost, 1e-5)
        abs_err = float(np.max(np.abs(g_an - g_fd)))
        rel = abs_err / max(float(np.max(np.abs(g_fd))), 1e-12)
        print(f"analytic vs FD jac: max abs {abs_err:.3e} rel {rel:.3e}", flush=True)

    nmpc_rows: list[dict] = []
    warm_u: dict[str, np.ndarray] = {}
    for br in brackets:
        ocp = MeanOcp(sde, sources, traces, br["t_nmpc_s"], x0, u_prev)
        u0 = np.zeros(ocp.n * ocp.nu)
        print(f"NMPC {br['label']} cold N={ocp.n} decisions={ocp.n * ocp.nu} ...", flush=True)
        cold = solve_nmpc(
            ocp,
            u0,
            br["maxiter"],
            "SLSQP",
            use_explicit_jac=args.explicit_jac,
            use_analytic_jac=args.analytic,
            timeout_s=args.timeout,
        )
        print(
            f"  cold {cold['elapsed_s']:.2f}s status={cold['status']} "
            f"nfev={cold['nfev']} njev={cold['njev']} nit={cold['nit']}",
            flush=True,
        )
        u_warm0 = cold["u_star"]
        print(f"NMPC {br['label']} warm-start ...", flush=True)
        warm = solve_nmpc(
            ocp,
            u_warm0,
            br["maxiter"],
            "SLSQP",
            use_explicit_jac=args.explicit_jac,
            use_analytic_jac=args.analytic,
            timeout_s=args.timeout,
        )
        print(
            f"  warm {warm['elapsed_s']:.2f}s status={warm['status']} "
            f"nfev={warm['nfev']} njev={warm['njev']} nit={warm['nit']}",
            flush=True,
        )
        warm_u[br["label"]] = warm["u_star"]
        nmpc_rows.append(
            {
                "label": br["label"],
                "t_nmpc_s": br["t_nmpc_s"],
                "N": ocp.n,
                "M": ocp.m,
                "n_decisions": ocp.n * ocp.nu,
                "maxiter": br["maxiter"],
                "method": "SLSQP",
                "cold_s": cold["elapsed_s"],
                "warm_s": warm["elapsed_s"],
                "cold_status": cold["status"],
                "warm_status": warm["status"],
                "cold_nfev": cold["nfev"],
                "warm_nfev": warm["nfev"],
                "cold_njev": cold["njev"],
                "warm_njev": warm["njev"],
                "analytic": bool(args.analytic),
                "cold_nit": cold["nit"],
                "warm_nit": warm["nit"],
                "cold_success": cold["success"],
                "warm_success": warm["success"],
                "cold_fun": cold["fun"],
                "warm_fun": warm["fun"],
                "cold_message": cold["message"],
                "warm_message": warm["message"],
                "moved": bool(np.isfinite(cold["fun"]) and cold["fun"] < 10.0),
            }
        )

    plan_label = brackets[0]["label"] if len(brackets) == 1 else "2 h"
    if plan_label not in warm_u:
        plan_label = next(iter(warm_u))
    t_nmpc = next(br["t_nmpc_s"] for br in brackets if br["label"] == plan_label)
    ocp_plan = MeanOcp(sde, sources, traces, t_nmpc, x0, u_prev)
    plot_plan(ocp_plan, warm_u[plan_label], INSPECT / f"{args.tag}_plan.png")
    plot_times(nmpc_rows, qp_rows, INSPECT / f"{args.tag}_solve_times.png")

    report = {
        "scenario": {
            "rooms": 2,
            "heaters": "one HeatPump heat_cool per room",
            "T_s_s": TS_S,
            "T_H_h": TH_H,
            "n_int_steps": N_INT,
            "live_traces": "waived; synthetic outdoor/solar/price",
            "cost_eval_1h_zero_u_s": t_cost,
            "cost_eval_1h_zero_u_J": j0,
        },
        "qp_baseline": qp_rows,
        "nmpc": nmpc_rows,
        "notes": [
            "Single shooting in U; F = production implicit Euler of HouseThermalSDE.f.",
            "SciPy SLSQP; default FD unless --explicit-jac.",
            "A solve that leaves J near the zero-u cost (~3.5e6) is a false success; inspectables keep only runs with J<10.",
        ],
        "tag": args.tag,
        "explicit_jac": bool(args.explicit_jac),
    }
    (INSPECT / f"{args.tag}_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"# Iteration {args.tag}: NMPC solve times",
        "",
        "Synthetic two-room 2R2C, production `HouseThermalSDE` + implicit Euler, "
        "one heat pump per room. Live traces waived. SciPy SLSQP.",
        "",
        f"One 36 h cost evaluation (1 h grid, u=0): **{t_cost:.3f} s**.",
        "",
        "## Linearised QP baseline (`HeatingLinearisedMPC`)",
        "",
        "| Horizon N | Cold [s] | Warm [s] |",
        "|-----------|----------|----------|",
    ]
    for q in qp_rows:
        lines.append(f"| {q['horizon']} | {q['cold_s']:.3f} | {q['warm_s']:.3f} |")
    lines += [
        "",
        "## Nonlinear OCP (single shooting, 36 h look-ahead)",
        "",
        "| T_NMPC | N | decisions | maxiter | cold [s] | warm [s] | cold status | warm status | cold nfev | cold nit | J |",
        "|--------|---|-----------|---------|----------|----------|-------------|-------------|-----------|----------|---|",
    ]
    for r in nmpc_rows:
        lines.append(
            f"| {r['label']} | {r['N']} | {r['n_decisions']} | {r['maxiter']} | "
            f"{r['cold_s']:.2f} | {r['warm_s']:.2f} | {r['cold_status']} | "
            f"{r['warm_status']} | {r['cold_nfev']} | {r['cold_nit']} | {r['cold_fun']:.3g} |"
        )
    lines += [
        "",
        f"Plots: `{args.tag}_solve_times.png`, `{args.tag}_plan.png`.",
        "",
        "Closed-loop P vs QP comfort/cost is not in this iteration.",
    ]
    (INSPECT / f"{args.tag}_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
