"""Tests for sensor entity metadata."""

from custom_components.heating_assistant.sensor import (
    HeatingPlanSensor,
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
