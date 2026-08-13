"""SWD-329: offline PE robustness on household-like 2R2C traces.

Completely separate from the running App. Calls the existing
``KalmanMLEstimator``; does not change production PE. Occupancy heat and
extra window UA are harness-only (best-effort). The report does not declare
a product winner.

Full factorial is ``pytest.mark.ondemand`` (not CI). Helper tests below have
no optimiser and stay on the fast shard.
"""
from __future__ import annotations

import math
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from unittest.mock import patch

import numpy as np
import pytest

from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room
from mbc.control import ScipyNLPBackend

DT_S = 900.0
ROOM = "lab"
HEATER = "lab_heater"
T0 = 1_700_000_000.0
SOLAR_OFF = 1.0  # W
HEATER_OFF = 0.02  # duty
N_STEPS = 96  # 24 h at 15 min
MAXITER = 25
UA_ASSUMED = 15.0  # W/K — harness guess, not plant truth
NIGHT_END_H = 6.0
REPORT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "docs", "agents",
    "REPORT-pe-robustness-household.md",
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

PRIOR = {
    "thermal_mass": 4.0e6,
    "r_external": 0.05,
    "solar_scale": 1.0,
    "power_scale": 1.0,
    "c_air_fraction": 0.05,
    "r_aw_fraction": 0.25,
    "internal_gain": 0.0,
}

OCC_PEAK = {"none": 0.0, "weak": 80.0, "strong": 250.0}
WIN_UA = {"none": 0.0, "weak": 8.0, "strong": 25.0}
OCC_LEVELS = ("none", "weak", "strong")
WIN_LEVELS = ("none", "weak", "strong")
PATHS = ("open_loop", "kalman")


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


def hour_of_day(k: int) -> float:
    """Hour in [0, 24) for step k at DT_S starting at midnight."""
    return (k * DT_S / 3600.0) % 24.0


