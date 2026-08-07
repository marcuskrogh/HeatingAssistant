"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.sensor.forecasts import (
    ElectricityPriceForecastSensor,
    ElectricityPriceSensor,
    HeatingPowerForecastSensor,
    OutdoorTemperatureForecastSensor,
    SolarGainForecastSensor,
    TemperatureForecastSensor,
)


def _make_coordinator(**overrides):
    room = SimpleNamespace(
        temperature=20.5,
        setpoint=21.0,
        windows=[SimpleNamespace(area=1.5)],
        comfort_offset=2.0,
    )
    sources = [SimpleNamespace(room="living_room", max_power=2000.0)]
    coord = SimpleNamespace(
        predictions=[{"living_room": 20.6}, {"living_room": 20.7}],
        heating_schedule=[{"living_room": 1234.0}, {"living_room": 1100.0}],
        solar_forecast=[{"living_room": 50.0}, {"living_room": 60.0}],
        outdoor_forecast=[5.0, 4.5],
        price_forecast=[0.12345, 0.23456],
        filtered_temperatures={"living_room": 20.63},
        outdoor_temp=5.0,
        dt=900,
        _price_entity="sensor.nordpool",
        heat_sources=sources,
        model=SimpleNamespace(
            rooms={"living_room": room},
            room_names=["living_room"],
        ),
        hass=SimpleNamespace(
            states=SimpleNamespace(
                get=lambda eid: SimpleNamespace(
                    state="0.12",
                    attributes={"unit_of_measurement": "EUR/kWh"},
                )
                if eid == "sensor.nordpool"
                else None
            )
        ),
    )
    coord.sources_for_room = lambda r: [s for s in coord.heat_sources if s.room == r]
    for key, value in overrides.items():
        setattr(coord, key, value)
    return coord


def test_outdoor_temperature_forecast_rounds_native_value():
    sensor = OutdoorTemperatureForecastSensor(_make_coordinator(outdoor_temp=5.456))
    assert sensor.native_value == pytest.approx(5.46)
    attrs = sensor.extra_state_attributes
    assert attrs["horizon_steps"] == 2
    assert attrs["step_seconds"] == 900


def test_outdoor_temperature_forecast_none_when_outdoor_temp_missing():
    sensor = OutdoorTemperatureForecastSensor(_make_coordinator(outdoor_temp=None))
    assert sensor.native_value is None


def test_temperature_forecast_native_value_uses_last_prediction():
    sensor = TemperatureForecastSensor(_make_coordinator(), "living_room")
    assert sensor.native_value == pytest.approx(20.7)
    trajectory = sensor.extra_state_attributes["trajectory"]
    assert trajectory == [pytest.approx(20.6), pytest.approx(20.7)]


def test_temperature_forecast_native_value_none_when_room_missing_from_prediction():
    coord = _make_coordinator(predictions=[{"bedroom": 19.0}])
    sensor = TemperatureForecastSensor(coord, "living_room")
    assert sensor.native_value is None


def test_temperature_forecast_reports_unknown_when_predictions_empty():
    """The sensor stays available (so dashboards keep rendering attributes)
    but the state is ``None`` — that's what tells the operator the MPC has
    no trajectory."""
    sensor = TemperatureForecastSensor(
        _make_coordinator(predictions=[]), "living_room"
    )
    assert sensor.available is True
    assert sensor.native_value is None


def test_heating_power_forecast_native_value_uses_first_schedule_step():
    sensor = HeatingPowerForecastSensor(_make_coordinator(), "living_room")
    assert sensor.native_value == pytest.approx(1234.0)
    attrs = sensor.extra_state_attributes
    assert attrs["max_power"] == pytest.approx(2000.0)


def test_heating_power_forecast_native_value_none_when_schedule_empty():
    sensor = HeatingPowerForecastSensor(
        _make_coordinator(heating_schedule=[]), "living_room"
    )
    assert sensor.available is True
    assert sensor.native_value is None


