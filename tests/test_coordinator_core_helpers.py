"""Unit tests for thin coordinator core helpers not covered by mpc_cycle tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.heating_assistant.const import (
    CONF_PLOT_FORECAST_HOURS,
    CONF_TRACKING_WEIGHT,
)
from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from tests.helpers.coordinator_stubs import make_minimal_coordinator


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
