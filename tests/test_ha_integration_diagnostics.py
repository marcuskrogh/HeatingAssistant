"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from heatingassistant.engine.const import DOMAIN
from custom_components.heating_assistant.diagnostics import (
    _get_model_fit_diagnostics,
    async_get_config_entry_diagnostics,
)
from heatingassistant.engine.heat_sources import ElectricHeater, HeatPump


def _make_room(name="living_room", *, setpoint=21.0):
    return SimpleNamespace(
        temperature=20.5,
        setpoint=setpoint,
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[],
        windows=[SimpleNamespace(area=2.0, orientation=180.0, tilt=90.0)],
    )


def _make_coordinator(*, history_buffer=None, room_names=None):
    names = room_names or ["living_room"]
    rooms = {n: _make_room(n) for n in names}
    heater = ElectricHeater("heater1", "living_room", max_power=2000.0)
    heater._current_power = 500.0
    hp = HeatPump("hp1", "living_room", max_power=5000.0)
    hp._current_power = 1200.0

    model = SimpleNamespace(
        room_names=names,
        rooms=rooms,
        time_constant=MagicMock(return_value=7200.0),
        steady_state_temperature=MagicMock(return_value=22.0),
    )

    buffer = deque(history_buffer or [])
    coord = SimpleNamespace(
        model=model,
        heat_sources=[heater, hp],
        actions={"heater1": 0.4, "hp1": 0.25},
        outdoor_temp=5.0,
        heat_flows={"living_room": {"total_loss": 800.0}},
        predictions=[{"living_room": 20.6}, {"living_room": 20.7}],
        heating_schedule=[{"heater1": 1000.0}, {"heater1": 900.0}],
        solar_forecast=[{"living_room": 50.0}, {"living_room": 60.0}],
        outdoor_forecast=[4.5, 4.0],
        solar_gains={"living_room": 42.0},
        history_buffer=buffer,
        _horizon=6,
        _dt=900,
        _latitude=55.0,
        _longitude=12.0,
        _cooling_active={"hp1": False},
    )
    return coord


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_structure():
    coord = _make_coordinator()
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coord}})
    entry = SimpleNamespace(entry_id="entry-1")

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["outdoor_temperature"] == 5.0
    assert "living_room" in result["rooms"]
    assert result["rooms"]["living_room"]["temperature"] == 20.5
    assert "heater1" in result["heat_sources"]
    assert result["heat_sources"]["hp1"]["type"] == "HeatPump"
    assert result["heat_sources"]["hp1"]["cop_current"] > 0
    assert len(result["predictions"]) == 2
    assert result["predictions"][0]["heating_power"]["heater1"] == 1000.0
    assert result["controller"]["horizon"] == 6
    assert "model_fit_diagnostics" in result
    assert "steady_state_analysis" in result


def test_get_model_fit_diagnostics_insufficient_history():
    coord = _make_coordinator(history_buffer=[{"y": [20.0]}])
    diag = _get_model_fit_diagnostics(coord)
    assert diag["error"] == "Insufficient history data"
    assert diag["n_steps"] == 1


def test_get_model_fit_diagnostics_with_history():
    history = [
        {"y": [20.0], "y_pred": [20.1]},
        {"y": [20.5], "y_pred": [20.4]},
        {"y": [21.0], "y_pred": [20.9]},
    ]
    coord = _make_coordinator(history_buffer=history)
    diag = _get_model_fit_diagnostics(coord)

    assert diag["n_steps"] == 3
    room_diag = diag["rooms"]["living_room"]
    assert "fit_metrics" in room_diag
    assert room_diag["fit_metrics"]["n_samples"] == 3
    assert "parameter_validation" in room_diag
    assert room_diag["parameter_validation"]["mass_valid"] is True
    assert "controller_performance" in room_diag
    assert room_diag["controller_performance"]["n_samples"] == 3
