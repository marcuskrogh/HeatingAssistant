"""Pure helpers for selecting history from stored datasets."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Dict, List, Optional


def _dataset_store(source: Any) -> Any:
    if hasattr(source, "get_records"):
        return source
    return getattr(source, "dataset_store", None)


def records_for_dataset(
    source: Any,
    dataset_id: Optional[str],
) -> Optional[List[Dict[str, Any]]]:
    """Return snapshotted records for a stored dataset, or ``None``."""

    if not dataset_id:
        return None
    store = _dataset_store(source)
    if store is None:
        return None
    records = store.get_records(dataset_id)
    return records if records else None


def records_for_datasets(
    source: Any,
    dataset_ids: Optional[Sequence[str]],
) -> Optional[List[Dict[str, Any]]]:
    """Return several stored datasets merged and sorted by timestamp."""

    if not dataset_ids:
        return None
    store = _dataset_store(source)
    if store is None:
        return None
    merged: List[Dict[str, Any]] = []
    for dataset_id in dataset_ids:
        records = store.get_records(dataset_id)
        if records:
            merged.extend(records)
    if not merged:
        return None
    merged.sort(key=lambda record: float(record.get("timestamp", 0.0)))
    return merged


def dataset_boundaries(
    source: Any,
    dataset_ids: Optional[Sequence[str]],
) -> Optional[List[float]]:
    """Return the first timestamp of each stored dataset, sorted ascending."""

    if not dataset_ids:
        return None
    store = _dataset_store(source)
    if store is None:
        return None
    starts: List[float] = []
    for dataset_id in dataset_ids:
        records = store.get_records(dataset_id)
        if not records:
            continue
        timestamp = records[0].get("timestamp")
        if timestamp is not None:
            starts.append(float(timestamp))
    return sorted(starts) if starts else None


__all__ = [
    "dataset_boundaries",
    "records_for_dataset",
    "records_for_datasets",
]
