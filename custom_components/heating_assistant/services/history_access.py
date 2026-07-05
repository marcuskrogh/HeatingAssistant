"""History buffer access for identification and simulation services."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.core import HomeAssistant

from ..coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


async def get_history_for_window(
    hass: HomeAssistant,
    coordinator: HeatingAssistantCoordinator,
    window_start: Optional[float],
    window_end: Optional[float],
) -> List[Dict[str, Any]]:
    """Return history records for a time window.

    Priority order:
    1. In-memory buffer when it already covers the requested window (fast path).
    2. Integration-managed JSONL store (covers up to ``retention_days`` back).
    3. HA Recorder (fallback for the period before the JSONL store existed).
    4. In-memory buffer as a last resort (with a debug log).
    """
    buf = list(coordinator.history_buffer)

    if window_start is None or window_end is None:
        return buf

    oldest_buf_ts = float(buf[0]["timestamp"]) if buf else None
    if oldest_buf_ts is not None and oldest_buf_ts <= window_start:
        return buf  # fast path: buffer already covers the window

    # Try the integration-managed JSONL store first.
    if coordinator.id_history_store is not None:
        try:
            records = await coordinator.id_history_store.async_query_range(
                window_start, window_end
            )
            if records:
                return records
        except Exception:
            _LOGGER.warning(
                "ID history store query failed for window [%s, %s]",
                window_start, window_end, exc_info=True,
            )

    # Fall back to HA Recorder (works for the initial period before the store
    # was populated, or if the store files were deleted).
    try:
        from ..history_seed import async_fetch_history_range

        records = await async_fetch_history_range(
            hass, coordinator, window_start, window_end
        )
        if records:
            return records
    except Exception:
        _LOGGER.debug(
            "Recorder fallback for window [%s, %s] failed",
            window_start, window_end, exc_info=True,
        )

    _LOGGER.debug(
        "No long-term history found for window [%s, %s]; using in-memory buffer",
        window_start, window_end,
    )
    return buf


async def get_history_for_horizon(
    hass: HomeAssistant,
    coordinator: HeatingAssistantCoordinator,
    horizon_hours: float,
) -> List[Dict[str, Any]]:
    """Return history for a trailing recent-horizon window.

    Uses the in-memory buffer when it already covers the requested span;
    otherwise fetches older records from the JSONL store / Recorder.
    """
    from ..history_window import select_recent_window

    horizon_s = float(horizon_hours) * 3600.0
    buf = list(coordinator.history_buffer)
    if not buf or horizon_s <= 0:
        return buf

    latest_ts = float(buf[-1]["timestamp"])
    window_start = latest_ts - horizon_s
    oldest_buf_ts = float(buf[0]["timestamp"])

    if oldest_buf_ts <= window_start:
        return select_recent_window(buf, horizon_s, coordinator.dt)

    history = await get_history_for_window(
        hass, coordinator, window_start, latest_ts
    )
    return select_recent_window(history, horizon_s, coordinator.dt)


async def get_history_with_leading(
    hass: HomeAssistant,
    coordinator: HeatingAssistantCoordinator,
    window_start: float,
    window_end: float,
    leading_hours: float = 6.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch leading calibration data and the simulation window.

    Returns ``(full_history, leading_history, simulation_history)``.
    """
    from ..history_window import (
        select_leading_window,
        select_window_by_timestamps,
    )

    leading_s = float(leading_hours) * 3600.0
    fetch_start = float(window_start) - leading_s
    fetch_end = float(window_end)
    full = await get_history_for_window(hass, coordinator, fetch_start, fetch_end)
    simulation = select_window_by_timestamps(full, window_start, window_end)
    leading = select_leading_window(full, window_start, leading_s)
    return full, leading, simulation


def records_for_dataset(
    coordinator: HeatingAssistantCoordinator,
    dataset_id: Optional[str],
) -> Optional[List[Dict[str, Any]]]:
    """Return the snapshotted records for a stored dataset, or ``None``.

    Used by the identification / simulation services so a stored dataset can be
    referenced directly (by ``dataset_id``) instead of a time window — the data
    is read from the dataset's own permanent snapshot, so it works even after the
    rolling history that produced it has been pruned.
    """
    if not dataset_id:
        return None
    store = getattr(coordinator, "dataset_store", None)
    if store is None:
        return None
    records = store.get_records(dataset_id)
    return records if records else None


def records_for_datasets(
    coordinator: HeatingAssistantCoordinator,
    dataset_ids: Optional[List[str]],
) -> Optional[List[Dict[str, Any]]]:
    """Return the concatenated records of several stored datasets, or ``None``.

    Each dataset's snapshotted records are gathered and merged into a single,
    timestamp-sorted history list.  The estimator splits the merged history into
    contiguous segments wherever the inter-record gap is large, so combining
    disjoint datasets (e.g. several overnight experiments) just yields several
    independent identification segments — exactly what we want for a joint fit.
    """
    if not dataset_ids:
        return None
    store = getattr(coordinator, "dataset_store", None)
    if store is None:
        return None
    merged: List[Dict[str, Any]] = []
    for ds_id in dataset_ids:
        recs = store.get_records(ds_id)
        if recs:
            merged.extend(recs)
    if not merged:
        return None
    merged.sort(key=lambda r: float(r.get("timestamp", 0.0)))
    return merged


def dataset_boundaries(
    coordinator: HeatingAssistantCoordinator,
    dataset_ids: Optional[List[str]],
) -> Optional[List[float]]:
    """Return the first timestamp of each stored dataset, sorted ascending.

    Passed to the multi-dataset estimator so it can assign an independent
    per-dataset wall-temperature parameter block to each dataset segment.
    Returns ``None`` when ``dataset_ids`` is empty or the store is unavailable.
    """
    if not dataset_ids:
        return None
    store = getattr(coordinator, "dataset_store", None)
    if store is None:
        return None
    starts: List[float] = []
    for ds_id in dataset_ids:
        recs = store.get_records(ds_id)
        if recs:
            ts = recs[0].get("timestamp")
            if ts is not None:
                starts.append(float(ts))
    return sorted(starts) if starts else None
