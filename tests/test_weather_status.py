"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.sensor import WeatherForecastStatusSensor


def _bare_coordinator(weather_entity: str = "weather.home") -> HeatingAssistantCoordinator:
    """Bypass __init__ and seed only the fields the methods under test touch."""
    coord = HeatingAssistantCoordinator.__new__(HeatingAssistantCoordinator)
    coord._weather_entity = weather_entity
    coord.weather_last_error = None
    coord.weather_last_error_at = None
    coord.weather_last_success_at = None
    coord.weather_consecutive_failures = 0
    coord._weather_warn_thresholds = (1, 2, 5, 10)
    coord.now_utc = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    return coord


# ── Health-counter unit tests ─────────────────────────────────────────────


def test_record_weather_success_clears_failure_state():
    coord = _bare_coordinator()
    coord.weather_consecutive_failures = 3
    coord.weather_last_error = "boom"
    coord.weather_last_error_at = coord.now_utc

    coord._record_weather_success()

    assert coord.weather_consecutive_failures == 0
    assert coord.weather_last_error is None
    assert coord.weather_last_error_at is None
    assert coord.weather_last_success_at == coord.now_utc


def test_record_weather_failure_increments_counter_and_stamps_error():
    coord = _bare_coordinator()

    coord._record_weather_failure("no data")

    assert coord.weather_consecutive_failures == 1
    assert coord.weather_last_error == "no data"
    assert coord.weather_last_error_at == coord.now_utc


def test_record_weather_failure_throttles_warn_log(caplog: pytest.LogCaptureFixture):
    """The WARN log fires only on threshold-crossing failures."""
    coord = _bare_coordinator()
    coord._weather_warn_thresholds = (1, 5)

    with caplog.at_level(logging.WARNING):
        for _ in range(6):
            coord._record_weather_failure("repeat")

    # Failures 1 and 5 cross the threshold; 2/3/4/6 must not log WARN.
    warn_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warn_messages) == 2
    assert "failure #1" in warn_messages[0]
    assert "failure #5" in warn_messages[1]


def test_recovery_logs_info(caplog: pytest.LogCaptureFixture):
    coord = _bare_coordinator()
    coord.weather_consecutive_failures = 4

    with caplog.at_level(logging.INFO):
        coord._record_weather_success()

    info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("recovered" in m and "4 consecutive failures" in m for m in info_messages)


# ── Integration with _async_get_forecast_entries ──────────────────────────


@pytest.mark.asyncio
async def test_forecast_fetch_records_success_and_returns_data():
    coord = _bare_coordinator()
    forecast = [{"temperature": 5.0}, {"temperature": 6.0}]
    coord.hass = MagicMock()
    coord.hass.services.has_service.return_value = True
    coord.hass.services.async_call = AsyncMock(
        return_value={"weather.home": {"forecast": forecast}}
    )

    result = await HeatingAssistantCoordinator._async_get_forecast_entries(coord)

    assert result == forecast
    assert coord.weather_consecutive_failures == 0
    assert coord.weather_last_success_at == coord.now_utc
    assert coord.weather_last_error is None


@pytest.mark.asyncio
async def test_forecast_fetch_records_failure_when_service_raises():
    coord = _bare_coordinator()
    coord.hass = MagicMock()
    coord.hass.services.has_service.return_value = True
    coord.hass.services.async_call = AsyncMock(side_effect=RuntimeError("upstream"))
    # Fallback to state attribute also yields nothing.
    coord.hass.states.get.return_value = None

    result = await HeatingAssistantCoordinator._async_get_forecast_entries(coord)

    assert result is None
    assert coord.weather_consecutive_failures == 1
    assert "RuntimeError" in (coord.weather_last_error or "")
    assert "upstream" in (coord.weather_last_error or "")


@pytest.mark.asyncio
async def test_forecast_fetch_no_weather_entity_is_silent():
    """No configured entity means the sensor reports 'disabled' — neither
    success nor failure should be recorded."""
    coord = _bare_coordinator(weather_entity="")
    coord.hass = MagicMock()

    result = await HeatingAssistantCoordinator._async_get_forecast_entries(coord)

    assert result is None
    assert coord.weather_consecutive_failures == 0
    assert coord.weather_last_error is None
    assert coord.weather_last_success_at is None


# ── Sensor surface tests ──────────────────────────────────────────────────


def test_weather_status_sensor_reports_disabled_without_entity():
    coord = SimpleNamespace(
        _weather_entity=None,
        weather_consecutive_failures=0,
        weather_last_error=None,
        weather_last_error_at=None,
        weather_last_success_at=None,
    )
    sensor = WeatherForecastStatusSensor(coord)
    assert sensor.native_value == "disabled"
    assert sensor.extra_state_attributes["weather_entity"] is None


def test_weather_status_sensor_reports_ok_after_success():
    now = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)
    coord = SimpleNamespace(
        _weather_entity="weather.home",
        weather_consecutive_failures=0,
        weather_last_error=None,
        weather_last_error_at=None,
        weather_last_success_at=now,
    )
    sensor = WeatherForecastStatusSensor(coord)
    assert sensor.native_value == "ok"
    assert sensor.extra_state_attributes["last_success_at"] == now.isoformat()
    assert sensor.extra_state_attributes["last_error"] is None


def test_weather_status_sensor_reports_failing_with_error_detail():
    now = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)
    coord = SimpleNamespace(
        _weather_entity="weather.home",
        weather_consecutive_failures=7,
        weather_last_error="no forecast data on weather entity",
        weather_last_error_at=now,
        weather_last_success_at=None,
    )
    sensor = WeatherForecastStatusSensor(coord)
    assert sensor.native_value == "failing"
    attrs = sensor.extra_state_attributes
    assert attrs["consecutive_failures"] == 7
    assert attrs["last_error"] == "no forecast data on weather entity"
    assert attrs["last_error_at"] == now.isoformat()
    assert attrs["weather_entity"] == "weather.home"
