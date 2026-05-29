"""Coordinator-level tests for the solar-forecast clearness integration.

Builds a bare coordinator via ``object.__new__`` (same pattern as
``test_coordinator_apply_actions.py``) and exercises ``_read_clearness`` and
``_room_solar_gain`` directly, covering the graceful-fallback paths that keep
the analytical model in charge when the forecast entity is missing / stale /
unparseable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.thermal_model import HouseModel, Room, Window
from custom_components.heating_assistant.solar_model import clear_sky_plane_poa


LAT, LON = 55.0, 12.0
NOW = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)


def _make_coordinator(solar_entity, entity_states, *, rooms=None):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._solar_forecast_entity = solar_entity
    coord._pv_plane_tilt = None
    coord._pv_plane_azimuth = None
    coord._pv_peak_power = None
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


def _watts_attr(factor=0.5):
    """Forecast.Solar-style watts dict tracking the generic clear-sky plane."""
    watts = {}
    for h in range(0, 5):
        ts = NOW + timedelta(hours=h)
        poa = clear_sky_plane_poa(ts, LAT, LON, 30.0, 180.0)
        watts[ts.isoformat()] = factor * poa
    return {"watts": watts}


class TestReadClearness:
    def test_no_entity_returns_none(self):
        coord = _make_coordinator(None, {})
        now_idx, fc = coord._read_clearness(NOW)
        assert now_idx is None and fc is None

    def test_unavailable_entity_records_failure(self):
        coord = _make_coordinator(
            "sensor.pv", {"sensor.pv": {"state": "unavailable", "attributes": {}}},
        )
        now_idx, fc = coord._read_clearness(NOW)
        assert now_idx is None and fc is None
        assert coord.solar_fc_consecutive_failures == 1

    def test_stale_entity_falls_back(self):
        coord = _make_coordinator(
            "sensor.pv",
            {"sensor.pv": {
                "state": "1500",
                "attributes": _watts_attr(),
                "last_updated": NOW - timedelta(hours=6),
            }},
        )
        now_idx, fc = coord._read_clearness(NOW)
        assert now_idx is None and fc is None
        assert coord.solar_fc_last_error == "forecast stale"

    def test_unparsable_falls_back(self):
        coord = _make_coordinator(
            "sensor.pv", {"sensor.pv": {"state": "1500", "attributes": {"foo": "bar"}}},
        )
        now_idx, fc = coord._read_clearness(NOW)
        assert now_idx is None and fc is None
        assert coord.solar_fc_consecutive_failures == 1

    def test_valid_forecast_solar_drives_clearness(self):
        coord = _make_coordinator(
            "sensor.pv",
            {"sensor.pv": {"state": "1500", "attributes": _watts_attr(0.5)}},
        )
        now_idx, fc = coord._read_clearness(NOW)
        assert fc is not None
        assert coord._solar_provider == "forecast_solar"
        assert coord.solar_fc_consecutive_failures == 0
        # Auto-calibrated against the generic plane → defined steps near 1.0.
        defined = [v for v in fc if v is not None]
        assert defined
        for v in defined:
            assert 0.9 <= v <= 1.1


class TestRoomSolarGain:
    def test_windowed_room_clearness_modulates(self):
        coord = _make_coordinator("sensor.pv", {})
        clear = coord._room_solar_gain("lr", NOW, None, None)
        dim = coord._room_solar_gain("lr", NOW, None, 0.5)
        assert clear > 0.0
        assert dim == pytest.approx(0.5 * clear, rel=1e-9)

    def test_windowless_room_uses_exposure(self):
        rooms = [
            Room(name="lr", thermal_mass=5e6, r_external=0.05, temperature=20.0,
                 solar_exposure_aperture=3.0, solar_facing=180.0),
        ]
        coord = _make_coordinator("sensor.pv", {}, rooms=rooms)
        gain = coord._room_solar_gain("lr", NOW, None, None)
        assert gain > 0.0

    def test_missing_entity_gain_matches_cloud_baseline(self):
        # With no clearness, the gain equals the plain cloud-cover path.
        coord = _make_coordinator(None, {})
        from custom_components.heating_assistant.solar_model import room_solar_gains
        baseline = room_solar_gains(
            coord.model.rooms["lr"].windows, NOW, LAT, LON, cloud_cover=0.5,
        )
        gain = coord._room_solar_gain("lr", NOW, 0.5, None)
        assert gain == pytest.approx(baseline, rel=1e-12)
