"""Unit tests for the solar-forecast GHI-derivation module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import math
import pytest

from custom_components.heating_assistant import solar_forecast as sf
from custom_components.heating_assistant.solar_model import (
    ghi_to_dni_dhi,
    solar_angles,
    transpose_to_surface,
)


# A clear summer midday at a mid-latitude site.
LAT, LON = 55.0, 12.0
NOON = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)
PLANE_TILT, PLANE_AZ = 35.0, 180.0


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _poa_on_plane(ghi: float, dt: datetime, tilt: float, az: float) -> float:
    """Model the POA a plane would see for a given GHI (for round-trip tests)."""
    alt, sun_az = solar_angles(dt, LAT, LON)
    n = dt.timetuple().tm_yday
    dni, dhi = ghi_to_dni_dhi(ghi, alt, n)
    return transpose_to_surface(dni, dhi, alt, sun_az, tilt, az)


# ---------------------------------------------------------------------------
# Parsers + kind detection
# ---------------------------------------------------------------------------


class TestParsers:
    def test_forecast_solar_watts_dict(self):
        attrs = {"watts": {_iso(NOON): 3000, _iso(NOON + timedelta(hours=1)): 3200}}
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "forecast_solar"
        assert series[0][1] == 3000.0 and series[1][1] == 3200.0

    def test_solcast_list_kw_to_w(self):
        attrs = {"detailedForecast": [{"period_start": _iso(NOON), "pv_estimate": 3.0}]}
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "solcast"
        assert series[0][1] == 3000.0  # kW → W

    def test_generic_ghi_series(self):
        attrs = {"forecast": [{"datetime": _iso(NOON), "ghi": 540}]}
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes=attrs))
        assert provider == "generic"
        assert series[0][1] == 540.0

    def test_no_forecast_returns_none(self):
        series, provider = sf.parse_pv_power_forecast(SimpleNamespace(attributes={}))
        assert series is None and provider == "none"

    def test_detect_irradiance_unit(self):
        s = SimpleNamespace(attributes={"unit_of_measurement": "W/m²"})
        assert sf.detect_value_kind(s) == "irradiance"

    def test_detect_power_default(self):
        s = SimpleNamespace(attributes={"unit_of_measurement": "W"})
        assert sf.detect_value_kind(s) == "power"
        assert sf.detect_value_kind(SimpleNamespace(attributes={})) == "power"


# ---------------------------------------------------------------------------
# read_value_now
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


def test_read_value_now_from_state():
    state = SimpleNamespace(state="2500", attributes={})
    assert sf.read_value_now(_FakeHass(state), "sensor.pv", now=NOON) == 2500.0


def test_read_value_now_none_when_unconfigured():
    assert sf.read_value_now(_FakeHass(None), None) is None


# ---------------------------------------------------------------------------
# POA → GHI inversion
# ---------------------------------------------------------------------------


class TestPoaToGhi:
    def test_round_trip_recovers_ghi(self):
        target = 600.0
        poa = _poa_on_plane(target, NOON, PLANE_TILT, PLANE_AZ)
        recovered = sf.poa_to_ghi(poa, NOON, LAT, LON, PLANE_TILT, PLANE_AZ)
        assert recovered == pytest.approx(target, rel=0.02)

    def test_zero_at_night(self):
        midnight = datetime(2024, 12, 21, 0, 0, tzinfo=timezone.utc)
        assert sf.poa_to_ghi(100.0, midnight, 60.0, 12.0, 35.0, 180.0) == 0.0


# ---------------------------------------------------------------------------
# compute_ghi_series
# ---------------------------------------------------------------------------


class TestGhiSeries:
    def _power_series(self, target_ghi, peak):
        """PV-power series that corresponds to ``target_ghi`` on the panel plane."""
        out = []
        for h in range(0, 6):
            ts = NOON + timedelta(hours=h)
            poa = _poa_on_plane(target_ghi, ts, PLANE_TILT, PLANE_AZ)
            out.append((ts.timestamp(), peak * poa / 1000.0))  # P = peak·POA/1000
        return out

    def test_irradiance_passthrough(self):
        series = [
            ((NOON + timedelta(hours=h)).timestamp(), 500.0) for h in range(6)
        ]
        result, ghi_now = sf.compute_ghi_series(
            series, 500.0, "irradiance", horizon=5, dt=3600.0,
            latitude=LAT, longitude=LON, now=NOON,
        )
        defined = [v for v in result if v is not None]
        assert defined
        for v in defined:
            assert v == pytest.approx(500.0, rel=1e-9)
        assert ghi_now == pytest.approx(500.0)

    def test_power_with_peak_and_plane_recovers_ghi(self):
        peak = 5000.0
        target = 600.0
        series = self._power_series(target, peak)
        result, ghi_now = sf.compute_ghi_series(
            series, None, "power", horizon=5, dt=3600.0,
            latitude=LAT, longitude=LON, now=NOON,
            plane_tilt=PLANE_TILT, plane_azimuth=PLANE_AZ, peak_power=peak,
        )
        defined = [v for v in result if v is not None]
        assert defined
        for v in defined:
            assert v == pytest.approx(target, rel=0.03)

    def test_power_without_peak_returns_none(self):
        series = [(NOON.timestamp(), 3000.0)]
        result, ghi_now = sf.compute_ghi_series(
            series, 3000.0, "power", horizon=3, dt=3600.0,
            latitude=LAT, longitude=LON, now=NOON,
        )
        assert result is None and ghi_now is None

    def test_empty_returns_none(self):
        result, ghi_now = sf.compute_ghi_series(
            [], None, "irradiance", horizon=3, dt=3600.0,
            latitude=LAT, longitude=LON, now=NOON,
        )
        assert result is None and ghi_now is None

    def test_steps_outside_coverage_are_none(self):
        # One forecast point only at NOON; later horizon steps are uncovered.
        series = [(NOON.timestamp(), 500.0)]
        result, _ = sf.compute_ghi_series(
            series, None, "irradiance", horizon=5, dt=3600.0,
            latitude=LAT, longitude=LON, now=NOON,
        )
        # Steps are at NOON+1h..NOON+5h, all past the single NOON point → None.
        assert all(v is None for v in result)


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
    fresh = SimpleNamespace(last_updated=NOON)
    assert sf.is_stale(fresh, NOON + timedelta(minutes=10)) is False
    assert sf.is_stale(fresh, NOON + timedelta(hours=5)) is True
