from datetime import datetime, timezone
from custom_components.heating_assistant.history_seed import (
    build_records_from_history, _samples_from_states, _earliest_usable_ts,
)

class S:
    """Minimal stand-in for a recorder State."""
    def __init__(self, state, ts):
        self.state = state
        self.last_updated = datetime.fromtimestamp(ts, tz=timezone.utc)
        self.last_changed = self.last_updated

IDS = {
    "room_names": ["living"], "source_names": ["heater"],
    "temp": ["t_living"], "solar": ["s_living"],
    "control": ["c_heater"], "outdoor": "outdoor",
}

def test_samples_filters_non_numeric():
    states = [S("20.0", 0), S("unavailable", 900), S("unknown", 1800),
              S("21.5", 2700), S("", 3600)]
    samples = _samples_from_states(states)
    assert samples == [(0.0, 20.0), (2700.0, 21.5)]

def test_build_records_basic():
    history = {
        "t_living": [S("20.0", 0), S("21.0", 1800)],
        "outdoor":  [S("5.0", 0)],
        "c_heater": [S("50", 0)],                 # 50% -> 0.5
        "s_living": [S("100", 0)],
    }
    grid = [0.0, 900.0, 1800.0]
    recs = build_records_from_history(history, IDS, grid)
    assert len(recs) == 3
    assert recs[0] == {
        "y": [20.0], "u": [0.5], "d_outdoor": 5.0,
        "d_solar": {"living": 100.0}, "timestamp": 0.0,
    }
    assert recs[1]["y"] == [20.0]          # ZOH holds 20 until t=1800
    assert recs[2]["y"] == [21.0]
    assert all(r["u"] == [0.5] for r in recs)

def test_build_records_skips_before_data():
    history = {
        "t_living": [S("20.0", 1800)],        # temp only from t=1800
        "outdoor":  [S("5.0", 0)],
        "c_heater": [], "s_living": [],
    }
    grid = [0.0, 900.0, 1800.0, 2700.0]
    recs = build_records_from_history(history, IDS, grid)
    # First two grid points have no temperature yet -> skipped.
    assert [r["timestamp"] for r in recs] == [1800.0, 2700.0]
    # Missing control/solar default to 0.
    assert recs[0]["u"] == [0.0]
    assert recs[0]["d_solar"] == {"living": 0.0}

def test_build_records_skips_when_outdoor_missing():
    history = {"t_living": [S("20.0", 0)], "outdoor": [],
               "c_heater": [], "s_living": []}
    recs = build_records_from_history(history, IDS, [0.0, 900.0])
    assert recs == []

def test_earliest_usable_ts():
    history = {"t_living": [S("20.0", 500)], "outdoor": [S("5.0", 1200)]}
    # earliest instant both essentials exist = max(500, 1200) = 1200
    assert _earliest_usable_ts(history, IDS) == 1200.0
    # missing essential -> None
    assert _earliest_usable_ts({"t_living": [], "outdoor": [S("5",0)]}, IDS) is None

def test_multi_room_ordering():
    ids = {
        "room_names": ["a", "b"], "source_names": ["h1", "h2"],
        "temp": ["t_a", "t_b"], "solar": ["s_a", "s_b"],
        "control": ["c1", "c2"], "outdoor": "out",
    }
    history = {
        "t_a": [S("20", 0)], "t_b": [S("18", 0)], "out": [S("3", 0)],
        "c1": [S("100", 0)], "c2": [S("0", 0)],
        "s_a": [S("200", 0)], "s_b": [S("50", 0)],
    }
    recs = build_records_from_history(history, ids, [0.0])
    assert recs[0]["y"] == [20.0, 18.0]
    assert recs[0]["u"] == [1.0, 0.0]
    assert recs[0]["d_solar"] == {"a": 200.0, "b": 50.0}
