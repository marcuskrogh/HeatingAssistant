"""Open-window override state machine and event-driven actuator push."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from homeassistant.core import callback

from ..const import CONF_ROOM_NAME, CONF_WINDOW_SENSORS

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def build_window_sensor_map(
    rooms_cfg: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Return ``{room_name: [binary_sensor_id, ...]}`` for window/door override."""
    mapping: Dict[str, List[str]] = {}
    for rc in rooms_cfg:
        room_name = rc[CONF_ROOM_NAME]
        sensors = [s for s in rc.get(CONF_WINDOW_SENSORS, []) if isinstance(s, str)]
        # Deduplicate while preserving order.
        deduped: List[str] = []
        for sensor_id in sensors:
            if sensor_id not in deduped:
                deduped.append(sensor_id)
        if deduped:
            mapping[room_name] = deduped
    return mapping


def read_binary_sensor_on(
    coordinator: HeatingAssistantCoordinator, entity_id: str
) -> bool:
    """Return True when the given binary sensor is currently ``on``."""
    state = coordinator.hass.states.get(entity_id)
    if state is None:
        return False
    return str(state.state).lower() == "on"


def set_window_state(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    state: str,
    now_utc: datetime,
) -> None:
    """Set per-room window state and timestamp the transition."""
    coordinator._window_state[room_name] = state
    coordinator._window_state_since[room_name] = now_utc


def update_window_state_machine(
    coordinator: HeatingAssistantCoordinator, now_utc: datetime
) -> None:
    """Advance the per-room window state machine for Phase 3 W1."""
    for room_name in coordinator.model.room_names:
        sensors = coordinator._window_sensors.get(room_name, [])
        if not sensors:
            coordinator._window_state[room_name] = "closed"
            coordinator._window_state_since.pop(room_name, None)
            continue

        any_open = any(
            read_binary_sensor_on(coordinator, entity_id) for entity_id in sensors
        )
        state = coordinator._window_state.get(room_name, "closed")
        since = coordinator._window_state_since.get(room_name, now_utc)
        elapsed = (now_utc - since).total_seconds()

        if state == "closed":
            if any_open:
                set_window_state(coordinator, room_name, "pending_open", now_utc)
            continue
        if state == "pending_open":
            if not any_open:
                set_window_state(coordinator, room_name, "closed", now_utc)
            elif elapsed >= coordinator._window_open_debounce:
                set_window_state(coordinator, room_name, "open", now_utc)
            continue
        if state == "open":
            if not any_open:
                set_window_state(coordinator, room_name, "pending_closed", now_utc)
            continue
        if state == "pending_closed":
            if any_open:
                set_window_state(coordinator, room_name, "open", now_utc)
            elif elapsed >= coordinator._window_open_close_settle:
                set_window_state(coordinator, room_name, "closed", now_utc)


