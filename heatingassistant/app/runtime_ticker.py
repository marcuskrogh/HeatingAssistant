"""Wall-clock history + control ticker for HeatingRuntime."""

from __future__ import annotations

import asyncio
import threading
import time

from heatingassistant.app.runtime_const import _HISTORY_MIN_INTERVAL_S, _logger
from heatingassistant.engine import const
from heatingassistant.engine.nmpc_timing import grid_slot_index, next_grid_ts


class TickerMixin:
    """Schedule plot/ID samples, P control, and NMPC on wall-clock grids."""

    def _history_tick_interval_s(self) -> float:
        """Seconds between history samples — matches derived NMPC sample interval."""

        update = float(self._derived_update_interval())
        return max(_HISTORY_MIN_INTERVAL_S, update)

    def _nmpc_period_s(self) -> float:
        try:
            return float(self.control_engine._nmpc_timing(self.options).period_s)
        except Exception:
            return float(self.options.get(const.CONF_NMPC_PERIOD, const.DEFAULT_NMPC_PERIOD))

    def _anchor_schedule_epoch(self, when: float) -> None:
        """Pin both countdown rings to a wall-clock origin (Start)."""

        stamp = float(when)
        self._last_nmpc_ts = stamp
        self._last_control_ts = stamp
        self._last_nmpc_slow_slot = None
        self._last_nmpc_attempt_ts = None

    def _nmpc_slow_slot_due(self, now: float | None = None) -> bool:
        """True when the next ``t0 + n * period`` slot has been reached."""

        epoch = self._last_nmpc_ts
        if epoch is None:
            return False
        period = self._nmpc_period_s()
        current = grid_slot_index(epoch, period, float(now if now is not None else time.time()))
        last = self._last_nmpc_slow_slot
        if last is None:
            return True
        return current > last

    def _mark_nmpc_slot_started(self, now: float | None = None) -> None:
        stamp = float(now if now is not None else time.time())
        self._last_nmpc_attempt_ts = stamp
        epoch = self._last_nmpc_ts
        if epoch is None:
            return
        self._last_nmpc_slow_slot = grid_slot_index(epoch, self._nmpc_period_s(), stamp)

    def _next_aligned_ts(self, period: float, now: float) -> float:
        epoch = self._last_nmpc_ts
        if epoch is None:
            return float(now) + float(period)
        return next_grid_ts(epoch, float(period), float(now))

    def _slow_slot_start(self, now: float) -> float | None:
        epoch = self._last_nmpc_ts
        if epoch is None:
            return None
        return slow_slot_start_s(epoch, self._nmpc_period_s(), float(now))

    def _sync_p_fast_index(self, now: float) -> None:
        """Point the P-law at the wall-clock substep of the installed plan."""

        controller = getattr(self.control_engine, "_controller", None)
        sync = getattr(controller, "sync_fast_index", None)
        if not callable(sync):
            return
        sync(float(now), fallback_epoch=self._last_nmpc_ts)

    def _control_tick_interval_s(self) -> float:
        """Seconds between background control cycles."""

        update = float(self._derived_update_interval())
        return max(30.0, update)

    def _derived_update_interval(self) -> float:
        try:
            return float(self.control_engine._derived_dt(self.options))
        except Exception:
            return float(self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL))

    def _start_background_ticker(self) -> None:
        """Start wall-clock history + control when MQTT tag events are quiet."""

        if self._ticker_thread is not None and self._ticker_thread.is_alive():
            return
        self._ticker_stop.clear()
        self._ticker_thread = threading.Thread(
            target=self._background_ticker_loop,
            name="heatingassistant-wall-clock-ticker",
            daemon=True,
        )
        self._ticker_thread.start()

    def _background_ticker_loop(self) -> None:
        """Record history and run control without relying on Ingress or tag spam."""

        history_every = self._history_tick_interval_s()
        control_every = self._control_tick_interval_s()
        nmpc_every = self._nmpc_period_s()
        now0 = time.time()
        epoch = self._last_nmpc_ts
        # Plot / ID cadence is not the NMPC clock — keep it on a simple
        # interval so sampling does not wait on the control grid.
        next_history = now0 + history_every
        if epoch is None:
            # First control soon after start so energy/actuators move without
            # waiting a full update_interval when tags are silent.
            next_control = now0 + min(control_every, history_every)
            next_nmpc = now0 + min(nmpc_every, next_control)
        else:
            next_control = next_grid_ts(epoch, control_every, now0)
            next_nmpc = next_grid_ts(epoch, nmpc_every, now0)
        while not self._ticker_stop.is_set():
            now = time.time()
            if now >= next_history:
                try:
                    self._record_history_samples()
                except Exception:
                    _logger.exception("Wall-clock history sample failed")
                try:
                    # Same cadence as plot history so estimation memory does not
                    # stall when control is quiet (SWD-318 Option B).
                    self._record_identification_sample(now)
                except Exception:
                    _logger.exception("Wall-clock identification sample failed")
                history_every = self._history_tick_interval_s()
                next_history = time.time() + history_every
            control_due_now = now >= next_control
            if control_due_now:
                last = self._last_control_ran_ts
                if last is not None and (now - last) < (control_every * 0.5):
                    # MQTT tag path already ran control recently — skip.
                    next_control = self._next_aligned_ts(control_every, now)
                else:
                    try:
                        asyncio.run(self.run_control_cycle())
                    except Exception:
                        _logger.exception("Wall-clock control cycle failed")
                    control_every = self._control_tick_interval_s()
                    next_control = self._next_aligned_ts(control_every, time.time())
            # Start NMPC after P on coincident slots so the first 15-minute
            # tick still uses the previous plan while the NLP runs.
            if now >= next_nmpc:
                try:
                    self._schedule_nmpc_worker()
                except Exception:
                    _logger.exception("Wall-clock NMPC schedule failed")
                nmpc_every = self._nmpc_period_s()
                next_nmpc = self._next_aligned_ts(nmpc_every, time.time())
            elif control_due_now:
                try:
                    self._schedule_nmpc_worker()
                except Exception:
                    _logger.exception("Wall-clock idle NMPC retry failed")
            sleep_for = max(
                0.2, min(next_history, next_control, next_nmpc) - time.time()
            )
            if self._ticker_stop.wait(timeout=sleep_for):
                return
