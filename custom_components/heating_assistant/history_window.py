"""
History-window selection for the identification diagnostics.

The rolling history buffer can contain gaps from controller restarts or brief
data outages (Home Assistant reloads, network drop-outs, the integration being
disabled for a while).  Because the thermal model is *continuous-time*, the EKF
reconstruction and the open-loop simulation can both propagate across such a gap
using the true elapsed time between samples — the gap itself carries no
information and is not a problem to bridge.

What *was* a problem is how the recent window is selected.  Selecting purely by
wall-clock time::

    window = [h for h in history if h["timestamp"] >= last_ts - horizon_seconds]

lets a single restart gap consume most of the requested horizon: if the user
asks for the last 6 h of data and a 4 h restart gap sits inside that window,
only the handful of samples recorded *after* the restart survive the filter,
even though plenty of usable data exists just before the gap.  The result is
that reconstruction / open-loop simulation appear to ignore everything before
the most recent restart.

``select_recent_window`` fixes this by measuring the horizon in **active
sampled time** instead of raw wall-clock time.  Walking backwards from the most
recent record it accumulates the elapsed time between consecutive samples, but
caps each interval at ``max_gap_factor * nominal_dt`` so a long restart gap
costs at most one nominal step of the budget.  The window therefore spans
roughly ``horizon_seconds`` of *real operation* regardless of how many restarts
occurred within it — small sample-interval discrepancies and short outages no
longer eat into the usable data.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: An inter-sample interval larger than ``DEFAULT_MAX_GAP_FACTOR * nominal_dt``
#: is treated as a discontinuity (restart / outage) rather than a regular step.
#: Kept consistent with the gap factor used by the parameter estimator's
#: contiguous-segment splitting.
DEFAULT_MAX_GAP_FACTOR = 1.5


def select_recent_window(
    history: List[Dict[str, Any]],
    horizon_seconds: float,
    nominal_dt: float,
    max_gap_factor: float = DEFAULT_MAX_GAP_FACTOR,
) -> List[Dict[str, Any]]:
    """Return the most recent slice of ``history`` covering ``horizon_seconds``
    of active (sampled) time, bridging restart gaps for free.

    Parameters
    ----------
    history
        History-buffer records, ordered oldest → newest, each carrying a
        numeric ``timestamp`` (UNIX seconds).
    horizon_seconds
        Requested span of *active* data, in seconds.  ``<= 0`` returns the full
        history.
    nominal_dt
        Nominal sampling interval [s].  Used only to size the gap cap.
    max_gap_factor
        Intervals longer than ``max_gap_factor * nominal_dt`` are capped at that
        value when accumulating the budget, so a restart gap does not consume
        the horizon.

    Returns
    -------
    list
        A contiguous tail of ``history`` (possibly the whole list).
    """
    if not history:
        return []
    if horizon_seconds <= 0 or len(history) < 2:
        return list(history)

    gap_cap = max(0.0, max_gap_factor * nominal_dt)
    acc = 0.0
    start = 0
    for k in range(len(history) - 1, 0, -1):
        dt_step = (
            float(history[k].get("timestamp", 0.0))
            - float(history[k - 1].get("timestamp", 0.0))
        )
        if dt_step > 0.0:
            # A restart gap (dt_step >> nominal_dt) is bridged at the cost of a
            # single capped step rather than its full — possibly multi-hour —
            # duration, so it does not exhaust the horizon budget.
            acc += min(dt_step, gap_cap) if gap_cap > 0.0 else dt_step
        if acc > horizon_seconds:
            start = k
            break

    return list(history[start:])
