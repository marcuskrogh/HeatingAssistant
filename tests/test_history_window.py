"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import numpy as np

from heatingassistant.engine.history_window import (
    DEFAULT_MAX_GAP_FACTOR,
    history_time_range,
    prune_stale_records,
    select_leading_window,
    select_recent_window,
    select_window_by_timestamps,
    split_contiguous_runs,
)


def _regular(n, dt=900.0, t0=1_000_000.0):
    return [{"timestamp": t0 + dt * i} for i in range(n)]


# ---------------------------------------------------------------------------
# select_recent_window — basics
# ---------------------------------------------------------------------------

def test_empty_and_singleton():
    assert select_recent_window([], 3600.0) == []
    one = [{"timestamp": 5.0}]
    assert select_recent_window(one, 3600.0) == one


def test_non_positive_horizon_returns_all():
    h = _regular(50)
    assert select_recent_window(h, 0.0) == h
    assert select_recent_window(h, -1.0) == h


def test_regular_data_is_exactly_wall_clock():
    dt = 900.0
    h = _regular(500, dt=dt)                         # ~5.2 days
    w = select_recent_window(h, 24 * 3600.0)         # last 24 h
    span = (w[-1]["timestamp"] - w[0]["timestamp"]) / 3600.0
    assert span <= 24.0
    # 24 h at 15-min spacing is 96 intervals → 96 or 97 inclusive samples.
    assert 96 <= len(w) <= 97
    assert w[-1] is h[-1]


def test_short_horizon():
    dt = 900.0
    h = _regular(200, dt=dt)
    w = select_recent_window(h, 0.5 * 3600.0)        # 30 min
    assert 2 <= len(w) <= 3


# ---------------------------------------------------------------------------
# Short restart gap is bridged; large epoch gap is excluded
# ---------------------------------------------------------------------------

def test_short_restart_gap_is_bridged():
    """A few-minutes restart (one or two missed samples) must keep data on both
    sides of the gap as long as it falls within the horizon."""
    dt = 900.0
    pre = _regular(80, dt=dt)                          # 20 h of data
    gap_start = pre[-1]["timestamp"] + 30 * 60.0       # 30-min interruption
    post = [{"timestamp": gap_start + dt * i} for i in range(8)]  # 2 h after
    history = pre + post

    w = select_recent_window(history, 24 * 3600.0)     # 24 h covers both sides
    pre_count = len([h for h in w if h["timestamp"] <= pre[-1]["timestamp"]])
    post_count = len([h for h in w if h["timestamp"] >= gap_start])
    assert pre_count >= 5 and post_count == 8, (
        f"restart not bridged: {pre_count} pre / {post_count} post"
    )


def test_large_epoch_gap_is_excluded_by_horizon():
    """The user scenario: a previous session weeks ago shares the buffer with a
    dense recent session.  A 24 h request must stay within the recent session."""
    dt = 900.0
    old = [{"timestamp": 1_000_000.0 + 3600.0 * i} for i in range(30)]
    recent_t0 = old[-1]["timestamp"] + 21 * 24 * 3600.0
    recent = [{"timestamp": recent_t0 + dt * i} for i in range(192)]  # 48 h
    history = old + recent

    w = select_recent_window(history, 24 * 3600.0)
    assert all(h["timestamp"] >= recent_t0 for h in w), "epoch leak into old data"
    span_hours = (w[-1]["timestamp"] - w[0]["timestamp"]) / 3600.0
    assert span_hours <= 24.0, f"window spans {span_hours:.1f} h"
    assert 96 <= len(w) <= 97


def test_sparse_uniform_data_does_not_overspan():
    """Even if recording were uniformly sparse (large but regular intervals), a
    24 h window must still span at most 24 h — never weeks."""
    dt = 4 * 3600.0  # pathological: one sample every 4 h
    h = _regular(200, dt=dt)
    w = select_recent_window(h, 24 * 3600.0)
    span = (w[-1]["timestamp"] - w[0]["timestamp"]) / 3600.0
    assert span <= 24.0


def test_non_monotonic_timestamps_are_normalised():
    dt = 900.0
    h = _regular(50, dt=dt)
    shuffled = [h[i] for i in [3, 1, 0, 2]] + h[4:]
    shuffled.append(dict(h[10]))                       # duplicate timestamp
    w = select_recent_window(shuffled, 24 * 3600.0)
    times = [r["timestamp"] for r in w]
    assert times == sorted(times)
    assert len(times) == len(set(times))


# ---------------------------------------------------------------------------
# split_contiguous_runs
# ---------------------------------------------------------------------------

