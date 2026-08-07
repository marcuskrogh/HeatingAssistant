"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from heatingassistant.engine.heat_sources import ElectricHeater
from custom_components.heating_assistant.services import simulation as sim
from tests.helpers.coordinator_stubs import make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _service_call(**data):
    return SimpleNamespace(data=data)


def test_effective_heater_scales_prefers_call_data():
    coord = make_minimal_coordinator()
    coord._last_identified_heater_scales = {"living_room_radiator": 0.8}
    call = _service_call(heater_scales={"living_room_radiator": 1.2, "kitchen": 0.9})

    scales = sim.effective_heater_scales(call, coord)

    assert scales == {"living_room_radiator": 1.2, "kitchen": 0.9}


def test_effective_heater_scales_falls_back_to_coordinator_cache():
    coord = make_minimal_coordinator()
    coord._last_identified_heater_scales = {"living_room_radiator": 0.75}
    call = _service_call()

    assert sim.effective_heater_scales(call, coord) == {"living_room_radiator": 0.75}


def test_extract_sim_room_params_collects_slugged_keys():
    call = _service_call(
        thermal_mass_living_room=5_000_000.0,
        r_external_living_room=0.05,
        solar_scale_kitchen=1.1,
        unrelated_key=99.0,
    )

    params = sim.extract_sim_room_params(call, ["Living Room", "Kitchen"])

    assert params["Living Room"] == {
        "thermal_mass": 5_000_000.0,
        "r_external": 0.05,
    }
    assert params["Kitchen"] == {"solar_scale": 1.1}


def test_patched_heat_sources_fast_path_empty_scales():
    heater = ElectricHeater("rad", "living_room", max_power=1500.0)
    heater.power_scale = 1.0
    coord = make_minimal_coordinator()
    coord.heat_sources = [heater]

    patched = sim.patched_heat_sources(coord, {})

    assert patched is coord.heat_sources
    assert patched[0].power_scale == 1.0


def test_patched_heat_sources_applies_scales_without_mutating_live():
    heater = ElectricHeater("rad", "living_room", max_power=1500.0)
    heater.power_scale = 1.0
    coord = make_minimal_coordinator()
    coord.heat_sources = [heater]

    patched = sim.patched_heat_sources(coord, {"rad": 0.6})

    assert patched is not coord.heat_sources
    assert patched[0].power_scale == 0.6
    assert coord.heat_sources[0].power_scale == 1.0


def test_inject_identified_t_wall_initial_from_sysid_results():
    coord = make_minimal_coordinator(room_names=["Living Room"])
    coord.sysid_results = {"Living Room": {"t_wall_initial": 18.5}}
    room_params: dict = {}

    sim.inject_identified_t_wall_initial(room_params, coord)

    assert room_params["Living Room"]["t_wall_initial"] == 18.5


def test_inject_identified_t_wall_initial_does_not_overwrite_explicit():
    coord = make_minimal_coordinator(room_names=["Living Room"])
    coord.sysid_results = {"Living Room": {"t_wall_initial": 18.5}}
    room_params = {"Living Room": {"t_wall_initial": 20.0}}

    sim.inject_identified_t_wall_initial(room_params, coord)

    assert room_params["Living Room"]["t_wall_initial"] == 20.0


def test_open_loop_t_wall_initial_dict_merges_sources():
    coord = make_minimal_coordinator(room_names=["Living Room", "Kitchen"])
    coord.sysid_results = {"Living Room": {"t_wall_initial": 17.0}}
    room_params = {"Kitchen": {"t_wall_initial": 19.5}}

    result = sim.open_loop_t_wall_initial_dict(
        room_params, coord, fast_estimated={"Bedroom": 16.0}
    )

    assert result == {"Living Room": 17.0, "Kitchen": 19.5}


def test_merge_per_room_into_sysid_results_preserves_existing():
    cache = {"Living Room": {"t_wall_initial": 18.0, "heater_scales": {"h": 1.0}}}
    per_room = {
        "Living Room": {"simulation": [21.0], "rmse": 0.3},
        "Kitchen": {"rmse": 0.5},
    }

    sim.merge_per_room_into_sysid_results(cache, per_room)

    assert cache["Living Room"]["t_wall_initial"] == 18.0
    assert cache["Living Room"]["heater_scales"] == {"h": 1.0}
    assert cache["Living Room"]["rmse"] == 0.3
    assert cache["Kitchen"]["rmse"] == 0.5


@pytest.mark.asyncio
async def test_compute_open_loop_rmse_by_horizon_mocks_executor(monkeypatch):
    """Heavy open-loop prediction runs in executor — mock it for a fast test."""
    room_names = ["Living Room"]
    history = [{"timestamp": 1.0, "y": [20.0], "u": [0.5]}]
    system = object()
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(
            side_effect=[
                {"per_room": {"Living Room": {"rmse": 0.1}}},
                {"per_room": {"Living Room": {"rmse": 0.2}}},
                {"error": "too short"},
            ]
        )
    )

    result = await sim.compute_open_loop_rmse_by_horizon(
        hass, history, system, room_names, n_rooms=1, dt=900.0, t_wall_initial=None
    )

    assert result["Living Room"]["4h"] == 0.1
    assert result["Living Room"]["12h"] == 0.2
    assert "24h" not in result["Living Room"]
    assert hass.async_add_executor_job.await_count == 3
