"""Coordinator-level tests for the solar-forecast (GHI) integration.

Builds a bare coordinator via ``object.__new__`` (same pattern as
``test_coordinator_apply_actions.py``) and exercises ``_read_ghi`` and
``_room_solar_gain`` directly, covering the graceful-fallback paths that keep
the analytical model in charge when the forecast entity is missing / stale /
unparseable / lacks a peak power.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.thermal_model import HouseModel, Room, Window


LAT, LON = 55.0, 12.0
NOW = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)


def _make_coordinator(
    solar_entity,
    entity_states,
    *,
    rooms=None,
    peak_power=None,
    plane_tilt=None,
    plane_azimuth=None,
):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._solar_forecast_entity = solar_entity
    coord._pv_plane_tilt = plane_tilt
    coord._pv_plane_azimuth = plane_azimuth
    coord._pv_peak_power = peak_power
    coord._latitude = LAT
    coord._longitude = LON
    coord._horizon = 4
    coord._update_interval = 3600  # backs the read-only ``dt`` property
    coord.now_utc = NOW
    # Failure-tracking state used by the record helpers.
    coord.solar_fc_last_error = None
    coord.solar_fc_last_error_at = None
    coord.solar_fc_last_success_at = None
    coord.solar_fc_consecutive_failures = 0
    coord._solar_provider = "none"
    coord._weather_warn_thresholds = (1, 2, 5, 10)

    rooms = rooms or [
        Room(name="lr", thermal_mass=5e6, r_external=0.05, temperature=20.0,
             windows=[Window(area=2.0, orientation=180.0, tilt=90.0)]),
    ]
    coord.model = HouseModel(rooms)

    hass = MagicMock()

    def _get_state(entity_id):
        if entity_id in entity_states:
            raw = entity_states[entity_id]
            s = SimpleNamespace(
                state=raw.get("state", "unknown"),
                attributes=raw.get("attributes", {}),
                last_updated=raw.get("last_updated", NOW),
            )
            return s
        return None

    hass.states.get = _get_state
    coord.hass = hass
    return coord


def _ghi_forecast_attrs(ghi=500.0):
    """Generic GHI forecast attribute series (W/m²) over the next hours."""
    fc = [
        {"datetime": (NOW + timedelta(hours=h)).isoformat(), "ghi": ghi}
        for h in range(0, 6)
    ]
    return {"forecast": fc, "unit_of_measurement": "W/m²"}


class TestReadGhi:
    def test_no_entity_returns_none(self):
        coord = _make_coordinator(None, {})
        now_v, fc = coord._read_ghi(NOW)
        assert now_v is None and fc is None

    def test_unavailable_entity_records_failure(self):
        coord = _make_coordinator(
            "sensor.pv", {"sensor.pv": {"state": "unavailable", "attributes": {}}},
        )
        now_v, fc = coord._read_ghi(NOW)
        assert now_v is None and fc is None
        assert coord.solar_fc_consecutive_failures == 1

    def test_stale_entity_falls_back(self):
        coord = _make_coordinator(
            "sensor.ghi",
            {"sensor.ghi": {
                "state": "500",
                "attributes": _ghi_forecast_attrs(),
                "last_updated": NOW - timedelta(hours=6),
            }},
        )
        now_v, fc = coord._read_ghi(NOW)
        assert now_v is None and fc is None
        assert coord.solar_fc_last_error == "forecast stale"

    def test_power_without_peak_falls_back(self):
        # A PV-power sensor with no configured peak power can't be scaled to
        # absolute irradiance → fall back to the analytical model.
        attrs = {"watts": {(NOW + timedelta(hours=h)).isoformat(): 3000 for h in range(5)}}
        coord = _make_coordinator(
            "sensor.pv", {"sensor.pv": {"state": "3000", "attributes": attrs}},
        )
        now_v, fc = coord._read_ghi(NOW)
        assert now_v is None and fc is None
        assert "peak" in (coord.solar_fc_last_error or "")

    def test_irradiance_sensor_drives_ghi(self):
        coord = _make_coordinator(
            "sensor.ghi",
            {"sensor.ghi": {"state": "500", "attributes": _ghi_forecast_attrs(500.0)}},
        )
        now_v, fc = coord._read_ghi(NOW)
        assert fc is not None
        assert coord.solar_fc_consecutive_failures == 0
        defined = [v for v in fc if v is not None]
        assert defined
        for v in defined:
            assert v == pytest.approx(500.0, rel=1e-6)

    def test_power_with_peak_drives_ghi(self):
        # Forecast.Solar-style watts; with peak power + plane geometry the
        # watts deconvolve to a positive GHI series.
        watts = {(NOW + timedelta(hours=h)).isoformat(): 2500 for h in range(5)}
        coord = _make_coordinator(
            "sensor.pv",
            {"sensor.pv": {"state": "2500", "attributes": {"watts": watts}}},
            peak_power=5000.0, plane_tilt=35.0, plane_azimuth=180.0,
        )
        now_v, fc = coord._read_ghi(NOW)
        assert fc is not None
        assert coord._solar_provider == "forecast_solar"
        defined = [v for v in fc if v is not None]
        assert defined and all(v > 0.0 for v in defined)


class TestRoomSolarGain:
    def test_windowed_room_ghi_overrides_cloud(self):
        coord = _make_coordinator("sensor.ghi", {})
        with_cloud = coord._room_solar_gain("lr", NOW, 1.0, 500.0)
        no_cloud = coord._room_solar_gain("lr", NOW, None, 500.0)
        assert no_cloud > 0.0
        assert with_cloud == pytest.approx(no_cloud, rel=1e-12)

    def test_windowless_room_uses_exposure(self):
        rooms = [
            Room(name="lr", thermal_mass=5e6, r_external=0.05, temperature=20.0,
                 solar_exposure_aperture=3.0, solar_facing=180.0),
        ]
        coord = _make_coordinator("sensor.ghi", {}, rooms=rooms)
        gain = coord._room_solar_gain("lr", NOW, None, 600.0)
        assert gain > 0.0

    def test_missing_entity_gain_matches_cloud_baseline(self):
        # With no GHI, the gain equals the plain cloud-cover path.
        coord = _make_coordinator(None, {})
        from custom_components.heating_assistant.solar_model import room_solar_gains
        baseline = room_solar_gains(
            coord.model.rooms["lr"].windows, NOW, LAT, LON, cloud_cover=0.5,
        )
        gain = coord._room_solar_gain("lr", NOW, 0.5, None)
        assert gain == pytest.approx(baseline, rel=1e-12)
