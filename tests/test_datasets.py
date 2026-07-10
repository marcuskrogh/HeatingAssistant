"""Unit tests for stored identification datasets (metadata + snapshots)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.heating_assistant import datasets as D
from custom_components.heating_assistant.const import (
    DATASET_SOURCE_MANUAL,
    MAX_DATASET_RECORDS,
    MAX_STORED_DATASETS,
)


def _records(n: int, t0: float = 1_000.0):
    return [
        {"timestamp": t0 + i * 900, "y": [20.0 + 0.1 * i, 19.0], "u": [0.5]}
        for i in range(n)
    ]


def test_summarize_records_basic():
    s = D.summarize_records(_records(5))
    assert s["record_count"] == 5
    assert s["start_ts"] == 1_000.0
    assert s["end_ts"] == 1_000.0 + 4 * 900
    assert s["duration_s"] == 4 * 900
    assert s["temp_min"] == 19.0
    assert abs(s["temp_max"] - (20.0 + 0.1 * 4)) < 1e-9


def test_summarize_handles_empty_and_bad_records():
    s = D.summarize_records([])
    assert s["record_count"] == 0
    assert s["start_ts"] is None and s["end_ts"] is None
    # Records with no/invalid timestamps are ignored for the span.
    s2 = D.summarize_records([{"y": [21.0]}, {"timestamp": "bad", "y": [22.0]}])
    assert s2["start_ts"] is None
    assert s2["temp_max"] == 22.0


def test_build_dataset_strips_records_in_meta():
    ds = D.build_dataset(
        "My set", _records(3), room_name="Living Room", room_slug="living_room"
    )
    assert ds["name"] == "My set"
    assert ds["source"] == DATASET_SOURCE_MANUAL
    assert ds["record_count"] == 3
    assert "records" in ds and len(ds["records"]) == 3
    assert "id" in ds and ds["id"]
    meta = D.dataset_meta(ds)
    assert "records" not in meta
    assert meta["record_count"] == 3


def test_build_dataset_records_requested_window():
    ds = D.build_dataset(
        "win", _records(3), window_start=500.0, window_end=9_999.0
    )
    assert ds["window_start"] == 500.0
    assert ds["window_end"] == 9_999.0
    # The actual captured span is recorded separately.
    assert ds["data_start_ts"] == 1_000.0


def test_trim_records_keeps_most_recent():
    recs = _records(10)
    trimmed = D.trim_records(recs, max_records=4)
    assert len(trimmed) == 4
    assert trimmed[0]["timestamp"] == recs[6]["timestamp"]
    assert trimmed[-1]["timestamp"] == recs[-1]["timestamp"]


def test_build_dataset_trims_to_cap():
    big = _records(MAX_DATASET_RECORDS + 50)
    ds = D.build_dataset("big", big)
    assert ds["record_count"] == MAX_DATASET_RECORDS
    assert len(ds["records"]) == MAX_DATASET_RECORDS


def test_build_dataset_defaults_window_to_data_span():
    ds = D.build_dataset("auto window", _records(4, t0=2_000.0))
    assert ds["window_start"] == 2_000.0
    assert ds["window_end"] == 2_000.0 + 3 * 900
    assert ds["data_start_ts"] == ds["window_start"]
    assert ds["data_end_ts"] == ds["window_end"]


@pytest.fixture
def dataset_store():
    """DatasetStore with an in-memory backing store."""
    hass = SimpleNamespace()
    store = D.DatasetStore(hass, "entry-test")
    persisted: list = []

    async def _save(data):
        persisted.append(data)

    store._store.async_save = _save
    store._store.async_load = AsyncMock(return_value=None)
    store._persisted = persisted
    return store


@pytest.mark.asyncio
async def test_dataset_store_async_add_persists(dataset_store):
    ds = D.build_dataset("saved", _records(2))
    result = await dataset_store.async_add(ds)

    assert result is ds
    assert dataset_store.get(ds["id"]) is ds
    assert dataset_store.get_records(ds["id"]) == ds["records"]
    assert len(dataset_store._persisted) == 1
    assert dataset_store._persisted[0]["datasets"] == [ds]


@pytest.mark.asyncio
async def test_dataset_store_async_load_reads_disk(dataset_store):
    existing = D.build_dataset("from disk", _records(1))
    dataset_store._store.async_load = AsyncMock(
        return_value={"datasets": [existing, "not-a-dict"]}
    )

    await dataset_store.async_load()

    assert dataset_store.get(existing["id"]) is existing
    # Second load is a no-op.
    await dataset_store.async_load()
    dataset_store._store.async_load.assert_awaited_once()


@pytest.mark.asyncio
async def test_dataset_store_list_meta_filters_and_sorts(dataset_store):
    older = D.build_dataset("old", _records(1))
    older["created_at"] = 100.0
    older["room_slug"] = "living_room"
    newer = D.build_dataset("new", _records(1))
    newer["created_at"] = 200.0
    newer["room_slug"] = "kitchen"
    dataset_store._datasets = [older, newer]

    all_meta = dataset_store.list_meta()
    assert [m["name"] for m in all_meta] == ["new", "old"]
    assert all("records" not in m for m in all_meta)

    kitchen_only = dataset_store.list_meta(room_slug="kitchen")
    assert len(kitchen_only) == 1
    assert kitchen_only[0]["name"] == "new"


@pytest.mark.asyncio
async def test_dataset_store_async_delete(dataset_store):
    keep = D.build_dataset("keep", _records(1))
    drop = D.build_dataset("drop", _records(1))
    dataset_store._datasets = [keep, drop]

    changed = await dataset_store.async_delete(drop["id"])

    assert changed is True
    assert dataset_store.get(drop["id"]) is None
    assert dataset_store.get(keep["id"]) is keep
    assert len(dataset_store._persisted) == 1

    assert await dataset_store.async_delete("missing") is False


@pytest.mark.asyncio
async def test_dataset_store_enforces_cap(dataset_store):
    datasets = []
    for i in range(MAX_STORED_DATASETS + 3):
        ds = D.build_dataset(f"ds-{i}", _records(1))
        ds["created_at"] = float(i)
        datasets.append(ds)
    dataset_store._datasets = list(datasets)
    dataset_store._enforce_cap()

    assert len(dataset_store._datasets) == MAX_STORED_DATASETS
    names = {d["name"] for d in dataset_store._datasets}
    assert "ds-0" not in names
    assert "ds-1" not in names
    assert "ds-2" not in names
    assert f"ds-{MAX_STORED_DATASETS + 2}" in names
