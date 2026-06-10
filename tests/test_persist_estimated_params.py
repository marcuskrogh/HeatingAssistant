"""Tests for persisting and restoring estimated parameters.

Covers:
- CONF_ESTIMATED_PARAMS is written to entry.data after a successful ML estimation.
- Persisted values are restored to the live model on coordinator __init__.
- reset_estimated_parameters() reverts the model and removes the snapshot.
- internal_gain and power_scale are applied from the ML result.
- ParameterConfidenceSensor.extra_state_attributes gains is_estimated/estimated_at.
- EstimatedParametersStatusSensor.native_value and attributes.
- HeaterScaleSensor.native_value reflects power_scale.
"""

from __future__ import annotations

import sys
import os
from collections import deque
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_room(name="living_room", thermal_mass=5_000_000.0, r_external=0.05, internal_gain=0.0):
    room = SimpleNamespace()
    room.name = name
    room.thermal_mass = thermal_mass
    room.r_external = r_external
    room.internal_gain = internal_gain
    room.connections = []
    room.temperature = 21.0
    room.setpoint = 21.0
    return room


def _make_source(name="heater", power_scale=1.0, max_power=2000.0, room="lr"):
    src = SimpleNamespace()
    src.name = name
    src.power_scale = power_scale
    src.max_power = max_power
    src.room = room
    return src


def _make_model(rooms):
    model = SimpleNamespace()
    model.rooms = {r.name: r for r in rooms}
    model.room_names = [r.name for r in rooms]
    model._C = None
    model._A = None
    model._B_ext = None
    model._B_ground = None
    model._build_matrices = lambda: ("C", "A", "B_ext")
    model.rebuild_derived_parameters = lambda: None
    return model


class _FakeCoordinator:
    """Minimal coordinator with the attributes needed by the methods under test.

    Uses a real class (not SimpleNamespace) so that ``estimated_params_snapshot``
    can be exposed as a proper property descriptor, matching how sensors access it.
    """

    def __init__(self, rooms=None, sources=None, entry_data=None):
        from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS as _KEY
        self._CONF_ESTIMATED_PARAMS = _KEY

        if rooms is None:
            rooms = [_make_room()]
        if sources is None:
            sources = [_make_source()]
        if entry_data is None:
            entry_data = {}

        self.model = _make_model(rooms)
        self.heat_sources = sources
        # Mirror the real coordinator's per-room source index so methods that
        # call ``self._rebuild_sources_by_room()`` (e.g. reset) succeed.
        self._sources_by_room: Dict[str, list] = {}
        self._history_buffer = deque()
        self._estimation_timestamp = None
        self._estimation_log_likelihood = None
        self._entry = SimpleNamespace(entry_id="test-entry-1", data=dict(entry_data))
        self._horizon = 6
        self._update_interval_s = 900
        self._tracking_weight = 0.0
        self._energy_weight = 0.01
        self._smoothing_weight = 0.1
        self._constraint_offset = 2.0
        self._soft_constraint_weight = 10.0
        self._soft_constraint_linear_weight = 0.0
        self._terminal_weight = 100.0
        self._base_setpoint = {}
        self._sigma_w = 0.25
        self._sigma_v = 0.75
        self._sigma_b = 0.01
        self._energy_price_weight = 1.0
        self._mpc_solver = "SLSQP"
        self._mpc_analytic_derivatives = True
        self._latitude = 0.0
        self._longitude = 0.0
        self.dt = 900.0

        # In-memory "real entry" that persists across _async_update_entry calls.
        stored_data: Dict[str, Any] = dict(entry_data)
        real_entry = SimpleNamespace(data=stored_data)
        self._real_entry = real_entry

        def _async_get_entry(entry_id):
            return self._real_entry

        def _async_update_entry(entry, data=None, **kwargs):
            if data is not None:
                new_data = dict(data)
                entry.data = new_data

        self.hass = SimpleNamespace()
        self.hass.config_entries = SimpleNamespace(
            async_get_entry=_async_get_entry,
            async_update_entry=_async_update_entry,
        )

    @property
    def estimated_params_snapshot(self) -> Optional[Dict[str, Any]]:
        """Mirror the real coordinator property for test use."""
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            return real_entry.data.get(self._CONF_ESTIMATED_PARAMS)
        return None

    def _rebuild_sources_by_room(self) -> None:
        """Mirror the real coordinator's room-index rebuild."""
        index: Dict[str, list] = {}
        for src in self.heat_sources:
            index.setdefault(src.room, []).append(src)
        self._sources_by_room = index


