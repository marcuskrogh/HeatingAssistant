"""Unit tests for advanced visualisation features."""

import sys
import os
import pytest
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
    Window,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from custom_components.heating_assistant.controller import HeatingMPCController as MPCController
from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.sensor import (
    HeatingPowerForecastSensor,
    SolarGainForecastSensor,
    TemperatureForecastSensor,
)


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
        """External loss is the instantaneous air-infiltration plus
        wall-conduction flow.  With the wall initialised at the air
        temperature it exceeds the steady-state value slightly (the wall
        is warmer than its equilibrium); once the wall settles to its
        steady state the total reduces exactly to (T_a − T_out)/R_ext."""
        model = make_single_room_model()
        room = model.rooms["studio"]
        flows = model.compute_heat_flows(outdoor_temp=5.0)
        g_inf, _g_aw, g_we = room.conductances()
        expected_inst = g_inf * (15.0 - 5.0) + g_we * (15.0 - 5.0)
        assert flows["studio"]["external_loss"] == pytest.approx(
            expected_inst, rel=1e-3
        )
        # Steady-state invariant: with the wall at its equilibrium value the
        # total external loss equals the 1R1C expression exactly.
        rho = _g_aw / (_g_aw + g_we)
        room.wall_temperature = rho * 15.0 + (1.0 - rho) * 5.0
        flows_ss = model.compute_heat_flows(outdoor_temp=5.0)
        assert flows_ss["studio"]["external_loss"] == pytest.approx(
            (15.0 - 5.0) / 0.05, rel=1e-3
        )

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

        assert len(ctrl.solar_forecast) == 5  # N+1: covers now through now+N*dt
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
        assert len(ctrl.solar_forecast) == 7  # N+1: covers now through now+N*dt
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
# Tests: forecast sensors surface failure (or first-cycle absence) as
# ``unknown`` instead of echoing current state, so dashboards show a gap.
# ---------------------------------------------------------------------------

class TestForecastSensorsReportUnknownOnEmpty:
    def test_temperature_forecast_is_none_before_first_plan(self):
        room = SimpleNamespace(temperature=20.37, setpoint=21.0, windows=[])
        coordinator = SimpleNamespace(
            predictions=[],
            model=SimpleNamespace(rooms={"living_room": room}),
            heat_sources=[],
            solar_gains={},
            outdoor_forecast=[],
            solar_forecast=[],
            heating_schedule=[],
            outdoor_temp=5.0,
            dt=900,
            controller=SimpleNamespace(constraint_offset=2.0),
        )

        sensor = TemperatureForecastSensor(coordinator, "living_room")

        assert sensor.native_value is None

    def test_heating_power_forecast_is_none_before_first_plan(self):
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        coordinator = SimpleNamespace(
            heating_schedule=[],
            heat_sources=[
                SimpleNamespace(room="living_room", current_power=432.14),
                SimpleNamespace(room="bedroom", current_power=999.0),
            ],
            model=SimpleNamespace(rooms={"living_room": room}),
            solar_gains={},
            predictions=[],
            outdoor_forecast=[],
            solar_forecast=[],
            outdoor_temp=5.0,
            dt=900,
        )

        sensor = HeatingPowerForecastSensor(coordinator, "living_room")

        assert sensor.native_value is None

    def test_solar_gain_forecast_is_none_before_first_plan(self):
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        coordinator = SimpleNamespace(
            solar_forecast=[],
            solar_gains={"living_room": 87.64},
            model=SimpleNamespace(rooms={"living_room": room}),
            heat_sources=[],
            predictions=[],
            outdoor_forecast=[],
            heating_schedule=[],
            outdoor_temp=5.0,
            dt=900,
        )

        sensor = SolarGainForecastSensor(coordinator, "living_room")

        assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Tests: forecast sensor timestamp correctness
# ---------------------------------------------------------------------------

