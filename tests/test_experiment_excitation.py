"""Tests for the coordinator's experiment → MPC input-clamp wiring.

An active experiment is applied by clamping the room's heater inputs over the
MPC horizon (``_build_experiment_clamps``) rather than overriding the chosen
action afterwards.  These build a partially-initialised coordinator (the same
pattern used by ``test_window_override``) and exercise the clamp builder so the
right rooms are clamped, the settle buffer releases, safety is applied at the
applied step, and open windows are respected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.experiments import Experiment, ExperimentManager


def _slug(name):
    from custom_components.heating_assistant.dashboard import slugify
    return slugify(name)


def _make_coord(rooms, sources, horizon=4, interval_s=3600):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = SimpleNamespace()
    coord.model = SimpleNamespace(room_names=list(rooms))
    coord.heat_sources = sources
    coord.measured_temperatures = {}
    coord.actions = {}
    coord.experiment_manager = ExperimentManager()
    coord.experiment_store = None
    coord.dataset_store = None
    coord._experiment_active_rooms = set()
    coord._window_state = {r: "closed" for r in rooms}
    coord._window_sensors = {}
    coord._horizon = horizon
    coord._update_interval_s = interval_s
    coord.controller = MagicMock()
    return coord


def _src(name, room):
    return SimpleNamespace(name=name, room=room, u_min=0.0, u_max=1.0)


def _exp(room, start, end, **kw):
    return Experiment(
        room_name=room, room_slug=_slug(room),
        start_ts=start, end_ts=end,
        signal_type=kw.get("signal_type", "step"),
        amplitude_high=kw.get("amplitude_high", 1.0),
        amplitude_low=kw.get("amplitude_low", 0.0),
        period_s=kw.get("period_s", 3600.0),
        settle_s=kw.get("settle_s", 0.0),
        min_temp=kw.get("min_temp", 12.0),
        max_temp=kw.get("max_temp", 26.0),
        auto_save=kw.get("auto_save", False),
        seed=kw.get("seed", 1),
    )


def test_clamp_built_for_active_room_only():
    coord = _make_coord(
        ["Living Room", "Kitchen"],
        [_src("lr_heater", "Living Room"), _src("k_heater", "Kitchen")],
    )
    coord.measured_temperatures = {"Living Room": 20.0, "Kitchen": 20.0}
    coord.experiment_manager.add(
        _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    )
    now = datetime.fromtimestamp(1_000.0 + 1800, tz=timezone.utc)

    clamps = coord._build_experiment_clamps(now)

    assert "Living Room" in clamps
    assert "Kitchen" not in clamps
    arr = clamps["Living Room"]
    assert arr.shape == (4,)
    # A step holds the high level across the whole (in-window) horizon.
    assert np.allclose(arr, 1.0)
    assert coord.is_experiment_active("Living Room") is True
    assert coord.is_experiment_active("Kitchen") is False


def test_clamp_safety_ceiling_forces_off_at_applied_step():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 27.0}  # above the 26° ceiling
    coord.experiment_manager.add(
        _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    )
    now = datetime.fromtimestamp(1_000.0 + 1800, tz=timezone.utc)

    arr = coord._build_experiment_clamps(now)["Living Room"]
    # The applied step is forced off for safety; future steps use the raw
    # signal (their temperatures are only known to the MPC's own rollout).
    assert arr[0] == 0.0
    assert arr[1] == 1.0


def test_window_override_excludes_room_from_clamps():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 20.0}
    coord._window_state["Living Room"] = "open"
    coord.experiment_manager.add(
        _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    )
    now = datetime.fromtimestamp(1_000.0 + 1800, tz=timezone.utc)

    clamps = coord._build_experiment_clamps(now)
    assert "Living Room" not in clamps
    assert coord.is_experiment_active("Living Room") is False


def test_no_active_experiment_returns_empty():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    # Experiment starts well beyond the horizon end.
    coord.experiment_manager.add(
        _exp("Living Room", 1_000.0 + 100 * 3600, 1_000.0 + 110 * 3600)
    )
    now = datetime.fromtimestamp(1_000.0, tz=timezone.utc)

    clamps = coord._build_experiment_clamps(now)
    assert clamps == {}
    assert coord._experiment_active_rooms == set()


def test_settle_buffer_releases_tail_within_horizon():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 20.0}
    # 4 h window with a 2 h settle buffer → excitation ends at +2 h.
    coord.experiment_manager.add(
        _exp("Living Room", 1_000.0, 1_000.0 + 4 * 3600,
             signal_type="step", settle_s=2 * 3600)
    )
    # Sample 1 h in; horizon steps land at +1, +2, +3, +4 h from start.
    now = datetime.fromtimestamp(1_000.0 + 1 * 3600, tz=timezone.utc)

    arr = coord._build_experiment_clamps(now)["Living Room"]
    assert arr[0] == 1.0          # still exciting
    assert arr[1] == 0.0          # settle buffer → released to low
    assert arr[2] == 0.0          # settle buffer
    assert np.isnan(arr[3])       # at/after window end → unclamped (MPC free)
