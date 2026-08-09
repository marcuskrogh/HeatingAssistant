"""Durable Ingress plot-entity history under the App data volume (SWD-281).

Room-view charts read ``/api/history``. After the HAOS App cutover those samples
lived only in an in-memory ring and vanished on every App update/restart.
This module mirrors the original integration's durable ownership: append-only
daily JSONL under ``<data_dir>/plot_history/``, independent of HA Recorder.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

_LOGGER = logging.getLogger(__name__)

PLOT_HISTORY_DIRNAME = "plot_history"
# Keep a few extra days beyond the UI window so restarts mid-window still fill.
_PLOT_RETENTION_PAD_DAYS = 2


class PlotHistoryStore:
    """Append-only per-day JSONL store for synthetic plot entity samples."""

    def __init__(self, data_dir: str | Path, *, retention_days: int = 3) -> None:
        self._dir = Path(data_dir) / PLOT_HISTORY_DIRNAME
        self._retention_days = max(1, int(retention_days))
        self._last_purge_date: date | None = None

    def setup(self) -> None:
        """Create the storage directory and purge stale day files."""

        self._dir.mkdir(parents=True, exist_ok=True)
        self.purge_old()

    def update_retention_days(self, days: int) -> None:
        self._retention_days = max(1, int(days))
        self._last_purge_date = None

    def append_samples(self, samples: Iterable[Mapping[str, Any]]) -> None:
        """Append ``{entity_id, s, lu}`` samples, grouped into day files by ``lu``."""

        by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for sample in samples:
            entity_id = sample.get("entity_id") or sample.get("e")
            state = sample.get("s")
            lu = sample.get("lu")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            if state is None:
                continue
            try:
                lu_f = float(lu)
                day = datetime.fromtimestamp(lu_f, tz=timezone.utc).date()
            except (TypeError, ValueError, OSError):
                continue
            by_day[day].append({"e": entity_id, "s": str(state), "lu": lu_f})

        if not by_day:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        for day, rows in by_day.items():
            path = self._dir / f"{day.isoformat()}.jsonl"
            try:
                with path.open("a", encoding="utf-8") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            except OSError as exc:
                _LOGGER.warning("Plot history store: append failed (%s): %s", path, exc)

    def load_recent(
        self,
        *,
        hours_back: float,
        max_per_entity: int,
        now_ts: float | None = None,
    ) -> dict[str, deque[dict[str, Any]]]:
        """Load samples within ``hours_back`` into bounded per-entity deques."""

        end = float(now_ts if now_ts is not None else datetime.now(tz=timezone.utc).timestamp())
        start = end - max(0.0, float(hours_back)) * 3600.0
        max_n = max(1, int(max_per_entity))
        buckets: dict[str, deque[dict[str, Any]]] = {}

        try:
            start_day = datetime.fromtimestamp(start, tz=timezone.utc).date()
            end_day = datetime.fromtimestamp(end, tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            return buckets

        day = start_day
        while day <= end_day:
            path = self._dir / f"{day.isoformat()}.jsonl"
            if path.exists():
                for entity_id, sample in self._read_day_file(path, start, end):
                    bucket = buckets.setdefault(entity_id, deque(maxlen=max_n))
                    bucket.append(sample)
            day += timedelta(days=1)
        return buckets

    def purge_old(self) -> None:
        """Delete day-files older than ``retention_days`` (at most once per day)."""

        today = date.today()
        if self._last_purge_date == today:
            return
        cutoff = today - timedelta(days=self._retention_days)
        try:
            for path in list(self._dir.glob("*.jsonl")):
                try:
                    day = date.fromisoformat(path.stem)
                    if day < cutoff:
                        path.unlink()
                        _LOGGER.debug("Plot history store: purged %s", path.name)
                except (ValueError, OSError):
                    pass
        except OSError:
            pass
        self._last_purge_date = today

    @staticmethod
    def retention_days_for_plot_hours(plot_history_hours: float) -> int:
        """Retention covering the UI window plus a small pad."""

        hours = max(1.0, float(plot_history_hours))
        return max(1, int(hours / 24.0) + _PLOT_RETENTION_PAD_DAYS)

    def _read_day_file(
        self,
        path: Path,
        start_ts: float,
        end_ts: float,
    ) -> list[tuple[str, dict[str, Any]]]:
        rows: list[tuple[str, dict[str, Any]]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    entity_id = rec.get("e") or rec.get("entity_id")
                    if not isinstance(entity_id, str) or not entity_id:
                        continue
                    try:
                        lu = float(rec.get("lu", 0))
                    except (TypeError, ValueError):
                        continue
                    if lu < start_ts or lu > end_ts:
                        continue
                    state = rec.get("s")
                    if state is None:
                        continue
                    rows.append((entity_id, {"s": str(state), "lu": lu}))
        except OSError:
            pass
        return rows
