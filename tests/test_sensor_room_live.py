"""Tests for live measurement and control sensors in sensor/room_live.py."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from custom_components.heating_assistant.sensor import (
    ControlActionSensor,
    EnergyBalanceSensor,
    HeatPumpCOPSensor,
    HeatingEnergyTotalSensor,
    InternalGainEstimatedSensor,
    SystemEfficiencySensor,
    WallTemperatureSensor,
)


def _living_room(*, internal_gain=150.0):
    return SimpleNamespace(
        temperature=20.4,
        setpoint=21.0,
        comfort_offset=2.0,
        thermal_mass=5_000_000.0,
        r_external=0.05,
        windows=[],
        internal_gain=internal_gain,
        solar_scale=1.0,
        c_air_fraction=0.55,
        r_aw_fraction=0.45,
    )


def _make_coordinator(
    *,
    measured=20.4,
    filtered=20.7,
    setpoint=21.0,
    constraint_offset=2.0,
    solar=42.0,
    heating_power=815.0,
    outdoor=4.5,
    wall_temp=19.8,
    wall_std=0.35,
    estimated_gain=210.0,
    heat_sources=None,
    actions=None,
    heat_flows=None,
):
    room = _living_room()
    if heat_sources is None:
        heat_sources = [
            SimpleNamespace(
                name="lr_heater",
                room="living_room",
                current_power=heating_power,
                max_power=2000.0,
            )
        ]
    coord = SimpleNamespace(
        model=SimpleNamespace(
            room_names=["living_room"],
            rooms={"living_room": room},
        ),
        measured_temperatures={"living_room": measured},
        filtered_temperatures={"living_room": filtered},
        wall_temperatures={"living_room": wall_temp},
        wall_temperature_stds={"living_room": wall_std},
        estimated_internal_gains={"living_room": estimated_gain},
        solar_gains={"living_room": solar},
        heat_sources=heat_sources,
        heat_flows=heat_flows
        or {"living_room": {"external_loss": 320.0, "total_loss": 353.0}},
        outdoor_temp=outdoor,
        actions={"lr_heater": 0.65} if actions is None else actions,
        controller=SimpleNamespace(
            constraint_offset=constraint_offset,
            gain_estimation_enabled=True,
        ),
        last_update_success=True,
        _horizon=6,
        dt=900,
        _room_enabled={"living_room": True},
        system_enabled=True,
        _schedule_disabled={"living_room": False},
        _time_in_range_pct_24h={},
    )
    coord.is_room_enabled = lambda name: coord._room_enabled.get(name, True)
    coord.sources_for_room = lambda r: [
        s for s in coord.heat_sources if getattr(s, "room", None) == r
    ]
    return coord


# ── WallTemperatureSensor ─────────────────────────────────────────────


def test_wall_temperature_sensor_unique_id_and_name():
    coord = _make_coordinator()
    sensor = WallTemperatureSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_wall_temperature"
    assert "Wall Temperature" in sensor._attr_name


def test_wall_temperature_sensor_returns_ekf_wall_estimate():
    coord = _make_coordinator(wall_temp=18.456)
    sensor = WallTemperatureSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(18.46)


def test_wall_temperature_sensor_none_when_wall_data_missing():
    coord = _make_coordinator()
    coord.wall_temperatures = {}
    sensor = WallTemperatureSensor(coord, "living_room")
    assert sensor.native_value is None


def test_wall_temperature_sensor_attributes_include_posterior_std():
    coord = _make_coordinator(wall_std=0.4123)
    attrs = WallTemperatureSensor(coord, "living_room").extra_state_attributes
    assert attrs["posterior_std"] == pytest.approx(0.412)
    assert attrs["c_air_fraction"] == pytest.approx(0.55)
    assert attrs["r_aw_fraction"] == pytest.approx(0.45)
    assert attrs["solar_scale"] == pytest.approx(1.0)
    assert "observability" in attrs


# ── InternalGainEstimatedSensor ───────────────────────────────────────


def test_internal_gain_estimated_unique_id_and_name():
    coord = _make_coordinator()
    sensor = InternalGainEstimatedSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_internal_gain_estimated"
    assert "Internal Gain Estimated" in sensor._attr_name


def test_internal_gain_estimated_returns_online_estimate():
    coord = _make_coordinator(estimated_gain=237.84)
    sensor = InternalGainEstimatedSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(237.8)


def test_internal_gain_estimated_none_when_missing():
    coord = _make_coordinator()
    coord.estimated_internal_gains = {}
    sensor = InternalGainEstimatedSensor(coord, "living_room")
    assert sensor.native_value is None


def test_internal_gain_estimated_attributes_show_deviation():
    coord = _make_coordinator(estimated_gain=210.0)
    room = coord.model.rooms["living_room"]
    room.internal_gain = 150.0
    attrs = InternalGainEstimatedSensor(coord, "living_room").extra_state_attributes
    assert attrs["nominal_gain_w"] == pytest.approx(150.0)
    assert attrs["estimated_deviation_w"] == pytest.approx(60.0)
    assert attrs["estimation_enabled"] is True


# ── ControlActionSensor ───────────────────────────────────────────────


def test_control_action_sensor_unique_id_and_name():
    coord = _make_coordinator()
    sensor = ControlActionSensor(coord, "lr_heater")
    assert sensor._attr_unique_id == "heating_assistant_lr_heater_control_action"
    assert "Control Action" in sensor._attr_name


def test_control_action_sensor_converts_fraction_to_percent():
    coord = _make_coordinator(actions={"lr_heater": 0.725})
    sensor = ControlActionSensor(coord, "lr_heater")
    assert sensor.native_value == pytest.approx(72.5)


def test_control_action_sensor_defaults_to_zero_without_action():
    coord = _make_coordinator(actions={})
    sensor = ControlActionSensor(coord, "lr_heater")
    assert sensor.native_value == pytest.approx(0.0)


def test_control_action_sensor_attributes_expose_source_context():
    coord = _make_coordinator(heating_power=1234.0)
    coord.heat_sources[0].name = "lr_heater"
    attrs = ControlActionSensor(coord, "lr_heater").extra_state_attributes
    assert attrs["room"] == "living_room"
    assert attrs["max_power"] == 2000.0
    assert attrs["current_power"] == pytest.approx(1234.0)


def test_control_action_sensor_attributes_empty_for_unknown_source():
    coord = _make_coordinator()
    attrs = ControlActionSensor(coord, "missing_source").extra_state_attributes
    assert attrs == {}


# ── HeatPumpCOPSensor ─────────────────────────────────────────────────


def test_heat_pump_cop_sensor_unique_id_and_name():
    hp = HeatPump("hp_living", "living_room", max_power=5000.0)
    coord = _make_coordinator(heat_sources=[hp], outdoor=5.0)
    sensor = HeatPumpCOPSensor(coord, "hp_living")
    assert sensor._attr_unique_id == "heating_assistant_hp_living_cop"
    assert "COP" in sensor._attr_name


def test_heat_pump_cop_sensor_returns_cop_at_outdoor_temp():
    hp = HeatPump("hp_living", "living_room", max_power=5000.0, cop_rated=3.5)
    coord = _make_coordinator(heat_sources=[hp], outdoor=7.0)
    sensor = HeatPumpCOPSensor(coord, "hp_living")
    assert sensor.native_value == pytest.approx(hp.cop(7.0), abs=0.01)


def test_heat_pump_cop_sensor_none_for_non_heat_pump():
    heater = ElectricHeater("rad1", "living_room", max_power=1500.0)
    coord = _make_coordinator(heat_sources=[heater], outdoor=5.0)
    sensor = HeatPumpCOPSensor(coord, "rad1")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_heat_pump_cop_sensor_none_without_outdoor_temp():
    hp = HeatPump("hp_living", "living_room", max_power=5000.0)
    coord = _make_coordinator(heat_sources=[hp], outdoor=None)
    sensor = HeatPumpCOPSensor(coord, "hp_living")
    assert sensor.native_value is None


def test_heat_pump_cop_sensor_attributes_include_rated_values():
    hp = HeatPump(
        "hp_living",
        "living_room",
        max_power=5000.0,
        cop_rated=3.8,
        cop_temp_ref=7.0,
        min_power=500.0,
    )
    coord = _make_coordinator(heat_sources=[hp], outdoor=4.0)
    attrs = HeatPumpCOPSensor(coord, "hp_living").extra_state_attributes
    assert attrs["cop_rated"] == pytest.approx(3.8)
    assert attrs["cop_temp_ref"] == pytest.approx(7.0)
    assert attrs["min_power"] == pytest.approx(500.0)
    assert attrs["outdoor_temp"] == pytest.approx(4.0)


# ── EnergyBalanceSensor ───────────────────────────────────────────────


def test_energy_balance_sensor_unique_id_and_name():
    coord = _make_coordinator()
    sensor = EnergyBalanceSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_energy_balance"
    assert "Energy Balance" in sensor._attr_name


def test_energy_balance_sensor_net_heating_plus_solar_minus_loss():
    coord = _make_coordinator(heating_power=800.0, solar=50.0)
    coord.heat_flows = {"living_room": {"external_loss": 300.0, "total_loss": 400.0}}
    sensor = EnergyBalanceSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(450.0)


def test_energy_balance_sensor_cooling_negative_heating():
    """Cooling (negative source power) reduces the net energy balance."""
    hp = HeatPump("hp_living", "living_room", max_power=5000.0)
    hp._current_power = -1200.0
    coord = _make_coordinator(
        heat_sources=[hp],
        heating_power=-1200.0,
        solar=0.0,
    )
    coord.heat_flows = {"living_room": {"external_loss": 200.0, "total_loss": 200.0}}
    sensor = EnergyBalanceSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(-1400.0)


def test_energy_balance_sensor_attributes_breakdown():
    coord = _make_coordinator(heating_power=900.0, solar=60.0)
    coord.heat_flows = {
        "living_room": {"external_loss": 250.0, "total_loss": 400.0},
    }
    attrs = EnergyBalanceSensor(coord, "living_room").extra_state_attributes
    assert attrs["heating_power"] == pytest.approx(900.0)
    assert attrs["solar_gain"] == pytest.approx(60.0)
    assert attrs["external_heat_loss"] == pytest.approx(250.0)
    assert attrs["inter_room_heat_exchange"] == pytest.approx(150.0)
    assert attrs["total_heat_loss"] == pytest.approx(400.0)
    assert attrs["net_energy_flow"] == pytest.approx(560.0)
    assert attrs["room_temperature"] == pytest.approx(20.4)
    assert attrs["setpoint"] == pytest.approx(21.0)


# ── SystemEfficiencySensor ────────────────────────────────────────────


def test_system_efficiency_sensor_unique_id_and_name():
    coord = _make_coordinator()
    sensor = SystemEfficiencySensor(coord)
    assert sensor._attr_unique_id == "heating_assistant_system_summary"
    assert "System Summary" in sensor._attr_name


def test_system_efficiency_sensor_sums_all_source_power():
    hp = HeatPump("hp_living", "living_room", max_power=5000.0)
    hp._current_power = 1800.0
    rad = ElectricHeater("rad_bed", "bedroom", max_power=1500.0)
    rad._current_power = 400.0
    coord = _make_coordinator(heat_sources=[hp, rad])
    coord.model.room_names = ["living_room", "bedroom"]
    coord.model.rooms["bedroom"] = _living_room()
    coord.solar_gains["bedroom"] = 0.0
    coord.heat_flows["bedroom"] = {"total_loss": 100.0}
    coord._room_enabled["bedroom"] = True
    coord._schedule_disabled["bedroom"] = False
    coord.filtered_temperatures["bedroom"] = 21.0
    coord.measured_temperatures["bedroom"] = 21.0
    coord._sources_by_room = {
        "living_room": [hp],
        "bedroom": [rad],
    }
    coord.sources_for_room = lambda r: coord._sources_by_room.get(r, [])
    sensor = SystemEfficiencySensor(coord)
    assert sensor.native_value == pytest.approx(2200.0)


def test_system_efficiency_sensor_effective_cop_with_heat_pump():
    hp = HeatPump("hp_living", "living_room", max_power=5000.0, cop_rated=3.5)
    hp._current_power = 3500.0
    coord = _make_coordinator(heat_sources=[hp], outdoor=7.0)
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["has_heat_pump"] is True
    assert attrs["effective_system_cop"] > 1.0
    assert attrs["electrical_input_estimate"] > 0.0


def test_system_efficiency_sensor_attributes_aggregate_totals():
    coord = _make_coordinator(heating_power=900.0, solar=60.0)
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["total_heating_power"] == pytest.approx(900.0)
    assert attrs["total_solar_gain"] == pytest.approx(60.0)
    assert attrs["total_heat_loss"] == pytest.approx(353.0)
    assert attrs["net_energy_flow"] == pytest.approx(607.0)
    assert attrs["active_sources"] == 1
    assert attrs["total_sources"] == 1
    assert attrs["system_enabled"] is True
    assert attrs["room_heating_power"]["living_room"] == pytest.approx(900.0)


# ── HeatingEnergyTotalSensor (restore path) ─────────────────────────


def test_energy_total_sensor_unique_id_pattern():
    coord = _make_coordinator()
    coord.heat_sources[0].name = "lr_heater"
    sensor = HeatingEnergyTotalSensor(coord, "lr_heater")
    assert sensor._attr_unique_id == "heating_assistant_lr_heater_energy_total"


@pytest.mark.asyncio
async def test_energy_total_sensor_async_added_to_hass_restores_native_value():
    coord = _make_coordinator(heating_power=0.0)
    coord.heat_sources[0].name = "lr_heater"
    sensor = HeatingEnergyTotalSensor(coord, "lr_heater")
    sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SimpleNamespace(native_value="12.345"),
    )
    await sensor.async_added_to_hass()
    assert sensor._total_kwh == pytest.approx(12.345)
    assert sensor.native_value == pytest.approx(12.345)


@pytest.mark.asyncio
async def test_energy_total_sensor_async_added_to_hass_ignores_invalid_restore():
    coord = _make_coordinator(heating_power=0.0)
    coord.heat_sources[0].name = "lr_heater"
    sensor = HeatingEnergyTotalSensor(coord, "lr_heater")
    sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SimpleNamespace(native_value="not-a-number"),
    )
    await sensor.async_added_to_hass()
    assert sensor._total_kwh == pytest.approx(0.0)


def test_energy_total_sensor_ignores_cooling_power(monkeypatch):
    """Negative (cooling) power must not decrement the cumulative total."""
    hp = HeatPump("hp_living", "living_room", max_power=5000.0)
    hp._current_power = -1500.0
    coord = _make_coordinator(heat_sources=[hp], heating_power=-1500.0)
    sensor = HeatingEnergyTotalSensor(coord, "hp_living")

    import datetime as _datetime_mod
    from types import SimpleNamespace as _NS

    state = _NS(now_ts=2_000_000.0)

    class _FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return _NS(timestamp=lambda: state.now_ts)

    monkeypatch.setattr(_datetime_mod, "datetime", _FakeDateTime)

    assert sensor.native_value == pytest.approx(0.0)
    state.now_ts += 3600
    assert sensor.native_value == pytest.approx(0.0)


def test_energy_total_sensor_returns_accumulated_total_when_source_missing():
    coord = _make_coordinator(heating_power=0.0)
    sensor = HeatingEnergyTotalSensor(coord, "missing_source")
    sensor._total_kwh = 7.5
    assert sensor.native_value == pytest.approx(7.5)