class TestForecastSensorTimestamps:
    """Verify that forecast attribute timestamps match the solar forecast values."""

    _DT = 900  # seconds per step

    def _make_coordinator(self, horizon: int, solar_values: list):
        """Create a minimal coordinator stub with populated solar_forecast."""
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        # solar_forecast has N+1 entries (k=0...N), one per time step from now
        return SimpleNamespace(
            solar_forecast=solar_values,
            solar_gains={"studio": solar_values[0].get("studio", 0.0)},
            outdoor_forecast=[5.0] * horizon,
            heating_schedule=[{"studio": 1000.0}] * horizon,
            predictions=[{"studio": 21.0}] * horizon,
            model=SimpleNamespace(rooms={"studio": room}),
            heat_sources=[],
            outdoor_temp=5.0,
            dt=self._DT,
            controller=SimpleNamespace(constraint_offset=2.0),
        )

    def test_solar_forecast_entry_count(self):
        """SolarGainForecastSensor forecast list must have N+1 entries (bridge + N steps)."""
        solar = [{"studio": float(k * 100)} for k in range(5)]  # N+1 = 5 → N=4
        coordinator = self._make_coordinator(horizon=4, solar_values=solar)
        sensor = SolarGainForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert len(attrs["forecast"]) == 5  # N+1 entries

    def test_solar_forecast_first_entry_at_now(self):
        """First forecast entry must be at 'now' (the bridge point)."""
        solar = [{"studio": 50.0}]
        coordinator = self._make_coordinator(horizon=0, solar_values=solar)
        sensor = SolarGainForecastSensor(coordinator, "studio")

        before = datetime.now(tz=timezone.utc)
        attrs = sensor.extra_state_attributes
        after = datetime.now(tz=timezone.utc)

        t0 = datetime.fromisoformat(attrs["forecast"][0]["time"])
        assert before <= t0 <= after

    def test_solar_forecast_timestamps_spaced_by_dt(self):
        """Each consecutive forecast entry must be exactly dt seconds apart."""
        dt = self._DT
        solar = [{"studio": float(k * 10)} for k in range(5)]  # N+1 = 5
        coordinator = self._make_coordinator(horizon=4, solar_values=solar)
        sensor = SolarGainForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        fc = attrs["forecast"]
        for i in range(1, len(fc)):
            t_prev = datetime.fromisoformat(fc[i - 1]["time"])
            t_curr = datetime.fromisoformat(fc[i]["time"])
            gap = (t_curr - t_prev).total_seconds()
            assert gap == pytest.approx(dt, abs=1.0)

    def test_solar_forecast_values_match_source(self):
        """Each entry's solar_gain must come from the corresponding solar_forecast slot."""
        solar = [{"studio": float(k * 10)} for k in range(5)]
        coordinator = self._make_coordinator(horizon=4, solar_values=solar)
        sensor = SolarGainForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        fc = attrs["forecast"]
        for i, expected in enumerate(solar):
            assert fc[i]["solar_gain"] == pytest.approx(expected["studio"], abs=0.11)

    def test_solar_forecast_horizon_steps_attribute(self):
        """horizon_steps must equal N (the OCP horizon), not N+1."""
        solar = [{"studio": 0.0}] * 7  # N+1 = 7 → N = 6
        coordinator = self._make_coordinator(horizon=6, solar_values=solar)
        sensor = SolarGainForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert attrs["horizon_steps"] == 6

    def test_solar_forecast_fallback_when_empty(self):
        """When solar_forecast is empty, a single bridge entry at now is returned."""
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        coordinator = SimpleNamespace(
            solar_forecast=[],
            solar_gains={"studio": 123.4},
            model=SimpleNamespace(rooms={"studio": room}),
            heat_sources=[],
            predictions=[],
            outdoor_forecast=[],
            heating_schedule=[],
            outdoor_temp=5.0,
            dt=self._DT,
        )
        sensor = SolarGainForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert len(attrs["forecast"]) == 1
        assert attrs["forecast"][0]["solar_gain"] == pytest.approx(123.4, abs=0.1)


# ---------------------------------------------------------------------------
# Tests: heating plan forecast timestamp correctness
# ---------------------------------------------------------------------------

