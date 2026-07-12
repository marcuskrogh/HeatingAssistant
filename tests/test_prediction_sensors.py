"""
Tests for the always-available forecast sensors.

These sensors override ``available`` so dashboards keep rendering predictions
across transient coordinator update failures, and declare no
``device_class`` / ``state_class`` so HA's strict sensor validator accepts
forward-looking values.
"""

import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.sensor import (
    HeatingPowerForecastSensor,
    OutdoorTemperatureForecastSensor,
    SolarGainForecastSensor,
    TemperatureForecastSensor,
)


def _make_room_coordinator():
    """Coordinator stub with populated horizon data for one room."""
    room = SimpleNamespace(temperature=20.5, setpoint=21.0, windows=[], comfort_offset=2.0)
    sources = [SimpleNamespace(room="living_room", current_power=900.0, max_power=2000.0)]
    coord = SimpleNamespace(
        predictions=[{"living_room": 20.6}, {"living_room": 20.7}],
        linearised_predictions=[{"living_room": 20.58}, {"living_room": 20.68}],
        heating_schedule=[{"living_room": 1234.0}, {"living_room": 1100.0}],
        solar_forecast=[
            {"living_room": 50.0},
            {"living_room": 60.0},
            {"living_room": 70.0},
        ],
        outdoor_forecast=[5.0, 4.5],
        price_forecast=[0.10, 0.11],
        filtered_temperatures={"living_room": 20.63},
        solar_gains={"living_room": 50.0},
        heat_sources=sources,
        model=SimpleNamespace(
            rooms={"living_room": room},
            room_names=["living_room"],
        ),
        outdoor_temp=5.0,
        dt=900,
        _update_interval_s=900,
        _control_trajectory=None,
        _sources_by_room={"living_room": sources},
        controller=SimpleNamespace(constraint_offset=2.0),
        last_update_success=False,  # simulate a recent UpdateFailed
    )
    coord.sources_for_room = lambda r: [s for s in coord.heat_sources if s.room == r]
    coord.is_room_enabled = lambda _name: True
    return coord


# ── Unique IDs and display names ────────────────────────────────────────


def test_temperature_forecast_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = TemperatureForecastSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_temperature_forecast"
    assert sensor._attr_name == "Heating Assistant – living_room – Temperature Forecast"


def test_heating_power_forecast_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = HeatingPowerForecastSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_heating_power_forecast"
    assert sensor._attr_name == "Heating Assistant – living_room – Heating Power Forecast"


def test_solar_gain_forecast_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = SolarGainForecastSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_solar_gain_forecast"
    assert sensor._attr_name == "Heating Assistant – living_room – Solar Gain Forecast"


def test_outdoor_temperature_forecast_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = OutdoorTemperatureForecastSensor(coord)
    assert sensor._attr_unique_id == "heating_assistant_outdoor_temperature_forecast"
    assert sensor._attr_name == "Heating Assistant – Outdoor Temperature Forecast"


# ── Sensor metadata avoids HA's strict device_class/state_class rules ──


@pytest.mark.parametrize(
    "sensor_cls",
    [
        TemperatureForecastSensor,
        HeatingPowerForecastSensor,
        SolarGainForecastSensor,
        OutdoorTemperatureForecastSensor,
    ],
)
def test_forecast_sensors_have_no_device_class(sensor_cls):
    assert getattr(sensor_cls, "_attr_device_class", "missing") is None


@pytest.mark.parametrize(
    "sensor_cls",
    [
        TemperatureForecastSensor,
        HeatingPowerForecastSensor,
        SolarGainForecastSensor,
        OutdoorTemperatureForecastSensor,
    ],
)
def test_forecast_sensors_have_no_state_class(sensor_cls):
    assert getattr(sensor_cls, "_attr_state_class", "missing") is None


# ── Availability is decoupled from coordinator.last_update_success ──────


