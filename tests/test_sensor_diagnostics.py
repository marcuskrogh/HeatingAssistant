"""Tests for diagnostic sensors in sensor/diagnostics.py."""

import os
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.sensor import (
    EstimatedParametersStatusSensor,
    HeaterScaleSensor,
    KalmanInnovationSensor,
    LoglikSliceSensor,
    MPCPerformanceSensor,
    ModelFitQualitySensor,
    OpenLoopRMSESensor,
    ParameterConfidenceSensor,
    PredictionErrorSensor,
    ResidualACFSensor,
    SolarRadiationStatusSensor,
    SysIdSimulationSensor,
    WeatherForecastStatusSensor,
)


def _make_coordinator(
    *,
    measured=20.4,
    filtered=20.7,
    setpoint=21.0,
    thermal_mass=1_000_000.0,
    r_external=0.05,
    history_buffer=None,
    open_loop_results=None,
    sysid_results=None,
    heat_sources=None,
):
    """Minimal coordinator stub for diagnostic sensor tests."""
    room = SimpleNamespace(
        temperature=measured,
        setpoint=setpoint,
        comfort_offset=2.0,
        thermal_mass=thermal_mass,
        r_external=r_external,
        internal_gain=120.0,
        windows=[],
    )
    sources = heat_sources or [
        SimpleNamespace(
            name="lr_radiator",
            room="living_room",
            current_power=800.0,
            max_power=2000.0,
            power_scale=0.95,
        )
    ]
    coord = SimpleNamespace(
        model=SimpleNamespace(
            room_names=["living_room"],
            rooms={"living_room": room},
        ),
        measured_temperatures={"living_room": measured},
        filtered_temperatures={"living_room": filtered},
        heat_sources=sources,
        history_buffer=history_buffer
        if history_buffer is not None
        else deque(
            [
                {"y": [20.0], "y_pred": [20.2], "timestamp": 1_000.0},
                {"y": [20.1], "y_pred": [20.3], "timestamp": 1_900.0},
                {"y": [20.2], "y_pred": [20.0], "timestamp": 2_800.0},
            ],
            maxlen=100,
        ),
        open_loop_results=open_loop_results or {},
        sysid_results=sysid_results or {},
        dt=900,
        controller=SimpleNamespace(
            last_solve_time=0.35,
            mean_solve_time=0.3,
            max_solve_time=0.5,
            n_solves=5,
            total_computes=5,
            horizon=24,
            solve_times=[0.28, 0.35],
            terminal_weight=1.0,
            constraint_offset=2.0,
        ),
        last_update_success=True,
        _last_mpc_run_ts=1_700_000_000.0,
        estimated_params_snapshot=None,
        _estimation_history=[],
        _weather_entity=None,
        weather_consecutive_failures=0,
        weather_last_error=None,
        weather_last_error_at=None,
        weather_last_success_at=None,
        _solar_radiation_entity=None,
        solar_fc_consecutive_failures=0,
        solar_fc_last_error=None,
        solar_fc_last_error_at=None,
        solar_fc_last_success_at=None,
        solar_source="analytical",
        ghi_now=350.0,
        ghi_now_effective=350.0,
        ghi_forecast=[300.0, 320.0],
    )
    coord.is_room_enabled = lambda _name: True
    coord.sources_for_room = lambda r: [s for s in coord.heat_sources if s.room == r]
    return coord


# ── Unique IDs ────────────────────────────────────────────────────────


def test_prediction_error_unique_id():
    coord = _make_coordinator()
    sensor = PredictionErrorSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_prediction_error"
    assert "Prediction Error" in sensor._attr_name


def test_model_fit_quality_unique_id():
    coord = _make_coordinator()
    sensor = ModelFitQualitySensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_model_fit_quality"


def test_parameter_confidence_unique_id():
    coord = _make_coordinator()
    sensor = ParameterConfidenceSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_parameter_confidence"


def test_open_loop_rmse_unique_id():
    coord = _make_coordinator()
    sensor = OpenLoopRMSESensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_open_loop_rmse"


def test_kalman_innovation_unique_id():
    coord = _make_coordinator()
    sensor = KalmanInnovationSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_kalman_innovation"


