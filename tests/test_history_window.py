"""
Tests for active-time history-window selection across restart gaps.

The identification diagnostics (EKF reconstruction and open-loop simulation)
must keep working across short system restarts: a gap in the history buffer
should be bridged by the continuous-time model rather than truncating the
usable data to whatever was recorded *after* the most recent restart.
"""

import numpy as np

from custom_components.heating_assistant.history_window import (
    DEFAULT_MAX_GAP_FACTOR,
    select_recent_window,
)


def _regular(n, dt=900.0, t0=1000.0):
    return [{"timestamp": t0 + dt * i} for i in range(n)]


# ---------------------------------------------------------------------------
# select_recent_window
# ---------------------------------------------------------------------------

def test_empty_and_singleton():
    assert select_recent_window([], 3600.0, 900.0) == []
    one = [{"timestamp": 5.0}]
    assert select_recent_window(one, 3600.0, 900.0) == one


def test_non_positive_horizon_returns_all():
    h = _regular(50)
    assert select_recent_window(h, 0.0, 900.0) == h
    assert select_recent_window(h, -1.0, 900.0) == h


def test_regular_data_matches_wall_clock():
    """With no gaps the active-time window equals the wall-clock window:
    ~horizon/dt steps (inclusive of the anchor sample)."""
    dt = 900.0
    h = _regular(200, dt=dt)
    w = select_recent_window(h, 6 * 3600.0, dt)   # 24 steps
    assert 24 <= len(w) <= 26
    assert w[-1] is h[-1]


def test_short_horizon_not_regressed():
    """A short horizon must still select only a few of the most recent steps
    (regression guard for the earlier short-horizon fix)."""
    dt = 900.0
    h = _regular(200, dt=dt)
    w = select_recent_window(h, 0.5 * 3600.0, dt)  # 2 steps
    assert 2 <= len(w) <= 4


def test_restart_gap_does_not_consume_budget():
    """A restart gap inside the requested horizon must NOT shrink the window to
    just the post-restart samples — the gap is bridged for ~one step's cost."""
    dt = 900.0
    pre = _regular(40, dt=dt)                       # 10 h before restart
    gap_start = pre[-1]["timestamp"] + 4 * 3600.0   # 4 h outage
    post = [{"timestamp": gap_start + dt * i} for i in range(8)]  # 2 h after
    history = pre + post

    # Naive wall-clock selection keeps only the couple of boundary samples
    # before the gap (the 4 h outage swallows the 6 h budget).
    cutoff = history[-1]["timestamp"] - 6 * 3600.0
    naive_pre = len([h for h in history
                     if h["timestamp"] >= cutoff and h["timestamp"] < gap_start])
    assert naive_pre <= 2

    # Active-time selection spans the gap and keeps plenty of pre-restart data.
    w = select_recent_window(history, 6 * 3600.0, dt)
    pre_count = len([h for h in w if h["timestamp"] < gap_start])
    assert pre_count >= 5, f"only {pre_count} pre-restart samples retained"
    assert pre_count > naive_pre


def test_small_interval_discrepancy_is_bridged():
    """Small sample-interval jitter (below the gap factor) is counted normally,
    so the window is not distorted by minor timing noise."""
    dt = 900.0
    rng = np.random.default_rng(0)
    ts = 1000.0
    history = []
    for _ in range(200):
        history.append({"timestamp": ts})
        ts += dt * (1.0 + 0.1 * rng.standard_normal())  # ±10 % jitter
    w = select_recent_window(history, 6 * 3600.0, dt)
    assert 22 <= len(w) <= 28


# ---------------------------------------------------------------------------
# End-to-end: EKF reconstruction spans a restart gap
# ---------------------------------------------------------------------------

def test_ekf_reconstruction_uses_pre_restart_data():
    from custom_components.heating_assistant.controller import HouseThermalSDE  # noqa: F401
    from custom_components.heating_assistant.thermal_model import HouseModel, Room
    from custom_components.heating_assistant.heat_sources import ElectricHeater
    from custom_components.heating_assistant.sysid import run_sysid_ekf

    dt = 900.0
    room = Room("studio", 4e6, 0.05, temperature=20.0, setpoint=21.0)
    model = HouseModel([room])
    heater = ElectricHeater("h", "studio", 3000.0)

    history = []
    ts = 1000.0
    for i in range(40):
        history.append({"y": [20.0 + 0.01 * i], "u": [0.3], "d_outdoor": 5.0,
                        "d_solar": {"studio": 50.0}, "timestamp": ts})
        ts += dt
    ts += 4 * 3600.0          # restart gap
    restart_ts = ts
    for i in range(8):
        history.append({"y": [21.0 + 0.01 * i], "u": [0.3], "d_outdoor": 5.0,
                        "d_solar": {"studio": 50.0}, "timestamp": ts})
        ts += dt

    horizon_steps = int(6 * 3600.0 / dt)   # 6 h horizon (< distance to restart)
    res = run_sysid_ekf(history, model, [heater], ["studio"], dt,
                        horizon_steps=horizon_steps, room_params={},
                        sigma_w=0.1, sigma_v=0.5)
    sim = res["per_room"]["studio"]["simulation"]
    pre = [s for s in sim if s["time"] < restart_ts]
    assert len(pre) >= 5, "reconstruction ignored pre-restart history"


def test_gap_factor_constant_sane():
    assert DEFAULT_MAX_GAP_FACTOR > 1.0
