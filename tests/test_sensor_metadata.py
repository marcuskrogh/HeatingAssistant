"""Tests for sensor entity metadata."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.const import UnitOfTime

from custom_components.heating_assistant.sensor import (
    HeatingPlanSensor,
    MPCPerformanceSensor,
    OutdoorForecastSensor,
    PredictedTemperatureSensor,
    SolarForecastSensor,
    TemperatureForecastSensor,
)


def test_prediction_entities_have_no_state_class():
    assert getattr(PredictedTemperatureSensor, "_attr_state_class", None) is None
    assert getattr(OutdoorForecastSensor, "_attr_state_class", None) is None
    assert getattr(TemperatureForecastSensor, "_attr_state_class", None) is None
    assert getattr(HeatingPlanSensor, "_attr_state_class", None) is None
    assert getattr(SolarForecastSensor, "_attr_state_class", None) is None


def test_mpc_performance_sensor_avoids_strict_statistics_metadata():
    assert getattr(MPCPerformanceSensor, "_attr_state_class", None) is None
    assert (
        getattr(MPCPerformanceSensor, "_attr_native_unit_of_measurement", None)
        == UnitOfTime.SECONDS
    )


@pytest.mark.parametrize("last_update_success", [False, True])
def test_mpc_performance_sensor_remains_available_with_controller(last_update_success):
    coordinator = SimpleNamespace(
        last_update_success=last_update_success,
        controller=SimpleNamespace(
            total_computes=12,
            _solve_times=[timedelta(milliseconds=80), timedelta(milliseconds=90)],
            last_solve_time=timedelta(milliseconds=90),
            mean_solve_time=timedelta(milliseconds=85),
            max_solve_time=timedelta(milliseconds=90),
            n_solves=2,
            _horizon=6,
            terminal_weight=100.0,
        ),
        dt=900,
        model=SimpleNamespace(
            room_names=["living_room"],
            rooms={"living_room": SimpleNamespace(temperature=20.0, setpoint=21.5)},
        ),
    )

    sensor = MPCPerformanceSensor(coordinator)

    assert sensor.available is True
    assert sensor.native_value == 0.09
    assert sensor.extra_state_attributes["last_solve_time_s"] == 0.09
    assert sensor.extra_state_attributes["recent_solve_times_s"] == [0.08, 0.09]
    assert sensor.extra_state_attributes["mean_tracking_error"] == 1.5
