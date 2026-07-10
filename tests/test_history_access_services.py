"""Unit tests for services/history_access.py."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.services import history_access as ha
from tests.helpers.coordinator_stubs import make_hass_stub, make_minimal_coordinator


def _records(start: float, count: int, step: float = 900.0):
    return [
        {"timestamp": start + i * step, "y": [20.0 + i * 0.1], "u": [0.5]}
        for i in range(count)
    ]


def _coord_with_buffer(records, *, dt: int = 900):
    coord = make_minimal_coordinator(dt=dt)
    coord._history_buffer = deque(records)
    coord.id_history_store = None
    return coord


@pytest.mark.asyncio
async def test_get_history_for_window_fast_path():
    """Buffer already spans the window — no store or recorder queries."""
    buf = _records(1_000.0, 10)
    coord = _coord_with_buffer(buf)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_range = AsyncMock()
    hass = make_hass_stub()

    result = await ha.get_history_for_window(hass, coord, 1_000.0, 8_000.0)

    assert result == buf
    coord.id_history_store.async_query_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_history_for_window_none_bounds_returns_buffer():
    coord = _coord_with_buffer(_records(1_000.0, 3))
    hass = make_hass_stub()

    assert await ha.get_history_for_window(hass, coord, None, 5_000.0) == list(
        coord.history_buffer
    )
    assert await ha.get_history_for_window(hass, coord, 1_000.0, None) == list(
        coord.history_buffer
    )


@pytest.mark.asyncio
async def test_get_history_for_window_jsonl_store_path():
    """When the buffer is too recent, records come from the JSONL store."""
    buf = _records(5_000.0, 4)
    coord = _coord_with_buffer(buf)
    store_records = _records(1_000.0, 6)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_range = AsyncMock(return_value=store_records)
    hass = make_hass_stub()

    result = await ha.get_history_for_window(hass, coord, 1_000.0, 4_500.0)

    assert result == store_records
    coord.id_history_store.async_query_range.assert_awaited_once_with(1_000.0, 4_500.0)


@pytest.mark.asyncio
async def test_get_history_for_window_recorder_fallback(monkeypatch):
    """Empty JSONL query falls through to HA Recorder."""
    buf = _records(5_000.0, 2)
    coord = _coord_with_buffer(buf)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_range = AsyncMock(return_value=[])
    recorder_records = _records(1_000.0, 5)
    mock_fetch = AsyncMock(return_value=recorder_records)
    monkeypatch.setattr(
        "custom_components.heating_assistant.history.seed.async_fetch_history_range",
        mock_fetch,
    )
    hass = make_hass_stub()

    result = await ha.get_history_for_window(hass, coord, 1_000.0, 4_000.0)

    assert result == recorder_records
    mock_fetch.assert_awaited_once_with(hass, coord, 1_000.0, 4_000.0)


@pytest.mark.asyncio
async def test_get_history_for_window_last_resort_buffer(monkeypatch):
    """When store and recorder both fail, return the in-memory buffer."""
    buf = _records(5_000.0, 3)
    coord = _coord_with_buffer(buf)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_range = AsyncMock(side_effect=RuntimeError("disk"))
    mock_fetch = AsyncMock(side_effect=RuntimeError("recorder"))
    monkeypatch.setattr(
        "custom_components.heating_assistant.history.seed.async_fetch_history_range",
        mock_fetch,
    )
    hass = make_hass_stub()

    result = await ha.get_history_for_window(hass, coord, 1_000.0, 4_000.0)

    assert result == buf


@pytest.mark.asyncio
async def test_get_history_for_horizon_buffer_covers_span():
    """Recent horizon fully inside the buffer uses select_recent_window locally."""
    buf = _records(10_000.0, 20)
    coord = _coord_with_buffer(buf, dt=900)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_range = AsyncMock()
    hass = make_hass_stub()

    result = await ha.get_history_for_horizon(hass, coord, horizon_hours=2.0)

    assert result
    assert result[0]["timestamp"] >= buf[-1]["timestamp"] - 2 * 3600
    coord.id_history_store.async_query_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_history_for_horizon_fetches_older_data(monkeypatch):
    """Horizon wider than the buffer triggers get_history_for_window."""
    buf = _records(10_000.0, 4)
    coord = _coord_with_buffer(buf, dt=900)
    extended = _records(1_000.0, 30)
    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_range = AsyncMock(return_value=extended)
    hass = make_hass_stub()

    result = await ha.get_history_for_horizon(hass, coord, horizon_hours=24.0)

    assert result
    latest = float(buf[-1]["timestamp"])
    assert result[0]["timestamp"] >= latest - 24 * 3600
    coord.id_history_store.async_query_range.assert_awaited()


@pytest.mark.asyncio
async def test_get_history_for_horizon_empty_or_nonpositive():
    coord = _coord_with_buffer([])
    hass = make_hass_stub()
    assert await ha.get_history_for_horizon(hass, coord, 6.0) == []

    coord = _coord_with_buffer(_records(1_000.0, 2))
    assert await ha.get_history_for_horizon(hass, coord, 0.0) == list(coord.history_buffer)


@pytest.mark.asyncio
async def test_get_history_with_leading_splits_windows(monkeypatch):
    full = _records(1_000.0, 20)
    mock_window = AsyncMock(return_value=full)
    monkeypatch.setattr(ha, "get_history_for_window", mock_window)
    coord = _coord_with_buffer(full)
    hass = make_hass_stub()

    window_start = 1_000.0 + 10 * 900
    window_end = 1_000.0 + 15 * 900
    full_out, leading, simulation = await ha.get_history_with_leading(
        hass, coord, window_start, window_end, leading_hours=2.0
    )

    assert full_out == full
    assert simulation
    assert all(window_start <= float(r["timestamp"]) <= window_end for r in simulation)
    assert leading
    assert all(float(r["timestamp"]) < window_start for r in leading)
    mock_window.assert_awaited_once()
    fetch_start = window_start - 2.0 * 3600
    mock_window.assert_awaited_with(hass, coord, fetch_start, window_end)


def test_records_for_dataset():
    coord = make_minimal_coordinator()
    recs = _records(100.0, 3)
    coord.dataset_store = MagicMock()
    coord.dataset_store.get_records = MagicMock(
        side_effect=lambda ds_id: recs if ds_id == "ds-1" else None
    )

    assert ha.records_for_dataset(coord, "ds-1") == recs
    assert ha.records_for_dataset(coord, None) is None
    assert ha.records_for_dataset(coord, "missing") is None

    coord.dataset_store.get_records = MagicMock(return_value=[])
    assert ha.records_for_dataset(coord, "ds-1") is None

    del coord.dataset_store
    assert ha.records_for_dataset(coord, "ds-1") is None


def test_records_for_datasets_merges_and_sorts():
    coord = make_minimal_coordinator()
    coord.dataset_store = MagicMock()

    def _get_records(ds_id: str):
        if ds_id == "a":
            return [{"timestamp": 300.0, "y": [20.0]}]
        if ds_id == "b":
            return [{"timestamp": 100.0, "y": [19.0]}]
        return None

    coord.dataset_store.get_records = MagicMock(side_effect=_get_records)

    merged = ha.records_for_datasets(coord, ["a", "b"])
    assert merged is not None
    assert [r["timestamp"] for r in merged] == [100.0, 300.0]

    assert ha.records_for_datasets(coord, None) is None
    assert ha.records_for_datasets(coord, []) is None
    assert ha.records_for_datasets(coord, ["missing"]) is None


def test_dataset_boundaries():
    coord = make_minimal_coordinator()
    coord.dataset_store = MagicMock()

    def _get_records(ds_id: str):
        mapping = {
            "late": [{"timestamp": 500.0}],
            "early": [{"timestamp": 100.0}],
        }
        return mapping.get(ds_id)

    coord.dataset_store.get_records = MagicMock(side_effect=_get_records)

    bounds = ha.dataset_boundaries(coord, ["late", "early"])
    assert bounds == [100.0, 500.0]

    assert ha.dataset_boundaries(coord, None) is None
    assert ha.dataset_boundaries(coord, ["missing"]) is None