def test_heater_scale_unique_id():
    coord = _make_coordinator()
    sensor = HeaterScaleSensor(coord, "lr_radiator")
    assert sensor._attr_unique_id == "heating_assistant_lr_radiator_heater_scale"


def test_estimated_parameters_status_unique_id():
    coord = _make_coordinator()
    sensor = EstimatedParametersStatusSensor(coord)
    assert sensor._attr_unique_id == "heating_assistant_estimated_parameters_status"


def test_sysid_simulation_unique_id():
    coord = _make_coordinator()
    sensor = SysIdSimulationSensor(coord, "living_room")
    assert sensor._attr_unique_id == "heating_assistant_living_room_sysid_simulation"


# ── Native values ─────────────────────────────────────────────────────


def test_prediction_error_native_value_from_latest_aligned_record():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"y": [20.0], "y_pred": None},
            {"y": [20.1], "y_pred": [20.5]},
        ],
        maxlen=50,
    )
    sensor = PredictionErrorSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(0.4)


def test_prediction_error_native_value_none_without_aligned_prediction():
    coord = _make_coordinator()
    coord.history_buffer = deque([{"y": [20.0]}], maxlen=50)
    sensor = PredictionErrorSensor(coord, "living_room")
    assert sensor.native_value is None


def test_model_fit_quality_native_value_computes_r_squared():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"y": [20.0], "y_pred": [20.0]},
            {"y": [20.5], "y_pred": [20.5]},
            {"y": [21.0], "y_pred": [21.0]},
        ],
        maxlen=50,
    )
    sensor = ModelFitQualitySensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(1.0)


def test_model_fit_quality_native_value_none_with_insufficient_data():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [{"y": [20.0], "y_pred": [20.1]}], maxlen=50
    )
    sensor = ModelFitQualitySensor(coord, "living_room")
    assert sensor.native_value is None


def test_model_fit_quality_attributes_report_insufficient_history():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [{"y": [20.0], "y_pred": [20.1]}], maxlen=50
    )
    sensor = ModelFitQualitySensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["error"] == "Insufficient data"
    assert attrs["n_samples"] == 1


def test_parameter_confidence_native_value_reflects_valid_params():
    coord = _make_coordinator(thermal_mass=1_000_000.0, r_external=0.05)
    sensor = ParameterConfidenceSensor(coord, "living_room")
    score = sensor.native_value
    assert score is not None
    assert 0.0 <= score <= 100.0


def test_open_loop_rmse_native_value_from_coordinator_cache():
    coord = _make_coordinator(
        open_loop_results={"living_room": {"rmse": 0.18, "mae": 0.14}}
    )
    sensor = OpenLoopRMSESensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(0.18)


def test_open_loop_rmse_native_value_none_before_first_run():
    coord = _make_coordinator()
    sensor = OpenLoopRMSESensor(coord, "living_room")
    assert sensor.native_value is None


def test_open_loop_rmse_attributes_format_simulation_trace():
    coord = _make_coordinator(
        open_loop_results={
            "living_room": {
                "rmse": 0.18,
                "mae": 0.14,
                "rmse_by_horizon": {"4h": 0.2},
                "simulation": [
                    {
                        "time": 1_700_000_000.0,
                        "measured": 20.0,
                        "predicted": 20.1,
                        "predicted_wall": 19.5,
                    }
                ],
                "error": "stale cache",
            }
        }
    )
    sensor = OpenLoopRMSESensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["open_loop_rmse"] == pytest.approx(0.18)
    assert attrs["rmse_by_horizon"] == {"4h": 0.2}
    assert attrs["error"] == "stale cache"
    assert len(attrs["simulation"]) == 1
    assert "T" in attrs["simulation"][0]["time"]
    assert attrs["simulation"][0]["predicted_wall"] == pytest.approx(19.5)


def test_kalman_innovation_native_value_from_history():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"kalman_innovation": [0.05], "timestamp": 1_000.0},
            {"kalman_innovation": [-0.12], "timestamp": 1_900.0},
        ],
        maxlen=50,
    )
    sensor = KalmanInnovationSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(-0.12)


def test_kalman_innovation_attributes_include_consistency_stats():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"kalman_innovation": [0.05], "timestamp": 1_000.0},
            {"kalman_innovation": [-0.05], "timestamp": 1_900.0},
            {"kalman_innovation": [0.04], "timestamp": 2_800.0},
        ],
        maxlen=50,
    )
    sensor = KalmanInnovationSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["n_samples"] == 3
    assert "mean" in attrs
    assert "is_consistent" in attrs
    assert len(attrs["innovations"]) == 3


