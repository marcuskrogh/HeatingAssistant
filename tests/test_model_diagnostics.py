"""Unit tests for model diagnostics and fit validation tools."""

import sys
import os

import pytest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.model_diagnostics import (
    compute_model_fit_metrics,
    analyze_residuals,
    validate_parameters,
    compute_controller_performance,
    analyze_innovations,
    generate_model_fit_report,
    THERMAL_MASS_MIN,
    THERMAL_MASS_MAX,
    R_EXTERNAL_MIN,
    R_EXTERNAL_MAX,
)


# ── Test model fit metrics ───────────────────────────────────────────────


def test_compute_model_fit_metrics_perfect_fit():
    """Test metrics for perfect predictions (zero error)."""
    predictions = [20.0, 21.0, 22.0, 21.5, 20.5]
    measurements = [20.0, 21.0, 22.0, 21.5, 20.5]

    metrics = compute_model_fit_metrics(predictions, measurements, "test_room")

    assert metrics.room_name == "test_room"
    assert metrics.rmse == pytest.approx(0.0, abs=1e-6)
    assert metrics.mae == pytest.approx(0.0, abs=1e-6)
    assert metrics.r_squared == pytest.approx(1.0, abs=1e-6)
    assert metrics.bias == pytest.approx(0.0, abs=1e-6)
    assert metrics.max_error == pytest.approx(0.0, abs=1e-6)
    assert metrics.n_samples == 5


def test_compute_model_fit_metrics_constant_bias():
    """Test metrics with constant positive bias."""
    predictions = [21.0, 22.0, 23.0, 22.5, 21.5]
    measurements = [20.0, 21.0, 22.0, 21.5, 20.5]

    metrics = compute_model_fit_metrics(predictions, measurements, "test_room")

    assert metrics.bias == pytest.approx(1.0, abs=1e-6)
    assert metrics.mae == pytest.approx(1.0, abs=1e-6)
    assert metrics.rmse == pytest.approx(1.0, abs=1e-6)
    assert metrics.n_samples == 5


def test_compute_model_fit_metrics_realistic():
    """Test metrics with realistic prediction errors."""
    rng = np.random.default_rng(42)
    true_temps = 20.0 + 2.0 * np.sin(np.linspace(0, 2 * np.pi, 100))
    predictions = true_temps + rng.normal(0, 0.3, 100)  # Add small noise
    measurements = list(true_temps)

    metrics = compute_model_fit_metrics(
        list(predictions), measurements, "living_room"
    )

    # With small noise, R² should be high
    assert metrics.r_squared > 0.9
    assert metrics.rmse < 0.5
    assert metrics.mae < 0.5
    assert abs(metrics.bias) < 0.1  # No systematic bias
    assert metrics.residual_autocorr_lag1 is not None


def test_compute_model_fit_metrics_poor_fit():
    """Test metrics when model predictions are poor."""
    predictions = [20.0] * 50  # Constant prediction
    measurements = list(15.0 + 5.0 * np.sin(np.linspace(0, 2 * np.pi, 50)))

    metrics = compute_model_fit_metrics(predictions, measurements, "test_room")

    # Constant prediction should have very poor R²
    assert metrics.r_squared < 0.1
    assert metrics.rmse > 2.0


def test_compute_model_fit_metrics_length_mismatch():
    """Test that length mismatch raises an error."""
    predictions = [20.0, 21.0, 22.0]
    measurements = [20.0, 21.0]

    with pytest.raises(ValueError, match="must have the same length"):
        compute_model_fit_metrics(predictions, measurements, "test_room")


def test_compute_model_fit_metrics_empty():
    """Test that empty arrays raise an error."""
    with pytest.raises(ValueError, match="Cannot compute metrics on empty arrays"):
        compute_model_fit_metrics([], [], "test_room")


# ── Test residual analysis ───────────────────────────────────────────────