def get_window_state(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> str:
    """Return the current window override state for a room."""
    return coordinator._window_state.get(room_name, "closed")


def is_window_override_active(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> bool:
    """Return True while the room's heater must be held off for a window.

    The override is active for the whole period the heater is suppressed:
    from the moment the open-debounce expires (state ``open``) until the
    close-settle timer expires (leaving ``pending_closed`` for ``closed``).
    It is **not** active during ``pending_open`` — opening a window starts
    the debounce timer but the heater keeps running until it elapses — nor
    once the room returns to ``closed``.
    """
    return get_window_state(coordinator, room_name) in ("open", "pending_closed")


def setup_window_listeners(
    coordinator: HeatingAssistantCoordinator,
) -> Optional[Callable]:
    """Set up window-sensor listeners for event-driven debounce/settle timing.

    The state machine is advanced DIRECTLY from the event callbacks rather
    than relying on coordinator refreshes to check elapsed time.  Actuator
    commands are pushed via ``async_push_window_override()`` rather than
    ``async_request_refresh()`` so the MPC and EKF are never triggered by
    window events — they run strictly at the scheduled update interval.

    * Sensor opens  → ``closed → pending_open`` and start the
      ``window_open_debounce`` timer.  The heater keeps running until it
      elapses; only then does the room advance to ``open`` and the
      heater-off override get pushed.
    * Sensor closes before the debounce elapses → cancel the timer and
      revert to ``closed``; the heater was never turned off.
    * All room sensors close while ``open`` → ``open → pending_closed``
      and start the ``window_open_close_settle`` timer.  The heater stays
      off until it elapses; only then does the room return to ``closed``
      and the heater resume at the actuation the MPC kept solving for in
      the background.
    * A sensor re-opens while ``pending_closed`` → cancel the settle timer
      and return to ``open`` immediately; the heater stays off.

    Each running timer cancels only on the transition that ends it, so a
    second sensor opening/closing in the same direction leaves the
    in-flight debounce/settle timer untouched rather than restarting it.

    Returns a cancel callable suitable for ``entry.async_on_unload``,
    or ``None`` when no window sensors are configured.
    """
    from . import async_call_later, async_track_state_change_event

    # Build sensor_id → room_name reverse lookup.
    sensor_to_room: Dict[str, str] = {
        sid: room_name
        for room_name, sensors in coordinator._window_sensors.items()
        for sid in sensors
    }
    all_sensor_ids = list(sensor_to_room)
    if not all_sensor_ids:
        return None

    # Pending timer cancel-handles keyed by room_name (one timer per room).
    _pending: Dict[str, Callable] = {}

    @callback
    def _on_window_changed(event) -> None:
        sensor_id = event.data.get("entity_id", "")
        room_name = sensor_to_room.get(sensor_id)
        if room_name is None:
            return

        new_state_obj = event.data.get("new_state")
        old_state_obj = event.data.get("old_state")
        if new_state_obj is None:
            return

        new_open = str(new_state_obj.state).lower() == "on"
        old_open = (
            old_state_obj is not None
            and str(old_state_obj.state).lower() == "on"
        )
        if new_open == old_open:
            return

        now_utc = datetime.now(tz=timezone.utc)

        def _cancel_pending() -> None:
            """Cancel this room's in-flight debounce/settle timer, if any."""
            if room_name in _pending:
                _pending.pop(room_name)()

        current = coordinator._window_state.get(room_name, "closed")
        sensors = coordinator._window_sensors.get(room_name, [])
        any_open_now = any(read_binary_sensor_on(coordinator, s) for s in sensors)

        if new_open:
            # ── A sensor in this room just opened ──────────────────────
            if current in ("open", "pending_open"):
                # Already open, or already counting down the open debounce:
                # a second window opening changes nothing and must NOT
                # restart the in-flight timer.
                return

            if current == "pending_closed":
                # Re-opened during the close-settle window — stop the
                # settle timer and return to the open override at once.
                # The heater stays off; no debounce is re-run.
                _cancel_pending()
                set_window_state(coordinator, room_name, "open", now_utc)
                _LOGGER.debug(
                    "Window sensor %s re-opened during settle for %s — "
                    "back to open (heater stays off)",
                    sensor_id, room_name,
                )
                coordinator.hass.async_create_task(
                    async_push_window_override(coordinator)
                )
                return

            # current == "closed": start the open-debounce timer.  The
            # heater keeps running until it elapses.
            _cancel_pending()
            set_window_state(coordinator, room_name, "pending_open", now_utc)
            _LOGGER.debug(
                "Window sensor %s opened for %s — pending_open (debounce %.0fs)",
                sensor_id, room_name, coordinator._window_open_debounce,
            )

            @callback
            def _advance_open(_now=None, _rn=room_name) -> None:
                _pending.pop(_rn, None)
                _now_inner = datetime.now(tz=timezone.utc)
                sensors_in = coordinator._window_sensors.get(_rn, [])
                any_open = any(
                    read_binary_sensor_on(coordinator, s) for s in sensors_in
                )
                if any_open and coordinator._window_state.get(_rn) == "pending_open":
                    set_window_state(coordinator, _rn, "open", _now_inner)
                    _LOGGER.debug(
                        "Window debounce elapsed for %s — heater override active",
                        _rn,
                    )
                coordinator.hass.async_create_task(
                    async_push_window_override(coordinator)
                )

            _pending[room_name] = async_call_later(
                coordinator.hass, coordinator._window_open_debounce, _advance_open
            )

        else:
            # ── A sensor in this room just closed ──────────────────────
            if any_open_now:
                # Another sensor in the room is still open — the override
                # state and any running timer are unaffected.
                return

            if current == "pending_open":
                # Closed before the open debounce elapsed — stop the timer
                # and revert to closed; the heater was never turned off.
                _cancel_pending()
                set_window_state(coordinator, room_name, "closed", now_utc)
                _LOGGER.debug(
                    "Window sensor %s closed before debounce for %s — reverted",
                    sensor_id, room_name,
                )
                coordinator.hass.async_create_task(
                    async_push_window_override(coordinator)
                )
                return

            if current == "open":
                # All sensors closed while open — start the close-settle
                # timer.  The heater stays off until it elapses.
                _cancel_pending()
                set_window_state(coordinator, room_name, "pending_closed", now_utc)
                _LOGGER.debug(
                    "Window sensor %s closed for %s — pending_closed (settle %.0fs)",
                    sensor_id, room_name, coordinator._window_open_close_settle,
                )

                @callback
                def _advance_closed(_now=None, _rn=room_name) -> None:
                    _pending.pop(_rn, None)
                    _now_inner = datetime.now(tz=timezone.utc)
                    sensors_in = coordinator._window_sensors.get(_rn, [])
                    any_open2 = any(
                        read_binary_sensor_on(coordinator, s) for s in sensors_in
                    )
                    if (
                        not any_open2
                        and coordinator._window_state.get(_rn) == "pending_closed"
                    ):
                        set_window_state(coordinator, _rn, "closed", _now_inner)
                        _LOGGER.debug(
                            "Window settle elapsed for %s — heater override "
                            "cleared, resuming MPC actuation",
                            _rn,
                        )
                    coordinator.hass.async_create_task(
                        async_push_window_override(coordinator)
                    )

                _pending[room_name] = async_call_later(
                    coordinator.hass,
                    coordinator._window_open_close_settle,
                    _advance_closed,
                )
            # current in ("closed", "pending_closed"): nothing to do.

    cancel_listener = async_track_state_change_event(
        coordinator.hass, all_sensor_ids, _on_window_changed
    )

    def _cleanup() -> None:
        cancel_listener()
        for cancel_cb in list(_pending.values()):
            cancel_cb()
        _pending.clear()

    return _cleanup


async def async_push_window_override(
    coordinator: HeatingAssistantCoordinator,
) -> None:
    """Apply window overrides, push actuator commands, and refresh entity states.

    Called directly by window-sensor callbacks so that the heater-off (window
    open) or heater-on (window closed) command is issued immediately, without
    triggering a coordinator refresh and without disturbing the MPC or EKF.

    Live inputs are re-read via ``_refresh_live_state()`` so entities report
    current values, and ``async_update_listeners()`` is always called at the
    end so the UI refreshes immediately regardless of whether actuator
    commands could be applied.

    The MPC runs strictly at the scheduled update interval via the coordinator
    timer.  This method is the sole path for window events to affect actuators
    between those scheduled ticks.
    """
    outdoor_temp = coordinator._refresh_live_state()

    # Push actuator commands only when we have an MPC solution and a valid
    # outdoor temperature.  Missing either means we're still in startup or
    # the outdoor sensor is transiently unavailable — in both cases the last
    # commanded state is the safest thing to leave the heaters in.
    if coordinator.actions and outdoor_temp is not None:
        # A room whose window-override just cleared (open/pending_closed →
        # closed) must come back on at the actuation the MPC kept solving
        # for while the heater was off, not the 0 it was clamped to.  Pull
        # that value from the shadow optimum before the override clamp
        # below.  Rooms disabled for other reasons (user toggle / schedule)
        # are still forced off by ``_apply_actions``.
        for src in coordinator.heat_sources:
            if (
                not is_window_override_active(coordinator, src.room)
                and coordinator.is_room_enabled(src.room)
                and src.name in coordinator._mpc_shadow_actions
            ):
                coordinator.actions[src.name] = coordinator._mpc_shadow_actions[src.name]

        for src in coordinator.heat_sources:
            if is_window_override_active(coordinator, src.room):
                coordinator.actions[src.name] = 0.0

        if coordinator._system_enabled:
            try:
                await coordinator._apply_actions(outdoor_temp)
            except Exception:
                _LOGGER.warning(
                    "Window override: failed to push actuator commands",
                    exc_info=True,
                )

    # Always notify entity subscribers so the UI immediately reflects both
    # the new window-override state and the freshly read sensor values.
    coordinator.async_update_listeners()