class TestHeatingPlanForecastTimestamps:
    """Verify that HeatingPowerForecastSensor forecast timestamps use start-of-interval labelling."""

    _DT = 900  # seconds per step

    def _make_coordinator(self, horizon: int, power: float = 1000.0):
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        return SimpleNamespace(
            heating_schedule=[{"studio": power}] * horizon,
            heat_sources=[SimpleNamespace(room="studio", current_power=power)],
            model=SimpleNamespace(rooms={"studio": room}),
            solar_forecast=[],
            solar_gains={},
            predictions=[],
            outdoor_forecast=[],
            outdoor_temp=5.0,
            dt=self._DT,
        )

    def test_heating_plan_entry_count_equals_horizon(self):
        """forecast list must have exactly N entries for an N-step schedule."""
        coordinator = self._make_coordinator(horizon=6)
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert len(attrs["forecast"]) == 6

    def test_heating_plan_first_entry_at_now(self):
        """First forecast entry must be labelled at 'now' (bridges history)."""
        coordinator = self._make_coordinator(horizon=4)
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        before = datetime.now(tz=timezone.utc)
        attrs = sensor.extra_state_attributes
        after = datetime.now(tz=timezone.utc)

        t0 = datetime.fromisoformat(attrs["forecast"][0]["time"])
        assert before <= t0 <= after

    def test_heating_plan_timestamps_spaced_by_dt(self):
        """Consecutive forecast entries must be exactly dt seconds apart."""
        coordinator = self._make_coordinator(horizon=5)
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        fc = attrs["forecast"]
        for i in range(1, len(fc)):
            t_prev = datetime.fromisoformat(fc[i - 1]["time"])
            t_curr = datetime.fromisoformat(fc[i]["time"])
            gap = (t_curr - t_prev).total_seconds()
            assert gap == pytest.approx(self._DT, abs=1.0)

    def test_heating_plan_values_match_schedule(self):
        """Each entry's heating_power must come from the corresponding schedule slot."""
        powers = [100.0 * (i + 1) for i in range(4)]
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        coordinator = SimpleNamespace(
            heating_schedule=[{"studio": p} for p in powers],
            heat_sources=[],
            model=SimpleNamespace(rooms={"studio": room}),
            solar_forecast=[],
            solar_gains={},
            predictions=[],
            outdoor_forecast=[],
            outdoor_temp=5.0,
            dt=self._DT,
        )
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        fc = attrs["forecast"]
        for i, expected in enumerate(powers):
            assert fc[i]["heating_power"] == pytest.approx(expected, abs=0.11)

    def test_heating_plan_horizon_steps_attribute(self):
        """horizon_steps must equal N (the OCP horizon length)."""
        coordinator = self._make_coordinator(horizon=8)
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert attrs["horizon_steps"] == 8

    def test_heating_plan_fallback_when_empty(self):
        """When schedule is empty, a single bridge entry from current_power is returned."""
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        sources = [SimpleNamespace(room="studio", current_power=432.0)]
        coordinator = SimpleNamespace(
            heating_schedule=[],
            heat_sources=sources,
            model=SimpleNamespace(rooms={"studio": room}),
            solar_forecast=[],
            solar_gains={},
            predictions=[],
            outdoor_forecast=[],
            outdoor_temp=5.0,
            dt=self._DT,
        )
        coordinator.sources_for_room = (
            lambda r: [s for s in coordinator.heat_sources if s.room == r]
        )
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert len(attrs["forecast"]) == 1
        assert attrs["forecast"][0]["heating_power"] == pytest.approx(432.0, abs=0.1)

    def test_heating_plan_fallback_missing_current_power(self):
        """Fallback must use getattr guard — missing current_power defaults to 0."""
        room = SimpleNamespace(temperature=20.0, setpoint=21.0, windows=[])
        sources = [SimpleNamespace(room="studio")]  # no current_power attr
        coordinator = SimpleNamespace(
            heating_schedule=[],
            heat_sources=sources,
            model=SimpleNamespace(rooms={"studio": room}),
            solar_forecast=[],
            solar_gains={},
            predictions=[],
            outdoor_forecast=[],
            outdoor_temp=5.0,
            dt=self._DT,
        )
        coordinator.sources_for_room = (
            lambda r: [s for s in coordinator.heat_sources if s.room == r]
        )
        sensor = HeatingPowerForecastSensor(coordinator, "studio")

        attrs = sensor.extra_state_attributes
        assert attrs["forecast"][0]["heating_power"] == pytest.approx(0.0, abs=0.1)


# ---------------------------------------------------------------------------
# Tests: coordinator update resilience
# ---------------------------------------------------------------------------

