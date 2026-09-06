#!/usr/bin/env python3
"""Finite-difference check of PE Jacobians and a short L-BFGS J trace.

Uses production ``nstep_pem_and_grad`` / ``_simulation_mse_and_grad`` /
``HouseThermalSDE.dfdx`` on one-room excited history (same generator as
``bench_window.py``). Not HAOS hardware.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from heatingassistant.engine.controller import HouseThermalSDE  # noqa: E402
from heatingassistant.engine.estimation.kalman_ml import (  # noqa: E402
    KalmanMLEstimator,
)
from heatingassistant.engine.estimation.nlp_eval import (  # noqa: E402
    RegularizedMseCache,
    solve_lbfgs,
)
from heatingassistant.engine.nmpc_timing import timing_from_options  # noqa: E402
from mbc.control import ScipyNLPBackend  # noqa: E402

from bench_window import excited_history, layout_and_theta  # noqa: E402

SANDBOX = Path(__file__).resolve().parent
INSPECT = SANDBOX / "inspect"

BAR_DFDX = 5e-4
BAR_THETA = 5e-2
N_STEPS = 32
N_HORIZON = 8
STRIDE = 4


def _central_diff_vec(fun, x, eps_rel=1e-5, eps_abs=1e-7):
    x = np.asarray(x, dtype=float)
    n = x.size
    g = np.zeros(n)
    f0 = float(fun(x))
    for i in range(n):
        h = max(eps_abs, eps_rel * max(1.0, abs(x[i])))
        xp = x.copy()
        xm = x.copy()
        xp[i] += h
        xm[i] -= h
        fp = float(fun(xp))
        fm = float(fun(xm))
        g[i] = (fp - fm) / (2.0 * h)
    return f0, g


def _rel_err(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = np.maximum(np.maximum(np.abs(a), np.abs(b)), 1e-12)
    return np.abs(a - b) / denom


def _theta_names(layout, n):
    names = []
    names.extend([f"log_mass[{i}]" for i in range(n)])
    names.extend([f"log_r[{i}]" for i in range(n)])
    names.extend([f"q_int[{i}]" for i in range(n)])
    a, b = layout.idx_t_wall_init
    names.extend([f"t_wall[{i}]" for i in range(b - a)])
    names.extend([f"log_alpha[{i}]" for i in range(len(layout.identifiable_sources))])
    names.extend([f"log_r_ij[{i}]" for i in range(len(layout.identifiable_pairs))])
    names.extend([f"log_solar[{i}]" for i in range(len(layout.identifiable_solar))])
    names.extend([f"c_air[{i}]" for i in range(len(layout.identifiable_splits))])
    names.extend([f"r_aw[{i}]" for i in range(len(layout.identifiable_splits))])
    names.extend([f"ua_open[{i}]" for i in range(len(layout.identifiable_ua))])
    return names


def _make_est(rooms, sources, dt, use_nstep):
    return KalmanMLEstimator(
        rooms,
        sources,
        dt=dt,
        n_horizon_steps=N_HORIZON,
        origin_stride=STRIDE,
        max_window_steps=16,
        max_compute_s=0.0,
        use_nstep_pem=use_nstep,
        regularization=0.01,
    )


def check_dfdx(rooms, sources, dt):
    from heatingassistant.engine.thermal_model import HouseModel

    model = HouseModel(rooms)
    sde = HouseThermalSDE(model, sources, dt=dt, augment_offsets=False)
    n = sde._n_rooms
    x = np.array([19.5] * n + [18.8] * n, dtype=float)
    u = np.array([0.4] * sde.nu, dtype=float)
    d = sde.disturbance_vector(4.0, {})
    p = np.array([])
    j_a = sde.dfdx(x, u, d, p, 0.0)

    def f_state(state):
        return sde.f(state, u, d, p, 0.0)

    nx = x.size
    j_fd = np.zeros((nx, nx))
    for i in range(nx):
        h = 1e-5 * max(1.0, abs(x[i]))
        xp = x.copy()
        xm = x.copy()
        xp[i] += h
        xm[i] -= h
        j_fd[:, i] = (f_state(xp) - f_state(xm)) / (2.0 * h)
    err = _rel_err(j_a, j_fd)
    return {
        "max_rel_err": float(np.max(err)),
        "within_bar": bool(np.max(err) <= BAR_DFDX),
        "bar": BAR_DFDX,
        "shape": list(j_a.shape),
    }


def check_theta_grad(kind, rooms, sources, dt, history):
    use_nstep = kind == "nstep"
    est = _make_est(rooms, sources, dt, use_nstep)
    layout, theta = layout_and_theta(est, history)
    std = est._convert_history_std(history, use_ym=True)
    est._pe_deadline_mono = None
    names = _theta_names(layout, est._n)[: len(theta)]

    def fun_raw(th):
        if use_nstep:
            mse, _ = est._nstep_pem_and_grad(th, layout, std, dt)
        else:
            mse, _ = est._simulation_mse_and_grad(
                th, layout, std, nominal_dt=dt,
                max_window_steps=est._max_window_steps,
                min_segment_steps=est._min_segment_steps,
            )
        return mse

    def grad_raw(th):
        if use_nstep:
            mse, g = est._nstep_pem_and_grad(th, layout, std, dt)
        else:
            mse, g = est._simulation_mse_and_grad(
                th, layout, std, nominal_dt=dt,
                max_window_steps=est._max_window_steps,
                min_segment_steps=est._min_segment_steps,
            )
        return mse, np.asarray(g, dtype=float)

    mse_a, g_a = grad_raw(theta)
    mse_fd, g_fd = _central_diff_vec(fun_raw, theta)
    err = _rel_err(g_a, g_fd)
    rows = []
    for i, name in enumerate(names):
        rows.append({
            "name": name,
            "theta": float(theta[i]),
            "analytic": float(g_a[i]),
            "fd": float(g_fd[i]),
            "rel_err": float(err[i]),
        })
    max_err = float(np.max(err)) if err.size else 0.0
    return {
        "kind": kind,
        "mse_analytic": float(mse_a),
        "mse_fd_center": float(mse_fd),
        "ntheta": int(len(theta)),
        "max_rel_err": max_err,
        "within_bar": bool(max_err <= BAR_THETA),
        "bar": BAR_THETA,
        "params": rows,
        "R_var": float(est._R_var),
    }


def record_lbfgs_hist(rooms, sources, dt, history):
    """Same recorder as production: tiled OE then N-step, shared f_hist."""
    est = _make_est(rooms, sources, dt, use_nstep=True)
    layout, theta = layout_and_theta(est, history)
    std = est._convert_history_std(history, use_ym=True)
    cache = RegularizedMseCache(est, layout, std, layout.identifiable_pairs, None)
    est._pe_nfev = 0
    est._pe_f_hist = []
    est._pe_t0_mono = None
    est._pe_deadline_mono = None
    est._max_compute_s = 0.0
    hist = []

    def on_progress(payload):
        hist.append({
            "nfev": payload.get("nfev"),
            "f": payload.get("f"),
            "phase": payload.get("phase"),
        })

    est._on_progress = on_progress
    lb = np.full(len(theta), -np.inf)
    ub = np.full(len(theta), np.inf)
    # Tight bounds from estimator priors would be better; keep wide enough
    # for a few L-BFGS steps without hitting sentinels.
    a_tw, b_tw = layout.idx_t_wall_init
    lb = theta - 2.0
    ub = theta + 2.0
    lb[a_tw:b_tw] = np.clip(theta[a_tw:b_tw] - 8.0, -20.0, 40.0)
    ub[a_tw:b_tw] = np.clip(theta[a_tw:b_tw] + 8.0, -20.0, 40.0)

    backend = ScipyNLPBackend(
        method="L-BFGS-B",
        options={"maxiter": 8, "ftol": 1e-12, "gtol": 1e-6},
    )
    est._use_nstep_pem = False
    cache.invalidate()
    try:
        solve_lbfgs(
            cache.fun, cache.jac, theta.copy(), lb, ub,
            invalidate=cache.invalidate, backend=backend,
        )
    finally:
        est._use_nstep_pem = True
    cache.invalidate()
    solve_lbfgs(
        cache.fun, cache.jac, theta.copy(), lb, ub,
        invalidate=cache.invalidate, backend=backend,
    )
    fs = [p["f"] for p in hist if p.get("f") is not None]
    return {
        "nfev": len(hist),
        "f_min": float(np.min(fs)) if fs else None,
        "f_max": float(np.max(fs)) if fs else None,
        "f_ratio": float(np.max(fs) / np.min(fs)) if fs and min(fs) > 0 else None,
        "phases": sorted({p.get("phase") for p in hist}),
        "f_hist": hist,
    }


def _plot_hist(hist, path: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = list(range(len(hist)))
    ys = [max(1e-12, float(p["f"])) for p in hist]
    phases = [p.get("phase") for p in hist]
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    ax.semilogy(xs, ys, "-", color="#4fc3f7", lw=2, label="fit error")
    ax.axhline(1e-12, color="#f5a623", ls="--", lw=1.4, label="ftol 1e-12")
    oe = [i for i, p in enumerate(phases) if p == "tiled_oe"]
    if oe:
        ax.axvspan(oe[0] - 0.4, oe[-1] + 0.4, color="#4fc3f7", alpha=0.08, label="tiled OE")
    ax.set_xlabel("evaluation")
    ax.set_ylabel("J (log)")
    ax.set_title("Production recorder: tiled OE then N-step L-BFGS")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    timing = timing_from_options(
        {},
        default_period=7200.0,
        default_substeps=8,
        default_horizon_h=36.0,
    )
    dt = timing.dt_s
    rooms, sources, history = excited_history(n_steps=N_STEPS, dt=dt)
    dfdx = check_dfdx(rooms, sources, dt)
    print("dfdx", json.dumps(dfdx), flush=True)
    oe = check_theta_grad("tiled_oe", rooms, sources, dt, history)
    print("tiled_oe max_rel_err", oe["max_rel_err"], "mse", oe["mse_analytic"], flush=True)
    pem = check_theta_grad("nstep", rooms, sources, dt, history)
    print("nstep max_rel_err", pem["max_rel_err"], "mse", pem["mse_analytic"], flush=True)
    trace = record_lbfgs_hist(rooms, sources, dt, history)
    print(
        "lbfgs nfev", trace["nfev"],
        "f_min", trace["f_min"], "f_max", trace["f_max"],
        "ratio", trace["f_ratio"],
        flush=True,
    )

    INSPECT.mkdir(parents=True, exist_ok=True)
    png = INSPECT / "04_convergence.png"
    _plot_hist(trace["f_hist"], png)
    report = {
        "host": platform.node(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "dt_s": dt,
        "n_steps": N_STEPS,
        "n_horizon": N_HORIZON,
        "origin_stride": STRIDE,
        "bar_dfdx": BAR_DFDX,
        "bar_theta": BAR_THETA,
        "dfdx": dfdx,
        "tiled_oe": oe,
        "nstep": pem,
        "lbfgs": {
            "nfev": trace["nfev"],
            "f_min": trace["f_min"],
            "f_max": trace["f_max"],
            "f_ratio": trace["f_ratio"],
            "phases": trace["phases"],
        },
        "note": (
            "End-to-end θ-gradient vs central differences at the prior. "
            "L-BFGS trace uses the production RegularizedMseCache recorder "
            "(tiled OE warm-start, then N-step) so J jumps across the phase "
            "change are the same class as the live popup."
        ),
    }
    json_path = INSPECT / "04_jacobian.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    hist_path = INSPECT / "04_f_hist.json"
    hist_path.write_text(json.dumps(trace["f_hist"], indent=2) + "\n")

    lines = [
        "# PE Jacobian and convergence (sandbox iteration 4)",
        "",
        f"- Host: `{report['host']}` `{report['machine']}` Python {report['python']}",
        f"- Window: {N_STEPS} steps, N={N_HORIZON}, stride={STRIDE}, dt={dt:.0f}s",
        f"- dfdx max rel err: **{dfdx['max_rel_err']:.3e}** (bar {BAR_DFDX}, "
        f"{'pass' if dfdx['within_bar'] else 'FAIL'})",
        f"- tiled OE θ-grad max rel err: **{oe['max_rel_err']:.3e}** "
        f"(bar {BAR_THETA}, {'pass' if oe['within_bar'] else 'FAIL'}); "
        f"J={oe['mse_analytic']:.4g}",
        f"- N-step θ-grad max rel err: **{pem['max_rel_err']:.3e}** "
        f"(bar {BAR_THETA}, {'pass' if pem['within_bar'] else 'FAIL'}); "
        f"J={pem['mse_analytic']:.4g}",
        f"- L-BFGS recorder: {trace['nfev']} evals, J in "
        f"[{trace['f_min']:.4g}, {trace['f_max']:.4g}] "
        f"(ratio {trace['f_ratio']:.3g}); phases {trace['phases']}",
        "",
        "## Worst N-step parameters",
        "",
        "| param | analytic | finite-diff | rel err |",
        "|-------|----------|-------------|---------|",
    ]
    worst = sorted(pem["params"], key=lambda r: -r["rel_err"])[:8]
    for row in worst:
        lines.append(
            f"| `{row['name']}` | {row['analytic']:.4g} | {row['fd']:.4g} | "
            f"{row['rel_err']:.3e} |"
        )
    lines.append("")
    md_path = INSPECT / "04_jacobian.md"
    md_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {json_path}")
    print(f"wrote {hist_path}")
    print(f"wrote {md_path}")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
