"""Tests for the coordinator's experiment → MPC input-clamp wiring.

An active experiment is applied by clamping each source's heater input over the
MPC horizon (``_build_experiment_clamps``) rather than overriding the chosen
action afterwards.  These build a partially-initialised coordinator (the same
pattern used by ``test_window_override``) and exercise the clamp builder so the
right sources are clamped, the multi-phase step (including the cool phase for
reversible units) is emitted, safety is applied at the applied step, and open
windows are respected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from custom_components.heating_assistant import experiments as E
from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.experiments import Experiment, ExperimentManager
import pytest

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


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


def _src(name, room, u_min=0.0, u_max=1.0, can_cool=False):
    return SimpleNamespace(name=name, room=room, u_min=u_min, u_max=u_max, can_cool=can_cool)


def _exp(room, start, end, **kw):
    return Experiment(
        room_name=room, room_slug=_slug(room),
        start_ts=start, end_ts=end,
        signal_type=kw.get("signal_type", "step"),
        step_pct=kw.get("step_pct", 1.0),
        period_s=kw.get("period_s", 3600.0),
        settle_s=kw.get("settle_s", 0.0),
        min_temp=kw.get("min_temp", 12.0),
        max_temp=kw.get("max_temp", 26.0),
        auto_save=kw.get("auto_save", False),
        seed=kw.get("seed", 1),
    )


def test_clamp_built_for_active_source_only():
    coord = _make_coord(
        ["Living Room", "Kitchen"],
        [_src("lr_heater", "Living Room"), _src("k_heater", "Kitchen")],
    )
    coord.measured_temperatures = {"Living Room": 20.0, "Kitchen": 20.0}
    exp = _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    coord.experiment_manager.add(exp)
    now_ts = 1_000.0 + 1800
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)

    clamps = coord._build_experiment_clamps(now)

    assert "lr_heater" in clamps
    assert "k_heater" not in clamps
    arr = clamps["lr_heater"]
    assert arr.shape == (4,)
    # The clamp samples the heat-only step pattern over the horizon.
    expected = [E.excitation_fraction(exp, now_ts + k * 3600, can_cool=False) for k in range(4)]
    assert np.allclose(arr, expected)
    assert coord.is_experiment_active("Living Room") is True
    assert coord.is_experiment_active("Kitchen") is False


def test_clamp_emits_cool_phase_for_reversible_source():
    coord = _make_coord(
        ["Living Room"],
        [_src("hp", "Living Room", u_min=-1.0, u_max=1.0, can_cool=True)],
    )
    coord.measured_temperatures = {"Living Room": 20.0}
    # 10 h window, 5 phases of 2 h: [0, +0.5, 0, -0.5, 0]; sample 7 h in → cool.
    exp = _exp("Living Room", 1_000.0, 1_000.0 + 10 * 3600,
               signal_type="step", step_pct=0.5)
    coord.experiment_manager.add(exp)
    now = datetime.fromtimestamp(1_000.0 + 7 * 3600, tz=timezone.utc)

    arr = coord._build_experiment_clamps(now)["hp"]
    assert arr[0] == -0.5            # cool phase, within [u_min, u_max] = [-1, 1]
    assert arr[1] == 0.0             # settle phase
    assert np.isnan(arr[3])          # at window end → released


def test_clamp_safety_ceiling_forces_off_at_applied_step():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 27.0}  # above the 26° ceiling
    # Sample 3 h in so the applied step lands in the heat phase.
    exp = _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    coord.experiment_manager.add(exp)
    now = datetime.fromtimestamp(1_000.0 + 3 * 3600, tz=timezone.utc)

    arr = coord._build_experiment_clamps(now)["lr_heater"]
    assert arr[0] == 0.0


def test_window_override_excludes_source_from_clamps():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 20.0}
    coord._window_state["Living Room"] = "open"
    coord.experiment_manager.add(
        _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    )
    now = datetime.fromtimestamp(1_000.0 + 3 * 3600, tz=timezone.utc)

    clamps = coord._build_experiment_clamps(now)
    assert "lr_heater" not in clamps
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
    assert coord._experiment_horizon_steps == {}


def test_build_clamps_records_per_room_horizon_mask():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 20.0}
    exp = _exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step")
    coord.experiment_manager.add(exp)
    now_ts = 1_000.0 + 1800
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)

    clamps = coord._build_experiment_clamps(now)

    # Mask is keyed by canonical room name and True exactly where the clamp is set.
    mask = coord._experiment_horizon_steps["Living Room"]
    arr = clamps["lr_heater"]
    assert mask == [not np.isnan(arr[k]) for k in range(len(arr))]
    assert all(mask)  # full window in horizon → every step governed


def test_relax_experiment_comfort_zeros_tracking_and_widens_corridor():
    from custom_components.heating_assistant.const import (
        EXPERIMENT_RELAXED_COMFORT_OFFSET,
    )

    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord._experiment_horizon_steps = {"Living Room": [True, True, False, False]}
    traj = SimpleNamespace(
        q_scales={"Living Room": np.array([1.0, 1.0, 1.0, 1.0])},
        comfort_offsets={"Living Room": np.array([2.0, 2.0, 2.0, 2.0])},
    )

    coord._relax_experiment_comfort(traj)

    # Governed steps: tracking weight zeroed, corridor opened wide; others intact.
    assert list(traj.q_scales["Living Room"]) == [0.0, 0.0, 1.0, 1.0]
    assert list(traj.comfort_offsets["Living Room"]) == [
        EXPERIMENT_RELAXED_COMFORT_OFFSET, EXPERIMENT_RELAXED_COMFORT_OFFSET, 2.0, 2.0,
    ]


def test_relax_experiment_comfort_noop_without_experiments():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord._experiment_horizon_steps = {}
    traj = SimpleNamespace(
        q_scales={"Living Room": np.array([1.0])},
        comfort_offsets={"Living Room": np.array([2.0])},
    )

    coord._relax_experiment_comfort(traj)  # must not raise
    assert list(traj.q_scales["Living Room"]) == [1.0]
    assert list(traj.comfort_offsets["Living Room"]) == [2.0]


def test_delete_experiment_removes_scheduled():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    exp = _exp("Living Room", 9_000.0, 9_000.0 + 3600)
    coord.experiment_manager.add(exp)

    assert coord.delete_experiment(exp.id) is True
    assert coord.experiment_manager.get(exp.id) is None
    # Deleting an unknown id is a no-op.
    assert coord.delete_experiment("nope") is False
