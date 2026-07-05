"""Shared coordinator types and coercion helpers."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any, Dict, Optional

import numpy as np


@dataclasses.dataclass
class ControlTrajectory:
    """Per-step schedule-projected control parameters for the MPC horizon.

    All arrays have shape ``(N,)`` where N is the prediction horizon.
    Room-keyed dicts contain one array per configured room.
    """

    setpoints: Dict[str, "np.ndarray"]        # room → per-step setpoint [°C]
    comfort_offsets: Dict[str, "np.ndarray"]  # room → per-step corridor half-width [°C]
    q_scales: Dict[str, "np.ndarray"]         # room → per-step Q multiplier [–]
    r_scales: Dict[str, "np.ndarray"]         # room → per-step R multiplier [–]
    enabled_steps: Dict[str, "np.ndarray"]    # room → per-step enabled flag [bool]


def _coerce_interval_seconds(value: Any) -> float:
    """Return a numeric interval in seconds from int/float/str/timedelta values."""
    if isinstance(value, timedelta):
        return float(value.total_seconds())
    return float(value)


def _coerce_opt_float(value: Any) -> Optional[float]:
    """Return ``float(value)`` or ``None`` for missing/blank/unparsable input."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
