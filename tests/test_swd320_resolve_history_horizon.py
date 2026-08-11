"""SWD-320: resolve_history(horizon_hours) merges id_history JSONL (Option A)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from heatingassistant.app import sysid_services


pytestmark = pytest.mark.unit


def _record(ts: float, y: float = 20.0) -> dict:
    return {
        "timestamp": ts,
        "y": [y],
        "u": [0.0],
        "d_outdoor": 5.0,
        "d_solar": {"Living Room": 0.0},
    }


@pytest.mark.asyncio
async def test_resolve_history_horizon_merges_jsonl_when_buffer_short():
    """JSONL-only older samples appear under horizon_hours (Option A)."""

    end = 100_000.0
    buffer = [_record(end - 900.0), _record(end)]
    stored = [
        _record(end - 3 * 3600.0, y=18.0),
        _record(end - 2 * 3600.0, y=19.0),
    ]
    store = SimpleNamespace(async_query_range=AsyncMock(return_value=stored))
    runtime = SimpleNamespace(
        _history_buffer=buffer,
        id_history_store=store,
        options={"update_interval": 900},
    )

    result = await sysid_services.resolve_history(runtime, horizon_hours=4.0)

    store.async_query_range.assert_awaited_once()
    call_start, call_end = store.async_query_range.await_args.args
    assert call_end == pytest.approx(end)
    assert call_start == pytest.approx(end - 4 * 3600.0)

    timestamps = [float(r["timestamp"]) for r in result]
    assert end - 3 * 3600.0 in timestamps
    assert end - 2 * 3600.0 in timestamps
    assert end in timestamps
    assert all(end - 4 * 3600.0 <= ts <= end for ts in timestamps)


@pytest.mark.asyncio
async def test_resolve_history_horizon_matches_equivalent_window():
    """Horizon coverage matches an explicit [end−H, end] window resolve."""

    end = 20_000.0
    buffer = [_record(end - 1800.0), _record(end)]
    stored = [_record(end - 5 * 3600.0, y=17.0), _record(end - 900.0, y=21.0)]
    store = SimpleNamespace(async_query_range=AsyncMock(return_value=list(stored)))
    runtime = SimpleNamespace(
        _history_buffer=list(buffer),
        id_history_store=store,
        options={"update_interval": 900},
    )

    horizon = await sysid_services.resolve_history(runtime, horizon_hours=6.0)
    store.async_query_range.reset_mock()
    store.async_query_range = AsyncMock(return_value=list(stored))
    runtime.id_history_store = store
    window = await sysid_services.resolve_history(
        runtime,
        window_start=end - 6 * 3600.0,
        window_end=end,
    )

    assert [r["timestamp"] for r in horizon] == [r["timestamp"] for r in window]


@pytest.mark.asyncio
async def test_resolve_history_horizon_empty_buffer_queries_jsonl(monkeypatch):
    """Empty buffer still merges JSONL using now as the horizon end."""

    now = 50_000.0
    monkeypatch.setattr(sysid_services.time, "time", lambda: now)
    stored = [
        _record(now - 2 * 3600.0, y=18.0),
        _record(now - 900.0, y=19.0),
    ]
    store = SimpleNamespace(async_query_range=AsyncMock(return_value=stored))
    runtime = SimpleNamespace(
        _history_buffer=[],
        id_history_store=store,
        options={"update_interval": 900},
    )

    result = await sysid_services.resolve_history(runtime, horizon_hours=3.0)

    store.async_query_range.assert_awaited_once_with(now - 3 * 3600.0, now)
    assert [float(r["timestamp"]) for r in result] == [
        now - 2 * 3600.0,
        now - 900.0,
    ]


@pytest.mark.asyncio
async def test_resolve_history_nonpositive_horizon_skips_jsonl():
    """Non-positive horizon keeps prior safe behaviour (buffer only, no scan)."""

    buffer = [_record(1_000.0), _record(1_900.0)]
    store = SimpleNamespace(async_query_range=AsyncMock())
    runtime = SimpleNamespace(
        _history_buffer=buffer,
        id_history_store=store,
        options={"update_interval": 900},
    )

    result = await sysid_services.resolve_history(runtime, horizon_hours=0.0)

    store.async_query_range.assert_not_awaited()
    assert [r["timestamp"] for r in result] == [1_000.0, 1_900.0]
