"""Model diagnostics for the HA-independent Heating Assistant engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from .const import DEFAULT_R_EXTERNAL, DEFAULT_THERMAL_MASS
from .history.window import split_contiguous_runs
from .integrator import ImplicitEulerConvergenceError, implicit_euler_substeps

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
                f"(< {THERMAL_MASS_MIN:.0f} J/K). Check room size or re-run parameter estimation."
            )
        else:
            warnings.append(
                f"Thermal mass {thermal_mass:.0f} J/K is unusually high "
                f"(> {THERMAL_MASS_MAX:.0f} J/K). Re-run parameter estimation or review inputs."
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
                f"(> {R_EXTERNAL_MAX:.1f} K/W). Re-run parameter estimation or review inputs."
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
            "re-run parameter estimation or review thermal mass and envelope resistance."
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
            message="Poor closed-loop fit — run automatic parameter estimation or check heater and sensor configuration.",
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
                    "validate with a fresh parameter-estimation window before changing parameters."
                ),
                severity="warn",
            ))
        elif acceptable_fit:
            warnings.append(IdentificationWarning(
                code="open_loop_elevated",
                message=(
                    f"Open-loop error is {open_loop_rmse:.2f} °C — re-estimate parameters "
                    "or widen the parameter-estimation window."
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




# ── Open-loop predictions ────────────────────────────────────────────────────

def compute_open_loop_predictions(
    history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    n_rooms: int,
    dt: float,
    segment_length: Optional[int] = None,
    t_wall_initial: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run open-loop simulations over observation history.

    ``segment_length=None`` runs one continuous free-run per contiguous data
    stretch. Passing an integer computes segmented N-step accuracy by
    re-initialising each fixed-length window from its first measurement.
    """

    n = int(n_rooms)
    if segment_length is not None:
        effective_segment_length = min(int(segment_length), max(1, len(history) // 2))
        if len(history) < effective_segment_length + 1:
            return {
                "error": (
                    f"Insufficient history: need >= {effective_segment_length + 1} "
                    f"steps, have {len(history)}."
                ),
                "per_room": {},
                "overall_rmse": {},
                "n_segments": 0,
                "segment_length": effective_segment_length,
            }
        segment_length = effective_segment_length
    elif len(history) < 2:
        return {
            "error": f"Insufficient history: need >= 2 steps, have {len(history)}.",
            "per_room": {},
            "overall_rmse": {},
            "n_segments": 0,
            "segment_length": None,
        }

    has_disturbance_vector = hasattr(system, "disturbance_vector")
    rooms = getattr(getattr(system, "_model", None), "rooms", {}) or {}
    set_window_open = getattr(system, "set_window_open", None)

    def _ua_modelled(room_name: str) -> bool:
        room_obj = rooms.get(room_name)
        return float(getattr(room_obj, "ua_open", 0.0) or 0.0) > 0.0

    def _gap_open(flags: Dict[str, Any], room_name: str) -> bool:
        return bool(flags.get(room_name, False)) and not _ua_modelled(room_name)

    def _make_d(record: Dict[str, Any]) -> np.ndarray:
        outdoor = float(record.get("d_outdoor", 0.0))
        d_solar = record.get("d_solar", {}) or {}
        if has_disturbance_vector:
            return np.asarray(system.disturbance_vector(outdoor, d_solar), dtype=float)

        n_d = int(getattr(system, "nd", 1 + 2 * n))
        d = np.zeros(n_d)
        d[0] = outdoor
        room_idx = getattr(system, "_room_idx", {name: idx for idx, name in enumerate(room_names)})
        n_model_rooms = len(room_idx)
        for name, idx in room_idx.items():
            if 1 + idx < n_d:
                d[1 + idx] = float(d_solar.get(name, 0.0))
            room_obj = rooms.get(name)
            air_gain_idx = 1 + n_model_rooms + idx
            if room_obj is not None and air_gain_idx < n_d:
                d[air_gain_idx] = float(getattr(room_obj, "internal_gain", 0.0))
        return d

    per_room_preds: Dict[str, List[float]] = {name: [] for name in room_names}
    per_room_meas: Dict[str, List[float]] = {name: [] for name in room_names}
    simulation: Dict[str, List[Dict[str, Any]]] = {name: [] for name in room_names}
    n_segments = 0
    is_first_dataset_segment = True

    contiguous_runs = list(split_contiguous_runs(history, float(dt)))
    if segment_length is None:
        segments = contiguous_runs
    else:
        segments = [
            run[start : start + segment_length]
            for run in contiguous_runs
            for start in range(0, max(1, len(run) - 1), segment_length)
        ]

    nominal_dt = float(getattr(system, "_dt", dt) or dt)
    n_u = int(getattr(system, "nu", 0))

    for seg in segments:
        if len(seg) < 2:
            continue
        y0 = seg[0].get("y", [])
        if len(y0) < n:
            continue

        nx = int(getattr(system, "nx", n))
        u_prev = np.zeros(n_u)
        for k, value in enumerate(seg[0].get("u", [])):
            if k < n_u:
                u_prev[k] = float(value)

        d_prev = _make_d(seg[0])
        init_fn = getattr(system, "initial_state_from_measurement", None)
        if callable(init_fn):
            y0_arr = np.asarray(y0[:n], dtype=float)
            wall_seed = "air" if is_first_dataset_segment else "steady_state"
            try:
                x = np.asarray(init_fn(y0_arr, u_prev, d_prev, wall_seed=wall_seed), dtype=float)
            except TypeError:
                try:
                    x = np.asarray(init_fn(y0_arr, u_prev, d_prev), dtype=float)
                except TypeError:
                    x = np.asarray(init_fn(y0_arr, u_prev), dtype=float)
            if is_first_dataset_segment and x.shape[0] >= 2 * n:
                if t_wall_initial:
                    for room_idx, room_name in enumerate(room_names):
                        if room_name in t_wall_initial:
                            x[n + room_idx] = float(t_wall_initial[room_name])
                        else:
                            x[n + room_idx] = float(y0_arr[room_idx])
                else:
                    x[n : 2 * n] = y0_arr[:n]
            is_first_dataset_segment = False
        else:
            x = np.zeros(nx, dtype=float)
            x[:n] = np.asarray(y0[:n], dtype=float)
            is_first_dataset_segment = False

        ts_prev = float(seg[0].get("timestamp", 0.0))
        has_wall_states = len(x) > n
        window_open0 = seg[0].get("window_open") or {}
        if callable(set_window_open):
            set_window_open(window_open0)
        for room_idx, room_name in enumerate(room_names):
            if room_idx >= len(y0):
                continue
            if _gap_open(window_open0, room_name):
                simulation[room_name].append(
                    {"time": ts_prev, "measured": None, "predicted": None}
                )
                continue
            init_value = round(float(y0[room_idx]), 3)
            entry: Dict[str, Any] = {
                "time": ts_prev,
                "measured": init_value,
                "predicted": init_value,
            }
            if has_wall_states and n + room_idx < len(x):
                entry["predicted_wall"] = round(float(x[n + room_idx]), 3)
            simulation[room_name].append(entry)

        valid_segment = True
        prev_window_open = window_open0
        for record in seg[1:]:
            if callable(set_window_open):
                set_window_open(prev_window_open)
            d = _make_d(record)
            u = np.zeros(n_u)
            for k, value in enumerate(record.get("u", [])):
                if k < n_u:
                    u[k] = float(value)

            ts = float(record.get("timestamp", 0.0))
            dt_step = ts - ts_prev
            if dt_step <= 0:
                dt_step = nominal_dt
            n_steps = max(1, min(200, round(dt_step * 10.0 / nominal_dt)))
            params = np.array([])

            def rhs(state: np.ndarray, u_loc: np.ndarray = u_prev, d_loc: np.ndarray = d_prev) -> np.ndarray:
                return system.f(state, u_loc, d_loc, params, 0.0)

            def jac(state: np.ndarray, u_loc: np.ndarray = u_prev, d_loc: np.ndarray = d_prev) -> np.ndarray:
                return system.dfdx(state, u_loc, d_loc, params, 0.0)

            try:
                x = implicit_euler_substeps(rhs, jac, x, dt_step, n_steps)
            except (ImplicitEulerConvergenceError, Exception):
                valid_segment = False
                break

            d_prev = d
            u_prev = u
            ts_prev = ts

            y_meas = record.get("y", [])
            window_open = record.get("window_open") or {}
            has_wall = len(x) > n
            for room_idx, room_name in enumerate(room_names):
                if room_idx >= len(y_meas):
                    continue
                meas_val = float(y_meas[room_idx])
                if _gap_open(window_open, room_name):
                    x[room_idx] = meas_val
                    simulation[room_name].append(
                        {"time": ts, "measured": None, "predicted": None}
                    )
                    continue
                pred_val = float(x[room_idx])
                per_room_preds[room_name].append(pred_val)
                per_room_meas[room_name].append(meas_val)
                sim_entry: Dict[str, Any] = {
                    "time": ts,
                    "measured": round(meas_val, 3),
                    "predicted": round(pred_val, 3),
                }
                if has_wall and n + room_idx < len(x):
                    sim_entry["predicted_wall"] = round(float(x[n + room_idx]), 3)
                simulation[room_name].append(sim_entry)
            prev_window_open = window_open

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
        preds = np.asarray(per_room_preds[room_name], dtype=float)
        meas = np.asarray(per_room_meas[room_name], dtype=float)
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


__all__ = [
    "IdentificationWarning",
    "ModelFitMetrics",
    "ParameterValidation",
    "THERMAL_MASS_MIN",
    "THERMAL_MASS_MAX",
    "R_EXTERNAL_MIN",
    "R_EXTERNAL_MAX",
    "build_identification_warnings",
    "compute_model_fit_metrics",
    "compute_open_loop_predictions",
    "is_default_thermal_configuration",
    "validate_parameters",
]
