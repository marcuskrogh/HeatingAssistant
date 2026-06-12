"""Tests for the dataset history-resolution helpers used by identification."""

from __future__ import annotations

from custom_components.heating_assistant import (
    _records_for_dataset,
    _records_for_datasets,
)


class _FakeStore:
    def __init__(self, mapping):
        self._mapping = mapping

    def get_records(self, dataset_id):
        return self._mapping.get(dataset_id)


class _FakeCoord:
    def __init__(self, mapping):
        self.dataset_store = _FakeStore(mapping)


def _recs(*ts):
    return [{"timestamp": t, "y": [20.0]} for t in ts]


def test_records_for_single_dataset():
    coord = _FakeCoord({"a": _recs(1, 2, 3)})
    assert _records_for_dataset(coord, "a") == _recs(1, 2, 3)
    assert _records_for_dataset(coord, "missing") is None
    assert _records_for_dataset(coord, None) is None


def test_records_for_datasets_merges_and_sorts():
    coord = _FakeCoord({"a": _recs(30, 40), "b": _recs(10, 20)})
    out = _records_for_datasets(coord, ["a", "b"])
    assert [r["timestamp"] for r in out] == [10, 20, 30, 40]


def test_records_for_datasets_skips_missing():
    coord = _FakeCoord({"a": _recs(5, 6)})
    out = _records_for_datasets(coord, ["a", "missing"])
    assert [r["timestamp"] for r in out] == [5, 6]


def test_records_for_datasets_returns_none_when_empty():
    coord = _FakeCoord({})
    assert _records_for_datasets(coord, None) is None
    assert _records_for_datasets(coord, []) is None
    assert _records_for_datasets(coord, ["missing"]) is None


def test_records_for_datasets_no_store():
    class _NoStore:
        dataset_store = None
    assert _records_for_datasets(_NoStore(), ["a"]) is None