def test_solar_gain_forecast_native_value_uses_first_forecast_step():
    sensor = SolarGainForecastSensor(_make_coordinator(), "living_room")
    assert sensor.native_value == pytest.approx(50.0)
    attrs = sensor.extra_state_attributes
    assert attrs["window_count"] == 1
    assert attrs["total_window_area"] == pytest.approx(1.5)


def test_solar_gain_forecast_native_value_none_when_forecast_empty():
    sensor = SolarGainForecastSensor(
        _make_coordinator(solar_forecast=[]), "living_room"
    )
    assert sensor.available is True
    assert sensor.native_value is None


def test_electricity_price_sensor_reads_unit_and_rounded_value():
    sensor = ElectricityPriceSensor(_make_coordinator())
    assert sensor.native_unit_of_measurement == "EUR/kWh"
    assert sensor.native_value == pytest.approx(0.12345)
    attrs = sensor.extra_state_attributes
    assert attrs["has_forecast"] is True
    assert attrs["horizon_steps"] == 2


def test_electricity_price_sensor_handles_missing_entity_and_bad_values():
    coord = _make_coordinator(_price_entity=None, price_forecast=[])
    sensor = ElectricityPriceSensor(coord)
    assert sensor.native_unit_of_measurement is None
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["has_forecast"] is False

    bad_coord = _make_coordinator(price_forecast=["not-a-number"])
    bad_sensor = ElectricityPriceSensor(bad_coord)
    assert bad_sensor.native_value is None


def test_electricity_price_sensor_unit_none_when_state_missing():
    coord = _make_coordinator()
    coord.hass.states.get = MagicMock(return_value=None)
    sensor = ElectricityPriceSensor(coord)
    assert sensor.native_unit_of_measurement is None


def test_electricity_price_forecast_sensor_matches_current_price():
    sensor = ElectricityPriceForecastSensor(_make_coordinator())
    assert sensor.native_unit_of_measurement == "EUR/kWh"
    assert sensor.native_value == pytest.approx(0.12345)
    attrs = sensor.extra_state_attributes
    assert attrs["horizon_steps"] == 2
    assert attrs["horizon_minutes"] == pytest.approx(30.0)


def test_electricity_price_sensor_handles_index_error_on_empty_forecast():
    sensor = ElectricityPriceSensor(_make_coordinator(price_forecast=[]))
    assert sensor.native_value is None


def test_electricity_price_forecast_sensor_unit_none_without_price_entity():
    sensor = ElectricityPriceForecastSensor(_make_coordinator(_price_entity=None))
    assert sensor.native_unit_of_measurement is None


def test_electricity_price_forecast_sensor_handles_invalid_forecast_value():
    sensor = ElectricityPriceForecastSensor(
        _make_coordinator(price_forecast=[object()])
    )
    assert sensor.native_value is None


def test_build_entities_includes_heat_pump_cop_and_price_sensors():
    from heatingassistant.engine.heat_sources import HeatPump
    from custom_components.heating_assistant.sensor import (
        ElectricityPriceForecastSensor,
        ElectricityPriceSensor,
        HeatPumpCOPSensor,
        _build_entities,
    )

    hp = object.__new__(HeatPump)
    hp.name = "lr_hp"
    hp.room = "living_room"
    coord = _make_coordinator(_price_entity="sensor.nordpool")
    coord.heat_sources = [hp]

    entities = _build_entities(coord)
    types = {type(entity) for entity in entities}

    assert HeatPumpCOPSensor in types
    assert ElectricityPriceSensor in types
    assert ElectricityPriceForecastSensor in types


@pytest.mark.asyncio
async def test_async_setup_entry_registers_entities():
    from custom_components.heating_assistant.sensor import async_setup_entry

    coord = _make_coordinator()
    coord.heat_sources = []
    added = []
    hass = SimpleNamespace(
        data={"heating_assistant": {"entry-1": coord}},
        async_add_executor_job=AsyncMock(side_effect=lambda fn: fn()),
    )
    entry = SimpleNamespace(entry_id="entry-1")

    await async_setup_entry(hass, entry, added.extend)

    assert added
    sensor = ElectricityPriceForecastSensor(
        _make_coordinator(price_forecast=[])
    )
    assert sensor.native_value is None
    assert sensor.available is True
