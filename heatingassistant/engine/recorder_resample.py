"""
Resample irregular recorder state-history onto a uniform time grid.

Home Assistant's recorder stores one row per *state change*, so each entity's
history is an irregular series of ``(timestamp, value)`` samples.  To rebuild
the identification history buffer we need every channel (room temperatures,
heater commands, outdoor temperature, solar gains) sampled on the *same*
uniform grid at the coordinator's ``dt`` spacing.

The controller applies its inputs with a zero-order hold (a command holds until
the next change), so the correct way to evaluate a channel at a grid instant is
"the most recent sample at or before that instant" — exactly what
``zoh_resample`` does.
"""

from __future__ import annotations

import bisect
from typing import List, Optional, Sequence, Tuple


def build_uniform_grid(start_ts: float, end_ts: float, dt: float) -> List[float]:
    """Return grid timestamps ``start_ts, start_ts+dt, … <= end_ts``.

    ``dt`` must be positive.  An empty list is returned when the span is empty.
    """
    if dt <= 0 or end_ts < start_ts:
        return []
    n = int((end_ts - start_ts) / dt)
    return [start_ts + i * dt for i in range(n + 1)]


def zoh_resample(
    samples: Sequence[Tuple[float, float]],
    grid: Sequence[float],
) -> List[Optional[float]]:
    """Zero-order-hold resample ``samples`` onto ``grid``.

    Parameters
    ----------
    samples
        ``(timestamp, value)`` pairs.  Need not be sorted; ``None`` values are
        dropped.  Timestamps in UNIX seconds.
    grid
        Target timestamps (ascending), e.g. from :func:`build_uniform_grid`.

    Returns
    -------
    list
        For each grid point, the value of the most recent sample at or before
        that timestamp, or ``None`` if no sample precedes it.
    """
    clean = sorted(
        (float(ts), float(v))
        for ts, v in samples
        if v is not None and _finite(ts) and _finite(v)
    )
    if not clean:
        return [None] * len(grid)

    times = [ts for ts, _ in clean]
    out: List[Optional[float]] = []
    for g in grid:
        # rightmost sample whose timestamp is <= g
        idx = bisect.bisect_right(times, g) - 1
        out.append(clean[idx][1] if idx >= 0 else None)
    return out


def _finite(x: float) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return xf == xf and xf not in (float("inf"), float("-inf"))