class TestCoordinatorUpdateResilience:
    @pytest.mark.asyncio
    async def test_apply_action_failure_keeps_visualisation_data(self):
        model = make_single_room_model()
        source = ElectricHeater("heater", "studio", 2000)

        coordinator = object.__new__(HeatingAssistantCoordinator)
        coordinator.hass = MagicMock()
        coordinator._temp_sensors = {}
        coordinator._window_sensors = {}
        coordinator._window_state = {"studio": "closed"}
        coordinator._window_state_since = {}
        coordinator._window_open_debounce = 0.0
        coordinator._window_open_close_settle = 0.0
        coordinator._window_open_q_inflation = 1.0
        coordinator._latitude = 0.0
        coordinator._longitude = 0.0
        coordinator._update_interval_s = 900
        coordinator.model = model
        coordinator.heat_sources = [source]
        coordinator.actions = {}
        coordinator.solar_gains = {}
        coordinator.outdoor_temp = 5.0
        coordinator.heat_flows = {}
        coordinator.predictions = []
        coordinator.outdoor_forecast = []
        coordinator.solar_forecast = []
        coordinator.heating_schedule = []
        coordinator.measured_temperatures = {"studio": 15.0}
        coordinator.filtered_temperatures = {"studio": 15.0}
        coordinator._room_schedule = {}
        coordinator._base_setpoint = {}
        coordinator._effective_setpoint = {}
        coordinator._schedule_disabled = {}
        coordinator._schedule_enabled = {}
        coordinator._room_comfort_offset = {}
        coordinator._room_enabled = {}
        coordinator._horizon = 4
        coordinator._weather_entity = None
        coordinator._price_entity = None
        coordinator._solar_radiation_entity = None
        coordinator._read_cloud_cover_now = MagicMock(return_value=None)
        coordinator._async_read_cloud_forecast = AsyncMock(return_value=None)
        coordinator._history_buffer = deque(maxlen=10)
        coordinator._read_outdoor_temp = MagicMock(return_value=4.0)
        coordinator._async_read_weather_forecast = AsyncMock(return_value=None)
        coordinator._apply_actions = AsyncMock(side_effect=RuntimeError("service failed"))
        coordinator.controller = MagicMock()
        coordinator.controller.compute.return_value = {"heater": 0.5}
        coordinator.controller.predictions = [{"studio": 21.25}]
        coordinator.controller.outdoor_forecast = [3.5]
        coordinator.controller.solar_forecast = [{"studio": 12.0}]
        coordinator.controller.heating_schedule = [{"studio": 900.0}]
        coordinator.controller.last_innovation = [0.0]
        coordinator.controller.filtered_temperatures = {"studio": 15.05}

        result = await coordinator._async_update_data()

        assert result["actions"] == {"heater": 0.5}
        assert result["predictions"] == [{"studio": 21.25}]
        assert result["outdoor_forecast"] == [3.5]
        assert result["solar_forecast"] == [{"studio": 12.0}]
        assert result["heating_schedule"] == [{"studio": 900.0}]
        assert len(coordinator.history_buffer) == 1

    @pytest.mark.asyncio
    async def test_missing_readings_clear_measured_temperatures(self):
        model = make_single_room_model()
        source = ElectricHeater("heater", "studio", 2000)

        coordinator = object.__new__(HeatingAssistantCoordinator)
        coordinator.hass = MagicMock()
        coordinator.hass.states.get = MagicMock(
            return_value=SimpleNamespace(state="unknown")
        )
        coordinator._temp_sensors = {"studio": ["sensor.studio_temp"]}
        coordinator._window_sensors = {}
        coordinator._window_state = {"studio": "closed"}
        coordinator._window_state_since = {}
        coordinator._window_open_debounce = 0.0
        coordinator._window_open_close_settle = 0.0
        coordinator._window_open_q_inflation = 1.0
        coordinator._latitude = 0.0
        coordinator._longitude = 0.0
        coordinator._update_interval_s = 900
        coordinator.model = model
        coordinator.heat_sources = [source]
        coordinator.actions = {}
        coordinator.solar_gains = {}
        coordinator.outdoor_temp = 5.0
        coordinator.heat_flows = {}
        coordinator.predictions = []
        coordinator.outdoor_forecast = []
        coordinator.solar_forecast = []
        coordinator.heating_schedule = []
        coordinator.measured_temperatures = {"studio": 15.0}
        coordinator.filtered_temperatures = {}
        coordinator._room_schedule = {}
        coordinator._base_setpoint = {}
        coordinator._effective_setpoint = {}
        coordinator._schedule_disabled = {}
        coordinator._schedule_enabled = {}
        coordinator._room_comfort_offset = {}
        coordinator._room_enabled = {}
        coordinator._horizon = 4
        coordinator._weather_entity = None
        coordinator._price_entity = None
        coordinator._solar_radiation_entity = None
        coordinator._read_cloud_cover_now = MagicMock(return_value=None)
        coordinator._async_read_cloud_forecast = AsyncMock(return_value=None)
        coordinator._history_buffer = deque(maxlen=10)
        coordinator._read_outdoor_temp = MagicMock(return_value=4.0)
        coordinator._async_read_weather_forecast = AsyncMock(return_value=None)
        coordinator._apply_actions = AsyncMock()
        coordinator.controller = MagicMock()
        coordinator.controller.compute.return_value = {"heater": 0.0}
        coordinator.controller.predictions = []
        coordinator.controller.outdoor_forecast = []
        coordinator.controller.solar_forecast = []
        coordinator.controller.heating_schedule = []
        coordinator.controller.last_innovation = None
        coordinator.controller.filtered_temperatures = {}

        assert coordinator.measured_temperatures == {"studio": 15.0}
        await coordinator._async_update_data()

        assert coordinator.measured_temperatures == {}

    @pytest.mark.asyncio
    async def test_none_sensor_state_is_ignored(self):
        model = make_single_room_model()
        source = ElectricHeater("heater", "studio", 2000)

        coordinator = object.__new__(HeatingAssistantCoordinator)
        coordinator.hass = MagicMock()
        coordinator.hass.states.get = MagicMock(return_value=SimpleNamespace(state=None))
        coordinator._temp_sensors = {"studio": ["sensor.studio_temp"]}
        coordinator._window_sensors = {}
        coordinator._window_state = {"studio": "closed"}
        coordinator._window_state_since = {}
        coordinator._window_open_debounce = 0.0
        coordinator._window_open_close_settle = 0.0
        coordinator._window_open_q_inflation = 1.0
        coordinator._latitude = 0.0
        coordinator._longitude = 0.0
        coordinator._update_interval_s = 900
        coordinator.model = model
        coordinator.heat_sources = [source]
        coordinator.actions = {}
        coordinator.solar_gains = {}
        coordinator.outdoor_temp = 5.0
        coordinator.heat_flows = {}
        coordinator.predictions = []
        coordinator.outdoor_forecast = []
        coordinator.solar_forecast = []
        coordinator.heating_schedule = []
        coordinator.measured_temperatures = {"studio": 15.0}
        coordinator.filtered_temperatures = {}
        coordinator._room_schedule = {}
        coordinator._base_setpoint = {}
        coordinator._effective_setpoint = {}
        coordinator._schedule_disabled = {}
        coordinator._schedule_enabled = {}
        coordinator._room_comfort_offset = {}
        coordinator._room_enabled = {}
        coordinator._horizon = 4
        coordinator._weather_entity = None
        coordinator._price_entity = None
        coordinator._solar_radiation_entity = None
        coordinator._read_cloud_cover_now = MagicMock(return_value=None)
        coordinator._async_read_cloud_forecast = AsyncMock(return_value=None)
        coordinator._history_buffer = deque(maxlen=10)
        coordinator._read_outdoor_temp = MagicMock(return_value=4.0)
        coordinator._async_read_weather_forecast = AsyncMock(return_value=None)
        coordinator._apply_actions = AsyncMock()
        coordinator.controller = MagicMock()
        coordinator.controller.compute.return_value = {"heater": 0.0}
        coordinator.controller.predictions = []
        coordinator.controller.outdoor_forecast = []
        coordinator.controller.solar_forecast = []
        coordinator.controller.heating_schedule = []
        coordinator.controller.last_innovation = None
        coordinator.controller.filtered_temperatures = {}

        await coordinator._async_update_data()

        assert coordinator.measured_temperatures == {}

    @pytest.mark.asyncio
    async def test_compute_failure_clears_forecasts_to_make_failure_visible(self):
        """When the MPC solver fails the coordinator clears the forecast and
        filtered fields so dashboards show a visible gap at the failure
        point instead of a fake thermal-model trajectory.  Applied actions
        and the weather forecast (read independently) are preserved so the
        system stays in a safe state and the outdoor chart keeps rendering.
        """
        model = make_single_room_model()
        source = ElectricHeater("heater", "studio", 2000)

        coordinator = object.__new__(HeatingAssistantCoordinator)
        coordinator.hass = MagicMock()
        coordinator._temp_sensors = {}
        coordinator._window_sensors = {}
        coordinator._window_state = {"studio": "closed"}
        coordinator._window_state_since = {}
        coordinator._window_open_debounce = 0.0
        coordinator._window_open_close_settle = 0.0
        coordinator._window_open_q_inflation = 1.0
        coordinator._latitude = 0.0
        coordinator._longitude = 0.0
        coordinator._update_interval_s = 900
        coordinator._horizon = 3
        coordinator.model = model
        coordinator.heat_sources = [source]
        coordinator.actions = {}
        coordinator.solar_gains = {}
        coordinator.outdoor_temp = 5.0
        coordinator.heat_flows = {}
        coordinator.predictions = []
        coordinator.outdoor_forecast = []
        coordinator.solar_forecast = []
        coordinator.heating_schedule = []
        coordinator.measured_temperatures = {"studio": 15.0}
        coordinator.filtered_temperatures = {"studio": 15.1}
        coordinator._room_schedule = {}
        coordinator._base_setpoint = {}
        coordinator._effective_setpoint = {}
        coordinator._schedule_disabled = {}
        coordinator._schedule_enabled = {}
        coordinator._room_comfort_offset = {}
        coordinator._room_enabled = {}
        coordinator._weather_entity = None
        coordinator._price_entity = None
        coordinator._solar_radiation_entity = None
        coordinator._read_cloud_cover_now = MagicMock(return_value=None)
        coordinator._async_read_cloud_forecast = AsyncMock(return_value=None)
        coordinator._history_buffer = deque(maxlen=10)
        coordinator._read_outdoor_temp = MagicMock(return_value=4.0)
        coordinator._async_read_weather_forecast = AsyncMock(
            return_value=[3.0, 2.5, 2.0]
        )
        coordinator._apply_actions = AsyncMock()
        coordinator.controller = MagicMock()
        coordinator.controller.compute.side_effect = RuntimeError("OCP failed")

        result = await coordinator._async_update_data()

        # Safe-state actions and the weather forecast pass through.
        assert result["actions"] == {"heater": 0.0}
        assert result["outdoor_forecast"] == [3.0, 2.5, 2.0]

        # MPC-dependent forecast fields are cleared so the gap is visible.
        assert result["predictions"] == []
        assert result["heating_schedule"] == []
        assert result["solar_forecast"] == []
        assert coordinator.filtered_temperatures == {}
        assert len(coordinator.history_buffer) == 1

    @pytest.mark.asyncio
    async def test_compute_failure_preserves_safe_actions(self):
        """A previously active heater's commanded fraction is preserved when
        the solver fails — clearing the forecast does not flip controls off.
        """
        model = make_single_room_model()
        source = ElectricHeater("heater", "studio", 2000)

        coordinator = object.__new__(HeatingAssistantCoordinator)
        coordinator.hass = MagicMock()
        coordinator._temp_sensors = {}
        coordinator._window_sensors = {}
        coordinator._window_state = {"studio": "closed"}
        coordinator._window_state_since = {}
        coordinator._window_open_debounce = 0.0
        coordinator._window_open_close_settle = 0.0
        coordinator._window_open_q_inflation = 1.0
        coordinator._latitude = 0.0
        coordinator._longitude = 0.0
        coordinator._update_interval_s = 900
        coordinator._horizon = 4
        coordinator.model = model
        coordinator.heat_sources = [source]
        # Heater was previously running at 80%
        coordinator.actions = {"heater": 0.8}
        coordinator.solar_gains = {}
        coordinator.outdoor_temp = 5.0
        coordinator.heat_flows = {}
        coordinator.predictions = []
        coordinator.outdoor_forecast = []
        coordinator.solar_forecast = []
        coordinator.heating_schedule = []
        coordinator.measured_temperatures = {"studio": 15.0}
        coordinator.filtered_temperatures = {"studio": 15.0}
        coordinator._room_schedule = {}
        coordinator._base_setpoint = {}
        coordinator._effective_setpoint = {}
        coordinator._schedule_disabled = {}
        coordinator._schedule_enabled = {}
        coordinator._room_comfort_offset = {}
        coordinator._room_enabled = {}
        coordinator._weather_entity = None
        coordinator._price_entity = None
        coordinator._solar_radiation_entity = None
        coordinator._read_cloud_cover_now = MagicMock(return_value=None)
        coordinator._async_read_cloud_forecast = AsyncMock(return_value=None)
        coordinator._history_buffer = deque(maxlen=10)
        coordinator._read_outdoor_temp = MagicMock(return_value=0.0)
        coordinator._async_read_weather_forecast = AsyncMock(return_value=None)
        coordinator._apply_actions = AsyncMock()
        coordinator.controller = MagicMock()
        coordinator.controller.compute.side_effect = RuntimeError("OCP failed")

        result = await coordinator._async_update_data()

        assert result["actions"] == {"heater": 0.8}
        assert result["outdoor_forecast"] == [result["outdoor_temp"]] * coordinator._horizon
        # Even with an active heater the forecast stays empty — failure must
        # remain visible, not be papered over with a thermal-model trajectory.
        assert result["predictions"] == []
        assert result["heating_schedule"] == []
        assert result["solar_forecast"] == []
        assert coordinator.filtered_temperatures == {}


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


