"""Unit tests for the MPC controller."""

import sys
import os
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from custom_components.heating_assistant.controller import MPCController


def make_model_and_sources():
    """Simple two-room model with one heater per room."""
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[RoomConnection("bedroom", 0.2)],
        temperature=18.0,
        setpoint=21.0,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=3_000_000.0,
        r_external=0.08,
        connections=[RoomConnection("living_room", 0.2)],
        temperature=17.0,
        setpoint=20.0,
    )
    model = HouseModel([living, bedroom])

    heater_lr = ElectricHeater("lr_heater", "living_room", max_power=2000.0)
    heater_br = ElectricHeater("br_heater", "bedroom", max_power=1500.0)

    return model, [heater_lr, heater_br]


class TestMPCController:
    def test_actions_cover_all_sources(self):
        model, sources = make_model_and_sources()
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)

        # All sources should receive a control action
        for src in sources:
            assert src.name in actions

    def test_fractions_in_range(self):
        model, sources = make_model_and_sources()
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)

        for name, frac in actions.items():
            assert 0.0 <= frac <= 1.0, f"Fraction out of range for {name}: {frac}"

    def test_heats_when_below_setpoint(self):
        """When rooms are well below setpoint and it is cold outside, heaters should activate."""
        model, sources = make_model_and_sources()
        # Set a very cold outdoor temperature to maximise the heating demand
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-10.0, now=now)

        # At least one source should be on
        assert any(frac > 0.0 for frac in actions.values())

    def test_no_heat_when_warm_enough(self):
        """When rooms are above setpoint, the controller should prefer turning heaters off."""
        living = Room(
            name="living_room",
            thermal_mass=5_000_000.0,
            r_external=0.05,
            temperature=25.0,  # well above setpoint
            setpoint=21.0,
        )
        bedroom = Room(
            name="bedroom",
            thermal_mass=3_000_000.0,
            r_external=0.08,
            temperature=24.0,
            setpoint=20.0,
        )
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        # Use warm outdoor temp so cooling is not needed
        actions = ctrl.compute(outdoor_temp=22.0, now=now)

        assert all(frac == 0.0 for frac in actions.values())

    def test_solar_gains_provided_externally(self):
        """Controller should accept pre-computed solar gains and return valid actions."""
        model, sources = make_model_and_sources()
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        gains = {"living_room": 300.0, "bedroom": 100.0}
        actions = ctrl.compute(outdoor_temp=5.0, solar_gains=gains, now=now)

        for src in sources:
            assert src.name in actions
            assert 0.0 <= actions[src.name] <= 1.0

    def test_controller_updates_source_state(self):
        """After compute(), each source's current_power should reflect the chosen fraction."""
        model, sources = make_model_and_sources()
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)

        for src in sources:
            expected = src.thermal_power(actions[src.name])
            assert src.current_power == pytest.approx(expected, rel=1e-6)

    def test_finer_granularity_levels(self):
        """Controller should use finer levels than the original [0, 0.33, 0.67, 1.0]."""
        model, sources = make_model_and_sources()
        ctrl = MPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)

        # With finer levels, we should see fractional values beyond {0, 0.33, 0.67, 1.0}
        # At minimum the controller should be able to produce 0.1-step fractions
        for frac in actions.values():
            assert frac * 10 == pytest.approx(round(frac * 10), abs=1e-9), (
                f"Fraction {frac} not on 0.1-step grid"
            )

    def test_heat_pump_min_power_respected(self):
        """A heat pump with min_power should never produce output below min_power."""
        living = Room(
            name="living_room",
            thermal_mass=5_000_000.0,
            r_external=0.05,
            temperature=20.5,  # close to setpoint → low demand
            setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump(
            "hp1", "living_room", max_power=6100.0,
            cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0,
        )
        ctrl = MPCController(model, [hp], horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=7.0, now=now)

        # The heat pump's current_power should be either 0 or >= min_power
        assert hp.current_power == 0.0 or hp.current_power >= 1000.0
