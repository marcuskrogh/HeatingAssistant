"""Integration tests for the coordinator's experiment-excitation overlay.

These build a partially-initialised coordinator (the same pattern used by
``test_window_override``) and exercise ``_apply_experiment_excitation`` and the
``_apply_actions`` gating so the excitation is applied to the right rooms,
respects safety overrides, and tells the controller the applied input.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.experiments import Experiment, ExperimentManager


def _slug(name):
    from custom_components.heating_assistant.dashboard import slugify
    return slugify(name)


def _make_coord(rooms, sources):
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
        min_temp=kw.get("min_temp", 12.0),
        max_temp=kw.get("max_temp", 26.0),
        auto_save=kw.get("auto_save", False),
        seed=kw.get("seed", 1),
    )


def test_excitation_overrides_action_for_active_room_only():
    coord = _make_coord(
        ["Living Room", "Kitchen"],
        [_src("lr_heater", "Living Room"), _src("k_heater", "Kitchen")],
    )
    coord.measured_temperatures = {"Living Room": 20.0, "Kitchen": 20.0}
    coord.actions = {"lr_heater": 0.0, "k_heater": 0.3}

    now = datetime.fromtimestamp(1_000.0 + 1800, tz=timezone.utc)
    coord.experiment_manager.add(_exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step"))

    coord._apply_experiment_excitation(now)

    assert coord.actions["lr_heater"] == 1.0          # excited
    assert coord.actions["k_heater"] == 0.3           # untouched
    assert coord.is_experiment_active("Living Room") is True
    assert coord.is_experiment_active("Kitchen") is False
    coord.controller.notify_applied_u.assert_any_call("lr_heater", 1.0)


def test_safety_ceiling_forces_off_when_overheating():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 27.0}
    coord.actions = {"lr_heater": 0.0}
    now = datetime.fromtimestamp(1_000.0 + 1800, tz=timezone.utc)
    coord.experiment_manager.add(_exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step"))

    coord._apply_experiment_excitation(now)
    assert coord.actions["lr_heater"] == 0.0


def test_window_override_suppresses_excitation():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.measured_temperatures = {"Living Room": 20.0}
    coord.actions = {"lr_heater": 0.0}
    coord._window_state["Living Room"] = "open"  # makes is_window_override_active True
    now = datetime.fromtimestamp(1_000.0 + 1800, tz=timezone.utc)
    coord.experiment_manager.add(_exp("Living Room", 1_000.0, 1_000.0 + 8 * 3600, signal_type="step"))

    coord._apply_experiment_excitation(now)
    # Excitation skipped for the open-window room; action left as-is.
    assert coord.actions["lr_heater"] == 0.0
    assert coord.is_experiment_active("Living Room") is False


def test_no_active_experiment_leaves_actions_untouched():
    coord = _make_coord(["Living Room"], [_src("lr_heater", "Living Room")])
    coord.actions = {"lr_heater": 0.42}
    now = datetime.fromtimestamp(5_000.0, tz=timezone.utc)
    # Experiment is in the future.
    coord.experiment_manager.add(_exp("Living Room", 9_000.0, 9_000.0 + 3600))

    coord._apply_experiment_excitation(now)
    assert coord.actions["lr_heater"] == 0.42
    assert coord._experiment_active_rooms == set()