def test_analyze_residuals_white_noise():
    """Test residual analysis with white noise (ideal case)."""
    rng = np.random.default_rng(42)
    predictions = list(rng.normal(20.0, 0.2, 100))
    measurements = list(rng.normal(20.0, 0.2, 100))

    analysis = analyze_residuals(predictions, measurements)

    assert "residuals" in analysis
    assert "histogram" in analysis
    assert "autocorrelation" in analysis
    assert "mean" in analysis
    assert "std" in analysis
    assert "skewness" in analysis
    assert "kurtosis" in analysis

    # Mean should be near zero
    assert abs(analysis["mean"]) < 0.1

    # Autocorrelations should be near zero (white noise)
    autocorr = analysis["autocorrelation"]
    assert autocorr[0] == pytest.approx(1.0)  # Lag 0 is always 1
    assert all(abs(ac) < 0.3 for ac in autocorr[1:])  # Small autocorrelations


def test_analyze_residuals_biased():
    """Test residual analysis with systematic bias."""
    predictions = [21.0] * 50
    measurements = [20.0] * 50

    analysis = analyze_residuals(predictions, measurements)

    # Should detect the +1.0 bias
    assert analysis["mean"] == pytest.approx(1.0)
    assert analysis["std"] == pytest.approx(0.0)


# ── Test parameter validation ────────────────────────────────────────────


def test_validate_parameters_valid():
    """Test validation of physically reasonable parameters."""
    validation = validate_parameters(
        "living_room",
        thermal_mass=5_000_000.0,  # 5 MJ/K
        r_external=0.05,            # 0.05 K/W
    )

    assert validation.room_name == "living_room"
    assert validation.mass_valid
    assert validation.r_external_valid
    assert validation.time_constant_valid
    assert len(validation.warnings) == 0

    # Time constant should be reasonable (RC = 5e6 * 0.05 = 250k seconds ≈ 69 hours)
    assert validation.time_constant_hours == pytest.approx(69.44, abs=1.0)


def test_validate_parameters_mass_too_low():
    """Test validation with unrealistically low thermal mass."""
    validation = validate_parameters(
        "test_room",
        thermal_mass=5_000.0,  # Too low
        r_external=0.05,
    )

    assert not validation.mass_valid
    assert validation.r_external_valid
    assert len(validation.warnings) > 0
    assert any("thermal mass" in w.lower() for w in validation.warnings)


def test_validate_parameters_mass_too_high():
    """Test validation with unrealistically high thermal mass."""
    validation = validate_parameters(
        "test_room",
        thermal_mass=1_000_000_000.0,  # Too high (1 GJ/K)
        r_external=0.05,
    )

    assert not validation.mass_valid
    assert len(validation.warnings) > 0


def test_validate_parameters_r_external_too_low():
    """Test validation with poor insulation (very low R)."""
    validation = validate_parameters(
        "test_room",
        thermal_mass=5_000_000.0,
        r_external=0.000001,  # Extremely poor insulation
    )

    assert validation.mass_valid
    assert not validation.r_external_valid
    assert len(validation.warnings) > 0


def test_validate_parameters_time_constant_extreme():
    """Test validation with extreme time constant."""
    # Very high mass + high R → very long time constant
    validation = validate_parameters(
        "test_room",
        thermal_mass=100_000_000.0,
        r_external=5.0,
    )

    # Time constant = 100e6 * 5.0 = 500e6 seconds ≈ 139k hours (too long)
    assert not validation.time_constant_valid
    assert len(validation.warnings) > 0


def test_validate_parameters_edge_cases():
    """Test validation at the boundaries of valid ranges."""
    # Minimum valid values
    validation_min = validate_parameters(
        "test_room",
        thermal_mass=THERMAL_MASS_MIN,
        r_external=R_EXTERNAL_MIN,
    )
    assert validation_min.mass_valid
    assert validation_min.r_external_valid

    # Maximum valid values
    validation_max = validate_parameters(
        "test_room",
        thermal_mass=THERMAL_MASS_MAX,
        r_external=R_EXTERNAL_MAX,
    )
    assert validation_max.mass_valid
    assert validation_max.r_external_valid