# ---------------------------------------------------------------------------
# Part 1: _apply_estimated_parameters applies internal_gain and power_scale
# ---------------------------------------------------------------------------

def test_apply_estimated_parameters_sets_internal_gain_and_power_scale():
    """internal_gain and power_scale must be applied to the live model."""
    import custom_components.heating_assistant.coordinator as coord_mod

    room = _make_room("lr", 5e6, 0.05, internal_gain=0.0)
    src = _make_source("hp", power_scale=1.0)
    coordinator = _FakeCoordinator([room], [src])

    mock_controller = MagicMock()
    with patch(
        "custom_components.heating_assistant.coordinator.HeatingMPCController",
        return_value=mock_controller,
    ) as m_controller:
        coord_mod.HeatingAssistantCoordinator._apply_estimated_parameters(
            coordinator,
            estimated_params={"lr": {"thermal_mass": 4e6, "r_external": 0.04}},
            estimated_inter_room_r={},
            estimated_internal_gains={"lr": 150.0},
            estimated_heater_scales={"hp": 1.25},
            log_likelihood=-42.5,
        )

    assert coordinator.model.rooms["lr"].thermal_mass == pytest.approx(4e6)
    assert coordinator.model.rooms["lr"].r_external == pytest.approx(0.04)
    assert coordinator.model.rooms["lr"].internal_gain == pytest.approx(150.0)
    assert src.power_scale == pytest.approx(1.25)
    kwargs = m_controller.call_args.kwargs
    assert kwargs["sigma_w"] == pytest.approx(0.25)
    assert kwargs["sigma_v"] == pytest.approx(0.75)
    assert kwargs["sigma_b"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Part 2: Snapshot is persisted to entry.data
# ---------------------------------------------------------------------------

def test_apply_estimated_parameters_persists_snapshot():
    """A CONF_ESTIMATED_PARAMS snapshot must be written to entry.data."""
    import custom_components.heating_assistant.coordinator as coord_mod
    from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS

    room = _make_room("lr", 5e6, 0.05)
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    mock_controller = MagicMock()
    with patch(
        "custom_components.heating_assistant.coordinator.HeatingMPCController",
        return_value=mock_controller,
    ):
        coord_mod.HeatingAssistantCoordinator._apply_estimated_parameters(
            coordinator,
            estimated_params={"lr": {"thermal_mass": 3e6, "r_external": 0.03}},
            log_likelihood=-10.0,
        )

    snap = coordinator._real_entry.data.get(CONF_ESTIMATED_PARAMS)
    assert snap is not None, "Snapshot must be written to entry.data"
    assert "lr" in snap["rooms"]
    assert snap["rooms"]["lr"]["thermal_mass"] == pytest.approx(3e6)
    assert snap["rooms"]["lr"]["r_external"] == pytest.approx(0.03)
    assert snap["log_likelihood"] == pytest.approx(-10.0)
    assert snap["estimated_at"] is not None


# ---------------------------------------------------------------------------
# Part 2b: Snapshot metadata is stored on the coordinator
# ---------------------------------------------------------------------------

def test_apply_estimated_parameters_updates_instance_vars():
    """_estimation_timestamp and _estimation_log_likelihood must be updated."""
    import custom_components.heating_assistant.coordinator as coord_mod

    coordinator = _FakeCoordinator()
    mock_controller = MagicMock()
    with patch(
        "custom_components.heating_assistant.coordinator.HeatingMPCController",
        return_value=mock_controller,
    ):
        coord_mod.HeatingAssistantCoordinator._apply_estimated_parameters(
            coordinator,
            estimated_params={
                "living_room": {
                    "thermal_mass": coordinator.model.rooms["living_room"].thermal_mass,
                    "r_external": coordinator.model.rooms["living_room"].r_external,
                }
            },
            log_likelihood=-99.0,
        )

    assert coordinator._estimation_timestamp is not None
    assert coordinator._estimation_log_likelihood == pytest.approx(-99.0)


# ---------------------------------------------------------------------------
# Part 2c: Snapshot is restored on coordinator __init__
# ---------------------------------------------------------------------------

def test_restore_estimated_parameters_applies_to_model():
    """_restore_estimated_parameters must set room/source values from snapshot."""
    import custom_components.heating_assistant.coordinator as coord_mod

    room = _make_room("lr", 5e6, 0.05, internal_gain=0.0)
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    snapshot = {
        "rooms": {
            "lr": {"thermal_mass": 2e6, "r_external": 0.02, "internal_gain": 80.0}
        },
        "sources": {"hp": {"power_scale": 1.4}},
        "connections": {},
        "estimated_at": "2025-01-01T00:00:00+00:00",
        "log_likelihood": -55.0,
    }

    coord_mod.HeatingAssistantCoordinator._restore_estimated_parameters(
        coordinator, snapshot
    )

    assert coordinator.model.rooms["lr"].thermal_mass == pytest.approx(2e6)
    assert coordinator.model.rooms["lr"].r_external == pytest.approx(0.02)
    assert coordinator.model.rooms["lr"].internal_gain == pytest.approx(80.0)
    assert src.power_scale == pytest.approx(1.4)
    assert coordinator.model._C == "C"
    assert coordinator.model._A == "A"
    assert coordinator.model._B_ext == "B_ext"
    assert coordinator._estimation_timestamp == "2025-01-01T00:00:00+00:00"
    assert coordinator._estimation_log_likelihood == pytest.approx(-55.0)


def test_restore_estimated_parameters_active_history_format():
    """Regression: parameters stored by store_identified_parameters use the
    ``{"active": {"rooms": {...}}, "history": [...]}`` layout.  The restore must
    unwrap ``active`` so manually-applied model parameters survive a restart
    (previously they were dropped, reverting the model to YAML defaults)."""
    import custom_components.heating_assistant.coordinator as coord_mod

    room = _make_room("lr", 5e6, 0.05, internal_gain=0.0)
    coordinator = _FakeCoordinator([room])

    snapshot = {
        "active": {
            "rooms": {
                "lr": {"thermal_mass": 1.5e6, "r_external": 0.07, "internal_gain": 42.0}
            },
            "estimated_at": "2025-02-02T00:00:00+00:00",
            "source": "manual",
        },
        "history": [
            {"rooms": {"lr": {"thermal_mass": 5e6, "r_external": 0.05}}},
        ],
    }

    coord_mod.HeatingAssistantCoordinator._restore_estimated_parameters(
        coordinator, snapshot
    )

    assert coordinator.model.rooms["lr"].thermal_mass == pytest.approx(1.5e6)
    assert coordinator.model.rooms["lr"].r_external == pytest.approx(0.07)
    assert coordinator.model.rooms["lr"].internal_gain == pytest.approx(42.0)


def test_store_identified_parameters_snapshot_survives_restart():
    """A snapshot written by store_identified_parameters must be restorable.

    Regression for the bug where manually-stored model parameters reverted to
    the configured defaults on restart: store_identified_parameters wrote a
    nested {"active": ..., "history": ...} snapshot but _restore_estimated_parameters
    read the flat {"rooms": ...} layout, so nothing was restored.
    """
    import custom_components.heating_assistant.coordinator as coord_mod
    from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS

    room = _make_room("lr", 5e6, 0.05)
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    mock_controller = MagicMock()
    with patch(
        "custom_components.heating_assistant.coordinator.HeatingMPCController",
        return_value=mock_controller,
    ):
        coord_mod.HeatingAssistantCoordinator.store_identified_parameters(
            coordinator, "lr", 2.5e6, 0.025, source="manual"
        )

    snapshot = coordinator._real_entry.data[CONF_ESTIMATED_PARAMS]
    # Flat mirror must be present so the restart-time restore can read it.
    assert snapshot["rooms"]["lr"]["thermal_mass"] == pytest.approx(2.5e6)
    assert snapshot["rooms"]["lr"]["r_external"] == pytest.approx(0.025)
    # And the nested history layout is preserved for the dashboard.
    assert "active" in snapshot and "history" in snapshot

    # Simulate a restart: build a fresh model at the configured defaults and
    # restore from the persisted snapshot.
    fresh_room = _make_room("lr", 5e6, 0.05)
    fresh_src = _make_source("hp", 1.0)
    fresh = _FakeCoordinator([fresh_room], [fresh_src])
    coord_mod.HeatingAssistantCoordinator._restore_estimated_parameters(
        fresh, snapshot
    )
    assert fresh.model.rooms["lr"].thermal_mass == pytest.approx(2.5e6)
    assert fresh.model.rooms["lr"].r_external == pytest.approx(0.025)


def test_restore_estimated_parameters_handles_legacy_nested_only():
    """A legacy nested-only snapshot (no flat mirror) must still restore."""
    import custom_components.heating_assistant.coordinator as coord_mod

    room = _make_room("lr", 5e6, 0.05)
    coordinator = _FakeCoordinator([room])
    legacy_snapshot = {
        "active": {
            "rooms": {"lr": {"thermal_mass": 1.5e6, "r_external": 0.015}},
            "estimated_at": "2025-01-01T00:00:00+00:00",
            "source": "manual",
        },
        "history": [],
    }
    coord_mod.HeatingAssistantCoordinator._restore_estimated_parameters(
        coordinator, legacy_snapshot
    )
    assert coordinator.model.rooms["lr"].thermal_mass == pytest.approx(1.5e6)
    assert coordinator.model.rooms["lr"].r_external == pytest.approx(0.015)


def test_restore_estimated_parameters_unknown_room_is_ignored():
    """Snapshot entries for rooms not in the model must be silently ignored."""
    import custom_components.heating_assistant.coordinator as coord_mod

    coordinator = _FakeCoordinator()
    snapshot = {
        "rooms": {"ghost_room": {"thermal_mass": 1e6, "r_external": 0.1}},
        "sources": {},
        "connections": {},
        "estimated_at": None,
        "log_likelihood": None,
    }
    coord_mod.HeatingAssistantCoordinator._restore_estimated_parameters(
        coordinator, snapshot
    )


# ---------------------------------------------------------------------------
# Part 4: reset_estimated_parameters
# ---------------------------------------------------------------------------

def test_reset_estimated_parameters_removes_snapshot_and_rebuilds():
    """reset_estimated_parameters must remove CONF_ESTIMATED_PARAMS from
    entry.data and rebuild the model/controller."""
    import custom_components.heating_assistant.coordinator as coord_mod
    from custom_components.heating_assistant.const import (
        CONF_ESTIMATED_PARAMS,
        CONF_ROOMS,
        CONF_HEAT_SOURCES,
    )

    entry_data = {
        CONF_ESTIMATED_PARAMS: {"rooms": {}, "sources": {}, "estimated_at": "now"},
        CONF_ROOMS: [],
        CONF_HEAT_SOURCES: [],
    }
    coordinator = _FakeCoordinator(entry_data=entry_data)
    # Sync the real_entry.data so the snapshot is present
    coordinator._real_entry.data = dict(entry_data)
    coordinator._estimation_timestamp = "now"
    coordinator._estimation_log_likelihood = -1.0

    mock_controller = MagicMock()
    with (
        patch(
            "custom_components.heating_assistant.coordinator.build_house_model",
            return_value=_make_model([_make_room()]),
        ),
        patch(
            "custom_components.heating_assistant.coordinator.build_heat_sources",
            return_value=[_make_source()],
        ),
        patch(
            "custom_components.heating_assistant.coordinator.HeatingMPCController",
            return_value=mock_controller,
        ) as m_controller,
    ):
        coord_mod.HeatingAssistantCoordinator.reset_estimated_parameters(coordinator)

    assert CONF_ESTIMATED_PARAMS not in coordinator._real_entry.data
    assert coordinator._estimation_timestamp is None
    assert coordinator._estimation_log_likelihood is None
    kwargs = m_controller.call_args.kwargs
    assert kwargs["sigma_w"] == pytest.approx(0.25)
    assert kwargs["sigma_v"] == pytest.approx(0.75)
    assert kwargs["sigma_b"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Part 5a: estimated_params_snapshot property
# ---------------------------------------------------------------------------

def test_estimated_params_snapshot_returns_none_when_absent():
    coordinator = _FakeCoordinator()
    assert coordinator.estimated_params_snapshot is None


def test_estimated_params_snapshot_returns_snapshot_when_present():
    from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS

    snap = {"rooms": {}, "sources": {}, "estimated_at": "T"}
    coordinator = _FakeCoordinator()
    coordinator._real_entry.data[CONF_ESTIMATED_PARAMS] = snap
    assert coordinator.estimated_params_snapshot == snap


# ---------------------------------------------------------------------------
# Part 5b: ParameterConfidenceSensor extra_state_attributes
# ---------------------------------------------------------------------------

def test_parameter_confidence_sensor_has_is_estimated_attribute():
    """extra_state_attributes must include is_estimated and estimated_at."""
    from custom_components.heating_assistant.sensor import ParameterConfidenceSensor
    from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS

    room = _make_room("lr", 5_000_000, 0.05)
    coordinator = _FakeCoordinator([room])
    snap = {
        "rooms": {"lr": {"thermal_mass": 5e6, "r_external": 0.05, "internal_gain": 0}},
        "sources": {},
        "connections": {},
        "estimated_at": "2025-06-01T00:00:00+00:00",
        "log_likelihood": -20.0,
    }
    coordinator._real_entry.data[CONF_ESTIMATED_PARAMS] = snap

    sensor = ParameterConfidenceSensor.__new__(ParameterConfidenceSensor)
    sensor._coordinator = coordinator
    sensor._room_name = "lr"

    attrs = sensor.extra_state_attributes
    assert "is_estimated" in attrs
    assert attrs["is_estimated"] is True
    assert attrs["estimated_at"] == "2025-06-01T00:00:00+00:00"
    assert "internal_gain" in attrs


def test_parameter_confidence_sensor_not_estimated_when_no_snapshot():
    from custom_components.heating_assistant.sensor import ParameterConfidenceSensor

    coordinator = _FakeCoordinator()
    sensor = ParameterConfidenceSensor.__new__(ParameterConfidenceSensor)
    sensor._coordinator = coordinator
    sensor._room_name = "living_room"

    attrs = sensor.extra_state_attributes
    assert attrs.get("is_estimated") is False
    assert attrs.get("estimated_at") is None


# ---------------------------------------------------------------------------
# Part 5c: HeaterScaleSensor
# ---------------------------------------------------------------------------

def test_heater_scale_sensor_native_value_default():
    """Default (unadjusted) power_scale must report 100 %."""
    from custom_components.heating_assistant.sensor import HeaterScaleSensor

    coordinator = _FakeCoordinator(sources=[_make_source("hp", 1.0)])
    sensor = HeaterScaleSensor.__new__(HeaterScaleSensor)
    sensor._coordinator = coordinator
    sensor._source_name = "hp"

    assert sensor.native_value == pytest.approx(100.0)


def test_heater_scale_sensor_native_value_estimated():
    from custom_components.heating_assistant.sensor import HeaterScaleSensor

    coordinator = _FakeCoordinator(sources=[_make_source("hp", 1.3)])
    sensor = HeaterScaleSensor.__new__(HeaterScaleSensor)
    sensor._coordinator = coordinator
    sensor._source_name = "hp"

    assert sensor.native_value == pytest.approx(130.0)


def test_heater_scale_sensor_attributes_is_estimated():
    from custom_components.heating_assistant.sensor import HeaterScaleSensor
    from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS

    coordinator = _FakeCoordinator(sources=[_make_source("hp", 1.1)])
    snap = {
        "rooms": {},
        "sources": {"hp": {"power_scale": 1.1}},
        "connections": {},
        "estimated_at": "2025-07-01T00:00:00+00:00",
        "log_likelihood": None,
    }
    coordinator._real_entry.data[CONF_ESTIMATED_PARAMS] = snap

    sensor = HeaterScaleSensor.__new__(HeaterScaleSensor)
    sensor._coordinator = coordinator
    sensor._source_name = "hp"

    attrs = sensor.extra_state_attributes
    assert attrs["is_estimated"] is True
    assert attrs["estimated_at"] == "2025-07-01T00:00:00+00:00"
    assert attrs["power_scale"] == pytest.approx(1.1)


# ---------------------------------------------------------------------------
# Part 5d: EstimatedParametersStatusSensor
# ---------------------------------------------------------------------------

def test_estimated_parameters_status_sensor_default():
    from custom_components.heating_assistant.sensor import EstimatedParametersStatusSensor

    coordinator = _FakeCoordinator()
    sensor = EstimatedParametersStatusSensor.__new__(EstimatedParametersStatusSensor)
    sensor._coordinator = coordinator

    assert sensor.native_value == "default"
    attrs = sensor.extra_state_attributes
    assert attrs["n_rooms_estimated"] == 0
    assert attrs["n_sources_estimated"] == 0
    assert attrs["estimated_at"] is None


def test_estimated_parameters_status_sensor_estimated():
    from custom_components.heating_assistant.sensor import EstimatedParametersStatusSensor
    from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS

    coordinator = _FakeCoordinator(
        rooms=[_make_room("lr")], sources=[_make_source("hp")]
    )
    snap = {
        "rooms": {"lr": {"thermal_mass": 3e6, "r_external": 0.03, "internal_gain": 50.0}},
        "sources": {"hp": {"power_scale": 1.2}},
        "connections": {},
        "estimated_at": "2025-08-01T00:00:00+00:00",
        "log_likelihood": -30.0,
    }
    coordinator._real_entry.data[CONF_ESTIMATED_PARAMS] = snap

    sensor = EstimatedParametersStatusSensor.__new__(EstimatedParametersStatusSensor)
    sensor._coordinator = coordinator

    assert sensor.native_value == "estimated"
    attrs = sensor.extra_state_attributes
    assert attrs["n_rooms_estimated"] == 1
    assert attrs["n_sources_estimated"] == 1
    assert attrs["estimated_at"] == "2025-08-01T00:00:00+00:00"
    assert attrs["log_likelihood"] == pytest.approx(-30.0)
    assert attrs["rooms"]["lr"]["is_estimated"] is True
    assert attrs["sources"]["hp"]["is_estimated"] is True


# ---------------------------------------------------------------------------
# Part 3: async_estimate_parameters_ml passes new args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_estimate_ml_passes_internal_gains_and_heater_scales():
    """async_estimate_parameters_ml must forward internal_gains / heater_scales."""
    import custom_components.heating_assistant.coordinator as coord_mod

    captured: dict = {}

    def _fake_apply(
        self_,
        estimated_params,
        estimated_inter_room_r=None,
        estimated_internal_gains=None,
        estimated_heater_scales=None,
        estimated_solar_scales=None,
        estimated_envelope_splits=None,
        log_likelihood=None,
    ):
        captured["internal_gains"] = estimated_internal_gains
        captured["heater_scales"] = estimated_heater_scales
        captured["log_likelihood"] = log_likelihood

    coordinator = _FakeCoordinator()
    # Add the method so the bound call works
    coordinator._apply_estimated_parameters = lambda *a, **kw: _fake_apply(coordinator, *a, **kw)

    ml_result = {
        "success": True,
        "estimated_params": {},
        "estimated_inter_room_r": {},
        "estimated_internal_gains": {"room_a": 100.0},
        "estimated_heater_scales": {"hp": 1.2},
        "log_likelihood": -77.0,
    }

    class _FakeEstimator:
        def __init__(self, **kw):
            pass
        def estimate(self, history):
            return ml_result

    coordinator.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(
            side_effect=lambda fn, *args: fn(*args)
        ),
        config_entries=coordinator.hass.config_entries,
    )

    with patch(
        "custom_components.heating_assistant.parameter_estimator.KalmanMLEstimator",
        _FakeEstimator,
    ):
        await coord_mod.HeatingAssistantCoordinator.async_estimate_parameters_ml(
            coordinator, apply_params=True
        )

    assert captured.get("internal_gains") == {"room_a": 100.0}
    assert captured.get("heater_scales") == {"hp": 1.2}
    assert captured.get("log_likelihood") == pytest.approx(-77.0)
