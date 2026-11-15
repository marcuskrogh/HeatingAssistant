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

        if const.coerce_mpc_mode(self.options.get(const.CONF_MPC_MODE)) == const.MPC_MODE_LINEAR:
            return
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
        self._nmpc_tick_now = float(getattr(self, "_active_tick_now", None) or time.time())
        self._mark_nmpc_slot_started()
        self._nmpc_computing = True
        # Ticks no longer call compute_actions, which used to fill these.
        self._stage_planner_inputs()
        self.control_engine.mark_nmpc_busy()
        self._nmpc_thread = threading.Thread(
            target=self._nmpc_worker_thread,
            name="heatingassistant-nmpc",
            daemon=True,
        )
        self._nmpc_thread.start()

    def _nmpc_worker_thread(self) -> None:
        self._publish_compute_status()
        started = time.time()
        applied = False
        result: Any = None
        plan_logged = False
        watchdog_tripped_now = False
        try:
            result = self.control_engine.solve_nmpc_blocking()
            self._last_nmpc_duration_s = max(0.0, time.time() - started)
            stamp = time.time()
            controller = getattr(self.control_engine, "_controller", None)
            watchdog_before = bool(getattr(controller, "_watchdog_tripped", False))
            epoch = self._slow_slot_start(stamp)
            applied = bool(
                self.control_engine.apply_nmpc_result(
                    result,
                    plan_epoch=epoch,
                    now=stamp,
                )
            )
            watchdog_after = bool(getattr(controller, "_watchdog_tripped", False))
            watchdog_tripped_now = watchdog_after and not watchdog_before
            self._record_control_log_plan(
                result if isinstance(result, dict) else None,
                applied=applied,
                watchdog_before=watchdog_before,
                watchdog_after=watchdog_after,
                now=stamp,
                epoch=epoch,
            )
            plan_logged = True
            note = self.control_engine.consume_watchdog_notification()
            if note:
                self._emit_nmpc_notify(note)
        except Exception:
            self._last_nmpc_duration_s = max(0.0, time.time() - started)
            _logger.exception("NMPC worker failed")
            controller = getattr(self.control_engine, "_controller", None)
            if controller is not None:
                was_tripped = bool(controller._watchdog_tripped)
                controller._nmpc_busy = False
                controller._record_nmpc_reject()
                watchdog_tripped_now = bool(controller._watchdog_tripped) and not was_tripped
                note = controller.consume_watchdog_notification()
                if note:
                    self._emit_nmpc_notify(note)
            if not plan_logged:
                self._record_control_log_plan(
                    result if isinstance(result, dict) else {"accepted": False, "message": "worker failed"},
                    applied=False,
                    watchdog_before=bool(getattr(controller, "_watchdog_tripped", False)),
                    watchdog_after=bool(getattr(controller, "_watchdog_tripped", False)),
                    now=time.time(),
                )
        finally:
            self._nmpc_result_ts = time.time()
            self._nmpc_computing = False
            self._note_nmpc_cycle_complete()
            self._publish_compute_status()
        if applied:
            self._install_nmpc_p_command()
        elif watchdog_tripped_now:
            self._publish_watchdog_zero()

    def _publish_compute_status(self) -> None:
        """Push computing flags and result stamps without waiting for a P tick."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(self._best_effort_mqtt(self.publish_status(), "compute status"))
            except Exception:
                _logger.exception("Failed to publish compute status")
            return
        # Caller is already on the app loop; a nested publish is the loop's job.
        return

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

    def _publish_watchdog_zero(self) -> None:
        """One actuator write that holds every source at zero after the watchdog."""

        tags = getattr(self.control_engine, "_source_output_tags", {}) or {}
        for tag in tags.values():
            self.actuator_outputs[str(tag)] = 0.0
        self._clamp_window_override_actuators()
        try:
            self._save_runtime_state()
        except Exception:
            _logger.exception("Failed to persist watchdog zero command")
        self._emit_nmpc_p_publish()

    def _emit_nmpc_p_publish(self) -> None:
        sample_now = getattr(self, "_nmpc_tick_now", None)

        async def _publish() -> None:
            await self._publish_tick_actuators(sample_now)
            await self._best_effort_mqtt(self.publish_status(), "NMPC P status")

        # Publish on this worker thread. The ticker's loop exists only for the
        # tick call and is gone before a slow solve finishes.
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
