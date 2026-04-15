"""Unit tests for advanced visualisation features."""

import sys
import os
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
    Window,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from custom_components.heating_assistant.controller import HeatingMPCController as MPCController


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_two_room_model():
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[RoomConnection("bedroom", 0.2)],
        temperature=20.0,
        setpoint=21.0,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=3_000_000.0,
        r_external=0.08,
        connections=[RoomConnection("living_room", 0.2)],
        temperature=18.0,
        setpoint=20.0,
    )
    return HouseModel([living, bedroom])


def make_single_room_model():
    room = Room(
        name="studio",
        thermal_mass=4_000_000.0,
        r_external=0.05,
        temperature=15.0,
        setpoint=21.0,
    )
    return HouseModel([room])


# ---------------------------------------------------------------------------
# Tests: compute_heat_flows
# ---------------------------------------------------------------------------

class TestComputeHeatFlows:
    def test_returns_all_rooms(self):
        model = make_two_room_model()
        flows = model.compute_heat_flows(outdoor_temp=0.0)
        assert "living_room" in flows
        assert "bedroom" in flows

    def test_external_loss_positive_when_warm(self):
        """Room warmer than outdoors → positive external_loss (losing heat)."""
        model = make_two_room_model()
        flows = model.compute_heat_flows(outdoor_temp=0.0)
        assert flows["living_room"]["external_loss"] > 0
        assert flows["bedroom"]["external_loss"] > 0

    def test_external_loss_correct_value(self):
        """External loss should be (T_room - T_out) / R_ext."""
        model = make_single_room_model()
        flows = model.compute_heat_flows(outdoor_temp=5.0)
        expected = (15.0 - 5.0) / 0.05  # 200 W
        assert flows["studio"]["external_loss"] == pytest.approx(expected, rel=1e-6)

    def test_inter_room_flow_direction(self):
        """Heat should flow from warmer to cooler room."""
        model = make_two_room_model()
        flows = model.compute_heat_flows(outdoor_temp=0.0)
        # living_room (20°C) → bedroom (18°C): positive flow
        assert flows["living_room"]["bedroom"] > 0
        # bedroom (18°C) → living_room (20°C): negative flow (gaining heat)
        assert flows["bedroom"]["living_room"] < 0

    def test_inter_room_flow_symmetry(self):
        """Flow from A→B should equal -flow from B→A."""
        model = make_two_room_model()
        flows = model.compute_heat_flows(outdoor_temp=0.0)
        assert flows["living_room"]["bedroom"] == pytest.approx(
            -flows["bedroom"]["living_room"], rel=1e-6
        )

    def test_total_loss_is_sum(self):
        """Total loss should be sum of external + inter-room flows."""
        model = make_two_room_model()
        flows = model.compute_heat_flows(outdoor_temp=0.0)
        lr = flows["living_room"]
        expected = lr["external_loss"] + lr["bedroom"]
        assert lr["total_loss"] == pytest.approx(expected, abs=0.1)

    def test_no_loss_at_equilibrium(self):
        """When room is at outdoor temp and no connections, no heat loss."""
        room = Room(
            name="test",
            thermal_mass=4_000_000.0,
            r_external=0.05,
            temperature=10.0,
        )
        model = HouseModel([room])
        flows = model.compute_heat_flows(outdoor_temp=10.0)
        assert flows["test"]["external_loss"] == pytest.approx(0.0)
        assert flows["test"]["total_loss"] == pytest.approx(0.0)

    def test_single_room_no_connections(self):
        """Single room without connections should only have external loss."""
        model = make_single_room_model()
        flows = model.compute_heat_flows(outdoor_temp=5.0)
        assert "external_loss" in flows["studio"]
        assert "total_loss" in flows["studio"]
        assert len(flows["studio"]) == 2  # only external_loss and total_loss


# ---------------------------------------------------------------------------
# Tests: time_constant
# ---------------------------------------------------------------------------

