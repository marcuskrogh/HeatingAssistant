"""History record parsing, window selection, seeding, and persistence."""

from .records import record_d, record_u, record_window_open
from .seed import (
    async_fetch_history_range,
    async_rebuild_history_from_recorder,
    build_records_from_history,
)
from .store import IdentificationHistoryStore
from .window import (
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
    "IdentificationHistoryStore",
    "async_fetch_history_range",
    "async_rebuild_history_from_recorder",
    "build_records_from_history",
    "history_time_range",
    "prune_stale_records",
    "record_d",
    "record_u",
    "record_window_open",
    "select_leading_window",
    "select_recent_window",
    "select_window_by_timestamps",
    "split_contiguous_runs",
]
