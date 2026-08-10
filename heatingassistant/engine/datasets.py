"""Named, stored system-identification datasets for the App engine."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .const import (
    DATASET_SOURCE_MANUAL,
    MAX_DATASET_RECORDS,
    MAX_STORED_DATASETS,
)

_LOGGER = logging.getLogger(__name__)
_DATASETS_DIRNAME = "datasets"


def _coerce_ts(value: Any) -> Optional[float]:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts != ts or ts <= 0.0:
        return None
    return ts


def summarize_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return count, span, and measured-temperature range for records."""

    count = len(records)
    timestamps = [
        ts for ts in (_coerce_ts(record.get("timestamp")) for record in records)
        if ts is not None
    ]
    start_ts = min(timestamps) if timestamps else None
    end_ts = max(timestamps) if timestamps else None

    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    for record in records:
        values = record.get("y")
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            try:
                temp = float(value)
            except (TypeError, ValueError):
                continue
            if temp != temp:
                continue
            temp_min = temp if temp_min is None else min(temp_min, temp)
            temp_max = temp if temp_max is None else max(temp_max, temp)

    return {
        "record_count": count,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "duration_s": (end_ts - start_ts) if start_ts is not None and end_ts is not None else None,
        "temp_min": temp_min,
        "temp_max": temp_max,
    }


def trim_records(
    records: List[Dict[str, Any]],
    max_records: int = MAX_DATASET_RECORDS,
) -> List[Dict[str, Any]]:
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
    """Build a complete dataset dict containing metadata and records."""

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
        "window_start": window_start if window_start is not None else summary["start_ts"],
        "window_end": window_end if window_end is not None else summary["end_ts"],
        "record_count": summary["record_count"],
        "data_start_ts": summary["start_ts"],
        "data_end_ts": summary["end_ts"],
        "temp_min": summary["temp_min"],
        "temp_max": summary["temp_max"],
        "experiment": experiment,
        "records": trimmed,
    }


def dataset_meta(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dataset dict without its potentially large ``records`` list."""

    return {key: value for key, value in dataset.items() if key != "records"}


def _json_default(value: Any) -> Any:
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    return str(value)


class DatasetStore:
    """File-backed store of named identification datasets."""

    def __init__(
        self,
        data_dir: str | Path,
        entry_id: str = "default",
        *,
        filename: str | None = None,
    ) -> None:
        self._dir = Path(data_dir) / _DATASETS_DIRNAME
        self._path = self._dir / (filename or f"{entry_id}.json")
        self._datasets: List[Dict[str, Any]] = []
        self._loaded = False

    @property
    def path(self) -> Path:
        """Path of the JSON file backing this store."""

        return self._path

    def load(self) -> None:
        """Load datasets from disk once."""

        if self._loaded:
            return
        self._datasets = self._read()
        self._loaded = True

    async def async_load(self) -> None:
        """Async wrapper around :meth:`load`."""

        await asyncio.to_thread(self.load)

    def _read(self) -> List[Dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Dataset store load failed from %s: %s", self._path, exc)
            return []
        if not isinstance(raw, dict) or not isinstance(raw.get("datasets"), list):
            return []
        return [item for item in raw["datasets"] if isinstance(item, dict)]

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f"{self._path.suffix}.tmp")
        payload = json.dumps(
            {"datasets": self._datasets},
            ensure_ascii=True,
            separators=(",", ":"),
            default=_json_default,
        )
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self._path)

    async def _async_persist(self) -> None:
        await asyncio.to_thread(self._persist)

    def list_meta(self, room_slug: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return metadata for all datasets, newest first."""

        self.load()
        datasets = self._datasets
        if room_slug is not None:
            datasets = [dataset for dataset in datasets if dataset.get("room_slug") == room_slug]
        metas = [dataset_meta(dataset) for dataset in datasets]
        metas.sort(key=lambda dataset: dataset.get("created_at") or 0.0, reverse=True)
        return metas

    def get(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """Return a complete dataset by id."""

        self.load()
        for dataset in self._datasets:
            if dataset.get("id") == dataset_id:
                return dataset
        return None

    def get_records(self, dataset_id: str) -> Optional[List[Dict[str, Any]]]:
        """Return snapshotted records for a dataset id."""

        dataset = self.get(dataset_id)
        if dataset is None:
            return None
        records = dataset.get("records")
        return list(records) if isinstance(records, list) else []

    def add(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        """Add a dataset, persist it, and enforce the retention cap."""

        self.load()
        self._datasets.append(dataset)
        self._enforce_cap()
        self._persist()
        return dataset

    async def async_add(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        """Async wrapper around :meth:`add`."""

        await self.async_load()
        self._datasets.append(dataset)
        self._enforce_cap()
        await self._async_persist()
        return dataset

    def _enforce_cap(self) -> None:
        if len(self._datasets) <= MAX_STORED_DATASETS:
            return
        self._datasets.sort(key=lambda dataset: dataset.get("created_at") or 0.0)
        self._datasets = self._datasets[len(self._datasets) - MAX_STORED_DATASETS :]

    def delete(self, dataset_id: str) -> bool:
        """Delete a dataset by id and return whether anything changed."""

        self.load()
        before = len(self._datasets)
        self._datasets = [
            dataset for dataset in self._datasets if dataset.get("id") != dataset_id
        ]
        changed = len(self._datasets) != before
        if changed:
            self._persist()
        return changed

    async def async_delete(self, dataset_id: str) -> bool:
        """Async wrapper around :meth:`delete`."""

        await self.async_load()
        before = len(self._datasets)
        self._datasets = [
            dataset for dataset in self._datasets if dataset.get("id") != dataset_id
        ]
        changed = len(self._datasets) != before
        if changed:
            await self._async_persist()
        return changed


__all__ = [
    "DatasetStore",
    "build_dataset",
    "dataset_meta",
    "summarize_records",
    "trim_records",
]
