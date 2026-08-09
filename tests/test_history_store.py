"""Unit tests for IdentificationHistoryStore JSONL persistence."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from heatingassistant.engine.history.store import IdentificationHistoryStore
from tests.helpers.setup_patches import ensure_hass_config


def _make_store(tmp_path, entry_id: str = "test-entry", retention_days: int = 90):
    hass = SimpleNamespace()
    ensure_hass_config(hass, str(tmp_path))
    return IdentificationHistoryStore(hass, entry_id, retention_days=retention_days)


def _record(ts: float, tag: str = "") -> dict:
    return {
        "y": [20.0],
        "u": [0.5],
        "d_outdoor": 5.0,
        "d_solar": {"living": 0.0},
        "timestamp": ts,
        "tag": tag,
    }


def test_sync_append_and_query_range_round_trip(tmp_path):
    store = _make_store(tmp_path)
    store._sync_setup()

    base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    recs = [_record(base + 900 * i, f"r{i}") for i in range(5)]
    for rec in recs:
        store._sync_append(rec)

    result = store._sync_query_range(base, base + 900 * 4 + 1)
    assert len(result) == 5
    assert [r["tag"] for r in result] == ["r0", "r1", "r2", "r3", "r4"]

    partial = store._sync_query_range(base + 900, base + 900 * 3)
    assert len(partial) == 2
    assert [r["tag"] for r in partial] == ["r1", "r2"]


def test_query_recent_returns_newest_first(tmp_path):
    store = _make_store(tmp_path)
    store._sync_setup()

    day1 = datetime(2025, 6, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    day2 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    for i in range(3):
        store._sync_append(_record(day1 + i * 900, f"old{i}"))
    for i in range(3):
        store._sync_append(_record(day2 + i * 900, f"new{i}"))

    recent = store._sync_query_recent(4)
    assert len(recent) == 4
    assert [r["tag"] for r in recent] == ["old2", "new0", "new1", "new2"]
    assert recent[-1]["timestamp"] == max(r["timestamp"] for r in recent)


def test_purge_old_respects_retention_days(tmp_path):
    store = _make_store(tmp_path, retention_days=7)
    store._sync_setup()

    today = date.today()
    old_day = today - timedelta(days=10)
    recent_day = today - timedelta(days=2)

    old_ts = datetime.combine(
        old_day, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()
    recent_ts = datetime.combine(
        recent_day, datetime.min.time(), tzinfo=timezone.utc
    ).timestamp()

    store._sync_append(_record(old_ts, "old"))
    store._sync_append(_record(recent_ts, "recent"))

    old_path = store._dir / f"{old_day.isoformat()}.jsonl"
    recent_path = store._dir / f"{recent_day.isoformat()}.jsonl"
    assert old_path.exists()
    assert recent_path.exists()

    # Setup already purged once today; reset the guard so this call runs.
    store._last_purge_date = None
    store._sync_purge_old()

    assert not old_path.exists()
    assert recent_path.exists()


@pytest.mark.asyncio
async def test_async_append_via_executor(tmp_path):
    store = _make_store(tmp_path)
    await store.async_setup()

    ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    rec = _record(ts)
    await store.async_append(rec)

    result = await store.async_query_range(ts - 1, ts + 1)
    assert len(result) == 1
    assert result[0]["timestamp"] == ts
