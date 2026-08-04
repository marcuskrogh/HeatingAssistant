"""Unit tests for coordinator.parameter_lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from custom_components.heating_assistant.const import CONF_ESTIMATED_PARAMS
from custom_components.heating_assistant.coordinator import parameter_lifecycle


def _make_room(
    name: str = "living_room",
    thermal_mass: float = 5_000_000.0,
    r_external: float = 0.05,
    internal_gain: float = 0.0,
):
    conn = SimpleNamespace(connected_room="kitchen", r_value=0.1)
    room = SimpleNamespace()
    room.name = name
    room.thermal_mass = thermal_mass
    room.r_external = r_external
    room.internal_gain = internal_gain
    room.solar_scale = 1.0
    room.c_air_fraction = 0.05
    room.r_aw_fraction = 0.05
    room.connections = [conn]
    return room


def _make_source(name: str = "heater", power_scale: float = 1.0, room: str = "living_room"):
    src = SimpleNamespace()
    src.name = name
    src.power_scale = power_scale
    src.room = room
    return src


def _make_model(rooms):
    import numpy as np

    model = SimpleNamespace()
    model.rooms = {r.name: r for r in rooms}
    model.room_names = [r.name for r in rooms]
    model._C = np.array([float(r.thermal_mass) for r in rooms])
    model._A = np.zeros((2 * len(rooms), 2 * len(rooms)))
    model._B_ext = np.zeros(2 * len(rooms))
    model._build_matrices = lambda: (model._C, model._A, model._B_ext)
    model.rebuild_derived_parameters = lambda: None
    return model


class _FakeCoordinator:
    def __init__(self, rooms=None, sources=None, entry_data=None):
        if rooms is None:
            rooms = [_make_room()]
        if sources is None:
            sources = [_make_source()]
        if entry_data is None:
            entry_data = {}

        self.model = _make_model(rooms)
        self.heat_sources = sources
        self._sources_by_room: Dict[str, list] = {}
        self._estimation_timestamp = None
        self._estimation_log_likelihood = None
        self._entry = SimpleNamespace(entry_id="test-entry-1", data=dict(entry_data))
        self._base_setpoint = {}
        self._horizon = 6
        self._update_interval_s = 900
        self._tracking_weight = 0.0
        self._energy_weight = 0.01
        self._smoothing_weight = 0.1
        self._constraint_offset = 2.0
        self._soft_constraint_weight = 10.0
        self._soft_constraint_linear_weight = 0.0
        self._terminal_weight = 100.0
        self._sigma_w = 0.25
        self._sigma_v = 0.75
        self._sigma_b = 0.01
        self._energy_price_weight = 1.0
        self._mpc_mode = "linear"
        self._mpc_solver = "qp"
        self._ipopt_available = False
        self._nonlinear_available = False
        self._nonlinear_backend = None
        self._ipopt_unavailable_reason = None
        self._mpc_analytic_derivatives = True
        self._latitude = 0.0
        self._longitude = 0.0
        self.dt = 900.0

        stored_data: Dict[str, Any] = dict(entry_data)
        self._real_entry = SimpleNamespace(data=stored_data)

        def _async_get_entry(entry_id):
            return self._real_entry

        def _async_update_entry(entry, data=None, **kwargs):
            if data is not None:
                entry.data = dict(data)

        self.hass = SimpleNamespace()
        self.hass.config_entries = SimpleNamespace(
            async_get_entry=_async_get_entry,
            async_update_entry=_async_update_entry,
        )

    @property
    def estimated_params_snapshot(self) -> Optional[Dict[str, Any]]:
        real_entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if real_entry is not None:
            return real_entry.data.get(CONF_ESTIMATED_PARAMS)
        return None

    def _rebuild_sources_by_room(self) -> None:
        index: Dict[str, list] = {}
        for src in self.heat_sources:
            index.setdefault(src.room, []).append(src)
        self._sources_by_room = index

    def _build_controller(self) -> None:
        from custom_components.heating_assistant.controller import factory as factory_mod

        config = factory_mod.ControllerBuildConfig.from_coordinator(self)
        self.controller = factory_mod.build_mpc_controller(config)

    def _drop_orphaned_sources(self, sources):
        known_rooms = set(self.model.rooms)
        return [s for s in sources if s.room in known_rooms]


def test_restore_estimated_parameters_applies_room_source_and_connection_values():
    room = _make_room("living_room")
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    snapshot = {
        "rooms": {
            "living_room": {
                "thermal_mass": 2e6,
                "r_external": 0.02,
                "internal_gain": 80.0,
                "solar_scale": 1.2,
                "c_air_fraction": 0.08,
                "r_aw_fraction": 0.06,
            }
        },
        "sources": {"hp": {"power_scale": 1.4}},
        "connections": {"living_room:kitchen": 0.25},
        "estimated_at": "2025-01-01T00:00:00+00:00",
        "log_likelihood": -55.0,
    }

    parameter_lifecycle.restore_estimated_parameters(coordinator, snapshot)

    assert coordinator.model.rooms["living_room"].thermal_mass == pytest.approx(2e6)
    assert coordinator.model.rooms["living_room"].r_external == pytest.approx(0.02)
    assert coordinator.model.rooms["living_room"].internal_gain == pytest.approx(80.0)
    assert coordinator.model.rooms["living_room"].solar_scale == pytest.approx(1.2)
    assert src.power_scale == pytest.approx(1.4)
    assert room.connections[0].r_value == pytest.approx(0.25)
    assert coordinator._estimation_timestamp == "2025-01-01T00:00:00+00:00"
    assert coordinator._estimation_log_likelihood == pytest.approx(-55.0)


def test_restore_estimated_parameters_unwraps_active_snapshot():
    room = _make_room("living_room")
    coordinator = _FakeCoordinator([room])

    snapshot = {
        "active": {
            "rooms": {
                "living_room": {"thermal_mass": 1.5e6, "r_external": 0.07}
            },
            "estimated_at": "2025-02-02T00:00:00+00:00",
        },
        "history": [],
    }

    parameter_lifecycle.restore_estimated_parameters(coordinator, snapshot)

    assert coordinator.model.rooms["living_room"].thermal_mass == pytest.approx(1.5e6)
    assert coordinator.model.rooms["living_room"].r_external == pytest.approx(0.07)


def test_apply_heater_scales_noop_on_empty_dict():
    coordinator = _FakeCoordinator()
    coordinator._build_controller = MagicMock()

    parameter_lifecycle.apply_heater_scales(coordinator, {})

    coordinator._build_controller.assert_not_called()
    assert coordinator.estimated_params_snapshot is None


def test_apply_heater_scales_updates_source_and_persists_snapshot():
    room = _make_room("living_room")
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    with patch(
        "custom_components.heating_assistant.controller.factory.HeatingMPCController",
        return_value=MagicMock(),
    ):
        parameter_lifecycle.apply_heater_scales(coordinator, {"hp": 1.35, "missing": 2.0})

    assert src.power_scale == pytest.approx(1.35)
    snap = coordinator.estimated_params_snapshot
    assert snap is not None
    assert snap["sources"]["hp"]["power_scale"] == pytest.approx(1.35)


def test_apply_estimated_parameters_persists_snapshot_and_metadata():
    room = _make_room("living_room")
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    with patch(
        "custom_components.heating_assistant.controller.factory.HeatingMPCController",
        return_value=MagicMock(),
    ):
        parameter_lifecycle.apply_estimated_parameters(
            coordinator,
            estimated_params={"living_room": {"thermal_mass": 3e6, "r_external": 0.03}},
            estimated_internal_gains={"living_room": 120.0},
            estimated_heater_scales={"hp": 1.2},
            estimated_solar_scales={"living_room": 0.9},
            estimated_envelope_splits={
                "living_room": {"c_air_fraction": 0.07, "r_aw_fraction": 0.04}
            },
            log_likelihood=-12.5,
        )

    assert coordinator.model.rooms["living_room"].thermal_mass == pytest.approx(3e6)
    assert coordinator.model.rooms["living_room"].internal_gain == pytest.approx(120.0)
    assert coordinator.model.rooms["living_room"].solar_scale == pytest.approx(0.9)
    assert coordinator.model.rooms["living_room"].c_air_fraction == pytest.approx(0.07)
    assert src.power_scale == pytest.approx(1.2)
    assert coordinator._estimation_log_likelihood == pytest.approx(-12.5)

    snap = coordinator.estimated_params_snapshot
    assert snap is not None
    assert snap["rooms"]["living_room"]["is_estimated"] is True
    assert snap["rooms"]["living_room"]["estimation_source"] == "ml"
    assert snap["log_likelihood"] == pytest.approx(-12.5)


def test_store_identified_parameters_writes_active_history_snapshot():
    room = _make_room("living_room")
    src = _make_source("hp", 1.0)
    coordinator = _FakeCoordinator([room], [src])

    with patch(
        "custom_components.heating_assistant.controller.factory.HeatingMPCController",
        return_value=MagicMock(),
    ):
        parameter_lifecycle.store_identified_parameters(
            coordinator,
            "living_room",
            thermal_mass=4e6,
            r_external=0.04,
            source="manual",
            heater_scales={"hp": 1.1},
        )

    snap = coordinator.estimated_params_snapshot
    assert snap is not None
    assert "active" in snap
    assert snap["active"]["rooms"]["living_room"]["thermal_mass"] == pytest.approx(4e6)
    assert snap["sources"]["hp"]["power_scale"] == pytest.approx(1.1)
    assert coordinator._estimation_timestamp is not None


def test_apply_manual_parameters_delegates_to_store():
    coordinator = _FakeCoordinator()
    with patch.object(
        parameter_lifecycle, "store_identified_parameters"
    ) as store_mock:
        parameter_lifecycle.apply_manual_parameters(
            coordinator, "living_room", 4e6, 0.04
        )
    store_mock.assert_called_once_with(
        coordinator, "living_room", 4e6, 0.04, source="manual"
    )


def test_delete_parameter_history_removes_entry():
    coordinator = _FakeCoordinator(
        entry_data={
            CONF_ESTIMATED_PARAMS: {
                "active": {"rooms": {}},
                "history": [
                    {"rooms": {"living_room": {"thermal_mass": 1e6, "r_external": 0.01}}},
                    {"rooms": {"living_room": {"thermal_mass": 2e6, "r_external": 0.02}}},
                ],
            }
        }
    )

    parameter_lifecycle.delete_parameter_history(coordinator, 0)

    snap = coordinator.estimated_params_snapshot
    assert len(snap["history"]) == 1
    assert snap["history"][0]["rooms"]["living_room"]["thermal_mass"] == pytest.approx(2e6)
