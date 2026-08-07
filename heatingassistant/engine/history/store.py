"""
Integration-managed append-only identification history store.

Writes one JSONL record per coordinator tick (every dt seconds, typically 15 min)
to a per-day file under ``<config_dir>/.heating_assistant_id_history/<entry_id>/``.
Reads back the exact files that overlap a requested time window without touching
any other day's data.

Design goals
------------
* **Pi-safe**: each write is a single ``open(path, 'a') + write()`` of ~200 bytes.
  No database, no WAL, no rewriting of existing data.
* **Integration-owned**: independent of HA Recorder's ``purge_keep_days`` and
  of how many other sensors the user has configured.
* **Bounded storage**: daily files older than ``retention_days`` are deleted on
  setup and once every 24 hours thereafter during normal operation.

File layout::

    .heating_assistant_id_history/
        <entry_id>/
            2025-06-01.jsonl
            2025-06-02.jsonl
            ...

Each line in a ``.jsonl`` file is one JSON-serialised history record with the
same schema as the in-memory ``history_buffer`` entries consumed by
``sysid.run_sysid_simulation`` and ``model_diagnostics.compute_open_loop_predictions``::

    {"y":[...], "u":[...], "d_outdoor":..., "d_solar":{...}, "timestamp":...}

Additional diagnostic keys (``y_pred``, ``kalman_innovation``, etc.) are also
stored so the full record is preserved for future diagnostics.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class IdentificationHistoryStore:
    """Append-only per-day JSONL history store for system-identification data."""

    def __init__(
        self,
        hass: Any,
        entry_id: str,
        retention_days: int = 90,
    ) -> None:
        self._hass = hass
        self._dir = (
            Path(hass.config.config_dir)
            / ".heating_assistant_id_history"
            / entry_id
        )
        self._retention_days = max(1, retention_days)
        self._last_purge_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Create the storage directory and run an initial purge."""
        await self._hass.async_add_executor_job(self._sync_setup)

    async def async_append(self, record: Dict[str, Any]) -> None:
        """Append a single record to today's JSONL file (non-blocking)."""
        await self._hass.async_add_executor_job(self._sync_append, record)

    async def async_query_range(
        self,
        start_ts: float,
        end_ts: float,
    ) -> List[Dict[str, Any]]:
        """Return all records with timestamp in ``[start_ts, end_ts]``.

        Reads only the day-files that overlap the requested range.  Returns an
        empty list when no data exists for the window.
        """
        return await self._hass.async_add_executor_job(
            self._sync_query_range, start_ts, end_ts
        )

    async def async_query_recent(self, max_records: int) -> List[Dict[str, Any]]:
        """Return the most recent ``max_records`` records across all stored days."""
        return await self._hass.async_add_executor_job(
            self._sync_query_recent, max_records
        )

    async def async_purge_old(self) -> None:
        """Delete day-files older than ``retention_days`` (runs in executor)."""
        today = date.today()
        if self._last_purge_date == today:
            return  # already ran today
        await self._hass.async_add_executor_job(self._sync_purge_old)
        self._last_purge_date = today

    def update_retention_days(self, days: int) -> None:
        """Update the retention period and reset the daily-purge guard so the
        new cutoff is applied at the next ``async_purge_old`` call."""
        self._retention_days = max(1, int(days))
        self._last_purge_date = None

    # ------------------------------------------------------------------
    # Synchronous helpers (run in executor threads)
    # ------------------------------------------------------------------

    def _sync_setup(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sync_purge_old()

    def _sync_append(self, record: Dict[str, Any]) -> None:
        ts = record.get("timestamp", 0.0)
        try:
            day = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            day = date.today()

        path = self._dir / f"{day.isoformat()}.jsonl"
        try:
            line = json.dumps(record, separators=(",", ":")) + "\n"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            _LOGGER.warning("ID history store: append failed: %s", exc)

    def _sync_query_range(
        self,
        start_ts: float,
        end_ts: float,
    ) -> List[Dict[str, Any]]:
        try:
            start_day = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
            end_day = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            return []

        records: List[Dict[str, Any]] = []
        day = start_day
        while day <= end_day:
            path = self._dir / f"{day.isoformat()}.jsonl"
            if path.exists():
                records.extend(self._read_day_file(path, start_ts, end_ts))
            day += timedelta(days=1)
        return records

    def _sync_query_recent(self, max_records: int) -> List[Dict[str, Any]]:
        """Read the most recent ``max_records`` records by scanning day files
        newest-first until enough are collected."""
        try:
            day_files = sorted(
                (p for p in self._dir.glob("*.jsonl") if p.stem),
                reverse=True,
            )
        except OSError:
            return []

        collected: List[Dict[str, Any]] = []
        for path in day_files:
            if len(collected) >= max_records:
                break
            try:
                day_records = self._read_day_file(path)
                collected = day_records + collected
            except OSError:
                pass

        return collected[-max_records:] if len(collected) > max_records else collected

    def _read_day_file(
        self,
        path: Path,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if start_ts is not None or end_ts is not None:
                        try:
                            ts = float(rec.get("timestamp", 0))
                        except (TypeError, ValueError):
                            continue
                        if start_ts is not None and ts < start_ts:
                            continue
                        if end_ts is not None and ts >= end_ts:
                            continue
                    records.append(rec)
        except OSError:
            pass
        return records

    def _sync_purge_old(self) -> None:
        cutoff = date.today() - timedelta(days=self._retention_days)
        try:
            for path in list(self._dir.glob("*.jsonl")):
                try:
                    day = date.fromisoformat(path.stem)
                    if day < cutoff:
                        path.unlink()
                        _LOGGER.debug("ID history store: purged %s", path.name)
                except (ValueError, OSError):
                    pass
        except OSError:
            pass
        self._last_purge_date = date.today()
