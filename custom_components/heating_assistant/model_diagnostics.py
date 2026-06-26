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

try:
    from scipy.stats import chi2 as _scipy_chi2
    from scipy.linalg import expm as _scipy_expm
except Exception:  # scipy absent or import error at startup
    _scipy_chi2 = None
    _scipy_expm = None

from .const import DEFAULT_R_EXTERNAL, DEFAULT_THERMAL_MASS
from .integrator import (
    ImplicitEulerConvergenceError,
    implicit_euler_substeps,
)

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
class IdentificationWarning:
    """Structured warning for the system-identification room cards."""

    code: str
    message: str
    severity: str = "warn"


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
# - Soft upper: ~100 hours — large open-plan rooms can exceed this with good fit
# - Hard upper: ~500 hours — beyond this is almost certainly wrong
TIME_CONSTANT_MIN_HOURS = 0.1
TIME_CONSTANT_SOFT_MAX_HOURS = 100.0
TIME_CONSTANT_MAX_HOURS = 500.0

# Closed-loop fit thresholds aligned with the dashboard fit badges.
_GOOD_FIT_R_SQUARED = 0.8
_GOOD_FIT_RMSE_C = 0.5
_ACCEPTABLE_FIT_R_SQUARED = 0.5
_OPEN_LOOP_WARN_RMSE_C = 0.5
_OPEN_LOOP_ALARM_RMSE_C = 1.0

# Categorical thermal presets (mirror configuration.js ROOM_SIZE_PRESETS ×
# HOUSE_AGE_PRESETS).  These are intentional user-facing defaults — not
# mis-identifications — so sysid card warnings should not fire for them.
_ROOM_SIZE_THERMAL_MASSES = (
    2_500_000.0,   # small
    5_000_000.0,   # medium (also DEFAULT_THERMAL_MASS)
    9_000_000.0,   # large
    14_000_000.0,  # open / open-plan
)
_HOUSE_AGE_R_EXTERNALS = (
    0.03,   # old / poorly insulated
    0.05,   # standard (also DEFAULT_R_EXTERNAL)
    0.08,   # modern / well insulated
    0.12,   # passive house
)


def _params_close(a: float, b: float) -> bool:
    """Match preset values with the same tolerance used in the sysid UI."""
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9 * max(1.0, abs(a), abs(b)))


def is_default_thermal_configuration(
    thermal_mass: float,
    r_external: float,
) -> bool:
    """Return True when params match canonical or preset defaults (unmodified baseline)."""
    if _params_close(thermal_mass, DEFAULT_THERMAL_MASS) and _params_close(
        r_external, DEFAULT_R_EXTERNAL
    ):
        return True
    for tm in _ROOM_SIZE_THERMAL_MASSES:
        if not _params_close(thermal_mass, tm):
            continue
        for r in _HOUSE_AGE_R_EXTERNALS:
            if _params_close(r_external, r):
                return True
    return False


def _good_closed_loop_fit(
    model_r_squared: Optional[float],
    model_rmse: Optional[float],
) -> bool:
    if model_r_squared is None or model_r_squared <= _GOOD_FIT_R_SQUARED:
        return False
    return model_rmse is None or model_rmse <= _GOOD_FIT_RMSE_C


