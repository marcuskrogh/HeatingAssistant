#!/usr/bin/env python3
"""Apply the normalised RMS proxy to production J traces.

η = sqrt(J / n_obs) = RMSE / σ_R.  J is the production summed SSE/(n R_var).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from heatingassistant.engine.nmpc_timing import timing_from_options  # noqa: E402

from bench_jacobian import (  # noqa: E402
    N_STEPS,
    _make_est,
    record_lbfgs_hist,
)
from bench_window import excited_history, layout_and_theta  # noqa: E402
from proxy import (  # noqa: E402
    ETA_NOISE,
    ETA_TOL,
    R_VAR,
    SIGMA_C,
    eta_from_j,
    n_obs_nstep,
    n_obs_tiled_oe,
    rmse_c_from_eta,
)

SANDBOX = Path(__file__).resolve().parent
INSPECT = SANDBOX / "inspect"


def annotate(hist, n_obs_oe, n_obs_pem):
    out = []
    for p in hist:
        n_obs = n_obs_pem if p.get("phase") == "nstep_pem" else n_obs_oe
        eta = eta_from_j(float(p["f"]), n_obs)
        out.append({
            **p,
            "n_obs": n_obs,
            "eta": eta,
            "rmse_c": None if eta is None else rmse_c_from_eta(eta),
            "within_tol": None if eta is None else eta <= ETA_TOL,
        })
    return out


def _plot(hist, path: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = list(range(len(hist)))
    ys = [max(0.08, float(p["eta"])) for p in hist]
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    ax.semilogy(xs, ys, "-", color="#4fc3f7", lw=2, label="η = RMSE / σ")
    ax.axhline(ETA_TOL, color="#f5a623", ls="--", lw=1.6, label=f"η_tol = {ETA_TOL:g} (1 °C)")
    ax.axhline(ETA_NOISE, color="#2ec4b6", ls=":", lw=1.2, label="η = 1 (sensor noise)")
    ax.axhspan(0.08, ETA_TOL, color="#2ec4b6", alpha=0.08)
    ax.set_xlabel("evaluation")
    ax.set_ylabel("normalised RMS")
    ax.set_title("Production J mapped to η (tiled OE then N-step)")
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
    est = _make_est(rooms, sources, dt, use_nstep=True)
    layout, theta = layout_and_theta(est, history)
    std = est._convert_history_std(history, use_ym=True)
    n_hist = len(std)
    n_obs_pem = n_obs_nstep(n_hist, est._n_horizon_steps, est._origin_stride)
    n_obs_oe = n_obs_tiled_oe(
        n_hist, est._max_window_steps, est._min_segment_steps,
    )
    mse_pem, _ = est._nstep_pem_and_grad(theta, layout, std, dt)
    mse_oe, _ = est._simulation_mse_and_grad(
        theta, layout, std, nominal_dt=dt,
        max_window_steps=est._max_window_steps,
        min_segment_steps=est._min_segment_steps,
    )
    eta_pem = eta_from_j(float(mse_pem), n_obs_pem)
    eta_oe = eta_from_j(float(mse_oe), n_obs_oe)
    trace = record_lbfgs_hist(rooms, sources, dt, history)
    annotated = annotate(trace["f_hist"], n_obs_oe, n_obs_pem)
    last_pem = next(p for p in reversed(annotated) if p["phase"] == "nstep_pem")

    long_h = int(round(5 * 24 * 3600 / dt))
    n_obs_5d = n_obs_nstep(long_h, timing.n_fast, timing.fast_substeps)
    j_shot = 54608.05
    eta_shot = eta_from_j(j_shot, n_obs_5d)

    INSPECT.mkdir(parents=True, exist_ok=True)
    png = INSPECT / "05_proxy.png"
    _plot(annotated, png)

    report = {
        "r_var": R_VAR,
        "sigma_c": SIGMA_C,
        "eta_tol": ETA_TOL,
        "eta_noise": ETA_NOISE,
        "n_hist_bench": n_hist,
        "n_obs_oe": n_obs_oe,
        "n_obs_nstep": n_obs_pem,
        "prior_oe": {
            "j": float(mse_oe),
            "eta": eta_oe,
            "rmse_c": rmse_c_from_eta(eta_oe),
            "within_tol": eta_oe <= ETA_TOL,
        },
        "prior_nstep": {
            "j": float(mse_pem),
            "eta": eta_pem,
            "rmse_c": rmse_c_from_eta(eta_pem),
            "within_tol": eta_pem <= ETA_TOL,
        },
        "lbfgs_last_nstep": {
            "j": last_pem["f"],
            "eta": last_pem["eta"],
            "rmse_c": last_pem["rmse_c"],
            "within_tol": last_pem["within_tol"],
        },
        "screenshot_5d": {
            "n_hist": long_h,
            "n_obs": n_obs_5d,
            "j": j_shot,
            "eta": eta_shot,
            "rmse_c": rmse_c_from_eta(eta_shot),
            "note": "Operator still J≈54608; η uses 5-day N-step n_obs on the live grid.",
        },
        "eta_min": min(p["eta"] for p in annotated),
        "eta_max": max(p["eta"] for p in annotated),
        "n_within_tol": sum(1 for p in annotated if p["within_tol"]),
        "n_evals": len(annotated),
    }
    json_path = INSPECT / "05_proxy.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    hist_path = INSPECT / "05_eta_hist.json"
    hist_path.write_text(json.dumps(annotated, indent=2) + "\n")
    md = [
        "# Normalised RMS proxy (sandbox iteration 5)",
        "",
        f"- σ_R = sqrt(R_var) = **{SIGMA_C:.2f} °C** (R_var={R_VAR})",
        f"- η = sqrt(J / n_obs) = RMSE / σ_R.  Tolerance **η ≤ {ETA_TOL:g}** → RMS ≤ **{ETA_TOL * SIGMA_C:.2f} °C**.",
        f"- Bench window {n_hist} steps: n_obs OE={n_obs_oe}, N-step={n_obs_pem}",
        f"- Prior tiled OE: J={mse_oe:.3g} → η={eta_oe:.2f} ({rmse_c_from_eta(eta_oe):.2f} °C)",
        f"- Prior N-step: J={mse_pem:.3g} → η={eta_pem:.2f} ({rmse_c_from_eta(eta_pem):.2f} °C)",
        f"- After short L-BFGS: η={last_pem['eta']:.2f} "
        f"({last_pem['rmse_c']:.2f} °C) "
        f"{'within' if last_pem['within_tol'] else 'above'} tolerance",
        f"- 5-day n_obs={n_obs_5d}; screenshot J=54608 → η={eta_shot:.2f} "
        f"({rmse_c_from_eta(eta_shot):.2f} °C)",
        "",
    ]
    md_path = INSPECT / "05_proxy.md"
    md_path.write_text("\n".join(md) + "\n")
    print(json.dumps({k: report[k] for k in (
        "n_obs_oe", "n_obs_nstep", "prior_oe", "prior_nstep",
        "lbfgs_last_nstep", "screenshot_5d", "eta_min", "eta_max",
    )}, indent=2))
    print(f"wrote {json_path}")
    print(f"wrote {hist_path}")
    print(f"wrote {md_path}")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
