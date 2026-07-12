"""Coordinator-level tests for the solar-radiation (irradiance) integration.

Builds a bare coordinator via ``object.__new__`` (same pattern as
``test_coordinator_apply_actions.py``) and exercises ``_read_ghi`` and
``_room_solar_gain`` directly, covering the graceful-fallback paths that keep
the analytical model in charge when the radiation entity is missing / stale /
carries no usable irradiance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.thermal_model import HouseModel, Room, Window

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


LAT, LON = 55.0, 12.0
NOW = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)


def _make_coordinator(radiation_entity, entity_states, *, rooms=None):
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._solar_radiation_entity = radiation_entity
    coord._latitude = LAT
    coord._longitude = LON
    coord._horizon = 4
    coord._update_interval_s = 3600  # backs the read-only ``dt`` property
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
            return SimpleNamespace(
                state=raw.get("state", "unknown"),
                attributes=raw.get("attributes", {}),
                last_updated=raw.get("last_updated", NOW),
            )
        return None

    hass.states.get = _get_state
    coord.hass = hass
    return coord


def _ghi_forecast_attrs(ghi=500.0):
    """An irradiance forecast attribute series (W/m²) over the next hours."""
    return {
        "forecast": [
            {"datetime": (NOW + timedelta(hours=h)).isoformat(), "ghi": ghi}
            for h in range(0, 6)
        ],
        "unit_of_measurement": "W/m²",
    }


class TestReadGhi:
    def test_no_entity_returns_none(self):
        coord = _make_coordinator(None, {})
        now_v, fc = coord._read_ghi(NOW)
        assert now_v is None and fc is None

    def test_unavailable_entity_records_failure(self):
        coord = _make_coordinator(
            "sensor.ghi", {"sensor.ghi": {"state": "unavailable", "attributes": {}}},
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

    def test_no_irradiance_falls_back(self):
        # Entity present but no numeric state and no irradiance attributes.
        coord = _make_coordinator(
            "sensor.ghi", {"sensor.ghi": {"state": "cloudy", "attributes": {"foo": "bar"}}},
        )
        now_v, fc = coord._read_ghi(NOW)
        assert now_v is None and fc is None
        assert coord.solar_fc_consecutive_failures == 1

    def test_irradiance_forecast_drives_ghi(self):
        coord = _make_coordinator(
            "sensor.ghi",
            {"sensor.ghi": {"state": "500", "attributes": _ghi_forecast_attrs(500.0)}},
        )
        now_v, fc = coord._read_ghi(NOW)
        assert fc is not None
        assert coord._solar_provider == "irradiance"
        assert coord.solar_fc_consecutive_failures == 0
        defined = [v for v in fc if v is not None]
        assert defined
        for v in defined:
            assert v == pytest.approx(500.0, rel=1e-6)


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
