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
        _horizon=6,
        dt=900,
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


# ── MPC-failure visibility ──────────────────────────────────────────────
#
# When the MPC solver fails the coordinator clears ``filtered_temperatures``
# (set to {}) and the forecast/schedule lists (set to []).  The sensors
# below must surface that as ``None`` so dashboards and the recorder show a
# visible gap rather than echoing the last measurement or current power.


def test_temperature_filtered_is_none_when_mpc_cleared_filtered_dict():
    """Empty filtered_temperatures (set by the coordinator on MPC failure)
    must produce a None state, not a fallback to the measurement."""
    coord = _make_coordinator()
    coord.filtered_temperatures = {}
    assert TemperatureFilteredSensor(coord, "living_room").native_value is None


def test_measured_sensors_still_populated_during_mpc_failure():
    """Measurements are read independently of MPC and must keep flowing so
    the historical part of the chart stays continuous up to "now"."""
    coord = _make_coordinator(measured=20.1, heating_power=750.0, solar=33.0)
    coord.filtered_temperatures = {}  # MPC cleared this
    assert TemperatureMeasuredSensor(coord, "living_room").native_value == pytest.approx(20.1)
    assert HeatingPowerMeasuredSensor(coord, "living_room").native_value == pytest.approx(750.0)
    assert SolarGainMeasuredSensor(coord, "living_room").native_value == pytest.approx(33.0)


# ── Forecast trajectories on the setpoint / constraint sensors ─────────
#
# Regression: PR #71 plotted setpoint/constraint as scalar lines with
# ``extend_to: end`` so the forecast region rendered as a flat extension of
# the last recorded value.  The sensors now expose a ``forecast`` attribute
# spanning the MPC horizon so dashboards can drive the line from a
# ``data_generator`` (same pattern as temperature_forecast).


def test_setpoint_sensor_exposes_horizon_forecast():
    coord = _make_coordinator(setpoint=21.5)
    attrs = SetpointSensor(coord, "living_room").extra_state_attributes
    fc = attrs["forecast"]
    # N+1 points so the trace spans [now, now + N*dt] with the same shape
    # as the temperature_forecast sensor.
    assert attrs["horizon_steps"] == 6
    assert attrs["step_seconds"] == 900
    assert len(fc) == 7
    for entry in fc:
        assert "time" in entry
        assert entry["setpoint"] == pytest.approx(21.5)


def test_constraint_upper_sensor_exposes_horizon_forecast():
    coord = _make_coordinator(setpoint=21.0, constraint_offset=2.0)
    attrs = ConstraintUpperSensor(coord, "living_room").extra_state_attributes
    fc = attrs["forecast"]
    assert len(fc) == 7
    for entry in fc:
        assert entry["constraint_upper"] == pytest.approx(23.0)


def test_constraint_lower_sensor_exposes_horizon_forecast():
    coord = _make_coordinator(setpoint=21.0, constraint_offset=2.0)
    attrs = ConstraintLowerSensor(coord, "living_room").extra_state_attributes
    fc = attrs["forecast"]
    assert len(fc) == 7
    for entry in fc:
        assert entry["constraint_lower"] == pytest.approx(19.0)


def test_constraint_band_width_matches_offset_in_forecast():
    """The band width in the forecast trace must stay 2 × constraint_offset."""
    coord = _make_coordinator(setpoint=20.0, constraint_offset=1.5)
    upper = ConstraintUpperSensor(coord, "living_room").extra_state_attributes["forecast"]
    lower = ConstraintLowerSensor(coord, "living_room").extra_state_attributes["forecast"]
    for u, l in zip(upper, lower):
        assert u["constraint_upper"] - l["constraint_lower"] == pytest.approx(3.0)


def test_setpoint_forecast_timestamps_advance_by_dt():
    """Successive forecast timestamps must be spaced by ``dt`` so apexcharts
    draws a horizontal line across the MPC horizon (rather than stacking all
    points at "now")."""
    from datetime import datetime as _dt

    coord = _make_coordinator()
    fc = SetpointSensor(coord, "living_room").extra_state_attributes["forecast"]
    times = [_dt.fromisoformat(e["time"]) for e in fc]
    for prev, curr in zip(times, times[1:]):
        assert (curr - prev).total_seconds() == pytest.approx(900)


def test_setpoint_forecast_empty_without_horizon():
    """When the coordinator has no horizon info the sensor degrades to an
    empty forecast rather than raising."""
    coord = _make_coordinator()
    del coord._horizon
    attrs = SetpointSensor(coord, "living_room").extra_state_attributes
    assert attrs["forecast"] == []


def test_constraint_forecast_omits_value_when_offset_missing():
    """If ``constraint_offset`` is unavailable the per-step value field is
    dropped (entries still have timestamps) and the scalar state is None."""
    coord = _make_coordinator()
    coord.controller = None
    sensor = ConstraintUpperSensor(coord, "living_room")
    assert sensor.native_value is None
    fc = sensor.extra_state_attributes["forecast"]
    assert len(fc) == 7
    for entry in fc:
        assert "time" in entry
        assert "constraint_upper" not in entry
