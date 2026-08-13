"""SWD-326: offline bake-off of combined vs separated/staged 2R2C PE.

Completely separate from the running App. Calls the existing open-loop
``KalmanMLEstimator``; does not change production PE. The report does not
declare a product winner.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Tuple

import numpy as np
import pytest

from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room

DT_S = 900.0
ROOM = "lab"
HEATER = "lab_heater"
T0 = 1_700_000_000.0
SOLAR_OFF = 1.0  # W
HEATER_OFF = 0.02  # duty
REPORT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "docs", "agents", "REPORT-pe-dataset-separation.md"
)

TRUTH = {
    "thermal_mass": 8.0e6,
    "r_external": 0.025,
    "solar_scale": 1.5,
    "power_scale": 1.0,
    "c_air_fraction": 0.08,
    "r_aw_fraction": 0.35,
    "internal_gain": 0.0,
}

# Deliberately wrong prior so recovery error is visible.
PRIOR = {
    "thermal_mass": 4.0e6,
    "r_external": 0.05,
    "solar_scale": 1.0,
    "power_scale": 1.0,
    "c_air_fraction": 0.05,
    "r_aw_fraction": 0.25,
    "internal_gain": 0.0,
}


def _truth_room(**overrides: Any) -> Room:
    kw = dict(
        name=ROOM,
        thermal_mass=TRUTH["thermal_mass"],
        r_external=TRUTH["r_external"],
        internal_gain=TRUTH["internal_gain"],
        solar_scale=TRUTH["solar_scale"],
        c_air_fraction=TRUTH["c_air_fraction"],
        r_aw_fraction=TRUTH["r_aw_fraction"],
        sky_radiative_ua=0.0,
        thermal_bridge_psi_l=0.0,
        facade_solar_share=0.0,
        temperature=20.0,
        wall_temperature=16.0,
    )
    kw.update(overrides)
    return Room(**kw)


def _prior_room() -> Room:
    return _truth_room(
        thermal_mass=PRIOR["thermal_mass"],
        r_external=PRIOR["r_external"],
        solar_scale=PRIOR["solar_scale"],
        c_air_fraction=PRIOR["c_air_fraction"],
        r_aw_fraction=PRIOR["r_aw_fraction"],
        temperature=20.0,
        wall_temperature=18.0,
    )


def _heater(power_scale: float = 1.0) -> ElectricHeater:
    return ElectricHeater(
        name=HEATER, room=ROOM, max_power=2000.0, power_scale=power_scale,
    )


def _hour_of_day(k: int) -> float:
    """Hour in [0, 24) for step k at DT_S starting at midnight."""
    return (k * DT_S / 3600.0) % 24.0


def _excitation(kind: str, k: int) -> Tuple[float, float]:
    """Return (heater duty, unscaled solar W) for scenario ``kind``."""
    h = _hour_of_day(k)
    night = h < 7.0 or h >= 19.0
    if kind == "strong_separable":
        if night:
            return (0.8 if (k // 4) % 2 == 0 else 0.0), 0.0
        return 0.0, max(0.0, 600.0 * math.sin(math.pi * (h - 7.0) / 12.0))
    if kind == "weaker":
        if night:
            return (0.35 if (k // 6) % 2 == 0 else 0.0), 0.0
        return 0.0, max(0.0, 250.0 * math.sin(math.pi * (h - 7.0) / 12.0))
    # mixed: some daytime heating (less separable)
    solar = 0.0
    if not night:
        solar = max(0.0, 500.0 * math.sin(math.pi * (h - 7.0) / 12.0))
    duty = 0.5 if (k // 5) % 2 == 0 else 0.0
    return duty, solar


def simulate_history(
    kind: str, n_steps: int = 160, seed: int = 0,
) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(seed)
    room = _truth_room()
    model = HouseModel([room])
    heater = _heater(TRUTH["power_scale"])
    recs: List[Dict[str, Any]] = []
    tout = 2.0
    for k in range(n_steps):
        duty, solar = _excitation(kind, k)
        tout = 2.0 + 4.0 * math.sin(2.0 * math.pi * k / 96.0)
        q_heat = duty * heater.max_power * heater.efficiency * TRUTH["power_scale"]
        model.step(
            dt=DT_S,
            heat_inputs={ROOM: q_heat},
            outdoor_temp=tout,
            solar_gains={ROOM: solar},
        )
        ta = float(model.rooms[ROOM].temperature)
        recs.append({
            "y": [ta + 0.05 * rng.standard_normal()],
            "u": [duty],
            "d_outdoor": tout,
            "d_solar": {ROOM: solar},
            "timestamp": T0 + DT_S * k,
            "ym": [ta],
        })
    return recs


def _solar_w(rec: Dict[str, Any]) -> float:
    return float(rec.get("d_solar", {}).get(ROOM, 0.0))


def _duty(rec: Dict[str, Any]) -> float:
    u = rec.get("u") or [0.0]
    return float(u[0]) if u else 0.0


def contiguous_fragments(
    history: Sequence[Dict[str, Any]],
    pred: Callable[[Dict[str, Any]], bool],
    min_steps: int = 8,
) -> List[List[Dict[str, Any]]]:
    frags: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    for rec in history:
        if pred(rec):
            cur.append(rec)
        elif cur:
            if len(cur) >= min_steps:
                frags.append(cur)
            cur = []
    if len(cur) >= min_steps:
        frags.append(cur)
    return frags


def concat_with_starts(
    frags: Sequence[Sequence[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[float]]:
    hist: List[Dict[str, Any]] = []
    starts: List[float] = []
    for frag in frags:
        if not frag:
            continue
        starts.append(float(frag[0]["timestamp"]))
        hist.extend(frag)
    return hist, starts


def _estimator() -> KalmanMLEstimator:
    return KalmanMLEstimator(
        [_prior_room()], [_heater(PRIOR["power_scale"])],
        dt=DT_S, regularization=0.01, max_window_steps=40,
    )


def _run_fit(
    history: List[Dict[str, Any]],
    starts: List[float] | None = None,
    locked: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return _estimator().estimate(
        history,
        locked_params=locked,
        dataset_start_timestamps=starts,
    )


def _theta_from_result(result: Dict[str, Any]) -> Dict[str, float]:
    ep = result.get("estimated_params", {}).get(ROOM, {})
    return {
        "thermal_mass": float(ep.get("thermal_mass", math.nan)),
        "r_external": float(ep.get("r_external", math.nan)),
        "solar_scale": float(
            result.get("estimated_solar_scales", {}).get(ROOM, math.nan)
        ),
        "power_scale": float(
            result.get("estimated_heater_scales", {}).get(HEATER, math.nan)
        ),
        "success": 1.0 if result.get("success") else 0.0,
    }


def _rel_err(est: float, truth: float) -> float:
    if not math.isfinite(est) or abs(truth) < 1e-12:
        return float("nan")
    return abs(est - truth) / abs(truth)


def procedure_combined(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    starts = [float(history[0]["timestamp"])] if history else []
    return _run_fit(history, starts)


def procedure_separated_joint(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    solar_off = contiguous_fragments(
        history, lambda r: _solar_w(r) <= SOLAR_OFF,
    )
    heater_off_sun = contiguous_fragments(
        history,
        lambda r: _duty(r) <= HEATER_OFF and _solar_w(r) > SOLAR_OFF,
    )
    hist, starts = concat_with_starts(list(solar_off) + list(heater_off_sun))
    if len(hist) < 12:
        return {"success": False, "message": "separated fragments too short"}
    return _run_fit(hist, starts)


def procedure_separated_staged(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    solar_off = contiguous_fragments(
        history, lambda r: _solar_w(r) <= SOLAR_OFF,
    )
    heater_off_sun = contiguous_fragments(
        history,
        lambda r: _duty(r) <= HEATER_OFF and _solar_w(r) > SOLAR_OFF,
    )
    hist_env, starts_env = concat_with_starts(solar_off)
    hist_sol, starts_sol = concat_with_starts(heater_off_sun)

    locked: Dict[str, Any] = {}
    stage_notes = []

    if len(hist_env) >= 12:
        r1 = _run_fit(hist_env, starts_env)
        stage_notes.append("stage1_solar_off_envelope")
        if r1.get("success"):
            t1 = _theta_from_result(r1)
            locked["thermal_mass"] = {ROOM: t1["thermal_mass"]}
            locked["r_external"] = {ROOM: t1["r_external"]}
    else:
        stage_notes.append("stage1_skipped_short")

    if len(hist_sol) >= 12:
        r2 = _run_fit(hist_sol, starts_sol, locked=locked or None)
        stage_notes.append("stage2_heater_off_solar")
        if r2.get("success"):
            t2 = _theta_from_result(r2)
            locked["solar_scale"] = {ROOM: t2["solar_scale"]}
    else:
        stage_notes.append("stage2_skipped_short")

    starts_all = [float(history[0]["timestamp"])]
    r3 = _run_fit(history, starts_all, locked=locked or None)
    r3 = dict(r3)
    r3["stage_notes"] = stage_notes
    r3["locked_keys"] = sorted(locked.keys())
    return r3


PROCEDURES = (
    ("combined_joint", procedure_combined),
    ("separated_joint", procedure_separated_joint),
    ("separated_staged", procedure_separated_staged),
)

SCENARIOS = ("strong_separable", "weaker", "mixed")


@dataclass
class Row:
    scenario: str
    procedure: str
    success: bool
    theta: Dict[str, float]
    rel: Dict[str, float]
    message: str
    extra: str


def run_bakeoff() -> List[Row]:
    rows: List[Row] = []
    for kind in SCENARIOS:
        history = simulate_history(kind)
        for name, fn in PROCEDURES:
            result = fn(history)
            theta = _theta_from_result(result)
            rel = {
                k: _rel_err(theta[k], TRUTH[k])
                for k in ("thermal_mass", "r_external", "solar_scale", "power_scale")
            }
            extra = ""
            if name == "separated_staged":
                extra = f"locked={result.get('locked_keys')}"
            rows.append(Row(
                scenario=kind,
                procedure=name,
                success=bool(result.get("success")),
                theta=theta,
                rel=rel,
                message=str(result.get("message") or ""),
                extra=extra,
            ))
    return rows


def write_report(rows: List[Row], path: str = REPORT_PATH) -> str:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [
        "# Report: Combined vs separated/staged 2R2C parameter recovery",
        "",
        "Offline synthetic bake-off for SWD-326. **No product winner is declared.**",
        "Judge whether separated/staged recovery of true θ is worth the extra code.",
        "",
        "## Truth",
        "",
        f"- thermal_mass = {TRUTH['thermal_mass']:.3e} J/K",
        f"- r_external = {TRUTH['r_external']} K/W",
        f"- solar_scale = {TRUTH['solar_scale']}",
        f"- power_scale (α) = {TRUTH['power_scale']}",
        f"- Prior (wrong on purpose): C={PRIOR['thermal_mass']:.3e}, "
        f"R={PRIOR['r_external']}, s={PRIOR['solar_scale']}",
        "",
        "## Procedures",
        "",
        "1. `combined_joint` — one window, shared θ, one T_w(t_0).",
        "2. `separated_joint` — solar-off fragments plus heater-off+solar "
        "fragments; shared θ; one T_w(t_0) per fragment.",
        "3. `separated_staged` — best-effort lock C,R from solar-off, solar "
        "scale from heater-off+solar, then remaining on the full window.",
        "",
        "Estimator: existing open-loop `KalmanMLEstimator` (production PE). "
        "Plant: 2R2C `HouseModel`. App / UI unused.",
        "",
        "## Relative |error| vs true θ",
        "",
        "| Scenario | Procedure | OK | C | R_ext | solar_scale | α | notes |",
        "|----------|-----------|----|---|-------|-------------|---|-------|",
    ]
    for row in rows:
        def fmt(key: str) -> str:
            v = row.rel.get(key, float("nan"))
            return "—" if not math.isfinite(v) else f"{100.0 * v:.1f}%"
        notes = (row.extra or "").replace("|", "/")
        lines.append(
            f"| {row.scenario} | {row.procedure} | "
            f"{'yes' if row.success else 'no'} | "
            f"{fmt('thermal_mass')} | {fmt('r_external')} | "
            f"{fmt('solar_scale')} | {fmt('power_scale')} | {notes} |"
        )
    lines.extend([
        "",
        "## Recovered values",
        "",
        "| Scenario | Procedure | C | R_ext | solar_scale | α |",
        "|----------|-----------|---|-------|-------------|---|",
    ])
    for row in rows:
        t = row.theta
        lines.append(
            f"| {row.scenario} | {row.procedure} | "
            f"{t.get('thermal_mass', float('nan')):.3e} | "
            f"{t.get('r_external', float('nan')):.4f} | "
            f"{t.get('solar_scale', float('nan')):.3f} | "
            f"{t.get('power_scale', float('nan')):.3f} |"
        )
    lines.extend([
        "",
        "## Staging recipe (best-effort)",
        "",
        "- Solar-off (`Q_solar` ≤ 1 W): identify envelope `thermal_mass` and "
        "`r_external`, then lock them.",
        "- Heater-off and solar on (`u` ≤ 0.02 and `Q_solar` > 1 W): identify "
        "`solar_scale`, then lock it.",
        "- Full combined window: remaining parameters (including heater α) "
        "with those locks.",
        "",
        "## Open",
        "",
        "Whether the gain (if any) is large enough to ship auto-separation "
        "in Parameter Estimation is a human decision on this report.",
        "",
    ])
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestSwd326PeSplitBenchmark:
    @pytest.mark.slow
    def test_bakeoff_runs_and_writes_report(self):
        rows = run_bakeoff()
        assert len(rows) == len(SCENARIOS) * len(PROCEDURES)
        path = write_report(rows)
        assert os.path.isfile(path)
        body = open(path, encoding="utf-8").read()
        assert "combined_joint" in body
        assert "separated_joint" in body
        assert "separated_staged" in body
        assert "No product winner is declared" in body
        # Combined baseline must actually fit on the strong case.
        strong_combined = next(
            r for r in rows
            if r.scenario == "strong_separable" and r.procedure == "combined_joint"
        )
        assert strong_combined.success
        # Do not assert which procedure recovers θ best.
