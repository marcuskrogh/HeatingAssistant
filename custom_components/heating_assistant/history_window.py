"""
History-window selection for the identification diagnostics.

The rolling history buffer can contain gaps from controller restarts or brief
data outages (Home Assistant reloads, network drop-outs, the integration being
disabled for a while).  It is also *count-bounded* (``deque(maxlen=…)``), not
time-bounded, so it can legitimately hold records from a previous operating
session weeks ago next to today's data.

Two modes of window selection are provided:

1. **Recent window** (``select_recent_window``): selects all records within
   ``horizon_seconds`` of the most recent record.  This is the original
   behaviour — the user controls the window width via the horizon slider.

2. **Explicit timestamp window** (``select_window_by_timestamps``): selects all
   records whose timestamp falls in ``[start_ts, end_ts]``.  This enables
   targeted identification using a specific experiment (e.g. a step-response
   performed the previous night) that may not be the most recent data.

The identification diagnostics (EKF reconstruction and open-loop simulation)
operate on the most recent ``horizon`` of data by default.  The window is
selected by **wall-clock time**: every record whose timestamp falls within
``horizon`` of the most recent record is included.  This is the behaviour the
user controls directly via the horizon slider and has the properties we want:

* A short restart (a few minutes) does *not* truncate the window — all data on
  both sides of the gap is within the horizon and is kept.  The continuous-time
  model bridges the gap using the true elapsed time between samples.
* The window can never reach back further than ``horizon`` of wall-clock time,
  so a week-old previous session is never pulled in and the chart's time axis
  always spans at most ``horizon`` (no "sporadic points over several weeks").

Earlier revisions tried to measure the horizon in "active sampled time" with a
per-gap cap; that inadvertently let uniformly sparse data — or a single large
gap — stretch the window across weeks, because every step was charged only a
capped cost.  Wall-clock selection avoids that failure mode entirely.

``split_contiguous_runs`` and ``prune_stale_records`` are companion helpers used
by the open-loop diagnostic and the persistence-restore path respectively.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

#: An inter-sample interval larger than ``DEFAULT_MAX_GAP_FACTOR * nominal_dt``
#: is treated as a discontinuity (restart / outage) rather than a regular step.
#: Kept consistent with the gap factor used by the parameter estimator's
#: contiguous-segment splitting.
DEFAULT_MAX_GAP_FACTOR = 1.5


def _normalise(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return ``history`` sorted ascending by timestamp with invalid timestamps
    dropped and exact-duplicate timestamps collapsed (keeping the last).

    The buffer is normally already monotonic, so this is a defensive no-op in
    the common case.  It protects the diagnostics from persistence quirks
    (records restored out of order, duplicated, or carrying a missing/zero
    timestamp) that would otherwise corrupt both the window selection and the
    continuous-time integration.
    """
    valid = []
    for rec in history:
        ts = rec.get("timestamp")
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(ts_f) or ts_f <= 0.0:
            continue
        valid.append((ts_f, rec))

    valid.sort(key=lambda item: item[0])

    deduped: List[Dict[str, Any]] = []
    last_ts: float | None = None
    for ts_f, rec in valid:
        if last_ts is not None and ts_f == last_ts:
            deduped[-1] = rec  # keep the most recent record for this timestamp
        else:
            deduped.append(rec)
            last_ts = ts_f
    return deduped


def select_recent_window(
    history: List[Dict[str, Any]],
    horizon_seconds: float,
    nominal_dt: float = 0.0,  # accepted for API compatibility; unused
    max_gap_factor: float = DEFAULT_MAX_GAP_FACTOR,  # unused
) -> List[Dict[str, Any]]:
    """Return the records within ``horizon_seconds`` (wall-clock) of the most
    recent record, normalised to ascending, de-duplicated timestamps.

    Parameters
    ----------
    history
        History-buffer records, each carrying a numeric ``timestamp`` (UNIX
        seconds).  Order is not trusted; it is normalised internally.
    horizon_seconds
        Width of the window, in seconds, measured back from the latest record.
        ``<= 0`` returns the full (normalised) history.
    nominal_dt, max_gap_factor
        Accepted for backward-compatibility with earlier call sites; ignored.

    Returns
    -------
    list
        The contiguous (in time) tail of the normalised history that falls
        within the requested horizon.
    """
    history = _normalise(history)
    if not history:
        return []
    if horizon_seconds <= 0:
        return history

    last_ts = float(history[-1]["timestamp"])
    cutoff = last_ts - horizon_seconds
    # history is sorted ascending; find the first record at/after the cutoff.
    start = 0
    for k in range(len(history) - 1, -1, -1):
        if float(history[k]["timestamp"]) < cutoff:
            start = k + 1
            break
    return history[start:]


