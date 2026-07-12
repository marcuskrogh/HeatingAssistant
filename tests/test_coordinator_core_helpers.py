"""Unit tests for thin coordinator core helpers not covered by mpc_cycle tests."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from custom_components.heating_assistant.const import (
    CONF_ESTIMATED_PARAMS,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_ROOM_ENABLED,
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_SETPOINTS,
    CONF_PLOT_FORECAST_HOURS,
    CONF_ROOM_NAME,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TRACKING_WEIGHT,
)
from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from tests.helpers.coordinator_stubs import make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _bare_coordinator(entry_data=None):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._entry = SimpleNamespace(entry_id="entry-1", data=entry_data or {})
    coord.hass = MagicMock()
    coord.model = SimpleNamespace(
        room_names=["living_room", "bedroom"],
        rooms={
            "living_room": SimpleNamespace(setpoint=21.0, comfort_offset=2.0),
            "bedroom": SimpleNamespace(setpoint=20.0, comfort_offset=1.5),
        },
    )
    return coord


def test_apply_pending_runtime_reconfiguration_noop_when_empty():
    coord = make_minimal_coordinator()
    coord._pending_runtime_reconfiguration = {}
    coord._build_controller = MagicMock()

    coord._apply_pending_runtime_reconfiguration()

    coord._build_controller.assert_not_called()
    assert coord._pending_runtime_reconfiguration == {}


def test_apply_pending_runtime_reconfiguration_applies_pending_keys():
    coord = make_minimal_coordinator()
    coord._pending_runtime_reconfiguration = {
        CONF_TRACKING_WEIGHT: 3.5,
        CONF_PLOT_FORECAST_HOURS: 18.0,
    }
    coord._plot_history_hours = 24.0
    coord._plot_forecast_hours = 12.0
    coord._build_controller = MagicMock()

    coord._apply_pending_runtime_reconfiguration()

    assert coord._tracking_weight == pytest.approx(3.5)
    assert coord._plot_forecast_hours == pytest.approx(18.0)
    assert coord._pending_runtime_reconfiguration == {}
    coord._build_controller.assert_called_once()


def test_apply_pending_runtime_reconfiguration_wires_runtime_reconfig_module():
    coord = make_minimal_coordinator()
    coord._pending_runtime_reconfiguration = {CONF_TRACKING_WEIGHT: 2.0}

    with patch(
        "custom_components.heating_assistant.coordinator.runtime_reconfig.apply_pending_runtime_reconfiguration"
    ) as apply_mock:
        coord._apply_pending_runtime_reconfiguration()

    apply_mock.assert_called_once_with(coord)


class TestReadOutdoorTempDelegation:
    def test_read_outdoor_temp_delegates_to_disturbances(self):
        coord = make_minimal_coordinator(outdoor_entity="sensor.outdoor")
        coord.hass.states.get = MagicMock(
            return_value=MagicMock(state="4.5", attributes={})
        )

        assert coord._read_outdoor_temp() == pytest.approx(4.5)

    def test_read_outdoor_temp_returns_none_when_unavailable(self):
        coord = make_minimal_coordinator(
            outdoor_entity="sensor.outdoor",
            weather_entity="weather.home",
        )
        coord.hass.states.get = MagicMock(return_value=None)

        assert coord._read_outdoor_temp() is None


class TestReadCloudCoverNowDelegation:
    def test_read_cloud_cover_now_delegates_to_disturbances(self):
        coord = make_minimal_coordinator(weather_entity="weather.home")
        coord.hass.states.get = MagicMock(
            return_value=MagicMock(
                state="partlycloudy",
                attributes={"cloud_coverage": 40},
            )
        )

        assert coord._read_cloud_cover_now() == pytest.approx(0.4)

    def test_read_cloud_cover_now_returns_none_without_entity(self):
        coord = make_minimal_coordinator(weather_entity=None)

        assert coord._read_cloud_cover_now() is None


class TestRuntimeReconfigDelegation:
    def test_apply_runtime_reconfiguration_delegates(self):
        coord = make_minimal_coordinator()
        with patch(
            "custom_components.heating_assistant.coordinator.runtime_reconfig.apply_runtime_reconfiguration",
            return_value=True,
        ) as apply_mock:
            assert coord.apply_runtime_reconfiguration({CONF_TRACKING_WEIGHT: 2.0}) is True
        apply_mock.assert_called_once_with(coord, {CONF_TRACKING_WEIGHT: 2.0})

    def test_apply_tuning_updates_delegates(self):
        coord = make_minimal_coordinator()
        with patch(
            "custom_components.heating_assistant.coordinator.runtime_reconfig.apply_tuning_updates"
        ) as apply_mock:
            coord.apply_tuning_updates({CONF_TRACKING_WEIGHT: 1.5})
        apply_mock.assert_called_once_with(coord, {CONF_TRACKING_WEIGHT: 1.5})


class TestTempSensorMap:
    def test_build_temp_sensor_map_merges_plural_and_singular_keys(self):
        mapping = HeatingAssistantCoordinator._build_temp_sensor_map(
            [
                {
                    CONF_ROOM_NAME: "living_room",
                    CONF_TEMP_SENSORS: ["sensor.lr_a", "sensor.lr_b"],
                    CONF_TEMP_SENSOR: "sensor.lr_b",
                },
                {
                    CONF_ROOM_NAME: "bedroom",
                    CONF_TEMP_SENSOR: "sensor.bed",
                },
            ]
        )
        assert mapping == {
            "living_room": ["sensor.lr_a", "sensor.lr_b"],
            "bedroom": ["sensor.bed"],
        }


class TestInitRoomStateOverlays:
    def test_init_room_state_applies_persisted_room_enabled_setpoints_offsets_schedules(
        self,
    ):
        coord = _bare_coordinator(
            {
                CONF_PERSISTED_ROOM_ENABLED: {"living_room": False},
                CONF_PERSISTED_SETPOINTS: {"living_room": 22.5},
                CONF_PERSISTED_COMFORT_OFFSETS: {"bedroom": 1.0},
                CONF_PERSISTED_SCHEDULES: {
                    "living_room": [
                        {
                            "name": "evening",
                            "mode": "comfort",
                            "start": "18:00",
                            "end": "22:00",
                        }
                    ]
                },
            }
        )
        coord._init_room_state(
            [
                {CONF_ROOM_NAME: "living_room", "setpoint": 21.0, "comfort_offset": 2.0},
                {CONF_ROOM_NAME: "bedroom", "setpoint": 20.0, "comfort_offset": 1.5},
            ]
        )

        assert coord._room_enabled["living_room"] is False
        assert coord._base_setpoint["living_room"] == pytest.approx(22.5)
        assert coord.model.rooms["living_room"].setpoint == pytest.approx(22.5)
        assert coord._room_comfort_offset["bedroom"] == pytest.approx(1.0)
        assert coord.model.rooms["bedroom"].comfort_offset == pytest.approx(1.0)
        assert len(coord._room_schedule["living_room"].periods) == 1


class TestCoordinatorProperties:
    def test_history_buffer_and_update_interval_seconds(self):
        coord = make_minimal_coordinator(dt=600)
        coord._history_buffer = deque([{"y": [20.0]}])
        coord._update_interval_s = 600

        assert coord.history_buffer is coord._history_buffer
        assert coord.update_interval_seconds == 600
        assert coord.dt == pytest.approx(600.0)

    def test_estimated_params_snapshot_reads_real_entry(self):
        coord = make_minimal_coordinator()
        snapshot = {"rooms": {"living_room": {"is_estimated": True}}}
        coord._entry = SimpleNamespace(entry_id="entry-1", data={})
        coord.hass.config_entries.async_get_entry = MagicMock(
            return_value=SimpleNamespace(
                data={CONF_ESTIMATED_PARAMS: snapshot}
            )
        )

        assert coord.estimated_params_snapshot == snapshot


class TestSourcesAndControllerHelpers:
    def test_rebuild_sources_by_room_indexes_sources(self):
        coord = make_minimal_coordinator(room_names=["living_room", "bedroom"])
        src_lr = SimpleNamespace(room="living_room", name="lr_hp")
        src_br = SimpleNamespace(room="bedroom", name="br_rad")
        coord.heat_sources = [src_lr, src_br]

        coord._rebuild_sources_by_room()

        assert coord._sources_by_room["living_room"] == [src_lr]
        assert coord._sources_by_room["bedroom"] == [src_br]
        assert coord.sources_for_room("missing") == []

    def test_build_controller_restores_prior_ekf_state(self):
        coord = make_minimal_coordinator()
        x_hat = np.array([20.0])
        P = np.eye(1)
        old_controller = SimpleNamespace(ekf_state=(x_hat, P))
        coord.controller = old_controller
        new_controller = SimpleNamespace(restore_ekf_state=MagicMock())
        with patch(
            "custom_components.heating_assistant.coordinator.core.build_mpc_controller",
            return_value=new_controller,
        ):
            coord._build_controller()
        new_controller.restore_ekf_state.assert_called_once_with(x_hat, P)

    def test_get_controller_config_snapshot_serialises_tuning(self):
        coord = make_minimal_coordinator()
        coord._tracking_weight = 2.5
        coord._energy_weight = 0.2
        coord._horizon = 24
        coord._room_comfort_offset = {"living_room": 2.0}
        coord.update_interval = SimpleNamespace(total_seconds=lambda: 900)

        snapshot = coord.get_controller_config_snapshot()

        assert snapshot["tracking_weight"] == pytest.approx(2.5)
        assert snapshot["energy_weight"] == pytest.approx(0.2)
        assert snapshot["horizon"] == 24
        assert snapshot["update_interval"] == 900
        assert snapshot["comfort_offset"] == pytest.approx(2.0)


# ── Pure facade → module delegation tests (table-driven) ─────────────────
# Each row exercises one thin coordinator facade method: the delegate module
# function is patched and we assert the facade forwards ``(coord, *args)``
# and passes the delegate's return value straight through.  Behaviour of the
# delegates themselves is tested elsewhere (test_runtime_state.py,
# test_window_override.py, test_parameter_lifecycle.py,
# test_coordinator_apply_actions.py, tests/integration/test_mpc_cycle.py).

_COORD_PKG = "custom_components.heating_assistant.coordinator."
_SRC = SimpleNamespace(name="lr_hp", room="living_room")
_STATE = SimpleNamespace(state="heat")
_NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
_U = np.array([0.5])
_D = np.array([5.0])

# (facade method, delegate target, call args, delegate return value)
_SYNC_DELEGATIONS = [
    ("_record_weather_success", "disturbances.record_weather_success", (), None),
    ("_record_weather_failure", "disturbances.record_weather_failure", ("timeout",), None),
    ("_resolve_display_cloud_cover", "disturbances.resolve_display_cloud_cover", (), 0.35),
    ("_read_wind_speed_now", "disturbances.read_wind_speed_now", (), 3.5),
    ("_record_solar_fc_success", "disturbances.record_solar_fc_success", (), None),
    ("_record_solar_fc_failure", "disturbances.record_solar_fc_failure", ("timeout",), None),
    ("_read_ghi", "disturbances.read_ghi", (_NOW,), (400.0, [350.0, 360.0])),
    ("_room_solar_gain", "disturbances.room_solar_gain", ("living_room", _NOW, 0.2, 400.0), 120.0),
    ("_publish_current_solar_gains", "disturbances.publish_current_solar_gains", (), None),
    ("_read_binary_sensor_on", "window.read_binary_sensor_on", ("binary_sensor.window",), True),
    ("_set_window_state", "window.set_window_state", ("living_room", "open", _NOW), None),
    ("get_window_state", "window.get_window_state", ("living_room",), "open"),
    ("apply_manual_parameters", "parameter_lifecycle.apply_manual_parameters", ("living_room", 5e6, 0.05), None),
    ("apply_heater_scales", "parameter_lifecycle.apply_heater_scales", ({"lr_hp": 0.95},), None),
    ("revert_parameters", "parameter_lifecycle.revert_parameters", ("living_room", 0), None),
    ("delete_parameter_history", "parameter_lifecycle.delete_parameter_history", (1,), None),
    ("reload_room_schedule", "schedule_control.reload_room_schedule", ("living_room", []), None),
    ("_read_delivered_fraction", "actuation.read_delivered_fraction", (_SRC,), 0.5),
    ("_read_delivered_fraction_climate", "actuation.read_delivered_fraction_climate", (_SRC, _STATE), 0.4),
    ("_read_delivered_actions", "actuation.read_delivered_actions", (5.0,), {"lr_hp": 0.5}),
    ("_set_source_power", "actuation.set_source_power", (_SRC, 0.5, 5.0), None),
    ("_climate_internal_temp", "actuation.climate_internal_temp", (_SRC, _STATE), 35.0),
    ("_climate_hp_command", "actuation.climate_hp_command", (_SRC, 0.5, 35.0, 5.0, _STATE), ("heat", 40.0)),
    ("_climate_thermostat_command", "actuation.climate_thermostat_command", (_SRC, 0.5, 35.0, 20.0, 21.0), ("heat", 22.0)),
    ("_refresh_live_state", "live_refresh.refresh_live_state", (), 5.0),
    ("_smooth_cloud_cover", "runtime_state.smooth_cloud_cover", (0.3,), 0.25),
    ("_save_runtime_state", "runtime_state.save_runtime_state", (), None),
    ("_system_nd", "runtime_state.system_nd", (), 4),
    ("_propagate_ekf_gap", "runtime_state.propagate_ekf_gap", (1_000.0, _U, _D), None),
    ("_build_gap_u_sequence", "runtime_state.build_gap_u_sequence", (1_000.0, 3, 900.0, _U), _U),
]

_ASYNC_DELEGATIONS = [
    ("_async_read_weather_forecast", "disturbances.async_read_weather_forecast", (), [5.0, 4.5]),
    ("_async_read_cloud_forecast", "disturbances.async_read_cloud_forecast", (0.3,), [0.4, 0.5]),
    ("_async_read_wind_forecast", "disturbances.async_read_wind_forecast", (1.5,), [2.0, 2.5]),
    ("_ensure_runtime_state_loaded", "runtime_state.ensure_runtime_state_loaded", (), None),
    ("_reapply_climate_setpoints", "actuation.reapply_climate_setpoints", (5.0,), None),
    ("_apply_actions", "actuation.apply_actions", (5.0,), None),
    ("_async_verify_heater_entities", "actuation.async_verify_heater_entities", (5.0,), True),
]


@pytest.mark.parametrize(
    "facade_method,delegate_target,args,retval",
    _SYNC_DELEGATIONS,
    ids=[row[0] for row in _SYNC_DELEGATIONS],
)
def test_facade_method_delegates_to_module(facade_method, delegate_target, args, retval):
    coord = make_minimal_coordinator()
    with patch(_COORD_PKG + delegate_target, return_value=retval) as delegate_mock:
        result = getattr(coord, facade_method)(*args)
    delegate_mock.assert_called_once_with(coord, *args)
    assert result is retval


@pytest.mark.parametrize(
    "facade_method,delegate_target,args,retval",
    _ASYNC_DELEGATIONS,
    ids=[row[0] for row in _ASYNC_DELEGATIONS],
)
async def test_async_facade_method_delegates_to_module(
    facade_method, delegate_target, args, retval
):
    coord = make_minimal_coordinator()
    with patch(
        _COORD_PKG + delegate_target, new=AsyncMock(return_value=retval)
    ) as delegate_mock:
        result = await getattr(coord, facade_method)(*args)
    delegate_mock.assert_called_once_with(coord, *args)
    assert result is retval


def test_build_forecast_payload_delegates_to_module():
    # Kept separate from the table: the facade forwards a long kwargs
    # signature, so only delegation + return passthrough are asserted.
    coord = make_minimal_coordinator()
    expected = {"rooms": {"living_room": {"forecast": []}}}
    with patch(
        _COORD_PKG + "forecast_payload.build_forecast_payload",
        return_value=expected,
    ) as build_mock:
        result = coord.build_forecast_payload(["living_room"], plot_forecast_steps=3)
    build_mock.assert_called_once()
    assert result is expected


class TestStartupListenerCallback:
    def test_setup_startup_listeners_triggers_refresh_on_unavailable_to_valid(
        self, monkeypatch
    ):
        import custom_components.heating_assistant.coordinator.core as core_mod

        scheduled = []

        class _FakeCoordinator:
            _outdoor_entity = "sensor.outdoor"
            _weather_entity = "weather.home"
            _temp_sensors = {"living_room": ["sensor.living_temp"]}
            hass = SimpleNamespace(
                states=SimpleNamespace(get=lambda _eid: None),
                async_create_task=lambda _coro: scheduled.append("refreshed"),
            )

            # Sync stand-in: the fake async_create_task above never awaits its
            # argument, so a real coroutine here would leak a "never awaited"
            # RuntimeWarning.
            def async_request_refresh(self):
                return None

        captured = {}

        def _track(_hass, entity_ids, callback):
            captured["entity_ids"] = entity_ids
            captured["callback"] = callback
            return lambda: None

        monkeypatch.setattr(core_mod, "async_track_state_change_event", _track)
        core_mod.HeatingAssistantCoordinator.setup_startup_listeners(_FakeCoordinator())

        assert "weather.home" in captured["entity_ids"]
        event = SimpleNamespace(
            data={
                "entity_id": "sensor.outdoor",
                "new_state": SimpleNamespace(state="5.0"),
                "old_state": SimpleNamespace(state="unavailable"),
            }
        )
        captured["callback"](event)
        assert scheduled == ["refreshed"]

    def test_setup_startup_listeners_returns_none_without_entities(self):
        import custom_components.heating_assistant.coordinator.core as core_mod

        class _FakeCoordinator:
            _outdoor_entity = None
            _weather_entity = None
            _temp_sensors = {}

        assert (
            core_mod.HeatingAssistantCoordinator.setup_startup_listeners(
                _FakeCoordinator()
            )
            is None
        )


class TestScheduleSerializationDelegation:
    def test_serialize_room_schedules_delegates(self):
        coord = make_minimal_coordinator()
        expected = {"living_room": []}
        with patch(
            "custom_components.heating_assistant.coordinator.schedule_control.serialize_room_schedules",
            return_value=expected,
        ) as serialize_mock:
            assert coord.serialize_room_schedules() == expected
        serialize_mock.assert_called_once_with(coord)


class TestEnablementDelegations:
    def test_enablement_helpers_delegate(self):
        coord = object.__new__(HeatingAssistantCoordinator)
        with patch(
            "custom_components.heating_assistant.coordinator.enablement.set_room_setpoint"
        ) as setpoint_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.get_room_setpoint",
            return_value=21.5,
        ) as get_setpoint_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.system_enabled",
            return_value=True,
        ) as enabled_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.is_room_enabled",
            return_value=False,
        ) as room_enabled_mock:
            coord.set_room_setpoint("living_room", 22.0)
            assert coord.get_room_setpoint("living_room") == pytest.approx(21.5)
            assert coord.system_enabled is True
            assert coord.is_room_enabled("living_room") is False
        setpoint_mock.assert_called_once_with(coord, "living_room", 22.0)
        get_setpoint_mock.assert_called_once_with(coord, "living_room")
        enabled_mock.assert_called_once_with(coord)
        room_enabled_mock.assert_called_once_with(coord, "living_room")

    def test_enablement_setters_delegate(self):
        coord = object.__new__(HeatingAssistantCoordinator)
        with patch(
            "custom_components.heating_assistant.coordinator.enablement.get_base_setpoint",
            return_value=20.0,
        ) as base_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.set_room_comfort_offset"
        ) as offset_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.get_room_comfort_offset",
            return_value=1.5,
        ) as get_offset_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.set_system_enabled"
        ) as system_mock, patch(
            "custom_components.heating_assistant.coordinator.enablement.set_room_enabled"
        ) as room_mock:
            assert coord.get_base_setpoint("living_room") == pytest.approx(20.0)
            coord.set_room_comfort_offset("living_room", 1.0)
            assert coord.get_room_comfort_offset("living_room") == pytest.approx(1.5)
            coord.set_system_enabled(True)
            coord.set_room_enabled("living_room", False)
        base_mock.assert_called_once_with(coord, "living_room")
        offset_mock.assert_called_once_with(coord, "living_room", 1.0)
        get_offset_mock.assert_called_once_with(coord, "living_room")
        system_mock.assert_called_once_with(coord, True)
        room_mock.assert_called_once_with(coord, "living_room", False)


class TestExperimentDelegations:
    def test_experiment_helpers_delegate(self):
        coord = make_minimal_coordinator()
        now = coord.now_utc
        with patch(
            "custom_components.heating_assistant.coordinator.experiments_mpc.is_experiment_active",
            return_value=True,
        ) as active_mock, patch(
            "custom_components.heating_assistant.coordinator.experiments_mpc.build_experiment_clamps",
            return_value={"living_room": []},
        ) as clamps_mock:
            assert coord.is_experiment_active("living_room") is True
            assert coord._build_experiment_clamps(now) == {"living_room": []}
        active_mock.assert_called_once_with(coord, "living_room")
        clamps_mock.assert_called_once_with(coord, now)


class TestAsyncLoglikSlice:
    @pytest.mark.asyncio
    async def test_async_compute_loglik_slice_caches_result(self):
        coord = make_minimal_coordinator()
        coord._history_buffer = deque([{"y": [20.0], "timestamp": 1.0}])
        coord._loglik_slices = {}
        coord.async_update_listeners = MagicMock()
        estimator_result = {
            "room": "living_room",
            "grid": [[0.0, 1.0]],
            "loglik": -10.0,
        }
        with patch(
            "custom_components.heating_assistant.parameter_estimator.KalmanMLEstimator"
        ) as estimator_cls:
            estimator_cls.return_value.compute_loglik_slice = MagicMock(
                return_value=estimator_result
            )
            result = await coord.async_compute_loglik_slice("living_room")
        assert result == estimator_result
        assert "living_room" in coord._loglik_slices
        assert coord._loglik_slices["living_room"]["loglik"] == pytest.approx(-10.0)
        coord.async_update_listeners.assert_called_once()
