"""Tests for the extracted weather helpers (S3).

The functions in ``heatingassistant.engine.weather`` used to live
inline in ``coordinator.py``.  The behavior is unchanged — these tests pin
down the contract so future tweaks don't regress it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from heatingassistant.engine import weather as w


# ── Coercion helpers ──────────────────────────────────────────────────────


def test_coerce_float_handles_strings_and_none():
    assert w.coerce_float("3.14") == pytest.approx(3.14)
    assert w.coerce_float(42) == 42.0
    assert w.coerce_float(None) is None
    assert w.coerce_float("abc") is None


def test_coerce_cloud_cover_percent_clamps_to_unit_interval():
    assert w.coerce_cloud_cover_percent(0) == pytest.approx(0.0)
    assert w.coerce_cloud_cover_percent(50) == pytest.approx(0.5)
    assert w.coerce_cloud_cover_percent(100) == pytest.approx(1.0)
    assert w.coerce_cloud_cover_percent(-1) == pytest.approx(0.0)
    assert w.coerce_cloud_cover_percent(150) == pytest.approx(1.0)
    assert w.coerce_cloud_cover_percent(None) is None
    assert w.coerce_cloud_cover_percent("nope") is None


def test_condition_table_covers_standard_ha_conditions():
    assert w.CONDITION_CLOUD_COVER["sunny"] == 0.0
    assert w.CONDITION_CLOUD_COVER["cloudy"] >= 0.5
    assert w.CONDITION_CLOUD_COVER["rainy"] == pytest.approx(0.95)


# ── Interpolation ─────────────────────────────────────────────────────────


def test_interpolate_clamps_outside_range():
    entries = [(0.0, 10.0), (100.0, 20.0)]
    assert w.interpolate_forecast(entries, -1.0) == 10.0
    assert w.interpolate_forecast(entries, 200.0) == 20.0


def test_interpolate_linearly_between_entries():
    entries = [(0.0, 10.0), (100.0, 20.0)]
    assert w.interpolate_forecast(entries, 50.0) == pytest.approx(15.0)
    assert w.interpolate_forecast(entries, 25.0) == pytest.approx(12.5)


def test_interpolate_handles_coincident_timestamps():
    """Two entries with the same ts must not crash with a divide-by-zero."""
    entries = [(0.0, 5.0), (0.0, 5.0), (100.0, 15.0)]
    # At the duplicate ts, return v0 of the matching segment.
    assert w.interpolate_forecast(entries, 0.0) == 5.0


# ── parse_forecast_field / parse_temperature_forecast / parse_cloud_forecast


def _temp_entries():
    return [
        {"datetime": "2026-05-16T00:00:00+00:00", "temperature": 10.0},
        {"datetime": "2026-05-16T01:00:00+00:00", "temperature": 12.0},
        {"datetime": "2026-05-16T02:00:00+00:00", "temperature": 14.0},
    ]


def test_parse_temperature_forecast_aligns_to_mpc_steps():
    now = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    result = w.parse_temperature_forecast(
        _temp_entries(), horizon=4, dt=900.0, now=now
    )
    assert result is not None
    assert len(result) == 4
    # First step is +900s from now, interpolated between t=0h (10°C) and t=1h (12°C).
    assert result[0] == pytest.approx(10.5, abs=1e-6)
    # Step 4 is t=+3600s exactly, hitting the second entry.
    assert result[3] == pytest.approx(12.0, abs=1e-6)


def test_parse_temperature_forecast_returns_none_when_no_valid_entries():
    bad = [
        {"datetime": "not-a-date", "temperature": 10.0},
        {"datetime": "2026-05-16T00:00:00+00:00"},  # missing field
    ]
    result = w.parse_temperature_forecast(bad, horizon=4, dt=900.0)
    assert result is None


def test_parse_temperature_forecast_accepts_zulu_suffix():
    """HA emits ISO-8601 with a trailing 'Z' instead of '+00:00'."""
    now = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    entries = [
        {"datetime": "2026-05-16T00:00:00Z", "temperature": 10.0},
        {"datetime": "2026-05-16T01:00:00Z", "temperature": 20.0},
    ]
    result = w.parse_temperature_forecast(entries, horizon=2, dt=1800.0, now=now)
    assert result is not None
    assert result[0] == pytest.approx(15.0, abs=1e-6)


def test_parse_cloud_forecast_uses_percentage_when_available():
    now = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    entries = [
        {"datetime": "2026-05-16T00:00:00+00:00", "cloud_coverage": 25.0},
        {"datetime": "2026-05-16T01:00:00+00:00", "cloud_coverage": 75.0},
    ]
    result = w.parse_cloud_forecast(entries, horizon=2, dt=1800.0, now=now)
    assert result is not None
    assert result[0] == pytest.approx(0.5, abs=1e-6)


def test_parse_cloud_forecast_falls_back_to_condition_field():
    now = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
    entries = [
        {"datetime": "2026-05-16T00:00:00+00:00", "condition": "sunny"},
        {"datetime": "2026-05-16T01:00:00+00:00", "condition": "cloudy"},
    ]
    result = w.parse_cloud_forecast(entries, horizon=2, dt=1800.0, now=now)
    assert result is not None
    # First step (+1800s) sits halfway between sunny (0.0) and cloudy (0.85).
    assert result[0] == pytest.approx(0.425, abs=1e-6)


# ── Entity readers ────────────────────────────────────────────────────────


def _state(state_value, attributes=None):
    return SimpleNamespace(state=state_value, attributes=attributes or {})


def test_read_outdoor_temp_prefers_dedicated_entity():
    hass = MagicMock()
    hass.states.get.side_effect = lambda eid: {
        "sensor.outdoor": _state("3.2"),
        "weather.home": _state("sunny", {"temperature": 99.9}),
    }[eid]
    assert w.read_outdoor_temp(hass, "sensor.outdoor", "weather.home") == pytest.approx(3.2)


def test_read_outdoor_temp_falls_back_to_weather_attribute():
    hass = MagicMock()
    hass.states.get.side_effect = lambda eid: {
        "sensor.outdoor": _state("unknown"),
        "weather.home": _state("sunny", {"temperature": 4.1}),
    }[eid]
    assert w.read_outdoor_temp(hass, "sensor.outdoor", "weather.home") == pytest.approx(4.1)


def test_read_outdoor_temp_returns_none_when_all_unavailable():
    """Unavailable readings return None so the coordinator can skip MPC."""
    hass = MagicMock()
    hass.states.get.return_value = None
    assert w.read_outdoor_temp(hass, "sensor.outdoor", "weather.home") is None


def test_read_cloud_cover_now_returns_none_without_entity():
    assert w.read_cloud_cover_now(MagicMock(), None) is None


def test_read_cloud_cover_now_prefers_percentage_attribute():
    hass = MagicMock()
    hass.states.get.return_value = _state("partlycloudy", {"cloud_coverage": 60})
    assert w.read_cloud_cover_now(hass, "weather.home") == pytest.approx(0.6)


def test_read_cloud_cover_now_falls_back_to_condition_string():
    hass = MagicMock()
    hass.states.get.return_value = _state("sunny", {})
    assert w.read_cloud_cover_now(hass, "weather.home") == 0.0


def test_read_cloud_cover_now_returns_none_when_unavailable():
    hass = MagicMock()
    hass.states.get.return_value = _state("unavailable", {})
    assert w.read_cloud_cover_now(hass, "weather.home") is None


# ── fetch_forecast_entries (async) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_returns_none_none_without_entity():
    data, err = await w.fetch_forecast_entries(MagicMock(), None)
    assert (data, err) == (None, None)


@pytest.mark.asyncio
async def test_fetch_uses_get_forecasts_service_when_available():
    hass = MagicMock()
    hass.services.has_service.return_value = True
    payload = [{"datetime": "2026-05-16T00:00:00Z", "temperature": 5.0}]
    hass.services.async_call = AsyncMock(
        return_value={"weather.home": {"forecast": payload}}
    )
    data, err = await w.fetch_forecast_entries(hass, "weather.home")
    assert data == payload
    assert err is None


@pytest.mark.asyncio
async def test_fetch_falls_back_to_state_attribute_when_service_fails():
    hass = MagicMock()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock(side_effect=RuntimeError("upstream"))
    payload = [{"datetime": "2026-05-16T00:00:00Z", "temperature": 5.0}]
    hass.states.get.return_value = _state("sunny", {"forecast": payload})

    data, err = await w.fetch_forecast_entries(hass, "weather.home")
    assert data == payload
    # The successful fallback returns no error message.
    assert err is None


@pytest.mark.asyncio
async def test_fetch_reports_error_when_state_missing():
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.states.get.return_value = None

    data, err = await w.fetch_forecast_entries(hass, "weather.home")
    assert data is None
    assert err is not None
    assert "weather.home" in err


@pytest.mark.asyncio
async def test_fetch_reports_error_when_no_forecast_data():
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.states.get.return_value = _state("sunny", {})

    data, err = await w.fetch_forecast_entries(hass, "weather.home")
    assert data is None
    assert err == "no forecast data on weather entity"