def test_split_no_gap_single_run():
    runs = split_contiguous_runs(_regular(50), 900.0)
    assert len(runs) == 1 and len(runs[0]) == 50


def test_split_at_gap():
    dt = 900.0
    a = _regular(20, dt=dt)
    b = [{"timestamp": a[-1]["timestamp"] + 5 * 3600.0 + dt * i} for i in range(15)]
    runs = split_contiguous_runs(a + b, dt)
    assert [len(r) for r in runs] == [20, 15]


# ---------------------------------------------------------------------------
# prune_stale_records
# ---------------------------------------------------------------------------

def test_prune_drops_old_keeps_recent():
    now = 10_000_000.0
    recs = [
        {"timestamp": now - 40 * 24 * 3600.0},
        {"timestamp": now - 3 * 24 * 3600.0},
        {"timestamp": now - 3600.0},
    ]
    kept = prune_stale_records(recs, now, max_age_seconds=5 * 24 * 3600.0)
    assert len(kept) == 2


def test_prune_non_positive_age_is_noop():
    recs = _regular(5)
    assert prune_stale_records(recs, 0.0, 0.0) == recs


def test_gap_factor_constant_sane():
    assert DEFAULT_MAX_GAP_FACTOR > 1.0


# ---------------------------------------------------------------------------
# select_window_by_timestamps
# ---------------------------------------------------------------------------

def test_select_by_timestamps_basic():
    """Records exactly within [start, end] are returned."""
    dt = 900.0
    h = _regular(200, dt=dt)                    # 200 steps at 15-min intervals
    t0 = h[0]["timestamp"]
    start = t0 + 24 * 3600.0                    # 24 h in
    end   = t0 + 36 * 3600.0                    # 36 h in

    w = select_window_by_timestamps(h, start, end)
    assert len(w) > 0
    assert all(start <= float(r["timestamp"]) <= end for r in w)
    span_h = (w[-1]["timestamp"] - w[0]["timestamp"]) / 3600.0
    assert abs(span_h - 12.0) < 0.25


def test_select_by_timestamps_inverted_returns_empty():
    """start > end must return an empty list (not raise)."""
    h = _regular(50)
    t0 = h[0]["timestamp"]
    assert select_window_by_timestamps(h, t0 + 3600.0, t0) == []


def test_select_by_timestamps_whole_range():
    """Selecting [min_ts, max_ts] must return the full history."""
    h = _regular(50)
    t0 = h[0]["timestamp"]
    t_end = h[-1]["timestamp"]
    w = select_window_by_timestamps(h, t0, t_end)
    assert len(w) == 50


def test_select_by_timestamps_outside_range_returns_empty():
    """Requesting a window entirely outside the history returns nothing."""
    h = _regular(20)
    future_start = h[-1]["timestamp"] + 100_000.0
    assert select_window_by_timestamps(h, future_start, future_start + 3600.0) == []


def test_select_by_timestamps_normalises_order():
    """Shuffled history is normalised before slicing."""
    dt = 900.0
    h = _regular(100, dt=dt)
    t0 = h[0]["timestamp"]
    shuffled = h[50:] + h[:50]                  # out of order

    start = t0 + 60 * dt
    end   = t0 + 80 * dt
    w = select_window_by_timestamps(shuffled, start, end)
    times = [r["timestamp"] for r in w]
    assert times == sorted(times)
    assert all(start <= t <= end for t in times)


# ---------------------------------------------------------------------------
# history_time_range
# ---------------------------------------------------------------------------

def test_history_time_range_basic():
    h = _regular(50)
    min_ts, max_ts = history_time_range(h)
    assert min_ts == h[0]["timestamp"]
    assert max_ts == h[-1]["timestamp"]


def test_history_time_range_empty():
    assert history_time_range([]) == (None, None)


# ---------------------------------------------------------------------------
# sysid window_spec (explicit timestamp range)
# ---------------------------------------------------------------------------

def test_sysid_window_spec_selects_correct_range():
    """run_sysid_ekf with window_spec selects only data in the given range."""
    from heatingassistant.engine.sysid import run_sysid_ekf

    dt = 900.0
    model, heater, _ = _make_system(dt)
    history, recent_t0 = _scenario_history(dt)

    # Select a 6-hour slice from the middle of the recent data
    mid = recent_t0 + 24 * 3600.0
    win_start = mid
    win_end   = mid + 6 * 3600.0

    res = run_sysid_ekf(
        history, model, [heater], ["studio"], dt,
        horizon_steps=96,            # ignored — window_spec takes priority
        room_params={}, sigma_w=0.1, sigma_v=0.5,
        window_spec=(win_start, win_end),
    )

    assert "error" not in res, res.get("error")
    sim = res["per_room"]["studio"]["simulation"]
    assert sim
    for entry in sim:
        assert win_start - dt <= entry["time"] <= win_end + dt, (
            f"entry at {entry['time']} outside [{win_start}, {win_end}]"
        )
    span_h = (sim[-1]["time"] - sim[0]["time"]) / 3600.0
    assert span_h <= 6.5, f"window_spec reconstruction spans {span_h:.1f} h"


