#!/usr/bin/env python3
"""SWD-431 visual sandbox: Tuning preview vs room-view HA Ingress plots.

Isolation tree only. Wraps production ControlEngine, preview_tuning_forecast,
and build_app_forecast_payload. Does not edit production source.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heatingassistant.app.forecast_payload import build_app_forecast_payload  # noqa: E402
from heatingassistant.engine.const import (  # noqa: E402
    DEFAULT_ENERGY_PRICE_WEIGHT,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_TRACKING_WEIGHT,
)
from heatingassistant.engine.control_loop import ControlEngine  # noqa: E402
from heatingassistant.engine.naming import room_slug  # noqa: E402

HERE = Path(__file__).resolve().parent
INSPECT = HERE / "inspect"
ROOM = "Living Room"
SLUG = room_slug(ROOM)
HISTORY_HOURS = 12.0
TICKS_INTO_PLAN = 7  # almost one 2 h period of 15 min ticks


def _config() -> dict[str, Any]:
    return {
        "nmpc_period": DEFAULT_NMPC_PERIOD,
        "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
        "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
        "latitude": 55.67,
        "longitude": 12.57,
        "tracking_weight": DEFAULT_TRACKING_WEIGHT,
        "energy_weight": DEFAULT_ENERGY_WEIGHT,
        "energy_price_weight": DEFAULT_ENERGY_PRICE_WEIGHT,
        "smoothing_weight": DEFAULT_SMOOTHING_WEIGHT,
        "soft_constraint_weight": DEFAULT_SOFT_CONSTRAINT_WEIGHT,
        "soft_constraint_linear_weight": DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
        "terminal_weight": DEFAULT_TERMINAL_WEIGHT,
        "comfort_offset": 2.0,
        "rooms": [
            {
                "name": ROOM,
                "setpoint": 23.5,
                "comfort_offset": 2.0,
                "temperature": 25.2,
                "solar_exposure": "high",
                "solar_facing": 180.0,
            }
        ],
        "heat_sources": [
            {
                "name": "hp",
                "type": "heat_pump",
                "room": ROOM,
                "max_power": 7000.0,
                "hvac_mode": "heat_cool",
            }
        ],
    }


def peaked_prices(n_fast: int, dt_s: float, *, hour0: float) -> list[float]:
    """Two-peak tariff (~0.5 to 2.6), shaped like the live Price Forecast."""
    hours = hour0 + (np.arange(n_fast, dtype=float) + 0.5) * (dt_s / 3600.0)
    hours = hours % 24.0
    base = 0.85
    p1 = 1.55 * np.exp(-0.5 * ((hours - 8.0) / 2.0) ** 2)
    p2 = 1.35 * np.exp(-0.5 * ((hours - 20.0) / 2.5) ** 2)
    night = 0.45 * np.exp(-0.5 * ((hours - 15.5) / 1.8) ** 2)
    return (base + p1 + p2 - night).tolist()


def outdoor_curve(n: int, dt_s: float, *, t0: datetime) -> list[float]:
    vals = []
    for i in range(n):
        t = t0 + timedelta(seconds=dt_s * i)
        hour = t.hour + t.minute / 60.0
        vals.append(18.5 + 5.5 * math.sin((hour - 9.0) / 24.0 * 2.0 * math.pi))
    return vals


def _preview_overrides() -> dict[str, float]:
    """Same keys the Tuning page sends via collectMpcParams()."""
    return {
        "comfort_offset": 2.0,
        "tracking_weight": DEFAULT_TRACKING_WEIGHT,
        "energy_weight": DEFAULT_ENERGY_WEIGHT,
        "energy_price_weight": DEFAULT_ENERGY_PRICE_WEIGHT,
        "smoothing_weight": DEFAULT_SMOOTHING_WEIGHT,
        "soft_constraint_weight": DEFAULT_SOFT_CONSTRAINT_WEIGHT,
        "soft_constraint_linear_weight": DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
        "terminal_weight": DEFAULT_TERMINAL_WEIGHT,
        "nmpc_period": DEFAULT_NMPC_PERIOD,
        "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
        "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
    }


def _ha_history(points: list[tuple[datetime, float]]) -> list[dict[str, Any]]:
    out = []
    for ts, val in points:
        out.append({"s": f"{val:.4f}", "lu": ts.timestamp()})
    return out


def _series(rows: list[dict[str, Any]], field: str) -> np.ndarray:
    vals = []
    for step in rows:
        v = step.get(field)
        if v is None:
            continue
        vals.append(float(v))
    return np.array(vals, dtype=float)


def _payload_metrics(room_payload: dict[str, Any], preview_payload: dict[str, Any]) -> dict[str, Any]:
    room_fc = room_payload["rooms"][SLUG]["forecast"]
    prev_fc = preview_payload["rooms"][SLUG]["forecast"]
    t_room = _series(room_fc, "temperature")
    t_prev = _series(prev_fc, "temperature")
    p_room = _series(room_fc, "heating_power")
    p_prev = _series(prev_fc, "heating_power")
    n = int(min(t_room.size, t_prev.size, p_room.size, p_prev.size))
    dT = t_room[:n] - t_prev[:n]
    dP = p_room[:n] - p_prev[:n]
    return {
        "n": n,
        "max_abs_dT_K": float(np.max(np.abs(dT))) if n else 0.0,
        "rms_dT_K": float(np.sqrt(np.mean(dT * dT))) if n else 0.0,
        "max_abs_dP_W": float(np.max(np.abs(dP))) if n else 0.0,
        "t_room_min": float(np.min(t_room)) if t_room.size else None,
        "t_room_max": float(np.max(t_room)) if t_room.size else None,
        "t_preview_min": float(np.min(t_prev)) if t_prev.size else None,
        "t_preview_max": float(np.max(t_prev)) if t_prev.size else None,
        "p_room_min_W": float(np.min(p_room)) if p_room.size else None,
        "p_room_max_W": float(np.max(p_room)) if p_room.size else None,
        "p_preview_min_W": float(np.min(p_prev)) if p_prev.size else None,
        "p_preview_max_W": float(np.max(p_prev)) if p_prev.size else None,
        "t_room_head": t_room[:8].tolist(),
        "t_preview_head": t_prev[:8].tolist(),
        "p_room_head_W": p_room[:8].tolist(),
        "p_preview_head_W": p_prev[:8].tolist(),
    }


def _source_power(engine: ControlEngine) -> float:
    ctrl = engine._controller
    if ctrl is None:
        return 0.0
    for src in getattr(ctrl, "_sources", []):
        val = getattr(src, "_current_power", None)
        if val is not None:
            return float(val)
    return 0.0


def _build_payload(
    engine: ControlEngine,
    snapshot: dict[str, Any],
    *,
    outdoor: float,
    price: float,
    now: datetime,
) -> dict[str, Any]:
    rooms = [
        {
            "name": ROOM,
            "setpoint": 23.5,
            "comfort_offset": 2.0,
            "enabled": True,
        }
    ]
    return build_app_forecast_payload(
        rooms=rooms,
        room_temperatures={ROOM: float(engine.model.rooms[ROOM].temperature)},
        outdoor_temp=outdoor,
        energy_price=price,
        snapshot=snapshot,
        now=now,
        room_power_meta=engine.room_power_meta(outdoor),
    )


def run(*, ticks_into_plan: int = TICKS_INTO_PLAN) -> dict[str, Any]:
    wall_now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    engine = ControlEngine(_config())
    ctrl = engine._controller
    assert ctrl is not None
    dt_s = float(ctrl._dt)
    n_fast = int(ctrl.horizon)
    epoch_dt = wall_now - timedelta(seconds=dt_s * ticks_into_plan)
    epoch = epoch_dt.timestamp()
    hist_start = wall_now - timedelta(hours=HISTORY_HOURS)

    n_hist = int(round(HISTORY_HOURS * 3600.0 / dt_s))
    n_out = n_hist + n_fast + 8
    outdoor_all = outdoor_curve(n_out, dt_s, t0=hist_start)
    prices_all = peaked_prices(n_out, dt_s, hour0=hist_start.hour + hist_start.minute / 60.0)

    t_meas_hist: list[tuple[datetime, float]] = []
    t_filt_hist: list[tuple[datetime, float]] = []
    p_meas_hist: list[tuple[datetime, float]] = []
    outdoor_hist: list[tuple[datetime, float]] = []
    price_hist: list[tuple[datetime, float]] = []
    sp_hist: list[tuple[datetime, float]] = []

    t_meas = 25.2
    # Pre-plan history: EKF + P with no accepted U* (U=0 remaining).
    for i in range(n_hist - ticks_into_plan):
        ts = hist_start + timedelta(seconds=dt_s * i)
        # Cool from the morning peak toward the band, like the live trace.
        hour = ts.hour + ts.minute / 60.0
        t_meas = 24.6 - 1.6 * max(0.0, math.sin((hour - 10.0) / 24.0 * 2.0 * math.pi))
        t_meas = 0.92 * t_meas + 0.08 * 23.4
        od = outdoor_all[i]
        pr = prices_all[i]
        engine.compute_actions(
            {ROOM: t_meas},
            od,
            {ROOM: 23.5},
            now=ts,
            outdoor_forecast=outdoor_all[i : i + n_fast],
            price_forecast=prices_all[i : i + n_fast],
        )
        filt = float(ctrl.filtered_temperatures[ROOM])
        t_meas_hist.append((ts, t_meas))
        t_filt_hist.append((ts, filt))
        p_meas_hist.append((ts, _source_power(engine)))
        outdoor_hist.append((ts, od))
        price_hist.append((ts, pr))
        sp_hist.append((ts, 23.5))

    # First compute at the plan origin, then the slow NLP.
    engine.compute_actions(
        {ROOM: t_meas},
        outdoor_all[n_hist - ticks_into_plan],
        {ROOM: 23.5},
        now=epoch_dt,
        outdoor_forecast=outdoor_all[n_hist - ticks_into_plan : n_hist - ticks_into_plan + n_fast],
        price_forecast=prices_all[n_hist - ticks_into_plan : n_hist - ticks_into_plan + n_fast],
    )
    engine.mark_nmpc_busy()
    t_solve0 = time.time()
    plan = engine.solve_nmpc_blocking()
    solve_s = time.time() - t_solve0
    accepted = bool(plan.get("accepted"))
    applied = False
    if accepted:
        applied = bool(
            engine.apply_nmpc_result(plan, plan_epoch=epoch, now=epoch)
        )
    if not accepted or not applied:
        raise RuntimeError(
            f"live NMPC did not install a plan accepted={accepted} applied={applied} "
            f"fun={plan.get('fun')}"
        )

    u_star = np.asarray(plan["u_star"], dtype=float).reshape(-1)
    snap_apply = engine.forecast_snapshot()

    # Fast ticks up to wall_now, matching runtime: sync k from the plan origin,
    # then EKF + P + remaining-U* publish.
    for k in range(1, ticks_into_plan + 1):
        ts = epoch_dt + timedelta(seconds=dt_s * k)
        idx = n_hist - ticks_into_plan + k
        hour = ts.hour + ts.minute / 60.0
        t_meas = 23.7 + 0.35 * math.sin((hour - 12.0) / 24.0 * 2.0 * math.pi)
        od = outdoor_all[idx]
        pr = prices_all[idx]
        ctrl.sync_fast_index(ts.timestamp(), fallback_epoch=epoch)
        engine.compute_actions(
            {ROOM: t_meas},
            od,
            {ROOM: 23.5},
            now=ts,
            outdoor_forecast=outdoor_all[idx : idx + n_fast],
            price_forecast=prices_all[idx : idx + n_fast],
        )
        filt = float(ctrl.filtered_temperatures[ROOM])
        t_meas_hist.append((ts, t_meas))
        t_filt_hist.append((ts, filt))
        p_meas_hist.append((ts, _source_power(engine)))
        outdoor_hist.append((ts, od))
        price_hist.append((ts, pr))
        sp_hist.append((ts, 23.5))

    snap_room = engine.forecast_snapshot()
    od_now = outdoor_all[n_hist]
    pr_now = prices_all[n_hist]
    room_payload = _build_payload(
        engine, snap_room, outdoor=od_now, price=pr_now, now=wall_now
    )

    preview = engine.preview_tuning_forecast(
        _preview_overrides(),
        {ROOM: t_meas},
        od_now,
        {ROOM: 23.5},
        outdoor_forecast=outdoor_all[n_hist : n_hist + n_fast],
        price_forecast=prices_all[n_hist : n_hist + n_fast],
        now=wall_now,
    )
    if preview.get("error"):
        raise RuntimeError(f"preview failed: {preview.get('error')}")
    preview_payload = _build_payload(
        engine, preview, outdoor=od_now, price=pr_now, now=wall_now
    )

    metrics = _payload_metrics(room_payload, preview_payload)
    U_rem = ctrl._forecast_U(n_fast)
    pack = {
        "now": wall_now.isoformat(),
        "now_ms": int(wall_now.timestamp() * 1000),
        "window_start_ms": int(hist_start.timestamp() * 1000),
        "dt_s": dt_s,
        "n_fast": n_fast,
        "ticks_into_plan": ticks_into_plan,
        "nmpc_k": int(ctrl._nmpc_k),
        "solve_s": solve_s,
        "fun": float(plan.get("fun", float("nan"))),
        "u_star": [float(v) for v in u_star],
        "u_remaining_head": [float(v) for v in U_rem[:8, 0]],
        "max_abs_U_remaining": float(np.max(np.abs(U_rem))),
        "filtered_now": float(ctrl.filtered_temperatures[ROOM]),
        "measured_now": float(t_meas),
        "metrics": metrics,
        "room": room_payload,
        "preview": preview_payload,
        "history": {
            "filtered": _ha_history(t_filt_hist),
            "measured": _ha_history(t_meas_hist),
            "power": _ha_history(p_meas_hist),
            "outdoor": _ha_history(outdoor_hist),
            "price": _ha_history(price_hist),
            "setpoint": _ha_history(sp_hist),
        },
        "apply_t_head": [
            float(step[ROOM]) for step in snap_apply.get("predictions", [])[:8]
        ],
        "room_t_head": [
            float(step[ROOM]) for step in snap_room.get("predictions", [])[:8]
        ],
        "preview_t_head": [
            float(step[ROOM]) for step in preview.get("predictions", [])[:8]
        ],
    }
    return pack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="01")
    parser.add_argument("--ticks", type=int, default=TICKS_INTO_PLAN)
    args = parser.parse_args()
    INSPECT.mkdir(parents=True, exist_ok=True)
    pack = run(ticks_into_plan=int(args.ticks))
    payload_path = INSPECT / "payloads.json"
    metrics_path = INSPECT / f"{args.tag}_metrics.json"
    payload_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    metrics_path.write_text(
        json.dumps(
            {
                "tag": args.tag,
                "now": pack["now"],
                "nmpc_k": pack["nmpc_k"],
                "solve_s": pack["solve_s"],
                "fun": pack["fun"],
                "max_abs_U_remaining": pack["max_abs_U_remaining"],
                "u_star": pack["u_star"],
                "u_remaining_head": pack["u_remaining_head"],
                "metrics": pack["metrics"],
                "apply_t_head": pack["apply_t_head"],
                "room_t_head": pack["room_t_head"],
                "preview_t_head": pack["preview_t_head"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    m = pack["metrics"]
    print(
        f"tag={args.tag} k={pack['nmpc_k']} solve={pack['solve_s']:.1f}s "
        f"max|dT|={m['max_abs_dT_K']:.3f}K rms|dT|={m['rms_dT_K']:.3f}K "
        f"max|dP|={m['max_abs_dP_W']:.1f}W Urem={pack['max_abs_U_remaining']:.3f}"
    )
    print(f"wrote {payload_path}")
    print(f"wrote {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