@pytest.mark.parametrize(
    "sensor_cls,args",
    [
        (TemperatureForecastSensor, ("living_room",)),
        (HeatingPowerForecastSensor, ("living_room",)),
        (SolarGainForecastSensor, ("living_room",)),
        (OutdoorTemperatureForecastSensor, ()),
    ],
)
def test_forecast_sensors_remain_available_when_update_failed(sensor_cls, args):
    """Forecast sensors stay available even when the coordinator's last
    update failed — their cached trajectory is still usable for dashboards."""
    coord = _make_room_coordinator()
    coord.last_update_success = False
    sensor = sensor_cls(coord, *args)
    assert sensor.available is True


# ── Forecast attributes: large arrays removed, metadata kept ──────────
# Forecast arrays are now served via the heating_assistant/get_forecasts
# WebSocket endpoint to avoid HA Recorder's 16 KB attribute size limit.


def test_temperature_forecast_exposes_forecast_attribute():
    """forecast array removed from attributes; lightweight metadata stays."""
    coord = _make_room_coordinator()
    sensor = TemperatureForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" not in attrs
    assert "trajectory" in attrs
    assert attrs["horizon_steps"] == 2


def test_temperature_forecast_bridge_uses_filtered_measurement_estimate():
    coord = _make_room_coordinator()
    sensor = TemperatureForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    # Bridge value is still surfaced via current_temperature attribute
    assert attrs["current_temperature"] == pytest.approx(20.63)


def test_temperature_forecast_bridge_falls_back_to_measurement_when_filtered_missing():
    coord = _make_room_coordinator()
    coord.filtered_temperatures = {}
    sensor = TemperatureForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["current_temperature"] == pytest.approx(20.5)


def test_heating_power_forecast_exposes_forecast_attribute():
    """forecast array removed from attributes; horizon_steps metadata stays."""
    coord = _make_room_coordinator()
    sensor = HeatingPowerForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" not in attrs
    assert attrs["horizon_steps"] == 2


def test_temperature_and_power_forecast_time_axes_are_interval_aligned():
    """Power plan is indexed at interval start; temperature forecast at interval end.

    Verified via build_forecast_payload() which is the authoritative source
    for forecast array data (previously was sensor attributes).
    """
    from datetime import timezone

    from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator

    coord = _make_room_coordinator()
    now = datetime(2026, 1, 5, 7, 30, tzinfo=timezone.utc)
    coord.now_utc = now

    payload = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"]
    )
    room_fc = payload["rooms"]["living_room"]
    temp_fc = room_fc["forecast"]
    dt = coord.dt

    t_temp0 = datetime.fromisoformat(temp_fc[0]["time"])
    t_temp1 = datetime.fromisoformat(temp_fc[1]["time"])

    # Bridge entry is at t=now; first predicted step is at t=now+dt
    assert t_temp0 == now
    assert (t_temp1 - t_temp0).total_seconds() == pytest.approx(dt)

    # Heating power and temperature share the same timestamped entries
    assert "heating_power" in temp_fc[1]  # first planned step
    assert "temperature" in temp_fc[1]


def test_solar_gain_forecast_exposes_forecast_attribute():
    """forecast array removed from attributes; horizon_steps metadata stays."""
    coord = _make_room_coordinator()
    sensor = SolarGainForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" not in attrs
    # solar_forecast has N+1 entries → horizon_steps = N
    assert attrs["horizon_steps"] == 2


def test_outdoor_temperature_forecast_exposes_forecast_attribute():
    """forecast array removed from attributes; horizon_steps metadata stays."""
    coord = _make_room_coordinator()
    sensor = OutdoorTemperatureForecastSensor(coord)
    attrs = sensor.extra_state_attributes
    assert "forecast" not in attrs
    assert attrs["horizon_steps"] == 2


# ── Empty data surfaces as "unknown" so failures are visible ──────────
# Covered canonically in tests/test_sensor_forecasts.py (empty predictions /
# schedule / solar forecast → native_value is None while available stays True).