def test_heater_scale_native_value_as_percentage():
    coord = _make_coordinator()
    sensor = HeaterScaleSensor(coord, "lr_radiator")
    assert sensor.native_value == pytest.approx(95.0)


def test_heater_scale_native_value_none_for_unknown_source():
    coord = _make_coordinator()
    sensor = HeaterScaleSensor(coord, "missing_source")
    assert sensor.native_value is None


def test_estimated_parameters_status_default_without_snapshot():
    coord = _make_coordinator()
    sensor = EstimatedParametersStatusSensor(coord)
    assert sensor.native_value == "default"
    assert sensor.extra_state_attributes["estimated_at"] is None


def test_estimated_parameters_status_estimated_with_snapshot():
    coord = _make_coordinator()
    coord.estimated_params_snapshot = {
        "rooms": {"living_room": {"is_estimated": True}},
        "sources": {"lr_radiator": {"power_scale": 0.95}},
        "connections": {},
        "estimated_at": "2026-01-15T10:00:00+00:00",
        "log_likelihood": -42.5,
    }
    sensor = EstimatedParametersStatusSensor(coord)
    assert sensor.native_value == "estimated"
    attrs = sensor.extra_state_attributes
    assert attrs["n_rooms_estimated"] == 1
    assert attrs["rooms"]["living_room"]["is_estimated"] is True


def test_mpc_performance_native_value_returns_last_solve_time():
    coord = _make_coordinator()
    coord.controller.last_solve_time = timedelta(milliseconds=420)
    sensor = MPCPerformanceSensor(coord)
    assert sensor.native_value == pytest.approx(0.42)
    assert sensor.available is True


def test_mpc_performance_ignores_invalid_solve_time_samples():
    coord = _make_coordinator()
    coord.controller.last_solve_time = "not-a-number"
    coord.controller.solve_times = [0.2, "bad", 0.3]
    sensor = MPCPerformanceSensor(coord)
    assert sensor.native_value is None
    attrs = sensor.extra_state_attributes
    assert attrs["last_solve_time_s"] is None
    assert attrs["n_solves"] == 5
    assert "current_tracking_errors" in attrs


def test_parameter_confidence_native_value_none_when_validation_raises(monkeypatch):
    coord = _make_coordinator()
    sensor = ParameterConfidenceSensor(coord, "living_room")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("validation failed")

    monkeypatch.setattr(
        "custom_components.heating_assistant.model_diagnostics.validate_parameters",
        _boom,
    )
    assert sensor.native_value is None


def test_weather_forecast_status_disabled_without_entity():
    coord = _make_coordinator()
    sensor = WeatherForecastStatusSensor(coord)
    assert sensor.native_value == "disabled"


def test_weather_forecast_status_ok_after_success():
    now = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)
    coord = _make_coordinator()
    coord._weather_entity = "weather.home"
    coord.weather_last_success_at = now
    sensor = WeatherForecastStatusSensor(coord)
    assert sensor.native_value == "ok"
    assert sensor.extra_state_attributes["last_success_at"] == now.isoformat()


def test_solar_radiation_status_failing_reports_error():
    now = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)
    coord = _make_coordinator()
    coord._solar_radiation_entity = "sensor.ghi"
    coord.solar_fc_consecutive_failures = 2
    coord.solar_fc_last_error = "timeout"
    coord.solar_fc_last_error_at = now
    sensor = SolarRadiationStatusSensor(coord)
    assert sensor.native_value == "failing"
    attrs = sensor.extra_state_attributes
    assert attrs["last_error"] == "timeout"
    assert attrs["ghi_now"] == pytest.approx(350.0)


def test_prediction_error_extra_state_attributes_compute_statistics():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"y": [20.0], "y_pred": [20.2]},
            {"y": [20.1], "y_pred": [20.5]},
            {"y": [20.2], "y_pred": [20.0]},
        ],
        maxlen=50,
    )
    sensor = PredictionErrorSensor(coord, "living_room")
    attrs = sensor.extra_state_attributes
    assert attrs["n_samples"] == 3
    assert attrs["rmse"] == pytest.approx(0.283, abs=0.01)
    assert len(attrs["recent_errors"]) == 3