def _acceptable_closed_loop_fit(model_r_squared: Optional[float]) -> bool:
    return model_r_squared is not None and model_r_squared > _ACCEPTABLE_FIT_R_SQUARED


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
    if n > 2:
        r0, r1 = residuals[:-1], residuals[1:]
        if np.std(r0) > 0 and np.std(r1) > 0:
            try:
                autocorr_lag1 = float(np.corrcoef(r0, r1)[0, 1])
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
    *,
    model_r_squared: Optional[float] = None,
    model_rmse: Optional[float] = None,
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
    good_fit = _good_closed_loop_fit(model_r_squared, model_rmse)

    # Check thermal mass
    mass_valid = THERMAL_MASS_MIN <= thermal_mass <= THERMAL_MASS_MAX
    if not mass_valid:
        if thermal_mass < THERMAL_MASS_MIN:
            warnings.append(
                f"Thermal mass {thermal_mass:.0f} J/K is unusually low "
                f"(< {THERMAL_MASS_MIN:.0f} J/K). Check room size or re-run identification."
            )
        else:
            warnings.append(
                f"Thermal mass {thermal_mass:.0f} J/K is unusually high "
                f"(> {THERMAL_MASS_MAX:.0f} J/K). Re-run identification or review inputs."
            )

    # Check external resistance
    r_valid = R_EXTERNAL_MIN <= r_external <= R_EXTERNAL_MAX
    if not r_valid:
        if r_external < R_EXTERNAL_MIN:
            warnings.append(
                f"Envelope resistance {r_external:.6f} K/W is unusually low "
                f"(< {R_EXTERNAL_MIN:.6f} K/W). Check insulation assumptions."
            )
        else:
            warnings.append(
                f"Envelope resistance {r_external:.6f} K/W is unusually high "
                f"(> {R_EXTERNAL_MAX:.1f} K/W). Re-run identification or review inputs."
            )

    # Compute time constant (τ = R × C)
    time_constant_seconds = thermal_mass * r_external
    time_constant_hours = time_constant_seconds / 3600.0

    # Time constant validity: hard bounds always apply; the soft upper bound
    # only reduces confidence when closed-loop fit is not good.
    tc_hard_valid = (
        TIME_CONSTANT_MIN_HOURS <= time_constant_hours <= TIME_CONSTANT_MAX_HOURS
    )
    tc_soft_valid = time_constant_hours <= TIME_CONSTANT_SOFT_MAX_HOURS
    tc_valid = tc_hard_valid and (tc_soft_valid or good_fit)

    if not tc_hard_valid:
        if time_constant_hours < TIME_CONSTANT_MIN_HOURS:
            warnings.append(
                f"Thermal time constant {time_constant_hours:.1f} h is very short "
                f"(< {TIME_CONSTANT_MIN_HOURS:.1f} h) — the room would respond almost instantly."
            )
        else:
            warnings.append(
                f"Thermal time constant {time_constant_hours:.0f} h is extremely long "
                f"(> {TIME_CONSTANT_MAX_HOURS:.0f} h) — parameters are likely inconsistent."
            )
    elif not tc_soft_valid and not good_fit:
        warnings.append(
            f"Thermal time constant {time_constant_hours:.0f} h is longer than typical "
            f"(> {TIME_CONSTANT_SOFT_MAX_HOURS:.0f} h) and closed-loop fit is not strong — "
            "re-run identification or review thermal mass and envelope resistance."
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


def build_identification_warnings(
    room_name: str,
    validation: ParameterValidation,
    *,
    model_r_squared: Optional[float] = None,
    model_rmse: Optional[float] = None,
    open_loop_rmse: Optional[float] = None,
    n_samples: Optional[int] = None,
) -> List[IdentificationWarning]:
    """Build structured, user-facing warnings for the sysid index cards."""
    del room_name  # reserved for future per-room tailoring
    warnings: List[IdentificationWarning] = []
    good_fit = _good_closed_loop_fit(model_r_squared, model_rmse)
    acceptable_fit = _acceptable_closed_loop_fit(model_r_squared)

    code_map = {
        "thermal mass": "thermal_mass",
        "envelope resistance": "r_external",
        "thermal time constant": "time_constant",
    }

    skip_param_warnings = is_default_thermal_configuration(
        validation.thermal_mass,
        validation.r_external,
    )

    for text in validation.warnings:
        if skip_param_warnings:
            continue
        code = "parameter_validation"
        lowered = text.lower()
        for needle, mapped in code_map.items():
            if needle in lowered:
                code = mapped
                break
        severity = "info" if code == "time_constant" and good_fit else "warn"
        warnings.append(IdentificationWarning(code=code, message=text, severity=severity))

    if n_samples is not None and n_samples < 2:
        warnings.append(IdentificationWarning(
            code="insufficient_data",
            message="Waiting for enough temperature history to assess model fit.",
            severity="info",
        ))
    elif model_r_squared is None:
        warnings.append(IdentificationWarning(
            code="no_fit_data",
            message="No closed-loop fit data yet — run the controller or EKF reconstruction.",
            severity="info",
        ))
    elif not acceptable_fit:
        warnings.append(IdentificationWarning(
            code="poor_fit",
            message="Poor closed-loop fit — run auto-identification or check heater and sensor configuration.",
            severity="alarm",
        ))

    if open_loop_rmse is not None and open_loop_rmse > _OPEN_LOOP_WARN_RMSE_C:
        if good_fit and open_loop_rmse <= _OPEN_LOOP_ALARM_RMSE_C:
            warnings.append(IdentificationWarning(
                code="open_loop_moderate",
                message=(
                    f"Open-loop drift is {open_loop_rmse:.2f} °C while closed-loop fit is good — "
                    "optional check with a longer open-loop simulation if forecasts look off."
                ),
                severity="info",
            ))
        elif good_fit:
            warnings.append(IdentificationWarning(
                code="open_loop_high",
                message=(
                    f"Open-loop error is {open_loop_rmse:.2f} °C despite good closed-loop fit — "
                    "validate with a fresh identification window before changing parameters."
                ),
                severity="warn",
            ))
        elif acceptable_fit:
            warnings.append(IdentificationWarning(
                code="open_loop_elevated",
                message=(
                    f"Open-loop error is {open_loop_rmse:.2f} °C — re-estimate parameters "
                    "or widen the identification window."
                ),
                severity="warn",
            ))
        else:
            warnings.append(IdentificationWarning(
                code="open_loop_high",
                message=(
                    f"Open-loop error is {open_loop_rmse:.2f} °C with weak closed-loop fit — "
                    "re-estimate parameters before relying on forecasts."
                ),
                severity="alarm",
            ))
    elif acceptable_fit and open_loop_rmse is None and n_samples is not None and n_samples >= 2:
        warnings.append(IdentificationWarning(
            code="open_loop_missing",
            message="Run open-loop simulation on the room page to validate forecast accuracy.",
            severity="info",
        ))


    # Deduplicate by code while preserving first occurrence.
    seen: set[str] = set()
    unique: List[IdentificationWarning] = []
    for warning in warnings:
        if warning.code in seen:
            continue
        seen.add(warning.code)
        unique.append(warning)
    return unique


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
    segment_length: Optional[int] = None,
    t_wall_initial: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Evaluate model quality by running open-loop (no Kalman correction)
    simulations over the history buffer.

    When ``segment_length`` is ``None`` (the default) the simulation runs as
    one **continuous** pass per contiguous data run — no artificial restarts.
    This is the correct mode for visualisation: the wall/envelope temperature
    evolves freely and the plot has no discontinuities.

    When ``segment_length`` is an integer the history is divided into
    fixed-length windows of that many steps, each re-initialised from the
    first measurement in the window.  Use this mode only for computing
    N-step-ahead prediction accuracy (e.g. the multi-horizon RMSE analysis).

    Integration uses the **actual elapsed time** between consecutive history
    entries (derived from their ``timestamp`` fields) rather than the nominal
    ``dt``.  This correctly handles history buffers that contain small gaps
    from restarts or brief data interruptions — the continuous-time model
    simply propagates over the true interval.

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
        Nominal sampling interval [s] – used only as a fallback when a
        consecutive pair of entries lacks a valid timestamp difference.
    segment_length : int or None
        Number of steps per open-loop segment.  ``None`` (default) runs each
        contiguous data run as a single uninterrupted simulation.  Pass an
        integer to force fixed-length re-initialised segments (N-step-ahead
        accuracy analysis).  Automatically clamped to ``len(history) // 2``
        when the history is shorter.

    Returns
    -------
    dict with:
        ``per_room``    : {room_name: {rmse, mae, simulation}}
            ``simulation``  : list of {time, measured, predicted}
        ``overall_rmse``    : {room_name: float}
        ``n_segments``      : int
        ``segment_length``  : int or None
        ``error``           : str (only present on failure)
    """
    n = n_rooms

    if segment_length is not None:
        # Clamp segment_length to what the available history allows.
        effective_segment_length = min(segment_length, max(1, len(history) // 2))
        if len(history) < effective_segment_length + 1:
            return {
                "error": (
                    f"Insufficient history: need ≥ {effective_segment_length + 1} steps, "
                    f"have {len(history)}."
                ),
                "per_room": {},
                "overall_rmse": {},
                "n_segments": 0,
                "segment_length": effective_segment_length,
            }
        segment_length = effective_segment_length
    else:
        if len(history) < 2:
            return {
                "error": f"Insufficient history: need ≥ 2 steps, have {len(history)}.",
                "per_room": {},
                "overall_rmse": {},
                "n_segments": 0,
                "segment_length": None,
            }

    # Prefer the system's own disturbance builder so the open-loop diagnostic
    # uses *exactly* the same heat balance as the live MPC/EKF.  Crucially this
    # folds ``Room.internal_gain`` into the disturbance channel — omitting it
    # (as the old local builder did) produced a systematic open-loop bias
    # equal to the steady-state temperature contribution of the internal gain,
    # because the estimator identifies and applies that gain but the diagnostic
    # was ignoring it.
    _has_disturbance_vector = hasattr(system, "disturbance_vector")
    _rooms = getattr(getattr(system, "_model", None), "rooms", {}) or {}

    def _make_d(record: Dict[str, Any]) -> np.ndarray:
        outdoor = float(record.get("d_outdoor", 0.0))
        d_solar = record.get("d_solar", {}) or {}
        if _has_disturbance_vector:
            return np.asarray(
                system.disturbance_vector(outdoor, d_solar), dtype=float
            )
        # Fallback: replicate the disturbance layout
        # d = [T_out, q_solar (n), q_air (n)] — solar gains in slots 1…n,
        # the per-room internal heat gain in the air-heat slots 1+n…2n.
        p = system.nd
        n_rooms = len(system._room_idx)
        d = np.zeros(p)
        d[0] = outdoor
        for name, idx in system._room_idx.items():
            d[1 + idx] = float(d_solar.get(name, 0.0))
            room_obj = _rooms.get(name)
            if room_obj is not None and 1 + n_rooms + idx < p:
                d[1 + n_rooms + idx] = float(
                    getattr(room_obj, "internal_gain", 0.0)
                )
        return d

    per_room_preds: Dict[str, List[float]] = {name: [] for name in room_names}
    per_room_meas: Dict[str, List[float]] = {name: [] for name in room_names}
    simulation: Dict[str, List[Dict[str, Any]]] = {name: [] for name in room_names}

    n_segments = 0

    # Split the history into contiguous runs at restart gaps so a segment never
    # straddles a dead interval: free-running the model across a multi-hour (or
    # multi-day) gap with stale held inputs would otherwise produce a large
    # spurious error spike.
    #
    # Continuous mode (segment_length is None): each contiguous run becomes a
    # single uninterrupted simulation — no re-initialisation mid-run, so the
    # wall/envelope temperature evolves smoothly without jumps.
    #
    # Segmented mode (segment_length is int): each run is further divided into
    # fixed-length windows for N-step-ahead accuracy analysis.  The final stride
    # covers the trailing records too (a partial last segment) so the most recent
    # samples are not dropped.
    from .history_window import split_contiguous_runs

    contiguous_runs = list(split_contiguous_runs(history, dt))
    if segment_length is None:
        segments = contiguous_runs
    else:
        segments = [
            run[s: s + segment_length]
            for run in contiguous_runs
            for s in range(0, max(1, len(run) - 1), segment_length)
        ]
    for seg in segments:
        if len(seg) < 2:
            continue
        y0 = seg[0].get("y", [])
        if len(y0) < n:
            continue

        nx = int(getattr(system, "nx", n))

        # u_prev holds the control applied during [t_{k-1}, t_k].
        # u_k (stored at step k) is the action applied from t_k onward,
        # so to advance x from t_0 to t_1 we need u_0 (= seg[0]["u"]).
        n_u = system.nu
        u_prev = np.zeros(n_u)
        for k, v in enumerate(seg[0].get("u", [])):
            if k < n_u:
                u_prev[k] = float(v)

        # Robust initial state: start the open-loop free-run at the *same*
        # state the data is in.  ``initial_state_from_measurement`` sets the
        # air temperatures from the measurement (so hm(x0) == y0 with the
        # offset block zeroed) and warm-starts the emitter-lag states to the
        # commanded fraction, avoiding a spurious cold-emitter transient at the
        # start of every segment.  The envelope is seeded at the air temperature
        # (``wall_seed="air"`` → T_air = T_envelope) so the displayed free-run
        # starts unbiased rather than jumping the wall to a parameter-dependent
        # steady state.  Fall back to the legacy room-only initialisation for
        # system objects that don't provide the helper.
        d_prev = _make_d(seg[0])
        init_fn = getattr(system, "initial_state_from_measurement", None)
        if callable(init_fn):
            y0_arr = np.asarray(y0[:n], dtype=float)
            try:
                x = np.asarray(init_fn(y0_arr, u_prev, d_prev, wall_seed="air"), dtype=float)
            except TypeError:
                try:
                    x = np.asarray(init_fn(y0_arr, u_prev, d_prev), dtype=float)
                except TypeError:
                    # Older system objects accept (y, u) only.
                    x = np.asarray(init_fn(y0_arr, u_prev), dtype=float)
            # Seed the wall/envelope state.  When an identified t_wall_initial
            # is provided use it; otherwise fall back to air temperature so the
            # free-run starts without a parameter-dependent jump.
            if x.shape[0] >= 2 * n:
                if t_wall_initial:
                    for room_idx, room_name in enumerate(room_names):
                        if room_idx < n and room_name in t_wall_initial:
                            x[n + room_idx] = float(t_wall_initial[room_name])
                        elif room_idx < n:
                            x[n + room_idx] = float(y0_arr[room_idx])
                else:
                    x[n:2 * n] = y0_arr[:n]
        else:
            x = np.zeros(nx, dtype=float)
            x[:n] = np.array(y0[:n], dtype=float)

        # Anchor point: record the t=0 initial state so the chart shows the
        # simulation starting exactly at the measurement.  predicted == measured
        # here by construction; the error at t=0 is always zero.
        ts_prev = float(seg[0].get("timestamp", 0.0))
        _nx_total = len(x)
        _has_wall_states = _nx_total > n
        wo0 = seg[0].get("window_open") or {}
        for room_idx, room_name in enumerate(room_names):
            if room_idx < len(y0):
                # Open window at the segment start → render a gap (the
                # open-window sample is excluded data).  The state is still
                # seeded from the real reading above so the free-run is anchored.
                if wo0.get(room_name, False):
                    simulation[room_name].append({
                        "time": ts_prev,
                        "measured": None,
                        "predicted": None,
                    })
                    continue
                init_val = round(float(y0[room_idx]), 3)
                anchor: Dict[str, Any] = {
                    "time": ts_prev,
                    "measured": init_val,
                    "predicted": init_val,
                }
                if _has_wall_states and n + room_idx < _nx_total:
                    anchor["predicted_wall"] = round(float(x[n + room_idx]), 3)
                simulation[room_name].append(anchor)

        valid_segment = True
        for record in seg[1:]:
            d = _make_d(record)

            u_raw = record.get("u", [])
            u = np.zeros(n_u)
            for k, v in enumerate(u_raw):
                if k < n_u:
                    u[k] = float(v)

            # Use actual elapsed time between the two history entries so the
            # integration is correct regardless of gaps in the buffer
            # (restarts, brief outages).  Fall back to the nominal dt only
            # when timestamps are unavailable or identical.
            ts = float(record.get("timestamp", 0.0))
            dt_step = ts - ts_prev
            if dt_step <= 0:
                dt_step = system._dt

            # Implicit-Euler sub-stepping over one cycle; matches the
            # controller's prediction scheme so open-loop and closed-loop
            # diagnostics use the same integrator.  Zero-order hold:
            # u_prev is the control applied during [t_{k-1}, t_k] (the
            # interval being reproduced here) and d_prev is the
            # disturbance at the start of that interval — the same ZOH
            # convention as the live MPC.
            n_steps = max(1, min(200, round(dt_step * 10.0 / system._dt)))

            d_zoh = d_prev
            u_zoh = u_prev
            _params = np.array([])

            def rhs(state, u_loc=u_zoh, d_loc=d_zoh):
                return system.f(state, u_loc, d_loc, _params, 0.0)

            def jac(state, u_loc=u_zoh, d_loc=d_zoh):
                return system.dfdx(state, u_loc, d_loc, _params, 0.0)

            try:
                x = implicit_euler_substeps(rhs, jac, x, dt_step, n_steps)
            except (ImplicitEulerConvergenceError, Exception):
                valid_segment = False
                break

            d_prev = d
            u_prev = u
            ts_prev = ts

            y_meas = record.get("y", [])
            _nx_total = len(x)
            _has_wall = _nx_total > n
            wo = record.get("window_open") or {}

            for room_idx, room_name in enumerate(room_names):
                if room_idx < len(y_meas):
                    meas_val = float(y_meas[room_idx])
                    # Per-room open-window exclusion: hold the open room's air
                    # node at its true reading (keeps the coupled free-run
                    # physical and restarts the room from reality on close),
                    # render a gap, and drop it from the RMSE/MAE.
                    if wo.get(room_name, False):
                        x[room_idx] = meas_val
                        simulation[room_name].append({
                            "time": ts,
                            "measured": None,
                            "predicted": None,
                        })
                        continue
                    pred_val = float(x[room_idx])
                    per_room_preds[room_name].append(pred_val)
                    per_room_meas[room_name].append(meas_val)
                    sim_entry: Dict[str, Any] = {
                        "time": ts,
                        "measured": round(meas_val, 3),
                        "predicted": round(pred_val, 3),
                    }
                    if _has_wall and n + room_idx < _nx_total:
                        sim_entry["predicted_wall"] = round(float(x[n + room_idx]), 3)
                    simulation[room_name].append(sim_entry)

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


def wall_state_observability(
    room: Any,
    dt: float = 900.0,
    horizon_steps: int = 96,
) -> Optional[float]:
    """Conditioning of the wall-state reconstruction for one 2R2C room.

    The wall node ``T_w`` is never measured — the EKF reconstructs it from
    the air-temperature dynamics.  This metric quantifies how well-posed
    that reconstruction is: it is the square root of the eigenvalue ratio

        sqrt( λ_min(W_o) / λ_max(W_o) )  ∈ (0, 1]

    of the discrete observability Gramian ``W_o = Σ_k (A_dᵀ)^k Cᵀ C A_d^k``
    of the room-local 2-state subsystem ([T_a, T_w], measurement [1, 0]),
    accumulated over ``horizon_steps`` steps of ``dt`` seconds (default
    24 h at 15-min steps — long enough to excite the slow mode).

    Interpretation: values near 1 mean both states are observed almost
    equally well; values near 0 mean the wall state is practically
    invisible from the air measurement (estimation of the envelope split
    will not converge).  Rule of thumb: > 0.05 healthy, 0.01–0.05
    marginal, < 0.01 poor.

    Inter-room couplings are neglected (they would only add information),
    so this is a conservative per-room bound.  Returns ``None`` when the
    room does not expose the 2R2C conductance split.
    """
    try:
        g_inf, g_aw, g_we = room.conductances()
        g_wout = (
            g_we
            + float(getattr(room, "sky_radiative_ua", 0.0))
            + float(getattr(room, "thermal_bridge_psi_l", 0.0))
        )
        c_a = float(room.c_air)
        c_w = float(room.c_wall)
    except Exception:
        return None
    if c_a <= 0.0 or c_w <= 0.0:
        return None

    if _scipy_expm is None:
        return None
    A = np.array([
        [-(g_inf + g_aw) / c_a, g_aw / c_a],
        [g_aw / c_w, -(g_aw + g_wout) / c_w],
    ])
    Ad = _scipy_expm(A * float(dt))

    C_meas = np.array([[1.0, 0.0]])
    W = np.zeros((2, 2))
    Ak = np.eye(2)
    for _ in range(max(2, int(horizon_steps))):
        W += Ak.T @ (C_meas.T @ C_meas) @ Ak
        Ak = Ad @ Ak

    eig = np.linalg.eigvalsh(W)
    if eig[-1] <= 0.0:
        return 0.0
    return float(np.sqrt(max(0.0, eig[0]) / eig[-1]))


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
        ``ljung_box_p_value``: float      – right-tail χ²_K p-value for Q
                               (small p ⇒ residuals are not white noise)
        ``n_samples``        : int
        ``is_white_noise``   : bool       – ``ljung_box_p_value > 0.05``
    """
    n = len(residuals)
    if n < 4:
        return {
            "acf": [1.0],
            "lags": [0],
            "confidence_bound": 1.0,
            "ljung_box_stat": 0.0,
            "ljung_box_p_value": 1.0,
            "n_samples": n,
            "is_white_noise": True,
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

    # χ²_K right-tail p-value. scipy.stats is optional so we fall back to a
    # series approximation when it is unavailable.
    dof = max(1, len(acf) - 1)
    p_value = _chi2_sf(lb_stat, dof)

    return {
        "acf": [round(v, 5) for v in acf],
        "lags": lags,
        "confidence_bound": round(ci, 4),
        "ljung_box_stat": round(lb_stat, 3),
        "ljung_box_p_value": round(p_value, 4),
        "n_samples": n,
        "is_white_noise": bool(p_value > 0.05),
    }


def _chi2_sf(x: float, dof: int) -> float:
    """Survival function (1 - cdf) of the χ² distribution with ``dof`` dof.

    Uses :func:`scipy.stats.chi2.sf` when available and falls back to a
    Wilson–Hilferty normal approximation otherwise so the diagnostics module
    keeps working even in stripped-down environments.
    """
    if x <= 0 or dof <= 0:
        return 1.0
    if _scipy_chi2 is not None:
        try:
            return float(_scipy_chi2.sf(x, dof))
        except Exception:
            pass
    # Wilson–Hilferty: ((x/dof)^(1/3) − (1 − 2/(9 dof))) / sqrt(2/(9 dof))
    # is approximately N(0, 1). Use the complementary error function for the
    # right tail as a fallback when scipy is unavailable or raises.
    a = 2.0 / (9.0 * dof)
    z = ((x / dof) ** (1.0 / 3.0) - (1.0 - a)) / math.sqrt(a)
    return 0.5 * math.erfc(z / math.sqrt(2.0))
