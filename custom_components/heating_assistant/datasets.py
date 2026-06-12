"""
Named, stored system-identification datasets.

A *stored dataset* is a named, self-contained snapshot of the controller's
observation records over a chosen time window.  Two things create them:

* the user manually saving a custom data window (giving it a unique name), and
* a scheduled experiment auto-saving its window when it completes.

Unlike the rolling JSONL identification-history store (which is bounded by
``retention_days`` and can be purged), a stored dataset copies the actual
records into its own persistent ``Store`` so the data is **kept indefinitely**
and can always be referenced for identification later, even after the raw
history has been pruned.

The module is free of Home Assistant imports apart from the optional ``Store``
used by :class:`DatasetStore`, so :func:`summarize_records` and the metadata
maths can be unit-tested without a HA install.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .const import (
    DATASET_SOURCE_MANUAL,
    DOMAIN,
    MAX_DATASET_RECORDS,
    MAX_STORED_DATASETS,
)


def _coerce_ts(value: Any) -> Optional[float]:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts != ts or ts <= 0.0:  # NaN or non-positive
        return None
    return ts


def summarize_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return summary statistics describing a list of observation records.

    Computes the record count, the covered time span, and (best-effort) the
    measured-temperature range across all rooms, used to label a dataset in the
    UI without re-reading the full record list.
    """
    count = len(records)
    timestamps = [t for t in (_coerce_ts(r.get("timestamp")) for r in records) if t is not None]
    start_ts = min(timestamps) if timestamps else None
    end_ts = max(timestamps) if timestamps else None

    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    for rec in records:
        ys = rec.get("y")
        if not isinstance(ys, (list, tuple)):
            continue
        for y in ys:
            try:
                yv = float(y)
            except (TypeError, ValueError):
                continue
            if yv != yv:  # NaN
                continue
            temp_min = yv if temp_min is None else min(temp_min, yv)
            temp_max = yv if temp_max is None else max(temp_max, yv)

    return {
        "record_count": count,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "duration_s": (end_ts - start_ts) if (start_ts is not None and end_ts is not None) else None,
        "temp_min": temp_min,
        "temp_max": temp_max,
    }


def trim_records(records: List[Dict[str, Any]], max_records: int = MAX_DATASET_RECORDS) -> List[Dict[str, Any]]:
    """Keep at most ``max_records`` records, preferring the most recent ones."""
    if max_records <= 0 or len(records) <= max_records:
        return list(records)
    return list(records[-max_records:])


def build_dataset(
    name: str,
    records: List[Dict[str, Any]],
    *,
    room_name: Optional[str] = None,
    room_slug: Optional[str] = None,
    source: str = DATASET_SOURCE_MANUAL,
    notes: str = "",
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
    experiment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a complete dataset dict (metadata + snapshotted records).

    The records are trimmed to :data:`MAX_DATASET_RECORDS` to keep the persistent
    store bounded.  The requested window bounds are recorded alongside the actual
    span of the snapshotted data.
    """
    trimmed = trim_records(records)
    summary = summarize_records(trimmed)
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "room_name": room_name,
        "room_slug": room_slug,
        "source": source,
        "notes": notes,
        "created_at": time.time(),
        # Requested window (what the user / experiment asked for) ...
        "window_start": window_start if window_start is not None else summary["start_ts"],
        "window_end": window_end if window_end is not None else summary["end_ts"],
        # ... and the actual span / size of the captured data.
        "record_count": summary["record_count"],
        "data_start_ts": summary["start_ts"],
        "data_end_ts": summary["end_ts"],
        "temp_min": summary["temp_min"],
        "temp_max": summary["temp_max"],
        "experiment": experiment,
        "records": trimmed,
    }


def dataset_meta(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dataset dict without its (potentially large) ``records`` list.

    Used for listing datasets in the UI / WebSocket responses so the heavy
    record payload is only transferred when a specific dataset is opened.
    """
    return {k: v for k, v in dataset.items() if k != "records"}


class DatasetStore:
    """Persistent store of named identification datasets (snapshotted records)."""

    def __init__(self, hass: Any, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._hass = hass
        self._store = Store(hass, version=1, key=f"{DOMAIN}_datasets_{entry_id}")
        self._datasets: List[Dict[str, Any]] = []
        self._loaded = False

    # -- lifecycle --------------------------------------------------------

    async def async_load(self) -> None:
        """Load datasets from disk once."""
        if self._loaded:
            return
        try:
            data = await self._store.async_load()
        except Exception:  # pragma: no cover - defensive
            data = None
        if isinstance(data, dict) and isinstance(data.get("datasets"), list):
            self._datasets = [d for d in data["datasets"] if isinstance(d, dict)]
        self._loaded = True

    async def _async_persist(self) -> None:
        await self._store.async_save({"datasets": self._datasets})

    # -- queries ----------------------------------------------------------

    def list_meta(self) -> List[Dict[str, Any]]:
        """Return metadata for all datasets (newest first), without records."""
        metas = [dataset_meta(d) for d in self._datasets]
        metas.sort(key=lambda d: d.get("created_at") or 0.0, reverse=True)
        return metas

    def get(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        for d in self._datasets:
            if d.get("id") == dataset_id:
                return d
        return None

    def get_records(self, dataset_id: str) -> Optional[List[Dict[str, Any]]]:
        d = self.get(dataset_id)
        if d is None:
            return None
        recs = d.get("records")
        return list(recs) if isinstance(recs, list) else []

    # -- mutation ---------------------------------------------------------

    async def async_add(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        """Add a dataset and persist, pruning the oldest manual datasets if the
        cap is exceeded."""
        await self.async_load()
        self._datasets.append(dataset)
        self._enforce_cap()
        await self._async_persist()
        return dataset

    def _enforce_cap(self) -> None:
        if len(self._datasets) <= MAX_STORED_DATASETS:
            return
        # Drop oldest first; this keeps recent datasets (including experiment
        # auto-saves) and discards stale manual snapshots.
        self._datasets.sort(key=lambda d: d.get("created_at") or 0.0)
        self._datasets = self._datasets[len(self._datasets) - MAX_STORED_DATASETS:]

    async def async_delete(self, dataset_id: str) -> bool:
        await self.async_load()
        before = len(self._datasets)
        self._datasets = [d for d in self._datasets if d.get("id") != dataset_id]
        changed = len(self._datasets) != before
        if changed:
            await self._async_persist()
        return changed
