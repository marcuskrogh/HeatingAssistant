"""Tests for sensor entity metadata."""

from types import SimpleNamespace

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
    assert getattr(MPCPerformanceSensor, "_attr_native_unit_of_measurement", None) is None


def test_mpc_performance_sensor_remains_available_on_update_failure():
    coordinator = SimpleNamespace(
        last_update_success=False,
        controller=SimpleNamespace(
            total_computes=12,
            _solve_times=[0.08, 0.09],
            last_solve_time=0.09,
            mean_solve_time=0.085,
            max_solve_time=0.09,
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
    assert sensor.native_value == 12
    assert sensor.extra_state_attributes["mean_tracking_error"] == 1.5