class TestTimeConstant:
    def test_positive(self):
        model = make_single_room_model()
        tau = model.time_constant("studio")
        assert tau > 0

    def test_single_room_value(self):
        """For a single isolated room, τ = C × R_ext."""
        model = make_single_room_model()
        tau = model.time_constant("studio")
        expected = 4_000_000.0 * 0.05  # 200_000 seconds
        assert tau == pytest.approx(expected, rel=1e-6)

    def test_connected_room_smaller_tau(self):
        """Adding connections should reduce the time constant (more paths for heat loss)."""
        model = make_two_room_model()
        tau_lr = model.time_constant("living_room")
        # Isolated equivalent: C * R_ext = 5e6 * 0.05 = 250_000
        isolated_tau = 5_000_000.0 * 0.05
        assert tau_lr < isolated_tau


# ---------------------------------------------------------------------------
# Tests: steady_state_temperature
# ---------------------------------------------------------------------------

class TestSteadyStateTemperature:
    def test_basic(self):
        """With heating, steady-state should be above outdoor temp."""
        model = make_single_room_model()
        t_ss = model.steady_state_temperature("studio", 2000.0, 0.0)
        assert t_ss > 0.0

    def test_correct_value_single_room(self):
        """T_ss = T_out + Q / G_total for a single room."""
        model = make_single_room_model()
        t_ss = model.steady_state_temperature("studio", 2000.0, 0.0)
        # G_total = 1/R_ext = 1/0.05 = 20 W/K
        expected = 0.0 + 2000.0 / 20.0  # 100 °C
        assert t_ss == pytest.approx(expected, rel=1e-6)

    def test_no_heating(self):
        """Without heating, steady-state equals outdoor temp."""
        model = make_single_room_model()
        t_ss = model.steady_state_temperature("studio", 0.0, 5.0)
        assert t_ss == pytest.approx(5.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Tests: controller predictions
# ---------------------------------------------------------------------------

class TestControllerPredictions:
    def test_predictions_stored_after_compute(self):
        living = Room(
            name="living_room", thermal_mass=5_000_000.0, r_external=0.05,
            connections=[RoomConnection("bedroom", 0.2)],
            temperature=18.0, setpoint=21.0,
        )
        bedroom = Room(
            name="bedroom", thermal_mass=3_000_000.0, r_external=0.08,
            connections=[RoomConnection("living_room", 0.2)],
            temperature=17.0, setpoint=20.0,
        )
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", 2000),
            ElectricHeater("br_heater", "bedroom", 1500),
        ]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now)

        preds = ctrl.predictions
        assert len(preds) == 4
        for p in preds:
            assert "living_room" in p
            assert "bedroom" in p

    def test_predictions_empty_before_compute(self):
        model = make_two_room_model()
        sources = [ElectricHeater("lr", "living_room", 2000)]
        ctrl = MPCController(model, sources, horizon=3, dt=900)
        assert ctrl.predictions == []

    def test_predictions_reflect_heating(self):
        """When heating, predicted temperatures should increase."""
        living = Room(
            name="living_room", thermal_mass=5_000_000.0, r_external=0.05,
            temperature=15.0, setpoint=21.0,
        )
        model = HouseModel([living])
        sources = [ElectricHeater("lr", "living_room", 5000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now)

        preds = ctrl.predictions
        # With strong heating demand, temperatures should trend upward
        temps = [p["living_room"] for p in preds]
        # At least the last temperature should be above the initial
        assert temps[-1] >= 15.0

    def test_predictions_dont_mutate_model(self):
        """Compute with predictions should not change model state."""
        living = Room(
            name="test", thermal_mass=5_000_000.0, r_external=0.05,
            temperature=20.0, setpoint=21.0,
        )
        model = HouseModel([living])
        sources = [ElectricHeater("h", "test", 2000)]
        ctrl = MPCController(model, sources, horizon=3, dt=900)
        original_temp = model.temperatures["test"]
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now)
        assert model.temperatures["test"] == pytest.approx(original_temp)


# ---------------------------------------------------------------------------
# Tests: controller forecast data storage
# ---------------------------------------------------------------------------

