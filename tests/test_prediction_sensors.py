"""
Tests for the always-available forecast sensors.

These sensors override ``available`` so dashboards keep rendering predictions
across transient coordinator update failures, and declare no
``device_class`` / ``state_class`` so HA's strict sensor validator accepts
forward-looking values.
"""

import os
import sys
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
    room = SimpleNamespace(temperature=20.5, setpoint=21.0, windows=[])
    return SimpleNamespace(
        predictions=[{"living_room": 20.6}, {"living_room": 20.7}],
        heating_schedule=[{"living_room": 1234.0}, {"living_room": 1100.0}],
        solar_forecast=[
            {"living_room": 50.0},
            {"living_room": 60.0},
            {"living_room": 70.0},
        ],
        outdoor_forecast=[5.0, 4.5],
        filtered_temperatures={"living_room": 20.63},
        solar_gains={"living_room": 50.0},
        heat_sources=[SimpleNamespace(room="living_room", current_power=900.0)],
        model=SimpleNamespace(rooms={"living_room": room}),
        outdoor_temp=5.0,
        dt=900,
        controller=SimpleNamespace(constraint_offset=2.0),
        last_update_success=False,  # simulate a recent UpdateFailed
    )


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


# ── Forecast attributes are produced as expected ───────────────────────


def test_temperature_forecast_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = TemperatureForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    assert "trajectory" in attrs
    assert attrs["horizon_steps"] == 2


def test_temperature_forecast_bridge_uses_filtered_measurement_estimate():
    coord = _make_room_coordinator()
    sensor = TemperatureForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["forecast"][0]["temperature"] == pytest.approx(20.63)
    assert attrs["current_temperature"] == pytest.approx(20.63)


def test_temperature_forecast_bridge_falls_back_to_measurement_when_filtered_missing():
    coord = _make_room_coordinator()
    coord.filtered_temperatures = {}
    sensor = TemperatureForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["forecast"][0]["temperature"] == pytest.approx(20.5)
    assert attrs["current_temperature"] == pytest.approx(20.5)


def test_heating_power_forecast_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = HeatingPowerForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    assert attrs["horizon_steps"] == 2


def test_solar_gain_forecast_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = SolarGainForecastSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    # solar_forecast has N+1 entries → horizon_steps = N
    assert attrs["horizon_steps"] == 2


def test_outdoor_temperature_forecast_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = OutdoorTemperatureForecastSensor(coord)
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    assert attrs["horizon_steps"] == 2


# ── Empty data surfaces as "unknown" so failures are visible ──────────


def test_temperature_forecast_reports_unknown_when_predictions_empty():
    """The sensor stays available (so dashboards keep rendering attributes)
    but the state is ``None`` — that's what tells the operator the MPC has
    no trajectory."""
    room = SimpleNamespace(temperature=19.42, setpoint=21.0, windows=[])
    coord = SimpleNamespace(
        predictions=[],
        heating_schedule=[],
        solar_forecast=[],
        outdoor_forecast=[],
        solar_gains={},
        heat_sources=[],
        model=SimpleNamespace(rooms={"living_room": room}),
        outdoor_temp=5.0,
        dt=900,
        controller=SimpleNamespace(constraint_offset=2.0),
        last_update_success=False,
    )
    sensor = TemperatureForecastSensor(coord, "living_room")
    assert sensor.available is True
    assert sensor.native_value is None


def test_heating_power_forecast_reports_unknown_when_schedule_empty():
    room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
    coord = SimpleNamespace(
        predictions=[],
        heating_schedule=[],
        solar_forecast=[],
        outdoor_forecast=[],
        solar_gains={},
        heat_sources=[
            SimpleNamespace(room="living_room", current_power=412.5),
            SimpleNamespace(room="bedroom", current_power=999.0),
        ],
        model=SimpleNamespace(rooms={"living_room": room}),
        outdoor_temp=5.0,
        dt=900,
        last_update_success=False,
    )
    sensor = HeatingPowerForecastSensor(coord, "living_room")
    assert sensor.available is True
    assert sensor.native_value is None


def test_solar_gain_forecast_reports_unknown_when_solar_forecast_empty():
    room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
    coord = SimpleNamespace(
        predictions=[],
        heating_schedule=[],
        solar_forecast=[],
        outdoor_forecast=[],
        solar_gains={"living_room": 60.0},
        heat_sources=[],
        model=SimpleNamespace(rooms={"living_room": room}),
        outdoor_temp=5.0,
        dt=900,
        controller=SimpleNamespace(constraint_offset=2.0),
        last_update_success=False,
    )
    sensor = SolarGainForecastSensor(coord, "living_room")
    assert sensor.available is True
    assert sensor.native_value is None
