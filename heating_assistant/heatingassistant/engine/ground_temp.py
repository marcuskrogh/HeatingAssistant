"""
Ground-temperature model for the Phase 1 A2 slab node.

The slab node in the 2R2C+slab thermal model conducts to the ground at
a temperature that's largely decoupled from outdoor air on the
timescale of weeks to months.  We model it as a simple sinusoidal
annual cycle parameterised by an annual mean, an amplitude, and the
day-of-year of the warmest soil temperature:

    T_g(t) = T_g_mean + T_g_amp · cos(2π · (day_of_year − peak_day) / 365)

This is a deliberately small model: it captures the ground's slow
annual swing (the dominant effect on slab-heat-loss seasonality)
without depending on any external data source.  Sophistications
available for later phases:

- Latitude-dependent parameter derivation (today the defaults are
  tuned for temperate residential climates around 55 °N).
- Depth-dependent damping and phase shift (Phase 4 once the
  parameter estimator learns slab-depth parameters).
- A diurnal cycle (negligible at typical slab depths; not worth the
  complexity).

The helper is intentionally pure-Python so it can be called from the
coordinator without pulling in numpy or HA dependencies.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from .const import (
    DEFAULT_GROUND_TEMP_AMPLITUDE,
    DEFAULT_GROUND_TEMP_MEAN,
    DEFAULT_GROUND_TEMP_PEAK_DAY,
)


def ground_temperature(
    now: Optional[datetime] = None,
    *,
    mean: float = DEFAULT_GROUND_TEMP_MEAN,
    amplitude: float = DEFAULT_GROUND_TEMP_AMPLITUDE,
    peak_day: float = DEFAULT_GROUND_TEMP_PEAK_DAY,
) -> float:
    """Return the slab-depth ground temperature at ``now`` [°C].

    Parameters
    ----------
    now : datetime, optional
        Wall-clock instant.  Defaults to the current UTC time if not
        provided.  Only the day-of-year matters; the time-of-day
        component is ignored (the soil cycle has negligible diurnal
        amplitude at slab depths).
    mean : float
        Annual mean ground temperature [°C].
    amplitude : float
        Annual amplitude of the cycle [°C] — half the peak-to-trough
        swing.
    peak_day : float
        Day-of-year (1–365) at which the soil reaches its annual peak.
        Typically lags the outdoor-air peak by ~20 days at ~30 cm depth.

    Returns
    -------
    float
        Ground temperature [°C] at ``now``.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    day_of_year = float(now.timetuple().tm_yday)
    phase = 2.0 * math.pi * (day_of_year - peak_day) / 365.0
    return float(mean + amplitude * math.cos(phase))