def test_model_fit_quality_native_value_none_when_metrics_raise(monkeypatch):
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"y": [20.0], "y_pred": [20.0]},
            {"y": [20.5], "y_pred": [20.5]},
        ],
        maxlen=50,
    )
    sensor = ModelFitQualitySensor(coord, "living_room")

    def _boom(*_args, **_kwargs):
        raise ValueError("bad fit")

    monkeypatch.setattr(
        "custom_components.heating_assistant.model_diagnostics.compute_model_fit_metrics",
        _boom,
    )
    assert sensor.native_value is None
    attrs = sensor.extra_state_attributes
    assert "bad fit" in attrs["error"]


def test_residual_acf_native_value_from_prediction_residuals():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"y": [20.0], "y_pred": [20.1]},
            {"y": [20.2], "y_pred": [20.0]},
            {"y": [20.4], "y_pred": [20.3]},
            {"y": [20.1], "y_pred": [20.2]},
        ],
        maxlen=50,
    )
    sensor = ResidualACFSensor(coord, "living_room")
    value = sensor.native_value
    assert value is not None
    assert -1.0 <= value <= 1.0
    attrs = sensor.extra_state_attributes
    assert "acf" in attrs
    assert attrs["n_samples"] >= 2


def test_residual_acf_native_value_none_with_single_sample():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [{"y": [20.0], "y_pred": [20.1]}], maxlen=50
    )
    sensor = ResidualACFSensor(coord, "living_room")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["n_samples"] == 1


def test_prediction_error_native_value_skips_records_without_prediction():
    coord = _make_coordinator()
    coord.history_buffer = deque(
        [
            {"y": [20.0]},
            {"y": [20.1], "y_pred": [20.4]},
        ],
        maxlen=50,
    )
    sensor = PredictionErrorSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(0.3)


def test_loglik_slice_native_value_none_before_computation():
    coord = _make_coordinator()
    coord._loglik_slices = {}
    sensor = LoglikSliceSensor(coord, "living_room")
    assert sensor.native_value is None
    attrs = sensor.extra_state_attributes
    assert attrs["status"] == "not_computed"
    assert attrs["room"] == "living_room"


def test_loglik_slice_native_value_and_attributes_from_coordinator_cache():
    coord = _make_coordinator()
    coord._loglik_slices = {
        "living_room": {
            "computed_at": "2026-05-16T09:00:00+00:00",
            "room": "living_room",
            "grid": [[0.0, 1.0], [0.1, 0.9]],
            "loglik": -12.5,
        }
    }
    sensor = LoglikSliceSensor(coord, "living_room")
    assert sensor.native_value == "2026-05-16T09:00:00+00:00"
    attrs = sensor.extra_state_attributes
    assert attrs["loglik"] == pytest.approx(-12.5)
    assert attrs["grid"] == [[0.0, 1.0], [0.1, 0.9]]


def test_residual_acf_attributes_surface_compute_errors(monkeypatch):
    coord = _make_coordinator()
    sensor = ResidualACFSensor(coord, "living_room")

    def _boom():
        raise RuntimeError("acf failed")

    monkeypatch.setattr(sensor, "_compute_acf", _boom)
    attrs = sensor.extra_state_attributes
    assert attrs["error"] == "acf failed"


def test_sysid_simulation_native_value_from_sysid_results():
    coord = _make_coordinator(
        sysid_results={
            "living_room": {
                "rmse": 0.22,
                "mae": 0.18,
                "thermal_mass": 1_000_000.0,
                "r_external": 0.05,
                "horizon_steps": 24,
                "simulation": [
                    {"time": 1_700_000_000.0, "measured": 20.0, "predicted": 20.1},
                ],
            }
        }
    )
    sensor = SysIdSimulationSensor(coord, "living_room")
    assert sensor.native_value == pytest.approx(0.22)
    attrs = sensor.extra_state_attributes
    assert attrs["thermal_mass"] == pytest.approx(1_000_000.0)
    assert attrs["horizon_hours"] == pytest.approx(6.0)
    assert len(attrs["simulation"]) == 1
    assert "time" in attrs["simulation"][0]
