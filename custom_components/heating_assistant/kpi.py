"""
Pure KPI calculation functions for Heating Assistant dashboards.

Formulas and thresholds follow ``docs/KPI_SPEC.md``.  No Home Assistant imports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

# Gauge scale constants (KPI_SPEC §4.3, §5.3, §5.5)
HEAT_LOSS_GAUGE_FLOOR_W = 3000.0
HEAT_LOSS_GAUGE_SCALE_FACTOR = 1.2
SOLAR_GAIN_GAUGE_DEFAULT_MAX_W = 1000.0
HOUSE_POWER_GAUGE_FLOOR_W = 3000.0
HOUSE_POWER_GAUGE_DEFAULT_MAX_W = 10000.0
HOUSE_POWER_GAUGE_SCALE_FACTOR = 1.2

MODEL_FIT_LABEL_GOOD = "GOOD"
MODEL_FIT_LABEL_ACCEPTABLE = "ACCEPTABLE"
MODEL_FIT_LABEL_POOR = "POOR"
MODEL_FIT_LABEL_EMPTY = "—"


@dataclass(frozen=True)
class RoomSnapshot:
    """Per-room inputs for comfort and tracking KPIs."""

    slug: str
    room_active: bool
    temperature_filtered: Optional[float] = None
    temperature_measured: Optional[float] = None
    constraint_lower: Optional[float] = None
    constraint_upper: Optional[float] = None
    setpoint: Optional[float] = None
    model_fit_r2: Optional[float] = None


def _is_finite(value: Optional[float]) -> bool:
    if value is None:
        return False
    return math.isfinite(value)


def room_temperature(room: RoomSnapshot) -> Optional[float]:
    """Prefer filtered temperature; fall back to measured (KPI_SPEC §6.3)."""
    if _is_finite(room.temperature_filtered):
        return room.temperature_filtered
    if _is_finite(room.temperature_measured):
        return room.temperature_measured
    return None


def comfort_deviation_c(temperature: float, lower: float, upper: float) -> float:
    """Signed distance outside the comfort corridor; zero when in band (§5.2)."""
    if temperature < lower:
        return lower - temperature
    if temperature > upper:
        return temperature - upper
    return 0.0


def room_comfort_deviation_c(room: RoomSnapshot) -> Optional[float]:
    """Room comfort deviation; ``None`` when inactive or required data missing."""
    if not room.room_active:
        return None
    temp = room_temperature(room)
    lower = room.constraint_lower
    upper = room.constraint_upper
    if not all(_is_finite(v) for v in (temp, lower, upper)):
        return None
    return comfort_deviation_c(temp, lower, upper)


def comfort_index_pct(rooms_data: Sequence[RoomSnapshot]) -> Optional[float]:
    """Percentage of active rooms within the comfort corridor (§4.2)."""
    active = [r for r in rooms_data if r.room_active]
    if not active:
        return None

    eligible = 0
    in_band = 0
    for room in active:
        temp = room_temperature(room)
        lower = room.constraint_lower
        upper = room.constraint_upper
        if not all(_is_finite(v) for v in (temp, lower, upper)):
            continue
        eligible += 1
        if lower <= temp <= upper:
            in_band += 1

    if eligible == 0:
        return None
    return 100.0 * in_band / eligible


def mean_tracking_error_c(rooms_data: Sequence[RoomSnapshot]) -> Optional[float]:
    """Mean |T − setpoint| across active rooms with valid data (§4.6)."""
    active = [r for r in rooms_data if r.room_active]
    if not active:
        return None

    errors: list[float] = []
    for room in active:
        temp = room_temperature(room)
        setpoint = room.setpoint
        if not _is_finite(temp) or not _is_finite(setpoint):
            continue
        errors.append(abs(temp - setpoint))

    if not errors:
        return None
    return sum(errors) / len(errors)


def model_fit_label(r2: float) -> str:
    """Map R² to GOOD / ACCEPTABLE / POOR (§4.7, §5.6)."""
    if r2 > 0.8:
        return MODEL_FIT_LABEL_GOOD
    if r2 > 0.5:
        return MODEL_FIT_LABEL_ACCEPTABLE
    return MODEL_FIT_LABEL_POOR


def aggregate_model_fit_r2(
    room_r2_values: Sequence[Optional[float]],
) -> tuple[float, str]:
    """Mean R² across rooms with valid data plus aggregate label (§4.7)."""
    valid = [v for v in room_r2_values if v is not None and _is_finite(v)]
    if not valid:
        return 0.0, MODEL_FIT_LABEL_EMPTY
    mean_r2 = sum(valid) / len(valid)
    return mean_r2, model_fit_label(mean_r2)


def effective_cop_display(
    cop: Optional[float],
    has_heat_pump: bool,
) -> Optional[float]:
    """Return COP for display, or ``None`` when the gauge should be hidden (§4.4)."""
    if not has_heat_pump:
        return None
    if cop is None or not _is_finite(cop) or cop == 0:
        return None
    return cop


def horizon_comfort_risk_steps(
    forecast_temps: Sequence[float],
    lower: float,
    upper: float,
) -> int:
    """Count forecast steps where predicted temperature exits the comfort band (§7)."""
    if not _is_finite(lower) or not _is_finite(upper):
        return 0
    count = 0
    for temp in forecast_temps:
        if not _is_finite(temp):
            continue
        if temp < lower or temp > upper:
            count += 1
    return count


def heat_loss_gauge_max(heat_loss_w: float) -> float:
    """Dynamic upper bound for the room heat-loss gauge (§5.5)."""
    if not _is_finite(heat_loss_w):
        return HEAT_LOSS_GAUGE_FLOOR_W
    return max(HEAT_LOSS_GAUGE_FLOOR_W, HEAT_LOSS_GAUGE_SCALE_FACTOR * heat_loss_w)


def solar_gain_gauge_max(solar_gain_w: float) -> float:
    """Dynamic upper bound for the room solar-gain gauge (§5.4)."""
    if not _is_finite(solar_gain_w):
        return SOLAR_GAIN_GAUGE_DEFAULT_MAX_W
    return max(SOLAR_GAIN_GAUGE_DEFAULT_MAX_W, solar_gain_w)


def house_heating_power_gauge_max(
    live_total_w: float,
    rated_capacity_w: Optional[float] = None,
) -> float:
    """Upper bound for house total-heating-power gauge normalisation (§4.3)."""
    if rated_capacity_w is not None and _is_finite(rated_capacity_w):
        return max(rated_capacity_w, HOUSE_POWER_GAUGE_FLOOR_W)
    if not _is_finite(live_total_w):
        return HOUSE_POWER_GAUGE_DEFAULT_MAX_W
    return max(
        HOUSE_POWER_GAUGE_DEFAULT_MAX_W,
        HOUSE_POWER_GAUGE_SCALE_FACTOR * live_total_w,
        HOUSE_POWER_GAUGE_FLOOR_W,
    )


def total_heating_power_kw(total_w: float) -> float:
    """Convert instantaneous total heating power to kW for display (§4.3)."""
    return round(total_w / 1000.0, 1)
