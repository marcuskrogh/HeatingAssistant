"""Slow NMPC worker thread for HeatingRuntime."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from heatingassistant.app.runtime_const import _logger
from heatingassistant.engine import const
from heatingassistant.mqtt.topics import DEFAULT_QOS, cmd


class NmpcMixin:
    """Start, run, and apply the slow NLP without blocking the P cycle."""

    def _schedule_nmpc_worker(self) -> None:
        """Start a slow NLP thread when due; never block the control cycle."""

        thread = getattr(self, "_nmpc_thread", None)
        if thread is not None and thread.is_alive():
            return
        controller = getattr(self.control_engine, "_controller", None)
        if controller is not None and bool(getattr(controller, "_nmpc_busy", False)):
            return
        idle = self.control_engine.nmpc_plan_idle()
        planner_due = bool(self.control_engine.nmpc_due())
        slow_due = self._nmpc_slow_slot_due()
        if not planner_due and not slow_due:
            return
        if idle and not slow_due:
            last = self._last_nmpc_attempt_ts
            dt_s = float(self._derived_update_interval())
            if last is not None and (time.time() - last) < max(1.0, dt_s):
                return
        try:
            self._nmpc_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._nmpc_loop = None
        self._mark_nmpc_slot_started()
        self._nmpc_computing = True
        self.control_engine.mark_nmpc_busy()
        self._nmpc_thread = threading.Thread(
            target=self._nmpc_worker_thread,
            name="heatingassistant-nmpc",
            daemon=True,
        )
        self._nmpc_thread.start()

    def _nmpc_worker_thread(self) -> None:
        started = time.time()
        try:
            result = self.control_engine.solve_nmpc_blocking()
            self._last_nmpc_duration_s = max(0.0, time.time() - started)
            stamp = time.time()
            applied = self.control_engine.apply_nmpc_result(
                result,
                plan_epoch=self._slow_slot_start(stamp),
                now=stamp,
            )
            note = self.control_engine.consume_watchdog_notification()
            if note:
                self._emit_nmpc_notify(note)
            if applied:
                self._install_nmpc_p_command()
        except Exception:
            self._last_nmpc_duration_s = max(0.0, time.time() - started)
            _logger.exception("NMPC worker failed")
            controller = getattr(self.control_engine, "_controller", None)
            if controller is not None:
                controller._nmpc_busy = False
                controller._record_nmpc_reject()
                note = controller.consume_watchdog_notification()
                if note:
                    self._emit_nmpc_notify(note)
        finally:
            self._nmpc_result_ts = time.time()
            self._nmpc_computing = False
            self._note_nmpc_cycle_complete()

    def _note_nmpc_cycle_complete(self) -> None:
        """Persist runtime state after a slow solve without moving the epoch.

        ``last_nmpc_ts`` is the Start grid origin. Stamping it here reset the
        two-hour countdown by the NLP duration.
        """

        try:
            self._save_runtime_state()
        except Exception:
            _logger.exception("Failed to persist last NMPC timestamp")

    def _emit_nmpc_notify(self, action: str) -> None:
        loop = self._nmpc_loop
        coro = self.publish_nmpc_notify(action)
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
            return
        try:
            asyncio.run(coro)
        except Exception:
            _logger.exception("Failed to publish NMPC watchdog notification")

    def _install_nmpc_p_command(self) -> None:
        """Put the accepted-plan P command on the actuators without an EKF tick.

        The slow NLP must not call ``run_control_cycle`` (that would predict
        another ``T_s``). P-only uses the new ``u_ref`` so the feedforward
        bias steps when the plan lands.
        """

        tags = dict(getattr(self.control_engine, "_last_p_actions", {}) or {})
        if not tags:
            return
        acquired = self._control_lock.acquire(blocking=True, timeout=10.0)
        if not acquired:
            _logger.warning("NMPC accept P publish timed out waiting for lock")
            return
        try:
            self.actuator_outputs.update(tags)
            self._clamp_window_override_actuators()
            try:
                self._save_runtime_state()
            except Exception:
                _logger.exception("Failed to persist P command after NMPC accept")
        finally:
            self._control_lock.release()
        self._emit_nmpc_p_publish()

    def _emit_nmpc_p_publish(self) -> None:
        loop = self._nmpc_loop

        async def _publish() -> None:
            await self._best_effort_mqtt(
                self.publish_actuator_outputs(), "NMPC P actuators"
            )
            await self._best_effort_mqtt(self.publish_status(), "NMPC P status")

        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(_publish(), loop)
            return
        try:
            asyncio.run(_publish())
        except Exception:
            _logger.exception("Failed to publish P command after NMPC accept")

    async def publish_nmpc_notify(self, action: str) -> None:
        payload: dict[str, Any] = {
            "action": action,
            "notification_id": const.NMPC_WATCHDOG_NOTIFICATION_ID,
        }
        if action == "create":
            payload["title"] = const.NMPC_WATCHDOG_TITLE
            payload["message"] = const.NMPC_WATCHDOG_MESSAGE
        await self.bus.publish(
            cmd(self.instance_id, "notify"),
            json.dumps(payload, sort_keys=True),
            qos=DEFAULT_QOS,
            retain=False,
        )
