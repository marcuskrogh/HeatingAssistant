"""Derive fast/slow NMPC grids from the substepping config triple."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_NMPC_KEYS = ("nmpc_period", "nmpc_fast_substeps", "nmpc_horizon_h")


@dataclass(frozen=True)
class NmpcTiming:
    """Integer two-rate grid derived from period, fast substeps, and look-ahead."""

    period_s: float
    fast_substeps: int
    horizon_h: float
    dt_s: float
    n_slow: int
    n_fast: int

    @property
    def m(self) -> int:
        return self.fast_substeps


def grid_slot_index(epoch_s: float, period_s: float, now_s: float) -> int:
    """Zero-based index of the grid slot containing ``now_s``.

    Slot 0 is ``[epoch, epoch+period)``. Times before the epoch map to 0.
    """

    period = float(period_s)
    if period <= 0.0:
        raise ValueError(f"period must be > 0; got {period}")
    elapsed = float(now_s) - float(epoch_s)
    if elapsed <= 0.0:
        return 0
    return int(elapsed // period)


def grid_remaining_s(epoch_s: float, period_s: float, now_s: float) -> float:
    """Seconds until the next exclusive grid time (full period on a boundary)."""

    period = float(period_s)
    if period <= 0.0:
        raise ValueError(f"period must be > 0; got {period}")
    elapsed = float(now_s) - float(epoch_s)
    if elapsed < 0.0:
        return period
    rem = period - (elapsed % period)
    if rem <= 0.0:
        return period
    return rem


def slow_slot_start_s(epoch_s: float, period_s: float, now_s: float) -> float:
    """Wall-clock start of the slow slot that contains ``now_s``."""

    n = grid_slot_index(epoch_s, period_s, now_s)
    return float(epoch_s) + n * float(period_s)


def next_grid_ts(epoch_s: float, period_s: float, now_s: float) -> float:
    """Return the next exclusive grid time after ``now_s``.

    If ``now_s`` is exactly on a slot, the following slot is returned so a
    just-finished tick does not schedule immediately again.
    """

    period = float(period_s)
    if period <= 0.0:
        raise ValueError(f"period must be > 0; got {period}")
    n = math.floor((float(now_s) - float(epoch_s)) / period) + 1
    if n < 1:
        n = 1
    return float(epoch_s) + n * period


def derive_nmpc_timing(
    period_s: float,
    fast_substeps: int,
    horizon_h: float,
) -> NmpcTiming:
    """Return timing with ``dt = period / M`` and ``N = horizon / period``.

    Raises ``ValueError`` when the triple does not divide exactly.
    """

    period = float(period_s)
    m = int(fast_substeps)
    horizon = float(horizon_h)
    if period <= 0.0:
        raise ValueError(f"nmpc_period must be > 0; got {period}")
    if m < 1:
        raise ValueError(f"nmpc_fast_substeps must be >= 1; got {m}")
    if horizon <= 0.0:
        raise ValueError(f"nmpc_horizon_h must be > 0; got {horizon}")
    dt = period / float(m)
    n_slow = horizon * 3600.0 / period
    if abs(n_slow - round(n_slow)) > 1e-6:
        raise ValueError(
            "look-ahead must be an integer number of NMPC periods "
            f"(horizon_h={horizon}, period_s={period})"
        )
    n = int(round(n_slow))
    if n < 1:
        raise ValueError("NMPC look-ahead must cover at least one slow step")
    return NmpcTiming(
        period_s=period,
        fast_substeps=m,
        horizon_h=horizon,
        dt_s=dt,
        n_slow=n,
        n_fast=n * m,
    )


def timing_from_dt_horizon(dt_s: float, horizon_steps: int) -> NmpcTiming:
    """Legacy/test constructor: one slow interval spanning the full horizon."""

    dt = float(dt_s)
    n_fast = int(horizon_steps)
    if dt <= 0.0 or n_fast < 1:
        raise ValueError("dt and horizon must be positive")
    period = dt * n_fast
    return derive_nmpc_timing(period, n_fast, period / 3600.0)


def _filled(value: Any, default: Any) -> Any:
    return default if value is None else value


def timing_from_options(
    options: Mapping[str, Any],
    *,
    default_period: float,
    default_substeps: int,
    default_horizon_h: float,
) -> NmpcTiming:
    """Build timing from config.

    The NMPC triple wins when any of its keys is set.  Otherwise a present
    ``update_interval`` / ``horizon`` pair is treated as a one-interval grid
    (tests and previews).  Completely empty config uses the production defaults.
    """

    if any(options.get(key) is not None for key in _NMPC_KEYS):
        return derive_nmpc_timing(
            float(_filled(options.get("nmpc_period"), default_period)),
            int(_filled(options.get("nmpc_fast_substeps"), default_substeps)),
            float(_filled(options.get("nmpc_horizon_h"), default_horizon_h)),
        )
    dt = options.get("update_interval")
    horizon = options.get("horizon")
    if dt is not None or horizon is not None:
        dt_s = float(_filled(dt, default_period / float(default_substeps)))
        n_fast = int(
            _filled(
                horizon,
                round(default_horizon_h * 3600.0 / dt_s),
            )
        )
        return timing_from_dt_horizon(dt_s, n_fast)
    return derive_nmpc_timing(default_period, default_substeps, default_horizon_h)


def timing_from_preview_overrides(
    base: Mapping[str, Any],
    overrides: Mapping[str, Any],
    *,
    default_period: float,
    default_substeps: int,
    default_horizon_h: float,
) -> NmpcTiming:
    """Resolve preview timing from draft knobs without ignoring a live triple.

    Draft NMPC keys win.  A draft ``horizon`` / ``update_interval`` still maps
    to a one-interval grid so Tuning previews stay small.  Otherwise the live
    config (including injected production defaults) is used.
    """

    ov = dict(overrides or {})
    merged = {**dict(base), **ov}
    if any(ov.get(key) is not None for key in _NMPC_KEYS):
        return timing_from_options(
            merged,
            default_period=default_period,
            default_substeps=default_substeps,
            default_horizon_h=default_horizon_h,
        )
    if ov.get("horizon") is not None or ov.get("update_interval") is not None:
        dt_s = float(
            _filled(
                ov.get("update_interval"),
                merged.get("update_interval") or (default_period / float(default_substeps)),
            )
        )
        n_fast = int(
            _filled(
                ov.get("horizon"),
                merged.get("horizon")
                or int(round(default_horizon_h * 3600.0 / dt_s)),
            )
        )
        return timing_from_dt_horizon(dt_s, n_fast)
    return timing_from_options(
        merged,
        default_period=default_period,
        default_substeps=default_substeps,
        default_horizon_h=default_horizon_h,
    )