def test_sysid_window_spec_invalid_returns_error():
    """window_spec with start > end returns an error, not a crash."""
    from heatingassistant.engine.sysid import run_sysid_ekf

    dt = 900.0
    model, heater, _ = _make_system(dt)
    history, _ = _scenario_history(dt)

    res = run_sysid_ekf(
        history, model, [heater], ["studio"], dt,
        horizon_steps=96,
        room_params={}, sigma_w=0.1, sigma_v=0.5,
        window_spec=(1e12, 1.0),  # wildly out-of-range
    )
    assert "error" in res


# ---------------------------------------------------------------------------
# End-to-end: EKF reconstruction and open-loop agree and stay within 24 h
# ---------------------------------------------------------------------------

def _make_system(dt):
    from heatingassistant.engine.controller import HouseThermalSDE
    from heatingassistant.engine.thermal_model import HouseModel, Room
    from heatingassistant.engine.heat_sources import ElectricHeater

    room = Room("studio", 4e6, 0.05, temperature=20.0, setpoint=21.0)
    model = HouseModel([room])
    heater = ElectricHeater("h", "studio", 3000.0)
    return model, heater, HouseThermalSDE(model, [heater], dt)


def _scenario_history(dt):
    """Old 3-week-ago session + dense recent 48 h of 15-min data."""
    history = []
    t = 1_000_000.0
    for i in range(30):
        history.append({"y": [19.0], "u": [0.2], "d_outdoor": 4.0,
                        "d_solar": {"studio": 0.0}, "timestamp": t + 3600.0 * i})
    t = history[-1]["timestamp"] + 21 * 24 * 3600.0
    for i in range(192):
        history.append({"y": [20.0 + 0.01 * (i % 20)], "u": [0.3], "d_outdoor": 5.0,
                        "d_solar": {"studio": 50.0}, "timestamp": t + dt * i})
    return history, t


def test_ekf_reconstruction_stays_in_recent_24h():
    from heatingassistant.engine.sysid import run_sysid_ekf

    dt = 900.0
    model, heater, _ = _make_system(dt)
    history, recent_t0 = _scenario_history(dt)

    horizon_steps = int(24 * 3600.0 / dt)
    res = run_sysid_ekf(history, model, [heater], ["studio"], dt,
                        horizon_steps=horizon_steps, room_params={},
                        sigma_w=0.1, sigma_v=0.5)
    sim = res["per_room"]["studio"]["simulation"]
    assert sim
    assert all(s["time"] >= recent_t0 for s in sim)
    span = (sim[-1]["time"] - sim[0]["time"]) / 3600.0
    assert span <= 24.0, f"reconstruction spans {span:.1f} h"


def test_open_loop_stays_in_recent_24h_and_keeps_latest():
    from custom_components.heating_assistant.model_diagnostics import (
        compute_open_loop_predictions,
    )

    dt = 900.0
    _, _, system = _make_system(dt)
    history, recent_t0 = _scenario_history(dt)

    window = select_recent_window(history, 24 * 3600.0)
    res = compute_open_loop_predictions(history=window, system=system,
                                        room_names=["studio"], n_rooms=1, dt=dt,
                                        segment_length=30)
    sim = res["per_room"]["studio"]["simulation"]
    assert sim
    assert all(s["time"] >= recent_t0 for s in sim)
    span = (sim[-1]["time"] - sim[0]["time"]) / 3600.0
    assert span <= 24.0, f"open-loop spans {span:.1f} h"
    # The latest sample must be represented (within one sample of the window end).
    assert window[-1]["timestamp"] - sim[-1]["time"] <= dt + 1.0


# ---------------------------------------------------------------------------
# select_leading_window
# ---------------------------------------------------------------------------

def test_select_leading_window_empty_when_no_data():
    assert select_leading_window([], 1000.0, 3600.0) == []


def test_select_leading_window_respects_bounds():
    hist = [
        {"timestamp": 1000.0, "y": [20.0]},
        {"timestamp": 4600.0, "y": [20.5]},
        {"timestamp": 8200.0, "y": [21.0]},
    ]
    leading = select_leading_window(hist, 8200.0, 3600.0)
    assert len(leading) == 1
    assert leading[0]["timestamp"] == 4600.0
