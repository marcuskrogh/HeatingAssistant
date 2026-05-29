"""Unit tests for the solar-radiation (irradiance) forecast module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.heating_assistant import solar_forecast as sf


NOW = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Parsing an irradiance forecast series
# ---------------------------------------------------------------------------


class TestParseIrradianceForecast:
    def test_list_with_ghi_key(self):
        attrs = {"forecast": [
            {"datetime": _iso(NOW), "ghi": 540},
            {"datetime": _iso(NOW + timedelta(hours=1)), "ghi": 560},
        ]}
        series = sf.parse_irradiance_forecast(SimpleNamespace(attributes=attrs))
        assert series[0][1] == 540.0 and series[1][1] == 560.0
        assert series[0][0] < series[1][0]

    def test_shortwave_radiation_dict(self):
        attrs = {"shortwave_radiation": {
            _iso(NOW): 480,
            _iso(NOW + timedelta(hours=1)): 500,
        }}
        series = sf.parse_irradiance_forecast(SimpleNamespace(attributes=attrs))
        assert {round(v) for _, v in series} == {480, 500}

    def test_negative_values_clamped(self):
        attrs = {"irradiance": [{"datetime": _iso(NOW), "irradiance": -5}]}
        series = sf.parse_irradiance_forecast(SimpleNamespace(attributes=attrs))
        assert series[0][1] == 0.0

    def test_no_series_returns_none(self):
        assert sf.parse_irradiance_forecast(SimpleNamespace(attributes={})) is None


# ---------------------------------------------------------------------------
# read_irradiance_now
# ---------------------------------------------------------------------------


class _FakeHass:
    def __init__(self, state):
        self._state = state

    class _States:
        def __init__(self, state):
            self._state = state

        def get(self, _entity):
            return self._state

    @property
    def states(self):
        return _FakeHass._States(self._state)


def test_read_irradiance_now_from_state():
    state = SimpleNamespace(state="420", attributes={})
    assert sf.read_irradiance_now(_FakeHass(state), "sensor.ghi", now=NOW) == 420.0


def test_read_irradiance_now_clamps_negative():
    state = SimpleNamespace(state="-10", attributes={})
    assert sf.read_irradiance_now(_FakeHass(state), "sensor.ghi", now=NOW) == 0.0


def test_read_irradiance_now_none_when_unconfigured():
    assert sf.read_irradiance_now(_FakeHass(None), None) is None


def test_read_irradiance_now_interpolates_series_when_state_nonnumeric():
    attrs = {"forecast": [
        {"datetime": _iso(NOW - timedelta(hours=1)), "ghi": 100},
        {"datetime": _iso(NOW + timedelta(hours=1)), "ghi": 300},
    ]}
    state = SimpleNamespace(state="sunny", attributes=attrs)
    val = sf.read_irradiance_now(_FakeHass(state), "sensor.ghi", now=NOW)
    assert val == pytest.approx(200.0, rel=1e-6)  # midpoint interpolation


# ---------------------------------------------------------------------------
# compute_ghi_series
# ---------------------------------------------------------------------------


class TestComputeGhiSeries:
    def test_aligns_to_steps(self):
        series = [
            ((NOW + timedelta(hours=h)).timestamp(), 100.0 * h) for h in range(0, 6)
        ]
        out, ghi_now = sf.compute_ghi_series(series, None, horizon=5, dt=3600.0, now=NOW)
        # Steps at NOW+1h..NOW+5h → values 100, 200, ... 500.
        assert out == pytest.approx([100.0, 200.0, 300.0, 400.0, 500.0])

    def test_current_value_anchors(self):
        out, ghi_now = sf.compute_ghi_series(
            None, 450.0, horizon=3, dt=3600.0, now=NOW,
        )
        assert ghi_now == 450.0
        # Only the now-anchor exists → all future steps are outside coverage.
        assert all(v is None for v in out)

    def test_steps_outside_coverage_are_none(self):
        series = [(NOW.timestamp(), 500.0)]
        out, _ = sf.compute_ghi_series(series, None, horizon=4, dt=3600.0, now=NOW)
        assert all(v is None for v in out)

    def test_empty_returns_none(self):
        out, ghi_now = sf.compute_ghi_series(None, None, horizon=3, dt=3600.0, now=NOW)
        assert out is None and ghi_now is None


# ---------------------------------------------------------------------------
# select_ghi_for_step
# ---------------------------------------------------------------------------


class TestSelectGhi:
    def test_in_range(self):
        assert sf.select_ghi_for_step([100.0, 200.0, 300.0], 1) == 200.0

    def test_none_entry_falls_back(self):
        assert sf.select_ghi_for_step([100.0, None, 300.0], 1, fallback=50.0) == 50.0

    def test_past_end_falls_back_not_persisted(self):
        # GHI must NOT persist past coverage (would leak daytime into night).
        assert sf.select_ghi_for_step([100.0, 200.0], 5, fallback=None) is None

    def test_empty_returns_fallback(self):
        assert sf.select_ghi_for_step(None, 0, fallback=42.0) == 42.0


def test_is_stale():
    fresh = SimpleNamespace(last_updated=NOW)
    assert sf.is_stale(fresh, NOW + timedelta(minutes=10)) is False
    assert sf.is_stale(fresh, NOW + timedelta(hours=5)) is True