# ---------------------------------------------------------------------------
# Tests: _parse_weather_forecast (coordinator static method)
# ---------------------------------------------------------------------------

class TestParseForecastData:
    """Test the _parse_weather_forecast static method on the coordinator."""

    def test_empty_returns_none(self):
        result = HeatingAssistantCoordinator._parse_weather_forecast([], 4, 900)
        assert result is None

    def test_all_missing_fields_returns_none(self):
        data = [{"condition": "sunny"}]  # no datetime or temperature
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900)
        assert result is None

    def test_correct_horizon_length(self):
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            {"datetime": (now + timedelta(hours=i)).isoformat(), "temperature": float(i)}
            for i in range(8)
        ]
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900, now=now)
        assert result is not None
        assert len(result) == 4

    def test_single_entry_constant_forecast(self):
        """A single forecast entry extrapolates as a constant."""
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [{"datetime": (now + timedelta(hours=1)).isoformat(), "temperature": 5.0}]
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900, now=now)
        assert result is not None
        for val in result:
            assert val == pytest.approx(5.0)

    def test_z_suffix_datetime_parsed(self):
        """Entries with ISO-8601 'Z' suffix timezone are parsed correctly."""
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        t_str = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = [{"datetime": t_str, "temperature": 3.0}]
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900, now=now)
        assert result is not None
        for val in result:
            assert val == pytest.approx(3.0)

    def test_interpolates_decreasing_temperatures(self):
        """Multiple entries with decreasing temps produce a decreasing forecast."""
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        # Entries at 15-min intervals with decreasing temp
        data = [
            {
                "datetime": (now + timedelta(minutes=15 * (i + 1))).isoformat(),
                "temperature": 10.0 - i,
            }
            for i in range(8)
        ]
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900, now=now)
        assert result is not None
        assert len(result) == 4
        # First step is warmer than last step
        assert result[0] > result[-1]

    def test_forecast_matches_entries_at_exact_timestamps(self):
        """Forecast values match entry temperatures exactly at horizon timestamps."""
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        dt = 900.0
        horizon = 4
        temps = [5.0, 4.0, 3.0, 2.0]
        data = [
            {
                "datetime": (now + timedelta(seconds=dt * (k + 1))).isoformat(),
                "temperature": temps[k],
            }
            for k in range(horizon)
        ]
        result = HeatingAssistantCoordinator._parse_weather_forecast(
            data, horizon, dt, now=now
        )
        assert result is not None
        for i, expected in enumerate(temps):
            assert result[i] == pytest.approx(expected)

    def test_datetime_object_entries(self):
        """Entries with datetime objects (not strings) are parsed correctly."""
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            {"datetime": now + timedelta(hours=1), "temperature": 7.0},
            {"datetime": now + timedelta(hours=2), "temperature": 5.0},
        ]
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900, now=now)
        assert result is not None
        assert len(result) == 4

    def test_skips_entries_with_invalid_temp(self):
        """Entries with non-numeric temperature are silently skipped."""
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            {"datetime": (now + timedelta(hours=1)).isoformat(), "temperature": "hot"},
            {"datetime": (now + timedelta(hours=2)).isoformat(), "temperature": 5.0},
        ]
        result = HeatingAssistantCoordinator._parse_weather_forecast(data, 4, 900, now=now)
        # Should still return a result using the valid entry
        assert result is not None
        assert len(result) == 4


