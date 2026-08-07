"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import pytest

from custom_components.heating_assistant.coordinator import setup_simulation as sim
from heatingassistant.engine.thermal_model import HouseModel, Room
from tests.helpers.coordinator_stubs import make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _make_sim_coord():
    room = Room(
        name="living_room",
        thermal_mass=4_000_000.0,
        r_external=0.05,
        temperature=18.0,
        setpoint=21.0,
    )
    model = HouseModel([room])
    coord = make_minimal_coordinator(room_names=["living_room"])
    coord.model = model
    return coord


def test_simulate_thermal_response_unknown_room_returns_error():
    coord = _make_sim_coord()

    result = sim.simulate_thermal_response(
        coord, "bedroom", 18.0, 5.0, 1000.0, duration_hours=1.0
    )

    assert result == {"error": "Room 'bedroom' not found"}


def test_simulate_thermal_response_warms_with_heating():
    coord = _make_sim_coord()

    result = sim.simulate_thermal_response(
        coord,
        room_name="living_room",
        initial_temp=10.0,
        outdoor_temp=0.0,
        heating_power=2000.0,
        duration_hours=2.0,
    )

    assert "error" not in result
    assert result["final_temperature"] > 10.0
    assert result["steady_state_temperature"] > 10.0
    assert result["time_constant_hours"] > 0.0
    assert len(result["trajectory"]) >= 2
    temps = [point["temperature"] for point in result["trajectory"]]
    assert temps[-1] == pytest.approx(result["final_temperature"])
    assert all(t >= 10.0 for t in temps)


def test_simulate_thermal_response_trajectory_sampling():
    coord = _make_sim_coord()

    result = sim.simulate_thermal_response(
        coord,
        room_name="living_room",
        initial_temp=20.0,
        outdoor_temp=20.0,
        heating_power=0.0,
        duration_hours=1.0,
    )

    minutes = [point["time_minutes"] for point in result["trajectory"]]
    assert minutes[0] == pytest.approx(1.0)
    assert minutes[-1] == pytest.approx(60.0)
    assert len(minutes) < 60


def test_estimate_parameters_unknown_room_returns_error():
    coord = _make_sim_coord()

    result = sim.estimate_parameters(
        coord,
        room_name="bedroom",
        heating_power=1000.0,
        outdoor_temp=5.0,
        initial_temp=18.0,
        final_temp=20.0,
        duration_seconds=3600.0,
    )

    assert result == {"error": "Room 'bedroom' not found"}


def test_estimate_parameters_computes_mass_when_heating_exceeds_loss():
    coord = _make_sim_coord()

    result = sim.estimate_parameters(
        coord,
        room_name="living_room",
        heating_power=1000.0,
        outdoor_temp=25.0,
        initial_temp=22.0,
        final_temp=20.0,
        duration_seconds=3600.0,
    )

    assert "error" not in result
    assert result["estimated_r_external"] == pytest.approx(0.05)
    assert result["estimated_thermal_mass"] == pytest.approx(1_944_000.0, rel=1e-3)
    assert result["current_thermal_mass"] == pytest.approx(4_000_000.0)
    assert "rough estimates" in result["notes"]


def test_estimate_parameters_heating_warmup_yields_zero_net_power():
    """When R is inferred from heating, loss balances input so mass estimate is zero."""
    coord = _make_sim_coord()

    result = sim.estimate_parameters(
        coord,
        room_name="living_room",
        heating_power=1000.0,
        outdoor_temp=5.0,
        initial_temp=18.0,
        final_temp=20.0,
        duration_seconds=3600.0,
    )

    assert result["estimated_r_external"] == pytest.approx(0.014, rel=1e-3)
    assert result["estimated_thermal_mass"] == 0.0


def test_estimate_parameters_falls_back_when_heating_zero():
    coord = _make_sim_coord()

    result = sim.estimate_parameters(
        coord,
        room_name="living_room",
        heating_power=0.0,
        outdoor_temp=5.0,
        initial_temp=18.0,
        final_temp=17.0,
        duration_seconds=3600.0,
    )

    assert result["estimated_r_external"] == pytest.approx(0.05)
    # Cooling with no heat input still yields a finite mass estimate from the loss balance.
    assert result["estimated_thermal_mass"] == pytest.approx(900_000.0, rel=1e-3)
