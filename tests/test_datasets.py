"""Unit tests for stored identification datasets (metadata + snapshots)."""

from __future__ import annotations

from custom_components.heating_assistant import datasets as D
from custom_components.heating_assistant.const import (
    DATASET_SOURCE_MANUAL,
    MAX_DATASET_RECORDS,
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
