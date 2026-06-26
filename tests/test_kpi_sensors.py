"""Tests for KPI attributes published on system and room sensors (WP-3)."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.heat_sources import HeatPump, ElectricHeater
from custom_components.heating_assistant.sensor import (
    MPCPerformanceSensor,
    SystemEfficiencySensor,
    TemperatureFilteredSensor,
)


def _living_room(*, setpoint=21.0, comfort_offset=2.0):
    return SimpleNamespace(
        temperature=20.5,
        setpoint=setpoint,
        comfort_offset=comfort_offset,
        windows=[],
        thermal_mass=50000.0,
        r_external=0.01,
        internal_gain=0.0,
        solar_scale=1.0,
        c_air_fraction=0.5,
        r_aw_fraction=0.5,
    )


def _bedroom(*, setpoint=20.0, comfort_offset=2.0):
    return SimpleNamespace(
        temperature=22.5,
        setpoint=setpoint,
        comfort_offset=comfort_offset,
        windows=[],
        thermal_mass=40000.0,
        r_external=0.012,
        internal_gain=0.0,
        solar_scale=1.0,
        c_air_fraction=0.5,
        r_aw_fraction=0.5,
    )


def _make_coordinator(
    *,
    living_active=True,
    bed_active=True,
    living_filtered=21.0,
    bed_filtered=22.5,
    living_measured=None,
    bed_measured=None,
    heat_sources=None,
):
    living = _living_room()
    bed = _bedroom()
    room_enabled = {"living_room": living_active, "bedroom": bed_active}
    schedule_disabled = {"living_room": False, "bedroom": False}

    coord = SimpleNamespace(
        heat_sources=heat_sources or [],
        solar_gains={"living_room": 0.0, "bedroom": 0.0},
        heat_flows={
            "living_room": {"total_loss": 500.0},
            "bedroom": {"total_loss": 400.0},
        },
        filtered_temperatures={
            "living_room": living_filtered,
            "bedroom": bed_filtered,
        },
        measured_temperatures={
            "living_room": living_measured if living_measured is not None else living_filtered,
            "bedroom": bed_measured if bed_measured is not None else bed_filtered,
        },
        outdoor_temp=5.0,
        system_enabled=True,
        model=SimpleNamespace(
            rooms={"living_room": living, "bedroom": bed},
            room_names=["living_room", "bedroom"],
        ),
        _room_enabled=room_enabled,
        _schedule_disabled=schedule_disabled,
        _sources_by_room={"living_room": [], "bedroom": []},
        controller=SimpleNamespace(
            last_solve_time=0.42,
            mean_solve_time=0.35,
            max_solve_time=0.9,
            n_solves=10,
            total_computes=10,
            horizon=48,
            solve_times=[0.3, 0.42],
            terminal_weight=1.0,
        ),
        dt=900,
        _last_mpc_run_ts=1_700_000_000.0,
        last_update_success=True,
        _time_in_range_pct_24h={},
    )

    def is_room_enabled(room_name):
        if not room_enabled.get(room_name, True):
            return False
        if schedule_disabled.get(room_name, False):
            return False
        return True

    coord.is_room_enabled = is_room_enabled
    coord.sources_for_room = lambda r: coord._sources_by_room.get(r, [])
    return coord


# ── System summary KPI attributes ─────────────────────────────────────────


def test_system_summary_comfort_index_all_in_band():
    coord = _make_coordinator(living_filtered=21.0, bed_filtered=20.5)
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["comfort_index_pct"] == 100


def test_system_summary_comfort_index_partial_in_band():
    coord = _make_coordinator(bed_filtered=25.0)
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["comfort_index_pct"] == 50


def test_system_summary_comfort_index_excludes_inactive():
    coord = _make_coordinator(bed_active=False, bed_filtered=10.0)
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["comfort_index_pct"] == 100


def test_system_summary_comfort_index_none_when_all_off():
    coord = _make_coordinator(living_active=False, bed_active=False)
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["comfort_index_pct"] is None


def test_system_summary_has_heat_pump_true():
    hp = HeatPump(
        "hp1",
        "living_room",
        max_power=3000.0,
        heater_entity="climate.hp",
        cop_rated=3.5,
    )
    coord = _make_coordinator(heat_sources=[hp])
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["has_heat_pump"] is True


def test_system_summary_has_heat_pump_false():
    rh = ElectricHeater("rad1", "living_room", max_power=1500.0, heater_entity="switch.rad")
    coord = _make_coordinator(heat_sources=[rh])
    attrs = SystemEfficiencySensor(coord).extra_state_attributes
    assert attrs["has_heat_pump"] is False


# ── MPC performance tracking error ─────────────────────────────────────────


def test_mpc_performance_mean_tracking_error_active_only():
    coord = _make_coordinator(
        living_filtered=21.0,
        bed_filtered=22.5,
        bed_active=False,
    )
    attrs = MPCPerformanceSensor(coord).extra_state_attributes
    # living: |21 - 21| = 0; bedroom excluded from mean despite 2.5 °C error
    assert attrs["mean_tracking_error"] == pytest.approx(0.0)
    assert attrs["current_tracking_errors"]["living_room"] == pytest.approx(0.0)
    assert attrs["current_tracking_errors"]["bedroom"] == pytest.approx(2.5)


def test_mpc_performance_mean_uses_filtered_temperature():
    coord = _make_coordinator(
        living_filtered=21.5,
        living_measured=19.0,
    )
    attrs = MPCPerformanceSensor(coord).extra_state_attributes
    assert attrs["current_tracking_errors"]["living_room"] == pytest.approx(0.5)


def test_mpc_performance_max_tracking_error_active_only():
    coord = _make_coordinator(
        living_filtered=21.0,
        bed_filtered=23.0,
        bed_active=False,
    )
    attrs = MPCPerformanceSensor(coord).extra_state_attributes
    assert attrs["max_tracking_error"] == pytest.approx(0.0)


# ── Per-room comfort deviation ────────────────────────────────────────────


def test_temperature_filtered_comfort_deviation_in_band():
    coord = _make_coordinator(living_filtered=21.0)
    attrs = TemperatureFilteredSensor(coord, "living_room").extra_state_attributes
    assert attrs["comfort_deviation"] == pytest.approx(0.0)


def test_temperature_filtered_comfort_deviation_out_of_band():
    coord = _make_coordinator(living_filtered=18.0)
    attrs = TemperatureFilteredSensor(coord, "living_room").extra_state_attributes
    assert attrs["comfort_deviation"] == pytest.approx(1.0)


def test_temperature_filtered_comfort_deviation_inactive_none():
    coord = _make_coordinator(living_active=False, living_filtered=18.0)
    attrs = TemperatureFilteredSensor(coord, "living_room").extra_state_attributes
    assert attrs["comfort_deviation"] is None


def test_temperature_filtered_time_in_range_from_cache():
    coord = _make_coordinator(living_filtered=21.0)
    coord._time_in_range_pct_24h = {"living_room": 87.5}
    attrs = TemperatureFilteredSensor(coord, "living_room").extra_state_attributes
    assert attrs["time_in_range_pct_24h"] == 88


def test_temperature_filtered_time_in_range_inactive_none():
    coord = _make_coordinator(living_active=False, living_filtered=21.0)
    coord._time_in_range_pct_24h = {"living_room": 100.0}
    attrs = TemperatureFilteredSensor(coord, "living_room").extra_state_attributes
    assert attrs["time_in_range_pct_24h"] is None


def test_temperature_filtered_time_in_range_missing_cache_none():
    coord = _make_coordinator(living_filtered=21.0)
    attrs = TemperatureFilteredSensor(coord, "living_room").extra_state_attributes
    assert attrs["time_in_range_pct_24h"] is None
