"""
Model diagnostics and fit validation tools.

This module provides algorithms and metrics to assess:
1. Model fit quality – how well the thermal model predictions match observations
2. Parameter validity – whether estimated thermal parameters are physically reasonable
3. Controller performance – how well the MPC controller tracks setpoints
4. Residual analysis – statistical properties of prediction errors

These tools help users identify when their model configuration needs adjustment
or when parameter estimation has produced unreliable results.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

import numpy as np

_LOGGER = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class ModelFitMetrics:
    """
    Comprehensive model fit quality metrics for a single room.

    Attributes
    ----------
    room_name : str
        Room identifier
    rmse : float
        Root mean squared error [°C]
    mae : float
        Mean absolute error [°C]
    r_squared : float
        Coefficient of determination (1.0 = perfect fit)
    bias : float
        Mean prediction error [°C] (positive = model over-predicts)
    max_error : float
        Maximum absolute error [°C]
    n_samples : int
        Number of samples used in computation
    residual_std : float
        Standard deviation of residuals [°C]
    residual_autocorr_lag1 : Optional[float]
        Lag-1 autocorrelation of residuals (0 = white noise, ideal)
    """
    room_name: str
    rmse: float
    mae: float
    r_squared: float
    bias: float
    max_error: float
    n_samples: int
    residual_std: float
    residual_autocorr_lag1: Optional[float] = None


@dataclass
class ParameterValidation:
    """
    Parameter validity assessment for a single room.

    Attributes
    ----------
    room_name : str
        Room identifier
    thermal_mass : float
        Current thermal mass [J/K]
    r_external : float
        Current external resistance [K/W]
    time_constant_hours : float
        Thermal time constant [hours]
    mass_valid : bool
        Whether thermal_mass is in physically reasonable range
    r_external_valid : bool
        Whether r_external is in physically reasonable range
    time_constant_valid : bool
        Whether time constant is reasonable for a room
    warnings : List[str]
        List of validation warnings
    """
    room_name: str
    thermal_mass: float
    r_external: float
    time_constant_hours: float
    mass_valid: bool
    r_external_valid: bool
    time_constant_valid: bool
    warnings: List[str]


@dataclass
class ControllerPerformance:
    """
    Controller performance metrics for setpoint tracking.

    Attributes
    ----------
    room_name : str
        Room identifier
    mean_tracking_error : float
        Mean deviation from setpoint [°C]
    tracking_error_std : float
        Standard deviation of tracking error [°C]
    time_above_setpoint : float
        Fraction of time above setpoint [0-1]
    time_below_setpoint : float
        Fraction of time below setpoint [0-1]
    time_in_deadband : float
        Fraction of time within ±0.5°C of setpoint [0-1]
    max_overshoot : float
        Maximum overshoot above setpoint [°C]
    max_undershoot : float
        Maximum undershoot below setpoint [°C]
    n_samples : int
        Number of samples used
    """
    room_name: str
    mean_tracking_error: float
    tracking_error_std: float
    time_above_setpoint: float
    time_below_setpoint: float
    time_in_deadband: float
    max_overshoot: float
    max_undershoot: float
    n_samples: int


# ── Physical parameter bounds ────────────────────────────────────────────────

# Thermal mass bounds [J/K]
# - Lower: ~10 kJ/K (very small, well-insulated room)
# - Upper: ~500 MJ/K (very large thermal mass with furniture, walls, etc.)
THERMAL_MASS_MIN = 1e4
THERMAL_MASS_MAX = 5e8

# External resistance bounds [K/W]
# - Lower: ~0.00001 K/W (extremely poor insulation, outdoor structure)
# - Upper: ~10 K/W (extremely well insulated)
R_EXTERNAL_MIN = 1e-5
R_EXTERNAL_MAX = 10.0

# Time constant bounds [hours]
# - Lower: ~0.1 hours (very fast response, poor insulation)
# - Upper: ~100 hours (extremely slow, very high thermal mass and insulation)
TIME_CONSTANT_MIN_HOURS = 0.1
TIME_CONSTANT_MAX_HOURS = 100.0


# ── Model fit metrics computation ───────────────────────────────────────────


def compute_model_fit_metrics(
    predictions: List[float],
    measurements: List[float],
    room_name: str,
) -> ModelFitMetrics:
    """
    Compute comprehensive model fit metrics from prediction/measurement pairs.

    Parameters
    ----------
    predictions : list of float
        Model predictions [°C]
    measurements : list of float
        Actual measured temperatures [°C]
    room_name : str
        Room identifier

    Returns
    -------
    ModelFitMetrics
        Comprehensive fit quality metrics
    """
    if len(predictions) != len(measurements):
        raise ValueError("predictions and measurements must have the same length")

    n = len(predictions)
    if n == 0:
        raise ValueError("Cannot compute metrics on empty arrays")

    pred = np.array(predictions)
    meas = np.array(measurements)

    # Residuals (errors)
    residuals = pred - meas

    # Basic error metrics
    mae = float(np.mean(np.abs(residuals)))
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    bias = float(np.mean(residuals))
    max_error = float(np.max(np.abs(residuals)))
    residual_std = float(np.std(residuals))

    # R-squared (coefficient of determination)
    ss_tot = np.sum((meas - np.mean(meas)) ** 2)
    ss_res = np.sum(residuals ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    r_squared = float(r_squared)

    # Lag-1 autocorrelation of residuals (test for whiteness)
    # Ideal: close to zero (uncorrelated errors = model captures dynamics)
    autocorr_lag1 = None
    if n > 1:
        try:
            autocorr_lag1 = float(np.corrcoef(residuals[:-1], residuals[1:])[0, 1])
        except Exception:
            pass

    return ModelFitMetrics(
        room_name=room_name,
        rmse=rmse,
        mae=mae,
        r_squared=r_squared,
        bias=bias,
        max_error=max_error,
        n_samples=n,
        residual_std=residual_std,
        residual_autocorr_lag1=autocorr_lag1,
    )


def analyze_residuals(
    predictions: List[float],
    measurements: List[float],
) -> Dict[str, Any]:
    """
    Perform detailed residual analysis.

    Parameters
    ----------
    predictions : list of float
        Model predictions [°C]
    measurements : list of float
        Actual measured temperatures [°C]

    Returns
    -------
    dict
        Residual statistics including histogram bins and autocorrelation
    """
    if len(predictions) != len(measurements):
        raise ValueError("predictions and measurements must have the same length")

    pred = np.array(predictions)
    meas = np.array(measurements)
    residuals = pred - meas

    # Compute histogram for distribution visualization
    hist, bin_edges = np.histogram(residuals, bins=20)

    # Compute autocorrelation function up to lag 10
    n = len(residuals)
    max_lag = min(10, n // 2)
    autocorr = []
    for lag in range(max_lag + 1):
        if lag == 0:
            autocorr.append(1.0)
        else:
            try:
                corr = np.corrcoef(residuals[:-lag], residuals[lag:])[0, 1]
                autocorr.append(float(corr))
            except Exception:
                autocorr.append(0.0)

    return {
        "residuals": residuals.tolist(),
        "histogram": {
            "counts": hist.tolist(),
            "bin_edges": bin_edges.tolist(),
        },
        "autocorrelation": autocorr,
        "mean": float(np.mean(residuals)),
        "std": float(np.std(residuals)),
        "skewness": float(_compute_skewness(residuals)),
        "kurtosis": float(_compute_kurtosis(residuals)),
    }


def _compute_skewness(data: np.ndarray) -> float:
    """Compute sample skewness (3rd standardized moment)."""
    if len(data) < 3:
        return 0.0
    mean = np.mean(data)
    std = np.std(data)
    if std == 0:
        return 0.0
    return float(np.mean(((data - mean) / std) ** 3))


def _compute_kurtosis(data: np.ndarray) -> float:
    """Compute sample excess kurtosis (4th standardized moment - 3)."""
    if len(data) < 4:
        return 0.0
    mean = np.mean(data)
    std = np.std(data)
    if std == 0:
        return 0.0
    return float(np.mean(((data - mean) / std) ** 4) - 3.0)


# ── Parameter validation ─────────────────────────────────────────────────────


def validate_parameters(
    room_name: str,
    thermal_mass: float,
    r_external: float,
) -> ParameterValidation:
    """
    Validate physical reasonableness of thermal parameters.

    Parameters
    ----------
    room_name : str
        Room identifier
    thermal_mass : float
        Thermal mass [J/K]
    r_external : float
        External resistance [K/W]

    Returns
    -------
    ParameterValidation
        Validation result with warnings
    """
    warnings = []

    # Check thermal mass
    mass_valid = THERMAL_MASS_MIN <= thermal_mass <= THERMAL_MASS_MAX
    if not mass_valid:
        if thermal_mass < THERMAL_MASS_MIN:
            warnings.append(
                f"Thermal mass {thermal_mass:.0f} J/K is unusually low "
                f"(< {THERMAL_MASS_MIN:.0f} J/K). Room may be too small or "
                "parameter estimation may have failed."
            )
        else:
            warnings.append(
                f"Thermal mass {thermal_mass:.0f} J/K is unusually high "
                f"(> {THERMAL_MASS_MAX:.0f} J/K). Check parameter estimation."
            )

    # Check external resistance
    r_valid = R_EXTERNAL_MIN <= r_external <= R_EXTERNAL_MAX
    if not r_valid:
        if r_external < R_EXTERNAL_MIN:
            warnings.append(
                f"External resistance {r_external:.6f} K/W is unusually low "
                f"(< {R_EXTERNAL_MIN:.6f} K/W). Room may have very poor insulation."
            )
        else:
            warnings.append(
                f"External resistance {r_external:.6f} K/W is unusually high "
                f"(> {R_EXTERNAL_MAX:.1f} K/W). Check parameter estimation."
            )

    # Compute time constant (τ = R × C)
    time_constant_seconds = thermal_mass * r_external
    time_constant_hours = time_constant_seconds / 3600.0

    # Check time constant
    tc_valid = (
        TIME_CONSTANT_MIN_HOURS <= time_constant_hours <= TIME_CONSTANT_MAX_HOURS
    )
    if not tc_valid:
        if time_constant_hours < TIME_CONSTANT_MIN_HOURS:
            warnings.append(
                f"Time constant {time_constant_hours:.2f} hours is unusually short "
                f"(< {TIME_CONSTANT_MIN_HOURS:.1f} hours). Room responds very quickly."
            )
        else:
            warnings.append(
                f"Time constant {time_constant_hours:.2f} hours is unusually long "
                f"(> {TIME_CONSTANT_MAX_HOURS:.0f} hours). Room responds very slowly."
            )

    return ParameterValidation(
        room_name=room_name,
        thermal_mass=thermal_mass,
        r_external=r_external,
        time_constant_hours=time_constant_hours,
        mass_valid=mass_valid,
        r_external_valid=r_valid,
        time_constant_valid=tc_valid,
        warnings=warnings,
    )


# ── Controller performance analysis ──────────────────────────────────────────


def compute_controller_performance(
    temperatures: List[float],
    setpoint: float,
    room_name: str,
) -> ControllerPerformance:
    """
    Compute controller performance metrics for setpoint tracking.

    Parameters
    ----------
    temperatures : list of float
        Measured room temperatures [°C]
    setpoint : float
        Target setpoint [°C]
    room_name : str
        Room identifier

    Returns
    -------
    ControllerPerformance
        Performance metrics
    """
    if len(temperatures) == 0:
        raise ValueError("Cannot compute metrics on empty temperature array")

    temps = np.array(temperatures)
    n = len(temps)

    # Tracking errors
    errors = temps - setpoint
    mean_error = float(np.mean(errors))
    std_error = float(np.std(errors))

    # Time statistics
    time_above = float(np.sum(errors > 0) / n)
    time_below = float(np.sum(errors < 0) / n)
    time_in_deadband = float(np.sum(np.abs(errors) <= 0.5) / n)

    # Overshoot/undershoot
    max_overshoot = float(np.max(errors)) if np.any(errors > 0) else 0.0
    max_undershoot = float(np.abs(np.min(errors))) if np.any(errors < 0) else 0.0

    return ControllerPerformance(
        room_name=room_name,
        mean_tracking_error=mean_error,
        tracking_error_std=std_error,
        time_above_setpoint=time_above,
        time_below_setpoint=time_below,
        time_in_deadband=time_in_deadband,
        max_overshoot=max_overshoot,
        max_undershoot=max_undershoot,
        n_samples=n,
    )


# ── Kalman filter innovation analysis ───────────────────────────────────────


def analyze_innovations(
    innovations: List[float],
    innovation_variances: List[float],
) -> Dict[str, Any]:
    """
    Analyze Kalman filter innovation sequence for consistency.

    A well-tuned Kalman filter should produce innovations (measurement
    prediction errors) that are zero-mean and white (uncorrelated).
    Large autocorrelation or non-zero mean suggests model mismatch or
    incorrect noise covariances.

    Parameters
    ----------
    innovations : list of float
        Innovation sequence ν[k] = y[k] - ŷ[k|k-1]
    innovation_variances : list of float
        Innovation covariance S[k] (diagonal elements if multi-output)

    Returns
    -------
    dict
        Innovation statistics and consistency test results
    """
    if len(innovations) == 0:
        return {
            "mean": 0.0,
            "std": 0.0,
            "normalized_mean": 0.0,
            "consistency_ratio": 0.0,
            "autocorr_lag1": 0.0,
            "is_consistent": False,
        }

    innov = np.array(innovations)
    innov_var = np.array(innovation_variances)

    mean_innov = float(np.mean(innov))
    std_innov = float(np.std(innov))

    # Normalized innovations (should be ~ N(0, 1) if filter is consistent)
    normalized_innov = innov / np.sqrt(innov_var + 1e-9)
    normalized_mean = float(np.mean(normalized_innov))

    # Consistency ratio: empirical variance / theoretical variance
    # Should be close to 1.0 if filter is consistent
    empirical_var = float(np.mean(innov ** 2))
    theoretical_var = float(np.mean(innov_var))
    consistency_ratio = empirical_var / (theoretical_var + 1e-9)

    # Autocorrelation (should be near zero)
    autocorr_lag1 = 0.0
    if len(innov) > 1:
        try:
            autocorr_lag1 = float(np.corrcoef(innov[:-1], innov[1:])[0, 1])
        except Exception:
            pass

    # Rough consistency check: ratio should be 0.8-1.2, autocorr < 0.3
    is_consistent = (
        0.8 <= consistency_ratio <= 1.2 and abs(autocorr_lag1) < 0.3
    )

    return {
        "mean": mean_innov,
        "std": std_innov,
        "normalized_mean": normalized_mean,
        "consistency_ratio": consistency_ratio,
        "autocorr_lag1": autocorr_lag1,
        "is_consistent": is_consistent,
    }


# ── Comprehensive model fit report ───────────────────────────────────────────


def generate_model_fit_report(
    history_buffer: List[Dict[str, Any]],
    room_names: List[str],
    room_params: Dict[str, Tuple[float, float]],  # {name: (thermal_mass, r_external)}
    setpoints: Dict[str, float],
) -> Dict[str, Any]:
    """
    Generate a comprehensive model fit report from the history buffer.

    Parameters
    ----------
    history_buffer : list of dict
        Rolling history buffer with keys:
        - 'y': list of measured temperatures [°C]
        - 'y_pred': list of predicted temperatures [°C] (if available)
        - 'setpoint': dict of setpoints {room_name: setpoint}
    room_names : list of str
        Ordered list of room names
    room_params : dict
        Current parameters {room_name: (thermal_mass, r_external)}
    setpoints : dict
        Current setpoints {room_name: setpoint}

    Returns
    -------
    dict
        Comprehensive report with fit metrics, parameter validation,
        and controller performance for each room
    """
    n_rooms = len(room_names)
    n_steps = len(history_buffer)

    if n_steps == 0:
        return {
            "error": "Empty history buffer",
            "n_steps": 0,
        }

    # Extract predictions and measurements per room
    report = {
        "n_steps": n_steps,
        "rooms": {},
    }

    for i, room_name in enumerate(room_names):
        # Collect data for this room
        measurements = []
        predictions = []

        for record in history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            if i < len(y):
                measurements.append(y[i])
            if y_pred is not None and i < len(y_pred):
                predictions.append(y_pred[i])

        # Skip if no prediction data available
        if len(predictions) == 0 or len(measurements) == 0:
            report["rooms"][room_name] = {
                "error": "No prediction data available",
            }
            continue

        # Ensure same length
        min_len = min(len(predictions), len(measurements))
        predictions = predictions[:min_len]
        measurements = measurements[:min_len]

        # Compute fit metrics
        try:
            fit_metrics = compute_model_fit_metrics(predictions, measurements, room_name)
        except Exception as exc:
            _LOGGER.warning("Failed to compute fit metrics for %s: %s", room_name, exc)
            fit_metrics = None

        # Validate parameters
        thermal_mass, r_external = room_params.get(room_name, (0.0, 0.0))
        try:
            param_validation = validate_parameters(room_name, thermal_mass, r_external)
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", room_name, exc)
            param_validation = None

        # Compute controller performance
        setpoint = setpoints.get(room_name, 21.0)
        try:
            controller_perf = compute_controller_performance(
                measurements, setpoint, room_name
            )
        except Exception as exc:
            _LOGGER.warning("Failed to compute controller performance for %s: %s", room_name, exc)
            controller_perf = None

        # Build room report
        room_report: Dict[str, Any] = {
            "n_samples": min_len,
        }

        if fit_metrics:
            room_report["fit_metrics"] = {
                "rmse": round(fit_metrics.rmse, 3),
                "mae": round(fit_metrics.mae, 3),
                "r_squared": round(fit_metrics.r_squared, 4),
                "bias": round(fit_metrics.bias, 3),
                "max_error": round(fit_metrics.max_error, 2),
                "residual_std": round(fit_metrics.residual_std, 3),
                "residual_autocorr_lag1": (
                    round(fit_metrics.residual_autocorr_lag1, 3)
                    if fit_metrics.residual_autocorr_lag1 is not None
                    else None
                ),
                "n_samples": fit_metrics.n_samples,
            }

        if param_validation:
            room_report["parameter_validation"] = {
                "thermal_mass": param_validation.thermal_mass,
                "r_external": param_validation.r_external,
                "time_constant_hours": round(param_validation.time_constant_hours, 2),
                "mass_valid": param_validation.mass_valid,
                "r_external_valid": param_validation.r_external_valid,
                "time_constant_valid": param_validation.time_constant_valid,
                "warnings": param_validation.warnings,
            }

        if controller_perf:
            room_report["controller_performance"] = {
                "mean_tracking_error": round(controller_perf.mean_tracking_error, 3),
                "tracking_error_std": round(controller_perf.tracking_error_std, 3),
                "time_above_setpoint": round(controller_perf.time_above_setpoint, 3),
                "time_below_setpoint": round(controller_perf.time_below_setpoint, 3),
                "time_in_deadband": round(controller_perf.time_in_deadband, 3),
                "max_overshoot": round(controller_perf.max_overshoot, 2),
                "max_undershoot": round(controller_perf.max_undershoot, 2),
            }

        report["rooms"][room_name] = room_report

    return report


# ── Open-loop simulation diagnostic ──────────────────────────────────────────


def compute_open_loop_predictions(
    history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    n_rooms: int,
    dt: float,
    segment_length: int = 30,
) -> Dict[str, Any]:
    """
    Evaluate model quality by running open-loop (no Kalman correction)
    simulations over sliding windows of the history buffer.

    For each window of ``segment_length`` steps the model is initialised from
    the first measurement in the window and then propagated forward purely
    using the recorded control inputs and disturbances — no state correction.
    This reveals the true multi-step prediction quality of the thermal model,
    which is what the MPC actually relies on.

    Parameters
    ----------
    history : list of dicts
        History buffer entries, each with keys ``y``, ``u``, ``d_outdoor``,
        ``d_solar``, ``timestamp``.
    system : HouseThermalSystem
        Thermal system object used for continuous-time integration.
        Passed as ``Any`` to avoid a circular import; only ``f``,
        ``n_d``, ``_room_idx``, ``n_u``, ``_dt`` are accessed.
    room_names : list of str
        Ordered room names (must match the ``y`` index order).
    n_rooms : int
        Number of rooms (= len(room_names)).
    dt : float
        Sampling interval [s] – must match the history buffer step.
    segment_length : int
        Number of steps per open-loop segment (default 30, i.e. 30 min at
        60 s / step).

    Returns
    -------
    dict with:
        ``per_room``    : {room_name: {rmse, mae, simulation}}
            ``simulation``  : list of {time, measured, predicted}
        ``overall_rmse``    : {room_name: float}
        ``n_segments``      : int
        ``segment_length``  : int
        ``error``           : str (only present on failure)
    """
    n = n_rooms
    if len(history) < segment_length + 1:
        return {
            "error": (
                f"Insufficient history: need ≥ {segment_length + 1} steps, "
                f"have {len(history)}."
            ),
            "per_room": {},
            "overall_rmse": {},
            "n_segments": 0,
            "segment_length": segment_length,
        }

    def _make_d(record: Dict[str, Any]) -> np.ndarray:
        p = system.n_d
        d = np.zeros(p)
        d[0] = float(record.get("d_outdoor", 0.0))
        d_solar = record.get("d_solar", {})
        for name, gain in d_solar.items():
            if name in system._room_idx:
                d[1 + system._room_idx[name]] = float(gain)
        return d

    per_room_preds: Dict[str, List[float]] = {name: [] for name in room_names}
    per_room_meas: Dict[str, List[float]] = {name: [] for name in room_names}
    simulation: Dict[str, List[Dict[str, Any]]] = {name: [] for name in room_names}

    n_segments = 0

    for start in range(0, len(history) - segment_length, segment_length):
        seg = history[start: start + segment_length]
        y0 = seg[0].get("y", [])
        if len(y0) < n:
            continue

        x = np.array(y0[:n], dtype=float)
        d_prev = _make_d(seg[0])

        valid_segment = True
        for record in seg[1:]:
            d = _make_d(record)

            u_raw = record.get("u", [])
            n_u = system.n_u
            u = np.zeros(n_u)
            for k, v in enumerate(u_raw):
                if k < n_u:
                    u[k] = float(v)

            # Continuous-time integration using forward Euler
            # This matches the approach used in CD-EKF
            dt = system._dt
            n_steps = 10  # Sub-steps for numerical stability
            dt_sub = dt / n_steps

            try:
                for _ in range(n_steps):
                    # Use previous disturbance (zero-order hold)
                    dx = system.f(x, u, d_prev, np.array([]), 0.0)
                    x = x + dx * dt_sub
            except Exception:
                valid_segment = False
                break

            d_prev = d

            y_meas = record.get("y", [])
            ts = record.get("timestamp", 0.0)

            for room_idx, room_name in enumerate(room_names):
                if room_idx < len(y_meas):
                    pred_val = float(x[room_idx])
                    meas_val = float(y_meas[room_idx])
                    per_room_preds[room_name].append(pred_val)
                    per_room_meas[room_name].append(meas_val)
                    simulation[room_name].append({
                        "time": ts,
                        "measured": round(meas_val, 3),
                        "predicted": round(pred_val, 3),
                    })
            d_prev = d

        if valid_segment:
            n_segments += 1

    if n_segments == 0:
        return {
            "error": "No valid segments found.",
            "per_room": {},
            "overall_rmse": {},
            "n_segments": 0,
            "segment_length": segment_length,
        }

    per_room_results: Dict[str, Any] = {}
    overall_rmse: Dict[str, float] = {}

    for room_name in room_names:
        preds = np.array(per_room_preds[room_name])
        meas = np.array(per_room_meas[room_name])
        if len(preds) == 0:
            per_room_results[room_name] = {
                "rmse": None,
                "mae": None,
                "simulation": [],
            }
            continue
        residuals = preds - meas
        rmse = float(np.sqrt(np.mean(residuals ** 2)))
        mae = float(np.mean(np.abs(residuals)))
        per_room_results[room_name] = {
            "rmse": round(rmse, 4),
            "mae": round(mae, 4),
            "simulation": simulation[room_name],
        }
        overall_rmse[room_name] = round(rmse, 4)

    return {
        "per_room": per_room_results,
        "overall_rmse": overall_rmse,
        "n_segments": n_segments,
        "segment_length": segment_length,
    }


def compute_autocorrelation_function(
    residuals: List[float],
    max_lag: int = 20,
) -> Dict[str, Any]:
    """
    Compute the sample autocorrelation function (ACF) of *residuals* up to
    *max_lag*, together with approximate 95 % confidence bounds and the
    Ljung-Box test statistic.

    Parameters
    ----------
    residuals : list of float
        Sequence of residuals (prediction errors) in time order.
    max_lag : int
        Maximum lag to include (inclusive).  Default 20.

    Returns
    -------
    dict with:
        ``acf``              : list[float] – ACF at lags 0, 1, …, max_lag
                               (acf[0] = 1.0 by definition)
        ``lags``             : list[int]  – [0, 1, …, max_lag]
        ``confidence_bound`` : float      – approximate ±95 % CI width
                               (= 1.96 / sqrt(n))
        ``ljung_box_stat``   : float      – Q = n(n+2) Σ ρ_k²/(n−k)
        ``n_samples``        : int
    """
    n = len(residuals)
    if n < 4:
        return {
            "acf": [1.0],
            "lags": [0],
            "confidence_bound": 1.0,
            "ljung_box_stat": 0.0,
            "n_samples": n,
        }

    r = np.array(residuals, dtype=float)
    r -= r.mean()
    var = float(np.dot(r, r))

    effective_max_lag = min(max_lag, n - 2)
    acf = [1.0]
    for lag in range(1, effective_max_lag + 1):
        c = float(np.dot(r[lag:], r[:-lag]))
        acf.append(c / var if var > 0 else 0.0)

    lags = list(range(len(acf)))
    ci = 1.96 / math.sqrt(n)

    # Ljung-Box: Q = n(n+2) Σ_{k=1}^{K} ρ_k² / (n-k)
    lb = sum(
        (acf[k] ** 2) / (n - k)
        for k in range(1, len(acf))
    )
    lb_stat = float(n * (n + 2) * lb)

    return {
        "acf": [round(v, 5) for v in acf],
        "lags": lags,
        "confidence_bound": round(ci, 4),
        "ljung_box_stat": round(lb_stat, 3),
        "n_samples": n,
    }
