"""Integration tests for controller.factory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heating_assistant.controller.factory import (
    ControllerBuildConfig,
    build_mpc_controller,
)
from custom_components.heating_assistant.controller.facade import (
    HeatingLinearisedMPC,
    HeatingNonlinearMPC,
)
from custom_components.heating_assistant.const import (
    CONF_MPC_MODE,
    MPC_MODE_NONLINEAR,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.mpc_mode_validation import (
    MpcModeUnavailableError,
)
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from tests.helpers.coordinator_stubs import make_minimal_coordinator, wire_room_enablement


def _make_house_model():
    room = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=20.0,
        setpoint=21.0,
    )
    return HouseModel([room])


@pytest.mark.integration
def test_controller_build_config_from_coordinator_applies_overrides():
    coord = make_minimal_coordinator()
    coord._tracking_weight = 2.0
    coord._horizon = 8
    coord._ipopt_available = True
    coord.model = _make_house_model()
    coord.heat_sources = [ElectricHeater("hp", "living_room", 2000.0)]

    config = ControllerBuildConfig.from_coordinator(
        coord,
        overrides={
            "tracking_weight": 5.0,
            "horizon": 12,
            CONF_MPC_MODE: MPC_MODE_NONLINEAR,
        },
    )

    assert config.tracking_weight == pytest.approx(5.0)
    assert config.horizon == 12
    assert config.dt == pytest.approx(900.0)
    assert config.mpc_mode == MPC_MODE_NONLINEAR


@pytest.mark.integration
def test_build_mpc_controller_constructs_controller():
    model = _make_house_model()
    sources = [ElectricHeater("hp", "living_room", 2000.0)]
    config = ControllerBuildConfig(
        model=model,
        heat_sources=sources,
        horizon=4,
        dt=900.0,
        latitude=55.0,
        longitude=12.0,
        tracking_weight=1.0,
        energy_weight=0.1,
        smoothing_weight=0.05,
        soft_constraint_weight=10.0,
        soft_constraint_linear_weight=0.0,
        terminal_weight=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
        sigma_b=0.002,
        energy_price_weight=0.0,
    )

    controller = build_mpc_controller(config)

    assert controller.horizon == 4
    assert controller._dt == pytest.approx(900.0)
    assert controller.mpc_mode == "linear"
    assert controller.solver_active == "highs"
    assert isinstance(controller._mpc, HeatingLinearisedMPC)


@pytest.mark.integration
def test_build_mpc_controller_constructs_nonlinear_ipopt_controller():
    model = _make_house_model()
    sources = [ElectricHeater("hp", "living_room", 2000.0)]
    config = ControllerBuildConfig(
        model=model,
        heat_sources=sources,
        horizon=2,
        dt=900.0,
        latitude=55.0,
        longitude=12.0,
        tracking_weight=1.0,
        energy_weight=0.1,
        smoothing_weight=0.05,
        soft_constraint_weight=10.0,
        soft_constraint_linear_weight=0.0,
        terminal_weight=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
        sigma_b=0.002,
        energy_price_weight=0.0,
        mpc_mode=MPC_MODE_NONLINEAR,
        ipopt_available=True,
    )

    controller = build_mpc_controller(config)

    assert controller.mpc_mode == MPC_MODE_NONLINEAR
    assert controller.solver_active == "ipopt"
    assert isinstance(controller._mpc, HeatingNonlinearMPC)


@pytest.mark.integration
def test_build_mpc_controller_rejects_nonlinear_when_ipopt_unavailable():
    model = _make_house_model()
    sources = [ElectricHeater("hp", "living_room", 2000.0)]
    config = ControllerBuildConfig(
        model=model,
        heat_sources=sources,
        horizon=2,
        dt=900.0,
        latitude=55.0,
        longitude=12.0,
        tracking_weight=1.0,
        energy_weight=0.1,
        smoothing_weight=0.05,
        soft_constraint_weight=10.0,
        soft_constraint_linear_weight=0.0,
        terminal_weight=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
        sigma_b=0.002,
        energy_price_weight=0.0,
        mpc_mode=MPC_MODE_NONLINEAR,
        ipopt_available=False,
        ipopt_unavailable_reason="solver probe failed",
    )

    with pytest.raises(MpcModeUnavailableError, match="IPOPT solver"):
        build_mpc_controller(config)
