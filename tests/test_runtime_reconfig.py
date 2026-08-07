"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from heatingassistant.engine.const import (
    CONF_ENERGY_WEIGHT,
    CONF_HORIZON,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PERSISTED_SETPOINTS,
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_ROOMS,
    CONF_SIGMA_W,
    CONF_TRACKING_WEIGHT,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
)
from custom_components.heating_assistant.coordinator import runtime_reconfig as rr
from tests.helpers.coordinator_stubs import make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _reconfig_coord():
    coord = make_minimal_coordinator(room_names=["living_room"])
    coord._last_runtime_config = {}
    coord._pending_runtime_reconfiguration = {}
    coord._plot_history_hours = 24.0
    coord._plot_forecast_hours = 12.0
    coord._identification_horizon_hours = 6.0
    coord._identification_history_days = 90
    coord.update_interval = timedelta(seconds=coord._update_interval_s)
    coord._build_controller = MagicMock()
    coord.id_history_store = MagicMock()
    return coord


def test_config_key_sets_partition_runtime_vs_reload_vs_persisted():
    """Runtime keys are disjoint from reload-required and persisted-state keys."""
    assert rr.RELOAD_REQUIRED_CONFIG_KEYS.isdisjoint(rr.RUNTIME_RECONFIG_KEYS)
    assert rr.PERSISTED_STATE_KEYS.isdisjoint(rr.RUNTIME_RECONFIG_KEYS)
    assert rr.PERSISTED_STATE_KEYS.isdisjoint(rr.RELOAD_REQUIRED_CONFIG_KEYS)
    assert CONF_TRACKING_WEIGHT in rr.RUNTIME_RECONFIG_KEYS
    assert CONF_ROOMS in rr.RELOAD_REQUIRED_CONFIG_KEYS
    assert CONF_PERSISTED_SETPOINTS in rr.PERSISTED_STATE_KEYS


def test_apply_runtime_reconfiguration_returns_true_when_unchanged():
    coord = _reconfig_coord()
    config = {CONF_TRACKING_WEIGHT: 1.0}
    coord._last_runtime_config = dict(config)

    assert rr.apply_runtime_reconfiguration(coord, config) is True
    assert coord._pending_runtime_reconfiguration == {}


def test_apply_runtime_reconfiguration_queues_runtime_keys():
    coord = _reconfig_coord()
    config = {
        CONF_TRACKING_WEIGHT: 2.5,
        CONF_OUTDOOR_TEMP_ENTITY: "sensor.outdoor",
        CONF_WEATHER_ENTITY: "weather.home",
    }

    assert rr.apply_runtime_reconfiguration(coord, config) is True
    pending = coord._pending_runtime_reconfiguration
    assert pending[CONF_TRACKING_WEIGHT] == 2.5
    assert pending[CONF_OUTDOOR_TEMP_ENTITY] == "sensor.outdoor"
    assert pending[CONF_WEATHER_ENTITY] == "weather.home"


def test_apply_runtime_reconfiguration_requires_reload_for_structural_keys():
    coord = _reconfig_coord()
    config = {CONF_ROOMS: [{"name": "living_room"}]}

    assert rr.apply_runtime_reconfiguration(coord, config) is False


def test_apply_runtime_reconfiguration_ignores_persisted_state_keys():
    coord = _reconfig_coord()
    coord._last_runtime_config = {CONF_TRACKING_WEIGHT: 1.0}
    config = {
        CONF_TRACKING_WEIGHT: 1.0,
        CONF_PERSISTED_SETPOINTS: {"living_room": 22.0},
    }

    assert rr.apply_runtime_reconfiguration(coord, config) is True
    assert coord._pending_runtime_reconfiguration == {}


def test_apply_pending_runtime_reconfiguration_applies_entities_and_plot_hours():
    coord = _reconfig_coord()
    coord._pending_runtime_reconfiguration = {
        CONF_OUTDOOR_TEMP_ENTITY: "sensor.new_outdoor",
        CONF_WEATHER_ENTITY: "weather.new",
        CONF_PLOT_HISTORY_HOURS: 48.0,
        CONF_PLOT_FORECAST_HOURS: 6.0,
        CONF_LATITUDE: 56.0,
        CONF_LONGITUDE: 10.0,
    }

    rr.apply_pending_runtime_reconfiguration(coord)

    assert coord._outdoor_entity == "sensor.new_outdoor"
    assert coord._weather_entity == "weather.new"
    assert coord._plot_history_hours == 48.0
    assert coord._plot_forecast_hours == 6.0
    assert coord._latitude == 56.0
    assert coord._longitude == 10.0
    assert coord._pending_runtime_reconfiguration == {}
    coord._build_controller.assert_not_called()


def test_apply_pending_runtime_reconfiguration_rebuilds_for_tuning_keys():
    coord = _reconfig_coord()
    coord._pending_runtime_reconfiguration = {
        CONF_TRACKING_WEIGHT: 3.0,
        CONF_ENERGY_WEIGHT: 0.25,
        CONF_SIGMA_W: 0.2,
    }

    rr.apply_pending_runtime_reconfiguration(coord)

    assert coord._tracking_weight == 3.0
    assert coord._energy_weight == 0.25
    assert coord._sigma_w == 0.2
    coord._build_controller.assert_called_once()


def test_apply_pending_runtime_reconfiguration_updates_interval_without_rebuild():
    coord = _reconfig_coord()
    coord._pending_runtime_reconfiguration = {CONF_UPDATE_INTERVAL: 600}

    rr.apply_pending_runtime_reconfiguration(coord)

    assert coord._update_interval_s == 600
    assert coord.update_interval == timedelta(seconds=600)
    coord._build_controller.assert_not_called()


def test_apply_pending_runtime_reconfiguration_rebuilds_when_horizon_queued():
    coord = _reconfig_coord()
    coord._horizon = 6
    coord._pending_runtime_reconfiguration = {CONF_HORIZON: 8}

    rr.apply_pending_runtime_reconfiguration(coord)

    assert coord._horizon == 8
    coord._build_controller.assert_called_once()


def test_apply_pending_runtime_reconfiguration_updates_history_retention():
    coord = _reconfig_coord()
    coord._pending_runtime_reconfiguration = {
        CONF_IDENTIFICATION_HISTORY_DAYS: 30,
    }

    rr.apply_pending_runtime_reconfiguration(coord)

    coord.id_history_store.update_retention_days.assert_called_once_with(30)
    assert coord._identification_history_days == 30


def test_apply_pending_runtime_reconfiguration_noop_when_empty():
    coord = _reconfig_coord()

    rr.apply_pending_runtime_reconfiguration(coord)

    coord._build_controller.assert_not_called()


def test_apply_tuning_updates_applies_immediately():
    coord = _reconfig_coord()

    rr.apply_tuning_updates(coord, {CONF_TRACKING_WEIGHT: 4.0, CONF_SIGMA_W: 0.15})

    assert coord._tracking_weight == 4.0
    assert coord._sigma_w == 0.15
    assert coord._pending_runtime_reconfiguration == {}
    coord._build_controller.assert_called_once()