def select_window_by_timestamps(
    history: List[Dict[str, Any]],
    start_ts: float,
    end_ts: float,
) -> List[Dict[str, Any]]:
    """Return records with timestamp in ``[start_ts, end_ts]``, normalised.

    Unlike :func:`select_recent_window`, this function selects an **explicit**
    time range rather than a trailing window anchored to the most recent record.
    This is the right tool when the user wants to identify from a specific
    past experiment (e.g. an overnight step-response test) that is not the
    most recent data in the buffer.

    Parameters
    ----------
    history
        History-buffer records, each carrying a numeric ``timestamp`` (UNIX
        seconds).  Order is not trusted; it is normalised internally.
    start_ts, end_ts
        Inclusive window bounds as UNIX timestamps [s].  ``start_ts`` must be
        ≤ ``end_ts``; passing ``start_ts > end_ts`` returns an empty list.

    Returns
    -------
    list
        The records whose timestamp lies in ``[start_ts, end_ts]``, sorted
        ascending, de-duplicated.
    """
    if start_ts > end_ts:
        return []
    history = _normalise(history)
    return [r for r in history if start_ts <= float(r["timestamp"]) <= end_ts]


def select_leading_window(
    history: List[Dict[str, Any]],
    window_start_ts: float,
    leading_seconds: float,
) -> List[Dict[str, Any]]:
    """Return records in ``[window_start_ts - leading_seconds, window_start_ts)``.

    Used to fetch a calibration period *before* an explicit identification or
    simulation window so the backend can estimate hidden envelope temperatures
    (and emitter-lag states) without fitting initial conditions on the same
    data being scored.

    The returned slice excludes the anchor record at ``window_start_ts`` so
    the simulation window and the leading calibration window do not overlap.
    """
    if leading_seconds <= 0:
        return []
    history = _normalise(history)
    if not history:
        return []
    start = float(window_start_ts) - float(leading_seconds)
    end = float(window_start_ts)
    return [r for r in history if start <= float(r["timestamp"]) < end]


def history_time_range(
    history: List[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float]]:
    """Return ``(min_ts, max_ts)`` for a normalised history buffer.

    Returns ``(None, None)`` for an empty or all-invalid buffer.
    """
    hist = _normalise(history)
    if not hist:
        return None, None
    return float(hist[0]["timestamp"]), float(hist[-1]["timestamp"])


def split_contiguous_runs(
    history: List[Dict[str, Any]],
    nominal_dt: float,
    max_gap_factor: float = DEFAULT_MAX_GAP_FACTOR,
) -> List[List[Dict[str, Any]]]:
    """Split ``history`` into runs broken wherever the inter-sample interval
    exceeds ``max_gap_factor * nominal_dt`` (or is non-monotonic).

    Used by the open-loop diagnostic so a restart gap starts a fresh free-run
    segment instead of integrating the model across the dead interval with
    stale, held inputs (which produces a large spurious error spike).
    """
    history = _normalise(history)
    if not history:
        return []

    gap = max(0.0, max_gap_factor * nominal_dt)
    runs: List[List[Dict[str, Any]]] = [[history[0]]]
    for prev, rec in zip(history, history[1:]):
        dt_step = float(rec["timestamp"]) - float(prev["timestamp"])
        if gap > 0.0 and 0.0 < dt_step <= gap:
            runs[-1].append(rec)
        else:
            runs.append([rec])
    return runs


def prune_stale_records(
    history: List[Dict[str, Any]],
    now_ts: float,
    max_age_seconds: float,
) -> List[Dict[str, Any]]:
    """Drop records older than ``max_age_seconds`` before ``now_ts``.

    The history buffer is a count-bounded deque, so without this a previous
    operating session's week-old records can survive across restarts and sit at
    the front of the buffer next to today's data.  Pruning on restore keeps the
    buffer time-bounded to its intended span and prevents the diagnostics from
    ever seeing a stale, unrelated epoch.
    """
    if max_age_seconds <= 0:
        return list(history)
    cutoff = now_ts - max_age_seconds
    kept = []
    for rec in history:
        try:
            ts = float(rec.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            kept.append(rec)
    return kept