# ── Test controller performance ──────────────────────────────────────────


def test_compute_controller_performance_perfect_tracking():
    """Test performance metrics with perfect setpoint tracking."""
    temperatures = [21.0] * 100
    setpoint = 21.0

    perf = compute_controller_performance(temperatures, setpoint, "test_room")

    assert perf.mean_tracking_error == pytest.approx(0.0)
    assert perf.tracking_error_std == pytest.approx(0.0)
    assert perf.time_in_deadband == pytest.approx(1.0)
    assert perf.time_above_setpoint == pytest.approx(0.0)
    assert perf.time_below_setpoint == pytest.approx(0.0)
    assert perf.max_overshoot == pytest.approx(0.0)
    assert perf.max_undershoot == pytest.approx(0.0)


def test_compute_controller_performance_oscillation():
    """Test performance with oscillating temperatures."""
    # Oscillate ±1°C around setpoint
    temperatures = [21.0 + np.sin(2 * np.pi * i / 20) for i in range(100)]
    setpoint = 21.0

    perf = compute_controller_performance(temperatures, setpoint, "test_room")

    # Mean error should be near zero (symmetric oscillation)
    assert abs(perf.mean_tracking_error) < 0.1
    # Should spend roughly equal time above and below
    assert abs(perf.time_above_setpoint - 0.5) < 0.1
    assert abs(perf.time_below_setpoint - 0.5) < 0.1
    # Max overshoot/undershoot should be about 1°C
    assert perf.max_overshoot == pytest.approx(1.0, abs=0.1)
    assert perf.max_undershoot == pytest.approx(1.0, abs=0.1)


def test_compute_controller_performance_biased_high():
    """Test performance when temperatures consistently run high."""
    temperatures = [22.0] * 100  # 1°C above setpoint
    setpoint = 21.0

    perf = compute_controller_performance(temperatures, setpoint, "test_room")

    assert perf.mean_tracking_error == pytest.approx(1.0)
    assert perf.time_above_setpoint == pytest.approx(1.0)
    assert perf.time_below_setpoint == pytest.approx(0.0)
    assert perf.max_overshoot == pytest.approx(1.0)
    assert perf.max_undershoot == pytest.approx(0.0)


def test_compute_controller_performance_empty():
    """Test that empty temperature list raises an error."""
    with pytest.raises(ValueError, match="Cannot compute metrics on empty"):
        compute_controller_performance([], 21.0, "test_room")


# ── Test innovation analysis ─────────────────────────────────────────────


def test_analyze_innovations_consistent():
    """Test innovation analysis for a well-tuned filter."""
    rng = np.random.default_rng(42)
    # Consistent filter: innovations ~ N(0, 1)
    innovations = list(rng.normal(0, 1.0, 100))
    innovation_variances = [1.0] * 100

    analysis = analyze_innovations(innovations, innovation_variances)

    assert abs(analysis["mean"]) < 0.2  # Near zero mean
    assert abs(analysis["normalized_mean"]) < 0.2
    assert 0.8 <= analysis["consistency_ratio"] <= 1.2  # Consistent
    assert abs(analysis["autocorr_lag1"]) < 0.3  # Low autocorrelation
    assert analysis["is_consistent"]


def test_analyze_innovations_inconsistent():
    """Test innovation analysis for a poorly tuned filter."""
    rng = np.random.default_rng(42)
    # Inconsistent: empirical variance much larger than theoretical
    innovations = list(rng.normal(0, 2.0, 100))  # Large variance
    innovation_variances = [0.5] * 100  # Small theoretical variance

    analysis = analyze_innovations(innovations, innovation_variances)

    # Consistency ratio should be >> 1
    assert analysis["consistency_ratio"] > 2.0
    assert not analysis["is_consistent"]


