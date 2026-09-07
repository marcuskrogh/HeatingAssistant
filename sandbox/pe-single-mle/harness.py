#!/usr/bin/env python3
"""Compare production PE multistart to a single N-step MLE L-BFGS.

Uses production ``KalmanMLEstimator.estimate`` and ``nstep_pem_and_grad`` on
the live NMPC grid. Synthetic excited history; estimator rooms are a
misspecified prior (not the truth used to generate data). Not HAOS hardware.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from types import MethodType

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from heatingassistant.engine.estimation.constants import PE_ETA_TOL  # noqa: E402
from heatingassistant.engine.estimation.kalman_ml import (  # noqa: E402
    KalmanMLEstimator,
)
from heatingassistant.engine.estimation.nlp_eval import solve_lbfgs  # noqa: E402
from heatingassistant.engine.heat_sources import ElectricHeater  # noqa: E402
from heatingassistant.engine.nmpc_timing import timing_from_options  # noqa: E402
from heatingassistant.engine.thermal_model import HouseModel, Room  # noqa: E402

SANDBOX = Path(__file__).resolve().parent
INSPECT = SANDBOX / "inspect"

TRUE_C = 8.0e6
TRUE_R = 0.025
PRIOR_C = 12.0e6
PRIOR_R = 0.040
NOISE_STD = 0.04
WINDOW_H = 24
CAP_S = 0.0  # run to L-BFGS stop; cap is a named gap vs HAOS 30 min

# Candidate is "significantly worse" if it misses the η bar while baseline
# meets it, or if η is >50% higher than baseline when baseline is already
# above the bar.
ETA_REL_WORSE = 1.5


def excited_history(*, n_steps: int, dt: float, seed: int = 3):
    rng = np.random.default_rng(seed)
    room = Room(
        name="living_room",
        thermal_mass=TRUE_C,
        r_external=TRUE_R,
        temperature=20.0,
        setpoint=21.0,
    )
    sources = [ElectricHeater("living_room_heater", room.name, max_power=2000.0)]
    model = HouseModel([room])
    t0 = 1_700_000_000.0
    history = []
    for k in range(n_steps):
        duty = 0.85 if (k // 4) % 2 == 0 else 0.05
        tout = 2.0 + 6.0 * math.sin(2.0 * math.pi * k / 96.0)
        y = float(model.rooms[room.name].temperature) + float(rng.normal(0.0, NOISE_STD))
        history.append(
            {
                "y": [y],
                "u": [duty],
                "d_outdoor": tout,
                "d_solar": {room.name: 0.0},
                "timestamp": t0 + k * dt,
            }
        )
        heat_inputs = {src.room: src.thermal_power(duty, tout) for src in sources}
        model.step(dt, heat_inputs, tout, {room.name: 0.0})
    return history


def prior_rooms():
    return [
        Room(
            name="living_room",
            thermal_mass=PRIOR_C,
            r_external=PRIOR_R,
            temperature=20.0,
            setpoint=21.0,
        )
    ]


def point_eta(p: dict) -> float | None:
    if p.get("eta") is not None and math.isfinite(float(p["eta"])):
        return float(p["eta"])
    n_obs = p.get("n_obs") or 0
    misfit = p.get("data_mse", p.get("f"))
    if n_obs and misfit is not None and float(misfit) >= 0.0:
        return math.sqrt(float(misfit) / float(n_obs))
    return None


def single_mle_multistart(mode: str):
    """``mode``: ``prior`` (one N-step start) or ``oe_then_nstep`` (OE + one N-step)."""

    def _multistart_joint_nlp(
        self,
        mse_cache,
        layout,
        std_history,
        dataset_start_timestamps,
        theta_prior,
        lb,
        ub,
        scipy_backend,
    ):
        best_theta = theta_prior.copy()
        best_f = float("inf")
        best_converged = False
        best_nstep_rmse = float("inf")
        starts = []
        if mode == "oe_then_nstep" and self._use_nstep_pem:
            self._use_nstep_pem = False
            mse_cache.invalidate()
            try:
                out_oe = solve_lbfgs(
                    mse_cache.fun,
                    mse_cache.jac,
                    theta_prior.copy(),
                    lb,
                    ub,
                    invalidate=mse_cache.invalidate,
                    backend=scipy_backend,
                )
            finally:
                self._use_nstep_pem = True
            mse_cache.invalidate()
            if out_oe is not None:
                starts.append(out_oe[1])
                best_theta = np.asarray(out_oe[1], dtype=float)
                best_nstep_rmse = self._nstep_rmse_theta(
                    best_theta, layout, std_history, dataset_start_timestamps,
                )
                best_f = float(mse_cache.fun(best_theta))
        if not starts:
            starts.append(theta_prior.copy())
        for theta_start in starts:
            out = solve_lbfgs(
                mse_cache.fun,
                mse_cache.jac,
                theta_start,
                lb,
                ub,
                invalidate=mse_cache.invalidate,
                backend=scipy_backend,
            )
            if out is None:
                continue
            f_cand, theta_c, converged = out
            rmse_c = self._nstep_rmse_theta(
                theta_c, layout, std_history, dataset_start_timestamps,
            )
            better = np.isfinite(rmse_c) and rmse_c < best_nstep_rmse
            if better or (not np.isfinite(best_nstep_rmse) and f_cand < best_f):
                best_nstep_rmse = float(rmse_c)
                best_f = f_cand
                best_theta = theta_c
                best_converged = converged
        return best_theta, best_f, best_converged

    return _multistart_joint_nlp


def run_method(name: str, history, dt, n_horizon, stride, patch):
    rooms = prior_rooms()
    sources = [ElectricHeater("living_room_heater", rooms[0].name, max_power=2000.0)]
    snaps: list[dict] = []
    est = KalmanMLEstimator(
        rooms,
        sources,
        dt=dt,
        n_horizon_steps=n_horizon,
        origin_stride=stride,
        max_compute_s=CAP_S,
        use_nstep_pem=True,
        on_progress=snaps.append,
    )
    if patch is not None:
        est._multistart_joint_nlp = MethodType(patch, est)
    t0 = time.perf_counter()
    result = est.estimate(history)
    elapsed = time.perf_counter() - t0
    last = snaps[-1] if snaps else {}
    params = (result.get("estimated_params") or {}).get("living_room") or {}
    c_hat = float(params.get("thermal_mass") or PRIOR_C)
    r_hat = float(params.get("r_external") or PRIOR_R)
    eta = point_eta(last)
    rmse_c = last.get("rmse_c")
    nstep_rmse = float("nan")
    try:
        nstep_rmse = float(est.score_nstep_rmse())
    except Exception:
        pass
    return {
        "name": name,
        "success": bool(result.get("success")),
        "timed_out": bool(result.get("timed_out")),
        "elapsed_s": elapsed,
        "nfev": int(last.get("nfev") or 0),
        "eta": eta,
        "rmse_c": None if rmse_c is None else float(rmse_c),
        "nstep_rmse": nstep_rmse,
        "C": c_hat,
        "R": r_hat,
        "C_rel_err": abs(c_hat - TRUE_C) / TRUE_C,
        "R_rel_err": abs(r_hat - TRUE_R) / TRUE_R,
        "alpha": float(
            (result.get("estimated_heater_scales") or {}).get("living_room_heater") or 1.0
        ),
        "hist": [
            {
                "nfev": p.get("nfev"),
                "eta": point_eta(p),
                "phase": p.get("phase"),
            }
            for p in snaps
        ],
        "message": result.get("message"),
    }


def significantly_worse(base: dict, cand: dict) -> bool:
    eb, ec = base.get("eta"), cand.get("eta")
    if eb is None or ec is None:
        return not cand.get("success")
    if eb <= PE_ETA_TOL and ec > PE_ETA_TOL:
        return True
    if eb > PE_ETA_TOL and ec > eb * ETA_REL_WORSE:
        return True
    return False


def plot_eta(rows: list[dict], dest: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    colors = {"production_multistart": "#4fc3f7", "single_mle": "#ef5350",
              "oe_then_nstep": "#ffb74d"}
    for row in rows:
        xs = [p["nfev"] for p in row["hist"] if p.get("eta")]
        ys = [p["eta"] for p in row["hist"] if p.get("eta")]
        if not xs:
            continue
        ax.semilogy(
            xs, ys, "-", color=colors.get(row["name"], "#aaa"),
            lw=1.6, label=row["name"].replace("_", " "),
        )
    ax.axhline(PE_ETA_TOL, color="#f5a623", ls="--", lw=1.1, label="tol η=2")
    ax.axhline(1.0, color="#66bb6a", ls=":", lw=1.1, label="noise η=1")
    ax.set_xlabel("evaluation")
    ax.set_ylabel("normalised RMS η")
    ax.set_title("N-step PE: multistart vs single MLE")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=120)
    plt.close(fig)


def plot_bars(rows: list[dict], dest: Path) -> None:
    labels = [r["name"].replace("_", "\n") for r in rows]
    metrics = [
        ("nfev", "evaluations"),
        ("elapsed_s", "wall time [s]"),
        ("eta", "final η"),
        ("C_rel_err", "C relative error"),
        ("R_rel_err", "R relative error"),
    ]
    fig, axes = plt.subplots(1, len(metrics), figsize=(12.5, 3.4))
    for ax, (key, title) in zip(axes, metrics):
        vals = [float(r[key] or 0.0) for r in rows]
        ax.bar(range(len(rows)), vals, color=["#4fc3f7", "#ef5350", "#ffb74d"][: len(rows)])
        ax.set_xticks(range(len(rows)))
        ax.set_xticklabels(labels, fontsize=7)
        ax.set_title(title, fontsize=9)
        ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)


def write_report(payload: dict) -> None:
    INSPECT.mkdir(parents=True, exist_ok=True)
    (INSPECT / "01_compare.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rows = payload["methods"]
    lines = [
        "# Single-start N-step MLE vs production multistart",
        "",
        f"- Grid: dt={payload['dt_s']} s, N={payload['n_horizon']}, stride={payload['origin_stride']}",
        f"- Window: {payload['window_h']} h ({payload['n_steps']} steps)",
        f"- Truth: C={TRUE_C:.0f} J/K, R={TRUE_R}",
        f"- Prior: C={PRIOR_C:.0f} J/K, R={PRIOR_R}",
        f"- Cap: {CAP_S or 'none (to L-BFGS stop)'}",
        f"- Bar: η ≤ {PE_ETA_TOL} (same as production overlay); significantly worse if candidate misses the bar while baseline meets it, or η > {ETA_REL_WORSE}× baseline.",
        "",
        "| method | success | nfev | s | η | RMS °C | C | C rel err | R | R rel err |",
        "|--------|---------|------|---|---|--------|---|-----------|---|-----------|",
    ]
    for r in rows:
        eta = "—" if r["eta"] is None else f"{r['eta']:.3f}"
        rms = "—" if r["rmse_c"] is None else f"{r['rmse_c']:.3f}"
        lines.append(
            f"| {r['name']} | {r['success']} | {r['nfev']} | {r['elapsed_s']:.1f} | "
            f"{eta} | {rms} | {r['C']:.0f} | {r['C_rel_err']*100:.1f}% | "
            f"{r['R']:.4f} | {r['R_rel_err']*100:.1f}% |"
        )
    lines += [
        "",
        f"**Verdict:** {payload['verdict']}",
        "",
        payload["note"],
        "",
    ]
    (INSPECT / "01_compare.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    timing = timing_from_options(
        {},
        default_period=7200.0,
        default_substeps=8,
        default_horizon_h=36.0,
    )
    dt = timing.dt_s
    n_horizon = timing.n_fast
    stride = timing.fast_substeps
    n_steps = max(12, int(round(WINDOW_H * 3600.0 / dt)))
    history = excited_history(n_steps=n_steps, dt=dt)

    rows = []
    rows.append(run_method("production_multistart", history, dt, n_horizon, stride, None))
    rows.append(
        run_method(
            "single_mle",
            history,
            dt,
            n_horizon,
            stride,
            single_mle_multistart("prior"),
        )
    )
    worse = significantly_worse(rows[0], rows[1])
    note = (
        "Single N-step MLE from the configured prior, one L-BFGS. "
        "Production path is untouched; the candidate monkeypatches "
        "`_multistart_joint_nlp` in this process only."
    )
    if worse:
        rows.append(
            run_method(
                "oe_then_nstep",
                history,
                dt,
                n_horizon,
                stride,
                single_mle_multistart("oe_then_nstep"),
            )
        )
        note = (
            "Single MLE from the prior was significantly worse. Second candidate "
            "keeps the cheap tiled-OE warm-start, then one N-step L-BFGS "
            "(drops physics-informed and extra prior starts)."
        )
        if significantly_worse(rows[0], rows[-1]):
            verdict = (
                "delta: single MLE and OE→N-step both miss the bar versus "
                "production multistart — name a further start or scaling delta"
            )
        else:
            verdict = (
                "delta: keep OE warm-start + one N-step; drop extra N-step starts"
            )
    elif rows[1]["eta"] is not None and rows[0]["eta"] is not None:
        if rows[1]["nfev"] < rows[0]["nfev"] * 0.7:
            verdict = (
                "accept: single MLE matches fit bar with fewer evaluations — "
                "promote dropping extra starts"
            )
        else:
            verdict = "accept: single MLE matches fit bar (cost similar)"
    else:
        verdict = "delta: missing η — inspect traces"

    payload = {
        "dt_s": dt,
        "n_horizon": n_horizon,
        "origin_stride": stride,
        "window_h": WINDOW_H,
        "n_steps": n_steps,
        "true_C": TRUE_C,
        "true_R": TRUE_R,
        "prior_C": PRIOR_C,
        "prior_R": PRIOR_R,
        "cap_s": CAP_S,
        "eta_tol": PE_ETA_TOL,
        "single_mle_significantly_worse": worse,
        "verdict": verdict,
        "note": note,
        "host": "cursor-sandbox",
        "methods": [
            {k: v for k, v in r.items() if k != "hist"} | {"n_hist": len(r["hist"])}
            for r in rows
        ],
    }
    plot_eta(rows, INSPECT / "01_eta_traces.png")
    plot_bars(rows, INSPECT / "01_metrics.png")
    write_report(payload)
    (INSPECT / "01_traces.json").write_text(
        json.dumps({r["name"]: r["hist"] for r in rows}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({k: payload[k] for k in payload if k != "methods_full"}, indent=2))
    print(f"wrote {INSPECT / '01_compare.md'}")


if __name__ == "__main__":
    main()
