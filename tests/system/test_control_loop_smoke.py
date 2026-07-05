"""System smoke test: physics model → controller factory → forecast payload."""

from __future__ import annotations

import pytest

from custom_components.heating_assistant.controller.factory import (
    ControllerBuildConfig,
    build_mpc_controller,
)
from custom_components.heating_assistant.coordinator.forecast_payload import (
    build_forecast_payload,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from tests.helpers.coordinator_stubs import make_minimal_coordinator, wire_room_enablement


def _build_stack():
    room = Room(
        name="studio",
        thermal_mass=4_000_000.0,
        r_external=0.05,
        temperature=19.0,
        setpoint=21.0,
    )
    model = HouseModel([room])
    source = ElectricHeater("heater", "studio", 2500.0)
    config = ControllerBuildConfig(
        model=model,
        heat_sources=[source],
        horizon=3,
        dt=900.0,
        latitude=55.7,
        longitude=12.6,
        tracking_weight=1.0,
        energy_weight=0.05,
        smoothing_weight=0.02,
        soft_constraint_weight=10.0,
        soft_constraint_linear_weight=0.0,
        terminal_weight=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
        sigma_b=0.002,
        energy_price_weight=0.0,
    )
    controller = build_mpc_controller(config)
    coord = make_minimal_coordinator(room_names=["studio"], horizon=3)
    coord.model = model
    coord.heat_sources = [source]
    coord.controller = controller
    coord.outdoor_temp = 2.0
    coord.filtered_temperatures = {"studio": 19.2}
    coord.measured_temperatures = {"studio": 19.0}
    wire_room_enablement(coord)
    coord.build_forecast_payload = (
        __import__(
            "custom_components.heating_assistant.coordinator.core",
            fromlist=["HeatingAssistantCoordinator"],
        ).HeatingAssistantCoordinator.build_forecast_payload.__get__(coord)
    )
    return coord, controller


@pytest.mark.system
def test_mpc_compute_populates_forecast_payload():
    """A minimal house stack must run MPC and produce a WebSocket-ready payload."""
    coord, controller = _build_stack()

    actions = controller.compute(
        outdoor_temp=2.0,
        run_optimization=True,
        cloud_cover_now=0.2,
    )
    assert isinstance(actions, dict)

    coord.predictions = list(controller.predictions)
    coord.heating_schedule = list(controller.heating_schedule)
    coord.outdoor_forecast = list(controller.outdoor_forecast)
    coord.solar_forecast = list(controller.solar_forecast)

    payload = coord.build_forecast_payload(room_names=["studio"])

    assert "studio" in payload["rooms"]
    assert len(payload["rooms"]["studio"]["forecast"]) >= 2
    assert payload["step_seconds"] == pytest.approx(900.0)