# ---------------------------------------------------------------------------
# Tests: _parse_cloud_forecast (coordinator static method)
# ---------------------------------------------------------------------------

class TestParseCloudForecast:
    """Test cloud-cover forecast parsing from raw HA weather entries."""

    def test_empty_returns_none(self):
        assert HeatingAssistantCoordinator._parse_cloud_forecast([], 4, 900) is None

    def test_percentage_converted_to_fraction(self):
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            {
                "datetime": (now + timedelta(seconds=900 * (k + 1))).isoformat(),
                "cloud_coverage": 80.0,
            }
            for k in range(4)
        ]
        result = HeatingAssistantCoordinator._parse_cloud_forecast(data, 4, 900, now=now)
        assert result is not None
        for val in result:
            assert val == pytest.approx(0.8)

    def test_falls_back_to_condition_when_percentage_missing(self):
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            {
                "datetime": (now + timedelta(seconds=900 * (k + 1))).isoformat(),
                "condition": "cloudy",
            }
            for k in range(4)
        ]
        result = HeatingAssistantCoordinator._parse_cloud_forecast(data, 4, 900, now=now)
        assert result is not None
        # "cloudy" maps to 0.85 in the condition table
        for val in result:
            assert val == pytest.approx(0.85)

    def test_unknown_condition_entry_skipped(self):
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            # Unknown condition + no percentage → entry is unusable.
            {"datetime": (now + timedelta(seconds=900)).isoformat(), "condition": "moon-eclipse"},
            # Valid entry as fallback
            {
                "datetime": (now + timedelta(seconds=1800)).isoformat(),
                "cloud_coverage": 50.0,
            },
        ]
        result = HeatingAssistantCoordinator._parse_cloud_forecast(data, 4, 900, now=now)
        assert result is not None
        assert len(result) == 4
        # All steps fall back to the only valid entry (0.5).
        for val in result:
            assert val == pytest.approx(0.5)

    def test_percentage_clamped_to_unit_interval(self):
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        data = [
            {
                "datetime": (now + timedelta(seconds=900 * (k + 1))).isoformat(),
                "cloud_coverage": 250.0,  # nonsense > 100 %
            }
            for k in range(4)
        ]
        result = HeatingAssistantCoordinator._parse_cloud_forecast(data, 4, 900, now=now)
        assert result is not None
        for val in result:
            assert val == pytest.approx(1.0)


class TestCoerceCloudCoverPercent:
    """Test the module-level cloud-cover percent → fraction coercion."""

    def test_none_returns_none(self):
        from custom_components.heating_assistant.weather import coerce_cloud_cover_percent
        assert coerce_cloud_cover_percent(None) is None

    def test_unparsable_returns_none(self):
        from custom_components.heating_assistant.weather import coerce_cloud_cover_percent
        assert coerce_cloud_cover_percent("partly") is None

    def test_typical_values(self):
        from custom_components.heating_assistant.weather import coerce_cloud_cover_percent
        assert coerce_cloud_cover_percent(0) == pytest.approx(0.0)
        assert coerce_cloud_cover_percent(50) == pytest.approx(0.5)
        assert coerce_cloud_cover_percent(100) == pytest.approx(1.0)

    def test_clamps_negative(self):
        from custom_components.heating_assistant.weather import coerce_cloud_cover_percent
        assert coerce_cloud_cover_percent(-10) == pytest.approx(0.0)

    def test_clamps_above_hundred(self):
        from custom_components.heating_assistant.weather import coerce_cloud_cover_percent
        assert coerce_cloud_cover_percent(150) == pytest.approx(1.0)
