"""Tests for the new visualisation sensors that mirror the data the README
advanced-dashboards plot: measured/filtered/setpoint/constraint per room."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.sensor import (
    ConstraintLowerSensor,
    ConstraintUpperSensor,
    HeatingPowerMeasuredSensor,
    OutdoorTemperatureMeasuredSensor,
    SetpointSensor,
    SolarGainMeasuredSensor,
    TemperatureFilteredSensor,
    TemperatureMeasuredSensor,
)


def _make_coordinator(
    measured=20.4,
    filtered=20.7,
    setpoint=21.0,
    constraint_offset=2.0,
    solar=42.0,
    heating_power=815.0,
    outdoor=4.5,
):
    room = SimpleNamespace(
        temperature=measured,
        setpoint=setpoint,
        thermal_mass=5_000_000.0,
        r_external=0.05,
        windows=[],
    )
    return SimpleNamespace(
        model=SimpleNamespace(
            room_names=["living_room"],
            rooms={"living_room": room},
        ),
        measured_temperatures={"living_room": measured},
        filtered_temperatures={"living_room": filtered},
        solar_gains={"living_room": solar},
        heat_sources=[
            SimpleNamespace(room="living_room", current_power=heating_power),
        ],
        outdoor_temp=outdoor,
        controller=SimpleNamespace(constraint_offset=constraint_offset),
        last_update_success=True,
    )


# ── Naming conventions ────────────────────────────────────────────────


def test_temperature_measured_unique_id_and_name():
    coord = _make_coordinator()
    sensor = TemperatureMeasuredSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_temperature_measured"
    assert "Temperature Measured" in sensor._attr_name


def test_temperature_filtered_unique_id_and_name():
    coord = _make_coordinator()
    sensor = TemperatureFilteredSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_temperature_filtered"
    assert "Temperature Filtered" in sensor._attr_name


def test_setpoint_unique_id():
    coord = _make_coordinator()
    sensor = SetpointSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_setpoint"


def test_constraint_sensors_unique_ids():
    coord = _make_coordinator()
    upper = ConstraintUpperSensor(coord, "living_room")
    lower = ConstraintLowerSensor(coord, "living_room")
    assert upper._attr_unique_id == "heating_assistant_living_room_constraint_upper"
    assert lower._attr_unique_id == "heating_assistant_living_room_constraint_lower"


def test_measured_aliases_match_pattern():
    coord = _make_coordinator()
    assert HeatingPowerMeasuredSensor(coord, "living_room")._attr_unique_id == (
        "heating_assistant_living_room_heating_power_measured"
    )
    assert SolarGainMeasuredSensor(coord, "living_room")._attr_unique_id == (
        "heating_assistant_living_room_solar_gain_measured"
    )
    assert OutdoorTemperatureMeasuredSensor(coord)._attr_unique_id == (
        "heating_assistant_outdoor_temperature_measured"
    )


# ── Native values ─────────────────────────────────────────────────────


def test_temperature_measured_returns_averaged_measurement():
    coord = _make_coordinator(measured=19.83, filtered=20.7)
    sensor = TemperatureMeasuredSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(19.83)


def test_temperature_filtered_returns_ekf_estimate():
    coord = _make_coordinator(measured=19.83, filtered=20.27)
    sensor = TemperatureFilteredSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(20.27)


def test_temperature_measured_and_filtered_are_independent():
    """The whole point of exposing both — they can differ."""
    coord = _make_coordinator(measured=19.5, filtered=20.5)
    measured = TemperatureMeasuredSensor(coord, "living_room").native_value
    filtered = TemperatureFilteredSensor(coord, "living_room").native_value
    assert measured != filtered


def test_setpoint_returns_current_setpoint():
    coord = _make_coordinator(setpoint=21.7)
    sensor = SetpointSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(21.7)


def test_constraint_upper_is_setpoint_plus_offset():
    coord = _make_coordinator(setpoint=21.0, constraint_offset=1.5)
    sensor = ConstraintUpperSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(22.5)


def test_constraint_lower_is_setpoint_minus_offset():
    coord = _make_coordinator(setpoint=21.0, constraint_offset=1.5)
    sensor = ConstraintLowerSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(19.5)


def test_constraint_band_width_is_twice_offset():
    coord = _make_coordinator(setpoint=20.0, constraint_offset=2.0)
    upper = ConstraintUpperSensor(coord, "living_room").native_value
    lower = ConstraintLowerSensor(coord, "living_room").native_value
    assert upper - lower == pytest.approx(4.0)


# ── Graceful degradation ──────────────────────────────────────────────


def test_temperature_filtered_none_before_first_compute():
    coord = _make_coordinator()
    coord.filtered_temperatures = {}
    sensor = TemperatureFilteredSensor(coord, "living_room")
    assert sensor.native_value is None


def test_temperature_measured_none_before_first_cycle():
    coord = _make_coordinator()
    coord.measured_temperatures = {}
    sensor = TemperatureMeasuredSensor(coord, "living_room")
    assert sensor.native_value is None


def test_constraint_sensors_return_none_without_controller():
    coord = _make_coordinator()
    coord.controller = None
    upper = ConstraintUpperSensor(coord, "living_room")
    lower = ConstraintLowerSensor(coord, "living_room")
    assert upper.native_value is None
    assert lower.native_value is None