class TestControllerForecastData:
    """Test that the controller stores forecast data for visualisation."""

    def test_outdoor_forecast_stored(self):
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)

        assert len(ctrl.outdoor_forecast) == 4
        # Persistence forecast: all values equal current outdoor temp
        for val in ctrl.outdoor_forecast:
            assert val == pytest.approx(5.0)

    def test_solar_forecast_stored(self):
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)

        assert len(ctrl.solar_forecast) == 4
        for step in ctrl.solar_forecast:
            assert "studio" in step
            assert isinstance(step["studio"], (int, float))

    def test_heating_schedule_stored(self):
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)

        assert len(ctrl.heating_schedule) == 4
        for step in ctrl.heating_schedule:
            assert "studio" in step
            assert step["studio"] >= 0.0

    def test_forecast_data_empty_before_compute(self):
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)

        assert ctrl.outdoor_forecast == []
        assert ctrl.solar_forecast == []
        assert ctrl.heating_schedule == []

    def test_heating_schedule_matches_predictions_count(self):
        model = make_two_room_model()
        sources = [
            ElectricHeater("lr", "living_room", 2000),
            ElectricHeater("br", "bedroom", 1500),
        ]
        ctrl = MPCController(model, sources, horizon=6, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now)

        assert len(ctrl.predictions) == 6
        assert len(ctrl.heating_schedule) == 6
        assert len(ctrl.solar_forecast) == 6
        assert len(ctrl.outdoor_forecast) == 6

    def test_heating_schedule_covers_all_rooms(self):
        model = make_two_room_model()
        sources = [
            ElectricHeater("lr", "living_room", 2000),
            ElectricHeater("br", "bedroom", 1500),
        ]
        ctrl = MPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now)

        for step in ctrl.heating_schedule:
            assert "living_room" in step
            assert "bedroom" in step


# ---------------------------------------------------------------------------
# Tests: coordinator simulate_thermal_response and estimate_parameters
# ---------------------------------------------------------------------------

class TestCoordinatorSetupHelpers:
    """Test the setup helper methods directly on HouseModel (coordinator tests
    require HA runtime, so we test the underlying model methods here)."""

    def test_simulate_response_warming(self):
        """Simulation with heating should show temperature increase."""
        model = make_single_room_model()
        # Simulate manually using predict
        steps = 120  # 2 hours at 1-minute steps
        dt = 60.0
        initial_temps = {"studio": 10.0}
        heat_schedule = [{"studio": 2000.0}] * steps
        outdoor_temps = [0.0] * steps
        solar_schedule = [{"studio": 0.0}] * steps

        preds = model.predict(
            horizon=steps, dt=dt,
            heat_schedule=heat_schedule,
            outdoor_temps=outdoor_temps,
            solar_gain_schedule=solar_schedule,
            initial_temps=initial_temps,
        )
        # Temperature should increase over time
        assert preds[-1]["studio"] > 10.0
        # Temperature should increase monotonically (with constant heat > loss)
        for i in range(1, len(preds)):
            assert preds[i]["studio"] >= preds[i - 1]["studio"] - 0.01

    def test_steady_state_heater_sizing(self):
        """Verify steady-state can be used to check heater sizing."""
        model = make_single_room_model()
        # With 1000W and R=0.05, T_ss = T_out + 1000/20 = T_out + 50
        t_ss = model.steady_state_temperature("studio", 1000.0, -10.0)
        assert t_ss == pytest.approx(40.0, rel=1e-6)  # -10 + 50 = 40

    def test_time_constant_in_hours(self):
        """Time constant for setup display."""
        model = make_single_room_model()
        tau = model.time_constant("studio")
        hours = tau / 3600.0
        # 4e6 * 0.05 = 200000s ≈ 55.6 hours
        assert hours == pytest.approx(200000 / 3600, rel=1e-3)


# ---------------------------------------------------------------------------
# Tests: outdoor forecast with weather data
# ---------------------------------------------------------------------------

