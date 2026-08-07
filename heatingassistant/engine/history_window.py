"""Backward-compatible re-export; prefer :mod:`history.window`."""

from __future__ import annotations

from .history.window import (
    DEFAULT_MAX_GAP_FACTOR,
    history_time_range,
    prune_stale_records,
    select_leading_window,
    select_recent_window,
    select_window_by_timestamps,
    split_contiguous_runs,
)

__all__ = [
    "DEFAULT_MAX_GAP_FACTOR",
    "history_time_range",
    "prune_stale_records",
    "select_leading_window",
    "select_recent_window",
    "select_window_by_timestamps",
    "split_contiguous_runs",
]