def test_analyze_innovations_empty():
    """Test innovation analysis with empty arrays."""
    analysis = analyze_innovations([], [])

    assert analysis["mean"] == 0.0
    assert analysis["std"] == 0.0
    assert not analysis["is_consistent"]


# ── Test comprehensive report generation ─────────────────────────────────


def test_generate_model_fit_report_empty_history():
    """Test report generation with empty history buffer."""
    report = generate_model_fit_report(
        history_buffer=[],
        room_names=["living_room"],
        room_params={"living_room": (5_000_000.0, 0.05)},
        setpoints={"living_room": 21.0},
    )

    assert "error" in report
    assert report["n_steps"] == 0


def test_generate_model_fit_report_single_room():
    """Test report generation for a single room with synthetic data."""
    # Create synthetic history buffer
    rng = np.random.default_rng(42)
    history_buffer = []

    for i in range(50):
        true_temp = 20.0 + 0.1 * i  # Slowly warming up
        measured = true_temp + rng.normal(0, 0.1)
        predicted = true_temp + rng.normal(0, 0.15)  # Slightly larger error

        history_buffer.append({
            "y": [measured],
            "y_pred": [predicted],
            "setpoint": {"living_room": 21.0},
        })

    report = generate_model_fit_report(
        history_buffer=history_buffer,
        room_names=["living_room"],
        room_params={"living_room": (5_000_000.0, 0.05)},
        setpoints={"living_room": 21.0},
    )

    assert report["n_steps"] == 50
    assert "living_room" in report["rooms"]

    room_data = report["rooms"]["living_room"]
    assert "fit_metrics" in room_data
    assert "parameter_validation" in room_data
    assert "controller_performance" in room_data

    # Check fit metrics
    fit = room_data["fit_metrics"]
    assert "rmse" in fit
    assert "mae" in fit
    assert "r_squared" in fit
    assert fit["n_samples"] == 50

    # Check parameter validation
    params = room_data["parameter_validation"]
    assert params["mass_valid"]
    assert params["r_external_valid"]

    # Check controller performance
    perf = room_data["controller_performance"]
    assert "mean_tracking_error" in perf
    assert "time_in_deadband" in perf


def test_generate_model_fit_report_multiple_rooms():
    """Test report generation for multiple rooms."""
    rng = np.random.default_rng(42)
    room_names = ["living_room", "bedroom"]
    history_buffer = []

    for i in range(30):
        history_buffer.append({
            "y": [20.0 + rng.normal(0, 0.1), 19.0 + rng.normal(0, 0.1)],
            "y_pred": [20.0 + rng.normal(0, 0.15), 19.0 + rng.normal(0, 0.15)],
        })

    report = generate_model_fit_report(
        history_buffer=history_buffer,
        room_names=room_names,
        room_params={
            "living_room": (5_000_000.0, 0.05),
            "bedroom": (3_000_000.0, 0.08),
        },
        setpoints={
            "living_room": 21.0,
            "bedroom": 20.0,
        },
    )

    assert report["n_steps"] == 30
    assert "living_room" in report["rooms"]
    assert "bedroom" in report["rooms"]

    # Both rooms should have complete diagnostics
    for room in room_names:
        assert "fit_metrics" in report["rooms"][room]
        assert "parameter_validation" in report["rooms"][room]
        assert "controller_performance" in report["rooms"][room]


def test_generate_model_fit_report_no_predictions():
    """Test report generation when y_pred is missing."""
    history_buffer = [
        {"y": [20.0], "y_pred": []},  # Missing predictions
        {"y": [20.5], "y_pred": []},
    ]

    report = generate_model_fit_report(
        history_buffer=history_buffer,
        room_names=["living_room"],
        room_params={"living_room": (5_000_000.0, 0.05)},
        setpoints={"living_room": 21.0},
    )

    assert "living_room" in report["rooms"]
    room_data = report["rooms"]["living_room"]
    assert "error" in room_data
    assert "No prediction data" in room_data["error"]