class TestOutdoorForecast:
    """Test that external outdoor forecasts are correctly used by the controller."""

    def test_external_forecast_replaces_persistence(self):
        """When an outdoor_forecast is provided, it replaces the persistence forecast."""
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        external_forecast = [3.0, 2.5, 2.0, 1.5]
        ctrl.compute(outdoor_temp=5.0, now=now, outdoor_forecast=external_forecast)

        assert len(ctrl.outdoor_forecast) == 4
        for i, val in enumerate(ctrl.outdoor_forecast):
            assert val == pytest.approx(external_forecast[i])

    def test_persistence_fallback_when_no_forecast(self):
        """Without an outdoor_forecast, the persistence forecast is used."""
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        ctrl.compute(outdoor_temp=5.0, now=now, outdoor_forecast=None)

        assert len(ctrl.outdoor_forecast) == 4
        for val in ctrl.outdoor_forecast:
            assert val == pytest.approx(5.0)

    def test_external_forecast_too_short_falls_back(self):
        """When the external forecast is shorter than the horizon, persistence is used."""
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=4, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        short_forecast = [3.0, 2.5]  # Only 2 entries, horizon is 4
        ctrl.compute(outdoor_temp=5.0, now=now, outdoor_forecast=short_forecast)

        # Falls back to persistence since forecast is too short
        assert len(ctrl.outdoor_forecast) == 4
        for val in ctrl.outdoor_forecast:
            assert val == pytest.approx(5.0)

    def test_external_forecast_longer_than_horizon(self):
        """When the external forecast is longer than needed, only N entries are used."""
        model = make_single_room_model()
        sources = [ElectricHeater("h", "studio", 2000)]
        ctrl = MPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        long_forecast = [3.0, 2.5, 2.0, 1.5, 1.0, 0.5]
        ctrl.compute(outdoor_temp=5.0, now=now, outdoor_forecast=long_forecast)

        assert len(ctrl.outdoor_forecast) == 3
        assert ctrl.outdoor_forecast[0] == pytest.approx(3.0)
        assert ctrl.outdoor_forecast[1] == pytest.approx(2.5)
        assert ctrl.outdoor_forecast[2] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Tests: coordinator weather forecast interpolation
# ---------------------------------------------------------------------------

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator


class TestForecastInterpolation:
    """Test the _interpolate_forecast static method on the coordinator."""

    def test_interpolate_exact_match(self):
        entries = [(100.0, 5.0), (200.0, 10.0), (300.0, 15.0)]
        result = HeatingAssistantCoordinator._interpolate_forecast(entries, 200.0)
        assert result == pytest.approx(10.0)

    def test_interpolate_midpoint(self):
        entries = [(100.0, 0.0), (200.0, 10.0)]
        result = HeatingAssistantCoordinator._interpolate_forecast(entries, 150.0)
        assert result == pytest.approx(5.0)

    def test_interpolate_before_first(self):
        entries = [(100.0, 5.0), (200.0, 10.0)]
        result = HeatingAssistantCoordinator._interpolate_forecast(entries, 50.0)
        assert result == pytest.approx(5.0)

    def test_interpolate_after_last(self):
        entries = [(100.0, 5.0), (200.0, 10.0)]
        result = HeatingAssistantCoordinator._interpolate_forecast(entries, 300.0)
        assert result == pytest.approx(10.0)

    def test_interpolate_empty_entries(self):
        result = HeatingAssistantCoordinator._interpolate_forecast([], 100.0)
        assert result == pytest.approx(5.0)  # fallback

    def test_interpolate_quarter_point(self):
        entries = [(0.0, 0.0), (100.0, 20.0)]
        result = HeatingAssistantCoordinator._interpolate_forecast(entries, 25.0)
        assert result == pytest.approx(5.0)

    def test_interpolate_multiple_segments(self):
        entries = [(0.0, 0.0), (100.0, 10.0), (200.0, 5.0)]
        # In first segment
        r1 = HeatingAssistantCoordinator._interpolate_forecast(entries, 50.0)
        assert r1 == pytest.approx(5.0)
        # In second segment
        r2 = HeatingAssistantCoordinator._interpolate_forecast(entries, 150.0)
        assert r2 == pytest.approx(7.5)
