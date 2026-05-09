"""
Tests for the always-available prediction sensor variants.

These sensors expose the same data as the forecast/plan sensors but
override ``available`` so dashboards keep rendering predictions across
transient coordinator update failures.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.sensor import (
    HeatingPlanPredictionSensor,
    HeatingPlanSensor,
    OutdoorForecastSensor,
    OutdoorTemperaturePredictionSensor,
    SolarForecastSensor,
    SolarPowerPredictionSensor,
    TemperatureForecastSensor,
    TemperaturePredictionSensor,
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
        solar_gains={"living_room": 50.0},
        heat_sources=[SimpleNamespace(room="living_room", current_power=900.0)],
        model=SimpleNamespace(rooms={"living_room": room}),
        outdoor_temp=5.0,
        dt=900,
        controller=SimpleNamespace(constraint_offset=2.0),
        last_update_success=False,  # simulate a recent UpdateFailed
    )


# ── Unique IDs and display names ────────────────────────────────────────


def test_temperature_prediction_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = TemperaturePredictionSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_temperature_prediction"
    assert sensor._attr_name == "Heating Assistant – living_room – Temperature Prediction"


def test_heating_plan_prediction_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = HeatingPlanPredictionSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_heating_plan_prediction"
    assert sensor._attr_name == "Heating Assistant – living_room – Heating Plan Prediction"


def test_solar_power_prediction_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = SolarPowerPredictionSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_solar_power_prediction"
    assert sensor._attr_name == "Heating Assistant – living_room – Solar Power Prediction"


def test_outdoor_temperature_prediction_unique_id_and_name():
    coord = _make_room_coordinator()
    sensor = OutdoorTemperaturePredictionSensor(coord)
    assert sensor._attr_unique_id == "heating_assistant_outdoor_temperature_prediction"
    assert sensor._attr_name == "Heating Assistant – Outdoor Temperature Prediction"


# ── Sensor metadata avoids HA's strict device_class/state_class rules ──
#
# Recent Home Assistant releases reject sensors that declare a
# ``device_class`` (TEMPERATURE / POWER) without a matching
# ``state_class``.  We cannot use ``state_class=MEASUREMENT`` because the
# predictions are forward-looking values that would corrupt the long-term
# statistics database, so the prediction sensors must drop both the
# ``device_class`` and ``state_class``.


@pytest.mark.parametrize(
    "sensor_cls",
    [
        TemperaturePredictionSensor,
        HeatingPlanPredictionSensor,
        SolarPowerPredictionSensor,
        OutdoorTemperaturePredictionSensor,
    ],
)
def test_prediction_sensors_have_no_device_class(sensor_cls):
    assert getattr(sensor_cls, "_attr_device_class", "missing") is None


@pytest.mark.parametrize(
    "sensor_cls",
    [
        TemperaturePredictionSensor,
        HeatingPlanPredictionSensor,
        SolarPowerPredictionSensor,
        OutdoorTemperaturePredictionSensor,
    ],
)
def test_prediction_sensors_have_no_state_class(sensor_cls):
    assert getattr(sensor_cls, "_attr_state_class", "missing") is None


# ── Availability is decoupled from coordinator.last_update_success ──────


@pytest.mark.parametrize(
    "sensor_cls,args",
    [
        (TemperaturePredictionSensor, ("living_room",)),
        (HeatingPlanPredictionSensor, ("living_room",)),
        (SolarPowerPredictionSensor, ("living_room",)),
        (OutdoorTemperaturePredictionSensor, ()),
    ],
)
def test_prediction_sensors_remain_available_when_update_failed(sensor_cls, args):
    """
    The whole point of the prediction variants: they remain ``available``
    even when ``coordinator.last_update_success`` is False, because the
    cached prediction data on the coordinator is still valid.
    """
    coord = _make_room_coordinator()
    coord.last_update_success = False
    sensor = sensor_cls(coord, *args)
    assert sensor.available is True


# ── Native values match the underlying forecast/plan sensors ────────────


def test_temperature_prediction_native_value_matches_forecast():
    coord = _make_room_coordinator()
    pred = TemperaturePredictionSensor(coord, "living_room")
    forecast = TemperatureForecastSensor(coord, "living_room")
    assert pred.native_value == forecast.native_value


def test_heating_plan_prediction_native_value_matches_plan():
    coord = _make_room_coordinator()
    pred = HeatingPlanPredictionSensor(coord, "living_room")
    plan = HeatingPlanSensor(coord, "living_room")
    assert pred.native_value == plan.native_value


def test_solar_power_prediction_native_value_matches_forecast():
    coord = _make_room_coordinator()
    pred = SolarPowerPredictionSensor(coord, "living_room")
    forecast = SolarForecastSensor(coord, "living_room")
    assert pred.native_value == forecast.native_value


def test_outdoor_temperature_prediction_native_value_matches_forecast():
    coord = _make_room_coordinator()
    pred = OutdoorTemperaturePredictionSensor(coord)
    forecast = OutdoorForecastSensor(coord)
    assert pred.native_value == forecast.native_value


# ── Forecast attributes are still produced (same shape as parent) ──────


def test_temperature_prediction_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = TemperaturePredictionSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    assert "trajectory" in attrs
    assert attrs["horizon_steps"] == 2


def test_heating_plan_prediction_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = HeatingPlanPredictionSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    assert attrs["horizon_steps"] == 2


def test_solar_power_prediction_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = SolarPowerPredictionSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    # solar_forecast has N+1 entries → horizon_steps = N
    assert attrs["horizon_steps"] == 2


def test_outdoor_temperature_prediction_exposes_forecast_attribute():
    coord = _make_room_coordinator()
    sensor = OutdoorTemperaturePredictionSensor(coord)
    attrs = sensor.extra_state_attributes
    assert "forecast" in attrs
    assert attrs["horizon_steps"] == 2


# ── Empty-data fallbacks behave like the parent sensors ────────────────


def test_temperature_prediction_falls_back_to_current_temperature():
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
    sensor = TemperaturePredictionSensor(coord, "living_room")
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(19.42)


def test_heating_plan_prediction_falls_back_to_current_heating():
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
    sensor = HeatingPlanPredictionSensor(coord, "living_room")
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(412.5)
