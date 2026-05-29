"""Unit tests for the solar-forecast clearness-index module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.heating_assistant import solar_forecast as sf
from custom_components.heating_assistant.solar_model import clear_sky_plane_poa


# A clear summer midday at a mid-latitude site, so the modelled clear-sky POA
# is comfortably above the night floor for the timestamps used below.
LAT, LON = 55.0, 12.0
NOON = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)
PLANE_TILT, PLANE_AZ = 35.0, 180.0


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


class TestParsers:
    def test_forecast_solar_watts_dict(self):
        attrs = {
            "watts": {
                _iso(NOON): 3000,
                _iso(NOON + timedelta(hours=1)): 3200,
            }
        }
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "forecast_solar"
        assert len(series) == 2
        # sorted ascending by epoch; values preserved as W
        assert series[0][1] == 3000.0
        assert series[1][1] == 3200.0
        assert series[0][0] < series[1][0]

    def test_solcast_list_kw_to_w(self):
        attrs = {
            "detailedForecast": [
                {"period_start": _iso(NOON), "pv_estimate": 3.0},
                {"period_start": _iso(NOON + timedelta(minutes=30)), "pv_estimate": 2.5},
            ]
        }
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "solcast"
        # kW → W
        assert series[0][1] == 3000.0
        assert series[1][1] == 2500.0

    def test_generic_list_power(self):
        attrs = {
            "forecast": [
                {"datetime": _iso(NOON), "power": 1500},
                {"datetime": _iso(NOON + timedelta(hours=1)), "power": 1700},
            ]
        }
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "generic"
        assert series[0][1] == 1500.0

    def test_generic_kw_unit_hint(self):
        attrs = {
            "power_forecast": [{"period_start": _iso(NOON), "pv_estimate": 2.0}],
            "power_forecast_unit": "kW",
        }
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "generic"
        assert series[0][1] == 2000.0

    def test_no_forecast_returns_none(self):
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes={}))
        assert series is None
        assert provider == "none"


# ---------------------------------------------------------------------------
# read_pv_power_now
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


def test_read_pv_power_now_from_state():
    state = SimpleNamespace(state="2500", attributes={})
    val = sf.read_pv_power_now(_FakeHass(state), "sensor.pv", now=NOON)
    assert val == 2500.0


def test_read_pv_power_now_none_when_unconfigured():
    assert sf.read_pv_power_now(_FakeHass(None), None) is None


# ---------------------------------------------------------------------------
# compute_clearness_series
# ---------------------------------------------------------------------------


class TestClearnessSeries:
    def _daytime_series(self, factor, tilt=PLANE_TILT, az=PLANE_AZ):
        """PV series == factor * modelled clear-sky POA for the given plane."""
        out = []
        for h in range(0, 6):
            ts = (NOON + timedelta(hours=h)).timestamp()
            poa = clear_sky_plane_poa(
                datetime.fromtimestamp(ts, tz=timezone.utc), LAT, LON, tilt, az
            )
            out.append((ts, factor * poa))
        return out

    def test_physical_path_recovers_factor(self):
        # With geometry + peak power, index = power / (peak * POA / 1000).
        peak = 5000.0
        series = []
        for h in range(0, 6):
            ts = (NOON + timedelta(hours=h)).timestamp()
            poa = clear_sky_plane_poa(
                datetime.fromtimestamp(ts, tz=timezone.utc), LAT, LON, PLANE_TILT, PLANE_AZ
            )
            series.append((ts, 0.8 * peak * poa / 1000.0))
        result, now_idx = sf.compute_clearness_series(
            series, horizon=5, dt=3600.0, latitude=LAT, longitude=LON, now=NOON,
            plane_tilt=PLANE_TILT, plane_azimuth=PLANE_AZ, pv_peak_power=peak,
        )
        defined = [r for r in result if r is not None]
        assert defined, "expected some daytime steps"
        for r in defined:
            assert r == pytest.approx(0.8, abs=1e-6)

    def test_autocalibrated_path_peaks_near_one(self):
        # No geometry / no peak → percentile auto-calibration against the
        # generic (tilt 30, az 180) reference plane.  A series proportional to
        # that same plane's clear-sky shape normalises to ~1 across the horizon.
        series = self._daytime_series(0.5, tilt=30.0, az=180.0)
        result, _ = sf.compute_clearness_series(
            series, horizon=5, dt=3600.0, latitude=LAT, longitude=LON, now=NOON,
        )
        defined = [r for r in result if r is not None]
        assert defined
        for r in defined:
            assert r == pytest.approx(1.0, abs=1e-6)

    def test_clamped_to_max(self):
        # A spike well above the clear-sky reference is clamped to CLEARNESS_MAX.
        peak = 5000.0
        ts = NOON.timestamp()
        poa = clear_sky_plane_poa(NOON, LAT, LON, PLANE_TILT, PLANE_AZ)
        series = [(ts, 10.0 * peak * poa / 1000.0)]  # 10x over-irradiance
        result, now_idx = sf.compute_clearness_series(
            series, horizon=1, dt=3600.0, latitude=LAT, longitude=LON, now=NOON,
            plane_tilt=PLANE_TILT, plane_azimuth=PLANE_AZ, pv_peak_power=peak,
            pv_power_now=10.0 * peak * poa / 1000.0,
        )
        assert now_idx == pytest.approx(sf.CLEARNESS_MAX)

    def test_night_steps_are_none(self):
        # Midwinter midnight at a high latitude → sun well below horizon.
        midnight = datetime(2024, 12, 21, 0, 0, tzinfo=timezone.utc)
        ts = midnight.timestamp()
        series = [(ts, 0.0)]
        result, now_idx = sf.compute_clearness_series(
            series, horizon=3, dt=3600.0, latitude=60.0, longitude=12.0, now=midnight,
        )
        # No daytime reference anywhere → undefined everywhere.
        assert all(r is None for r in (result or []))

    def test_empty_input_returns_none(self):
        result, now_idx = sf.compute_clearness_series(
            [], horizon=3, dt=3600.0, latitude=LAT, longitude=LON, now=NOON,
        )
        assert result is None and now_idx is None


# ---------------------------------------------------------------------------
# select_clearness_for_step
# ---------------------------------------------------------------------------


class TestSelectClearness:
    def test_in_range(self):
        assert sf.select_clearness_for_step([0.5, 0.6, 0.7], 1) == 0.6

    def test_none_entry_falls_back(self):
        assert sf.select_clearness_for_step([0.5, None, 0.7], 1, fallback=0.3) == 0.3

    def test_persistence_past_end(self):
        assert sf.select_clearness_for_step([0.5, 0.9], 5) == 0.9

    def test_persistence_skips_trailing_none(self):
        assert sf.select_clearness_for_step([0.5, 0.9, None], 5) == 0.9

    def test_empty_returns_fallback(self):
        assert sf.select_clearness_for_step(None, 0, fallback=0.42) == 0.42


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def test_is_stale():
    fresh = SimpleNamespace(last_updated=NOON)
    assert sf.is_stale(fresh, NOON + timedelta(minutes=10)) is False
    assert sf.is_stale(fresh, NOON + timedelta(hours=5)) is True
