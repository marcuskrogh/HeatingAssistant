"""Unit tests for the thermal model."""

import pytest
import sys
import os

# Allow imports from the custom_components directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
    Window,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_two_room_model() -> HouseModel:
    """Living room connected to bedroom, both losing heat to a cold outside."""
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,   # J/K
        r_external=0.05,             # K/W
        connections=[RoomConnection(connected_room="bedroom", r_value=0.2)],
        temperature=20.0,
        setpoint=21.0,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=3_000_000.0,
        r_external=0.08,
        connections=[RoomConnection(connected_room="living_room", r_value=0.2)],
        temperature=18.0,
        setpoint=20.0,
    )
    return HouseModel([living, bedroom])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRoomConstruction:
    def test_room_names(self):
        model = make_two_room_model()
        assert set(model.room_names) == {"living_room", "bedroom"}

    def test_initial_temperatures(self):
        model = make_two_room_model()
        temps = model.temperatures
        assert temps["living_room"] == pytest.approx(20.0)
        assert temps["bedroom"] == pytest.approx(18.0)

    def test_set_temperatures(self):
        model = make_two_room_model()
        model.set_temperatures({"living_room": 22.5, "bedroom": 19.0})
        assert model.temperatures["living_room"] == pytest.approx(22.5)
        assert model.temperatures["bedroom"] == pytest.approx(19.0)


class TestThermalModelStep:
    def test_cooling_without_heat_input(self):
        """Rooms should cool towards outdoor temperature when no heat is applied."""
        model = make_two_room_model()
        outdoor_temp = 0.0

        for _ in range(20):
            model.step(
                dt=900,
                heat_inputs={},
                outdoor_temp=outdoor_temp,
                solar_gains={},
            )

        temps = model.temperatures
        # Both rooms must be closer to 0 than their initial temperatures
        assert temps["living_room"] < 20.0
        assert temps["bedroom"] < 18.0

    def test_heating_warms_room(self):
        """Applying heat should raise the room temperature above the no-heat baseline."""
        model_heated = make_two_room_model()
        model_free = make_two_room_model()
        outdoor_temp = 0.0

        heat_inputs = {"living_room": 3000.0}  # 3 kW

        for _ in range(10):
            model_heated.step(dt=900, heat_inputs=heat_inputs, outdoor_temp=outdoor_temp, solar_gains={})
            model_free.step(dt=900, heat_inputs={}, outdoor_temp=outdoor_temp, solar_gains={})

        assert (
            model_heated.temperatures["living_room"]
            > model_free.temperatures["living_room"]
        )

    def test_solar_gain_warms_room(self):
        """Solar gains must raise room temperature compared to the no-gain case."""
        model_solar = make_two_room_model()
        model_free = make_two_room_model()
        outdoor_temp = 5.0

        for _ in range(10):
            model_solar.step(
                dt=900,
                heat_inputs={},
                outdoor_temp=outdoor_temp,
                solar_gains={"living_room": 500.0},
            )
            model_free.step(
                dt=900,
                heat_inputs={},
                outdoor_temp=outdoor_temp,
                solar_gains={},
            )

        assert (
            model_solar.temperatures["living_room"]
            > model_free.temperatures["living_room"]
        )

    def test_inter_room_heat_flow(self):
        """Heat should flow from warmer to cooler room."""
        warm = Room(
            name="warm",
            thermal_mass=5_000_000.0,
            r_external=1e6,  # virtually no external loss
            connections=[RoomConnection(connected_room="cool", r_value=0.1)],
            temperature=25.0,
        )
        cool = Room(
            name="cool",
            thermal_mass=5_000_000.0,
            r_external=1e6,
            connections=[RoomConnection(connected_room="warm", r_value=0.1)],
            temperature=15.0,
        )
        model = HouseModel([warm, cool])

        for _ in range(20):
            model.step(dt=900, heat_inputs={}, outdoor_temp=20.0, solar_gains={})

        temps = model.temperatures
        # warm room cools, cool room warms
        assert temps["warm"] < 25.0
        assert temps["cool"] > 15.0

    def test_state_not_mutated_by_predict(self):
        """predict() must not alter the model's current state."""
        model = make_two_room_model()
        original_temps = dict(model.temperatures)

        model.predict(
            horizon=4,
            dt=900,
            heat_schedule=[{"living_room": 1000}] * 4,
            outdoor_temps=[0.0] * 4,
            solar_gain_schedule=[{}] * 4,
        )

        assert model.temperatures == pytest.approx(original_temps)

    def test_predict_returns_correct_length(self):
        model = make_two_room_model()
        preds = model.predict(
            horizon=6,
            dt=900,
            heat_schedule=[{}] * 6,
            outdoor_temps=[5.0] * 6,
            solar_gain_schedule=[{}] * 6,
        )
        assert len(preds) == 6
        for p in preds:
            assert set(p.keys()) == {"living_room", "bedroom"}

    def test_single_room_model(self):
        """The model should work with a single isolated room."""
        room = Room(
            name="studio",
            thermal_mass=2_000_000.0,
            r_external=0.1,
            temperature=20.0,
        )
        model = HouseModel([room])
        new_temps = model.step(dt=900, heat_inputs={"studio": 1000}, outdoor_temp=5.0, solar_gains={})
        assert "studio" in new_temps


class TestWindowModel:
    def test_window_can_be_added_to_room(self):
        room = Room(
            name="sunny",
            thermal_mass=4_000_000.0,
            r_external=0.05,
            windows=[
                Window(area=2.0, orientation=180.0, tilt=90.0)
            ],
        )
        assert len(room.windows) == 1
        assert room.windows[0].area == pytest.approx(2.0)
