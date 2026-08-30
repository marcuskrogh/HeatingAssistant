"""Plot and identification history sampling for HeatingRuntime."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

import time

from heatingassistant.app.plot_history import PlotHistoryStore
from heatingassistant.app.runtime_const import _HISTORY_MAX_SAMPLES, _logger
from heatingassistant.engine import const


class HistoryMixin:
    """Restore, gate, and persist plot + identification samples."""

    def _restore_durable_history(self, *, plot_hours: float) -> None:
        """Load plot + identification history from the App data volume (SWD-281)."""

        try:
            self._plot_history_store.setup()
            restored = self._plot_history_store.load_recent(
                hours_back=max(plot_hours, const.DEFAULT_PLOT_HISTORY_HOURS),
                max_per_entity=_HISTORY_MAX_SAMPLES,
            )
            with self._history_lock:
                self._history = restored
                if restored:
                    last_ts = 0.0
                    for samples in restored.values():
                        if samples:
                            last_ts = max(last_ts, float(samples[-1]["lu"]))
                    if last_ts > 0.0:
                        self._history_last_ts = last_ts
            if restored:
                _logger.info(
                    "Restored plot history for %d entities from App data volume",
                    len(restored),
                )
        except Exception:
            _logger.exception("Failed to restore plot history from App data volume")

        try:
            self.id_history_store.setup()
            rebuilt = self.id_history_store.query_recent(const.HISTORY_BUFFER_SIZE)
            if rebuilt:
                self._history_buffer.extend(rebuilt[-const.HISTORY_BUFFER_SIZE :])
                try:
                    self._id_history_last_ts = float(self._history_buffer[-1].get("timestamp") or 0.0)
                except (TypeError, ValueError, IndexError):
                    self._id_history_last_ts = 0.0
                self._id_history_disk_last_ts = float(self._id_history_last_ts)
                self._id_history_last_append_ok = True
                self._id_history_append_failure_streak = 0
                _logger.info(
                    "Restored %d identification history steps from JSONL store",
                    len(self._history_buffer),
                )
        except Exception:
            _logger.exception("Failed to restore identification history from JSONL")

    def _sync_history_retention(self) -> None:
        """Refresh store retention from the current options."""

        plot_hours = float(
            self.options.get(const.CONF_PLOT_HISTORY_HOURS, const.DEFAULT_PLOT_HISTORY_HOURS)
        )
        self._plot_history_store.update_retention_days(
            PlotHistoryStore.retention_days_for_plot_hours(plot_hours)
        )
        id_days = int(
            self.options.get(
                const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS,
                const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
            )
        )
        self.id_history_store.update_retention_days(id_days)

    def _record_history_samples(
        self, *, force: bool = False, persist: bool = True
    ) -> list[dict[str, Any]]:
        """Append current synthetic entity values into the history ring + JSONL.

        Samples are gated to ``update_interval`` so Ingress plots align with the
        control cadence (SWD-277). ``force=True`` is reserved for init/config.
        Durable appends keep room charts alive across App updates (SWD-281).

        When ``persist=False``, durable writes are skipped and the pending samples
        are returned so an async caller can offload them via ``asyncio.to_thread``.
        """

        now = time.time()
        interval = self._history_tick_interval_s()
        # Build states outside the lock — only the ring mutation needs serialization.
        states = self.hass_states()
        persisted: list[dict[str, Any]] = []
        with self._history_lock:
            if not force and (now - self._history_last_ts) < interval:
                return []
            self._history_last_ts = now
            for entity_id, state in states.items():
                if not entity_id.startswith("sensor.heating_assistant_"):
                    continue
                if entity_id.endswith("_model_fit_quality"):
                    continue
                raw = state.get("state")
                if raw in {None, "unknown", "unavailable"}:
                    continue
                try:
                    float(raw)
                except (TypeError, ValueError):
                    continue
                sample = {"s": str(raw), "lu": now}
                bucket = self._history.setdefault(
                    entity_id, deque(maxlen=_HISTORY_MAX_SAMPLES)
                )
                bucket.append(sample)
                persisted.append({"entity_id": entity_id, "s": sample["s"], "lu": now})
        if persist and persisted:
            try:
                self._plot_history_store.append_samples(persisted)
                self._plot_history_store.purge_old()
            except Exception:
                _logger.exception("Failed to persist plot history samples")
            return []
        return persisted

    def _take_identification_sample(
        self, now: float | None = None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Build one identification observation for durable-first write.

        Does **not** mutate ``_history_buffer`` or ``_id_history_last_ts`` —
        callers must durable-append first, then
        :meth:`_commit_identification_sample` (SWD-318). Returns ``None`` when
        gated by ``update_interval`` or unwired / no measured temps.
        """

        now_ts = float(now if now is not None else time.time())
        interval = self._history_tick_interval_s()
        if not force and (now_ts - self._id_history_last_ts) < interval:
            return None

        room_names = [
            name
            for room in self._rooms()
            if isinstance((name := room.get("name")), str) and name
        ]
        if not room_names:
            return None
        # Do not seed empty/unknown ticks into ID history (startup before MQTT).
        if not any(self.room_temperatures.get(name) is not None for name in room_names):
            return None

        y: list[float] = []
        for name in room_names:
            temp = self.room_temperatures.get(name)
            if temp is None:
                temp = self._coerce_number(
                    getattr(self.control_engine.model.rooms.get(name), "temperature", None)
                )
            y.append(float(temp) if temp is not None else 0.0)

        u: list[float] = []
        source_tags = getattr(self.control_engine, "_source_output_tags", {}) or {}
        for source in self.control_engine.heat_sources:
            tag = source_tags.get(source.name, source.name)
            value = self._coerce_number(self.actuator_outputs.get(tag))
            if value is None:
                value = self._coerce_number(self.tag_values.get(tag))
            u.append(float(value) if value is not None else 0.0)

        outdoor = self._outdoor_temperature()
        if outdoor is None:
            outdoor = 0.0

        solar = self._applied_solar_gains()
        for name in room_names:
            if name not in solar:
                solar[name] = 0.0

        y_pred_aligned = None
        if self._history_buffer:
            y_pred_aligned = self._history_buffer[-1].get("y_pred_for_next")

        forecast = self.control_engine.forecast_snapshot()
        filtered = forecast.get("filtered_temperatures") or {}
        predictions = forecast.get("predictions") or []
        if predictions and isinstance(predictions[0], Mapping):
            y_pred_for_next = [
                float(predictions[0].get(name, y[idx] if idx < len(y) else 0.0) or 0.0)
                for idx, name in enumerate(room_names)
            ]
        elif isinstance(filtered, Mapping) and filtered:
            y_pred_for_next = [
                float(filtered.get(name, y[idx] if idx < len(y) else 0.0) or 0.0)
                for idx, name in enumerate(room_names)
            ]
        else:
            y_pred_for_next = list(y)

        return {
            "y": y,
            "y_pred": y_pred_aligned,
            "y_pred_for_next": y_pred_for_next,
            "u": u,
            "d_outdoor": float(outdoor),
            "d_solar": solar,
            "window_open": {
                name: self.is_window_override_active(name) for name in room_names
            },
            "timestamp": now_ts,
        }

    def _commit_identification_sample(self, record: Mapping[str, Any]) -> None:
        """Buffer + advance gate after a successful durable append (SWD-318)."""

        payload = dict(record)
        now_ts = float(payload.get("timestamp") or time.time())
        payload["timestamp"] = now_ts
        self._history_buffer.append(payload)
        self._id_history_last_ts = now_ts
        self._id_history_disk_last_ts = now_ts
        self._id_history_append_failure_streak = 0
        self._id_history_last_append_ok = True

    def _note_identification_append_failure(self) -> None:
        """Record a durable ID append failure for System Status (SWD-317)."""

        self._id_history_append_failure_streak = int(
            self._id_history_append_failure_streak
        ) + 1
        self._id_history_last_append_ok = False

    def _record_identification_sample(
        self, now: float | None = None, *, force: bool = False
    ) -> None:
        """Sync path: durable JSONL append, then buffer (ticker / update_tag)."""

        record = self._take_identification_sample(now, force=force)
        if record is None:
            return
        try:
            self.id_history_store.append(record)
        except Exception:
            _logger.exception("Failed to persist identification history record")
            self._note_identification_append_failure()
            return
        self._commit_identification_sample(record)
        try:
            self.id_history_store.purge_old()
        except Exception:
            _logger.exception("Failed to purge old identification history")