def occupancy_watts(level: str, k: int) -> float:
    """Bursty household occupancy [W]. Estimators must not read this."""
    peak = OCC_PEAK[level]
    if peak <= 0.0:
        return 0.0
    h = hour_of_day(k)
    if h < NIGHT_END_H or h >= 23.0:
        return 0.0
    if 7.0 <= h < 8.5:
        return peak
    if 8.5 <= h < 12.0:
        return 0.15 * peak if level == "strong" else 0.0
    if 12.0 <= h < 13.0:
        return 0.7 * peak
    if 13.0 <= h < 17.0:
        return 0.1 * peak if level == "strong" else 0.0
    if 17.0 <= h < 23.0:
        return peak if (k // 3) % 2 == 0 else 0.3 * peak
    return 0.0


def window_is_open(level: str, k: int) -> bool:
    """Known contact schedule. UA-using candidates may read this."""
    if level == "none":
        return False
    h = hour_of_day(k)
    evening = 18.0 <= h < 18.75
    if level == "weak":
        return evening
    morning = 7.5 <= h < 8.0
    afternoon = 15.0 <= h < 15.5
    late = 21.0 <= h < 21.5
    return morning or evening or afternoon or late


def _excitation(k: int) -> Tuple[float, float]:
    """Household-like mixed heater + solar (not cleanly separable)."""
    h = hour_of_day(k)
    night = h < 7.0 or h >= 19.0
    solar = 0.0
    if not night:
        solar = max(0.0, 500.0 * math.sin(math.pi * (h - 7.0) / 12.0))
    duty = 0.5 if (k // 5) % 2 == 0 else 0.0
    if night:
        duty = 0.8 if (k // 4) % 2 == 0 else 0.0
        solar = 0.0
    return duty, solar


def simulate_history(
    occ: str,
    win: str,
    n_steps: int = N_STEPS,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    """2R2C plant with extras added on the air node via heat_inputs."""
    rng = np.random.default_rng(seed)
    room = _truth_room()
    model = HouseModel([room])
    heater = _heater(TRUTH["power_scale"])
    ua = WIN_UA[win]
    recs: List[Dict[str, Any]] = []
    for k in range(n_steps):
        duty, solar = _excitation(k)
        tout = 2.0 + 4.0 * math.sin(2.0 * math.pi * k / 96.0)
        ta = float(model.rooms[ROOM].temperature)
        q_heat = duty * heater.max_power * heater.efficiency * TRUTH["power_scale"]
        q_occ = occupancy_watts(occ, k)
        open_k = window_is_open(win, k)
        q_win = ua * (tout - ta) if open_k else 0.0
        model.step(
            dt=DT_S,
            heat_inputs={ROOM: q_heat + q_occ + q_win},
            outdoor_temp=tout,
            solar_gains={ROOM: solar},
        )
        ta_after = float(model.rooms[ROOM].temperature)
        recs.append({
            "y": [ta_after + 0.05 * rng.standard_normal()],
            "u": [duty],
            "d_outdoor": tout,
            "d_solar": {ROOM: solar},
            "timestamp": T0 + DT_S * k,
            "ym": [ta_after],
            "window_open": {ROOM: open_k},
            "occupancy_w": q_occ,  # plant truth; procedures must not use this
            "q_win_w": q_win,
        })
    return recs


def _solar_w(rec: Dict[str, Any]) -> float:
    return float(rec.get("d_solar", {}).get(ROOM, 0.0))


def _duty(rec: Dict[str, Any]) -> float:
    u = rec.get("u") or [0.0]
    return float(u[0]) if u else 0.0


def _is_open(rec: Dict[str, Any]) -> bool:
    return bool((rec.get("window_open") or {}).get(ROOM, False))


def _is_night(rec: Dict[str, Any]) -> bool:
    k = int((float(rec["timestamp"]) - T0) / DT_S)
    h = hour_of_day(k)
    return h < NIGHT_END_H or h >= 23.0


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


def history_with_ua_channel(
    history: Sequence[Dict[str, Any]],
    ua: float = UA_ASSUMED,
) -> List[Dict[str, Any]]:
    """Include open samples; attach assumed-UA air heat. Occupancy watts omitted."""
    out: List[Dict[str, Any]] = []
    for rec in history:
        rec2 = dict(rec)
        rec2["window_open"] = {ROOM: False}
        y = float((rec.get("y") or rec.get("ym") or [0.0])[0])
        tout = float(rec["d_outdoor"])
        open_k = _is_open(rec)
        rec2["q_ua_w"] = ua * (tout - y) if open_k else 0.0
        rec2["window_contact"] = open_k
        out.append(rec2)
    return out


def _estimator() -> KalmanMLEstimator:
    return KalmanMLEstimator(
        [_prior_room()], [_heater(PRIOR["power_scale"])],
        dt=DT_S, regularization=0.01, max_window_steps=40,
    )


@contextmanager
def _capped_scipy(maxiter: int = MAXITER) -> Iterator[None]:
    orig = ScipyNLPBackend.__init__

    def _init(self, *, method="L-BFGS-B", options=None, scaling=None, **kwargs):  # type: ignore[no-untyped-def]
        options = dict(options or {})
        options["maxiter"] = min(int(options.get("maxiter", maxiter)), maxiter)
        orig(self, method=method, options=options, scaling=scaling, **kwargs)

    with patch.object(ScipyNLPBackend, "__init__", _init):
        yield


def _install_ua_disturbance(est: KalmanMLEstimator) -> None:
    orig = est._convert_history_std

    def wrapped(history, use_ym=False):  # type: ignore[no-untyped-def]
        std = orig(history, use_ym=use_ym)
        n = est._n
        for rec_in, rec_out in zip(history, std):
            rec_out["d"][1 + n] = float(rec_in.get("q_ua_w", 0.0))
        return std

    est._convert_history_std = wrapped  # type: ignore[method-assign]


def _install_ped_objective(est: KalmanMLEstimator) -> None:
    def ped(*args, **kwargs):  # type: ignore[no-untyped-def]
        seq = args
        if seq and not isinstance(seq[0], np.ndarray):
            seq = seq[1:]
        theta, layout, std_history = seq[0], seq[1], seq[2]
        try:
            system0 = est._build_parametric_system(layout, np.asarray(theta))
            if system0 is None:
                ntheta = len(np.asarray(theta))
                return 1e10, np.zeros(ntheta)
            ym0 = np.asarray(std_history[0]["ym"], dtype=float)
            u0 = std_history[0].get("u")
            d0 = std_history[0].get("d")
            x0, P0 = est._initial_state_and_covariance(system0, ym0, u=u0, d=d0)
            return est._cd_ped_neg_ll_and_grad(
                np.asarray(theta), layout, std_history, x0, P0,
            )
        except Exception:
            ntheta = len(np.asarray(theta))
            return 1e10, np.zeros(ntheta)

    est._simulation_mse_and_grad = ped  # type: ignore[method-assign]


def _run_fit(
    history: List[Dict[str, Any]],
    starts: List[float] | None = None,
    locked: Dict[str, Any] | None = None,
    path: str = "open_loop",
    use_ua: bool = False,
) -> Dict[str, Any]:
    est = _estimator()
    est._physics_informed_theta = lambda *a, **k: None  # type: ignore[method-assign]
    if use_ua:
        _install_ua_disturbance(est)
    if path == "kalman":
        _install_ped_objective(est)
    with _capped_scipy():
        return est.estimate(
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


def procedure_today_combined(
    history: List[Dict[str, Any]], path: str,
) -> Dict[str, Any]:
    starts = [float(history[0]["timestamp"])] if history else []
    return _run_fit(history, starts, path=path)


def procedure_occupancy_tv(
    history: List[Dict[str, Any]], path: str, use_ua: bool = False,
) -> Dict[str, Any]:
    """Best-effort: night clock fragments for C,R; then day with C,R locked.

    Uses hour-of-day, not plant occupancy watts.
    """
    night = contiguous_fragments(history, lambda r: _is_night(r) and not _is_open(r))
    day = contiguous_fragments(history, lambda r: (not _is_night(r)) and not _is_open(r))
    hist_n, starts_n = concat_with_starts(night)
    hist_d, starts_d = concat_with_starts(day)
    locked: Dict[str, Any] = {"internal_gain": {ROOM: 0.0}}
    notes = []
    if len(hist_n) >= 12:
        r1 = _run_fit(hist_n, starts_n, locked=locked, path=path, use_ua=use_ua)
        notes.append("stage1_night_envelope")
        if r1.get("success"):
            t1 = _theta_from_result(r1)
            locked = {
                "thermal_mass": {ROOM: t1["thermal_mass"]},
                "r_external": {ROOM: t1["r_external"]},
            }
    else:
        notes.append("stage1_skipped_short")
        locked = {}
    if len(hist_d) >= 12:
        r2 = _run_fit(hist_d, starts_d, locked=locked or None, path=path, use_ua=use_ua)
        notes.append("stage2_day_disturbance")
        result = dict(r2)
    else:
        notes.append("stage2_skipped_short")
        starts_all = [float(history[0]["timestamp"])]
        result = dict(_run_fit(history, starts_all, locked=locked or None, path=path, use_ua=use_ua))
    result["stage_notes"] = notes
    result["locked_keys"] = sorted(locked.keys())
    return result


def procedure_window_ua(
    history: List[Dict[str, Any]], path: str,
) -> Dict[str, Any]:
    hist = history_with_ua_channel(history)
    starts = [float(hist[0]["timestamp"])] if hist else []
    result = dict(_run_fit(hist, starts, path=path, use_ua=True))
    result["stage_notes"] = ["include_open_assumed_ua"]
    return result


def procedure_both(
    history: List[Dict[str, Any]], path: str,
) -> Dict[str, Any]:
    hist = history_with_ua_channel(history)
    result = procedure_occupancy_tv(hist, path, use_ua=True)
    notes = list(result.get("stage_notes") or [])
    notes.append("plus_assumed_ua")
    result["stage_notes"] = notes
    return result


def procedure_separated_joint(
    history: List[Dict[str, Any]], path: str,
) -> Dict[str, Any]:
    solar_off = contiguous_fragments(
        history, lambda r: _solar_w(r) <= SOLAR_OFF and not _is_open(r),
    )
    heater_off_sun = contiguous_fragments(
        history,
        lambda r: (
            _duty(r) <= HEATER_OFF
            and _solar_w(r) > SOLAR_OFF
            and not _is_open(r)
        ),
    )
    hist, starts = concat_with_starts(list(solar_off) + list(heater_off_sun))
    if len(hist) < 12:
        return {"success": False, "message": "separated fragments too short"}
    return _run_fit(hist, starts, path=path)


def procedure_separated_staged(
    history: List[Dict[str, Any]], path: str,
) -> Dict[str, Any]:
    solar_off = contiguous_fragments(
        history, lambda r: _solar_w(r) <= SOLAR_OFF and not _is_open(r),
    )
    heater_off_sun = contiguous_fragments(
        history,
        lambda r: (
            _duty(r) <= HEATER_OFF
            and _solar_w(r) > SOLAR_OFF
            and not _is_open(r)
        ),
    )
    hist_env, starts_env = concat_with_starts(solar_off)
    hist_sol, starts_sol = concat_with_starts(heater_off_sun)
    locked: Dict[str, Any] = {}
    stage_notes = []
    if len(hist_env) >= 12:
        r1 = _run_fit(hist_env, starts_env, path=path)
        stage_notes.append("stage1_solar_off_envelope")
        if r1.get("success"):
            t1 = _theta_from_result(r1)
            locked["thermal_mass"] = {ROOM: t1["thermal_mass"]}
            locked["r_external"] = {ROOM: t1["r_external"]}
    else:
        stage_notes.append("stage1_skipped_short")
    if len(hist_sol) >= 12:
        r2 = _run_fit(hist_sol, starts_sol, locked=locked or None, path=path)
        stage_notes.append("stage2_heater_off_solar")
        if r2.get("success"):
            t2 = _theta_from_result(r2)
            locked["solar_scale"] = {ROOM: t2["solar_scale"]}
    else:
        stage_notes.append("stage2_skipped_short")
    starts_all = [float(history[0]["timestamp"])]
    r3 = dict(_run_fit(history, starts_all, locked=locked or None, path=path))
    r3["stage_notes"] = stage_notes
    r3["locked_keys"] = sorted(locked.keys())
    return r3


PROCEDURES: Tuple[Tuple[str, Callable[..., Dict[str, Any]]], ...] = (
    ("today_combined", procedure_today_combined),
    ("occupancy_tv", procedure_occupancy_tv),
    ("window_ua", procedure_window_ua),
    ("both", procedure_both),
    ("separated_joint", procedure_separated_joint),
    ("separated_staged", procedure_separated_staged),
)


def scenario_name(occ: str, win: str) -> str:
    return f"occ_{occ}__win_{win}"


@dataclass
class Row:
    scenario: str
    procedure: str
    path: str
    success: bool
    theta: Dict[str, float]
    rel: Dict[str, float]
    message: str
    extra: str = field(default="")


def run_bakeoff(
    scenarios: Optional[Sequence[Tuple[str, str]]] = None,
    procedures: Optional[Sequence[Tuple[str, Callable[..., Dict[str, Any]]]]] = None,
    paths: Sequence[str] = PATHS,
) -> List[Row]:
    rows: List[Row] = []
    grid = scenarios or [(o, w) for o in OCC_LEVELS for w in WIN_LEVELS]
    procs = procedures or PROCEDURES
    for occ, win in grid:
        history = simulate_history(occ, win)
        kind = scenario_name(occ, win)
        for name, fn in procs:
            for path in paths:
                result = fn(history, path)
                theta = _theta_from_result(result)
                rel = {
                    k: _rel_err(theta[k], TRUTH[k])
                    for k in (
                        "thermal_mass", "r_external", "solar_scale", "power_scale",
                    )
                }
                extra = ""
                notes = result.get("stage_notes")
                if notes:
                    extra = f"notes={notes}"
                locked = result.get("locked_keys")
                if locked:
                    extra = (extra + f" locked={locked}").strip()
                rows.append(Row(
                    scenario=kind,
                    procedure=name,
                    path=path,
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
    n_expected = len(OCC_LEVELS) * len(WIN_LEVELS) * len(PROCEDURES) * len(PATHS)
    lines = [
        "# Report: PE robustness on household-like 2R2C traces",
        "",
        "Offline synthetic factorial for SWD-329. **No product winner is declared.**",
        "Judge whether extras / procedures recover true θ well enough to ship.",
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
        "## Plant extras (known; not production HouseModel)",
        "",
        "- Occupancy: bursty household schedule on the air node "
        f"(none / weak {OCC_PEAK['weak']:.0f} W peak / strong "
        f"{OCC_PEAK['strong']:.0f} W peak).",
        "- Open window/door: extra outdoor exchange "
        r"Q = UA (T_out - T_a) "
        f"(none / weak {WIN_UA['weak']:.0f} W/K / strong {WIN_UA['strong']:.0f} W/K).",
        "- Occupancy watts are stored on records for the plant only; "
        "procedures never pass them to the estimator.",
        "",
        "## Procedures (best-effort harness)",
        "",
        "1. `today_combined` — combined joint, constant `internal_gain`, "
        "SWD-322 `window_open` mask (production open-loop exclusion).",
        "2. `occupancy_tv` — night/empty **clock** fragments (00:00–06:00 and "
        "23:00–24:00) for envelope C,R with `internal_gain` locked 0; then "
        "daytime with C,R locked. Does **not** use plant occupancy watts.",
        "3. `window_ua` — include open samples; inject assumed UA "
        f"= {UA_ASSUMED:.0f} W/K × contact × (T_out − y) into the air-heat "
        "disturbance slot. Assumed UA is not plant truth.",
        "4. `both` — occupancy_tv + assumed-UA channel.",
        "5. `separated_joint` — SWD-326 solar-off + heater-off+solar fragments, "
        "open samples dropped.",
        "6. `separated_staged` — SWD-326 staged locking, open samples dropped.",
        "",
        "## Estimator paths",
        "",
        "- `open_loop` — production `KalmanMLEstimator.estimate()` (open-loop "
        "simulation MSE, SciPy L-BFGS-B).",
        "- `kalman` — same `estimate()` entry, with the open-loop objective "
        "swapped for CD-EKF PED NLL (`_cd_ped_neg_ll_and_grad`). Harness-only; "
        "production `estimate()` is unchanged. PED may still score open-window "
        "samples (exclusion is an open-loop MSE feature).",
        "",
        "## Runtime caps",
        "",
        f"- n_steps = {N_STEPS} (24 h at 15 min).",
        f"- maxiter = {MAXITER}; physics-informed start skipped (prior only).",
        f"- Grid size: {n_expected} fits "
        f"({len(OCC_LEVELS)}×{len(WIN_LEVELS)} scenarios × "
        f"{len(PROCEDURES)} procedures × {len(PATHS)} paths); staged "
        "procedures add extra inner fits.",
        "- Marker: `pytest.mark.ondemand` — not CI.",
        "",
        "## Relative |error| vs true θ",
        "",
        "| Scenario | Procedure | Path | OK | C | R_ext | solar_scale | α | notes |",
        "|----------|-----------|------|----|---|-------|-------------|---|-------|",
    ]
    for row in rows:
        def fmt(key: str) -> str:
            v = row.rel.get(key, float("nan"))
            return "—" if not math.isfinite(v) else f"{100.0 * v:.1f}%"
        notes = (row.extra or "").replace("|", "/")
        lines.append(
            f"| {row.scenario} | {row.procedure} | {row.path} | "
            f"{'yes' if row.success else 'no'} | "
            f"{fmt('thermal_mass')} | {fmt('r_external')} | "
            f"{fmt('solar_scale')} | {fmt('power_scale')} | {notes} |"
        )
    lines.extend([
        "",
        "## Recovered values",
        "",
        "| Scenario | Procedure | Path | C | R_ext | solar_scale | α |",
        "|----------|-----------|------|---|-------|-------------|---|",
    ])
    for row in rows:
        t = row.theta
        lines.append(
            f"| {row.scenario} | {row.procedure} | {row.path} | "
            f"{t.get('thermal_mass', float('nan')):.3e} | "
            f"{t.get('r_external', float('nan')):.4f} | "
            f"{t.get('solar_scale', float('nan')):.3f} | "
            f"{t.get('power_scale', float('nan')):.3f} |"
        )
    lines.extend([
        "",
        "## Open",
        "",
        "Whether any procedure or extra is worth shipping in Parameter "
        "Estimation is a human decision on this report.",
        "",
    ])
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestSwd329Helpers:
    def test_occupancy_none_is_zero(self):
        assert all(occupancy_watts("none", k) == 0.0 for k in range(N_STEPS))

    def test_occupancy_is_bursty_not_square(self):
        weak = [occupancy_watts("weak", k) for k in range(N_STEPS)]
        strong = [occupancy_watts("strong", k) for k in range(N_STEPS)]
        assert max(weak) == pytest.approx(OCC_PEAK["weak"])
        assert max(strong) > max(weak)
        assert min(weak) == 0.0
        # Not a single daily square wave: more than two transitions.
        trans = sum(1 for a, b in zip(weak, weak[1:]) if (a > 0) != (b > 0))
        assert trans >= 4

    def test_window_levels(self):
        none = sum(1 for k in range(N_STEPS) if window_is_open("none", k))
        weak = sum(1 for k in range(N_STEPS) if window_is_open("weak", k))
        strong = sum(1 for k in range(N_STEPS) if window_is_open("strong", k))
        assert none == 0
        assert 0 < weak < strong

    def test_plant_occupancy_warms_air(self):
        cold = simulate_history("none", "none", seed=1)
        occ = simulate_history("strong", "none", seed=1)
        mean_c = float(np.mean([r["ym"][0] for r in cold]))
        mean_o = float(np.mean([r["ym"][0] for r in occ]))
        assert mean_o > mean_c + 0.05

    def test_plant_open_window_cools_air(self):
        closed = simulate_history("none", "none", seed=2)
        opened = simulate_history("none", "strong", seed=2)
        open_idx = [k for k in range(N_STEPS) if window_is_open("strong", k)]
        assert open_idx
        # Compare shortly after openings.
        deltas = []
        for k in open_idx:
            j = min(k + 1, N_STEPS - 1)
            deltas.append(opened[j]["ym"][0] - closed[j]["ym"][0])
        assert float(np.mean(deltas)) < 0.0

    def test_procedures_do_not_read_occupancy_watts(self):
        src = open(__file__, encoding="utf-8").read()
        # occupancy_w is plant metadata; fitting helpers must not consume it.
        assert "occupancy_w" in src
        banned = (
            'rec["occupancy_w"]',
            "rec.get(\"occupancy_w\"",
            "r.get(\"occupancy_w\"",
            "r[\"occupancy_w\"]",
        )
        body = src.split("class TestSwd329Helpers")[0]
        for token in banned:
            assert token not in body

    def test_ua_channel_uses_contact_not_plant_q(self):
        hist = simulate_history("none", "strong")
        ua_hist = history_with_ua_channel(hist)
        assert any(abs(r["q_ua_w"]) > 1.0 for r in ua_hist)
        assert all(r["window_open"][ROOM] is False for r in ua_hist)
        # Assumed UA, not plant truth.
        plant = next(r for r in hist if _is_open(r) and abs(r["q_win_w"]) > 1.0)
        ua_rec = next(r for r in ua_hist if r.get("window_contact"))
        assert abs(ua_rec["q_ua_w"]) != pytest.approx(abs(plant["q_win_w"]))

    def test_write_report_declares_no_winner(self, tmp_path):
        row = Row(
            scenario="occ_none__win_none",
            procedure="today_combined",
            path="open_loop",
            success=True,
            theta={
                "thermal_mass": TRUTH["thermal_mass"],
                "r_external": TRUTH["r_external"],
                "solar_scale": TRUTH["solar_scale"],
                "power_scale": TRUTH["power_scale"],
            },
            rel={
                "thermal_mass": 0.0,
                "r_external": 0.0,
                "solar_scale": 0.0,
                "power_scale": 0.0,
            },
            message="ok",
        )
        path = write_report([row], path=str(tmp_path / "report.md"))
        body = open(path, encoding="utf-8").read()
        assert "No product winner is declared" in body
        assert "today_combined" in body
        assert "occupancy_tv" in body
        assert "window_ua" in body


class TestSwd329Bakeoff:
    @pytest.mark.ondemand
    def test_factorial_runs_and_writes_report(self):
        rows = run_bakeoff()
        assert len(rows) == (
            len(OCC_LEVELS) * len(WIN_LEVELS) * len(PROCEDURES) * len(PATHS)
        )
        path = write_report(rows)
        assert os.path.isfile(path)
        body = open(path, encoding="utf-8").read()
        assert "No product winner is declared" in body
        assert "today_combined" in body
        assert "occupancy_tv" in body
        assert "window_ua" in body
        assert "separated_joint" in body
        assert "separated_staged" in body
        none_today = next(
            r for r in rows
            if r.scenario == "occ_none__win_none"
            and r.procedure == "today_combined"
            and r.path == "open_loop"
        )
        assert none_today.success
        # Do not assert which procedure recovers θ best.
