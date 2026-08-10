"""Open-window / open-door heater override state machine (App-side).

Port of the pre-SWD-262 ``coordinator/window.py`` behaviour: per-room
``closed → pending_open → open → pending_closed → closed``, driven by MQTT
window tags and App timers rather than HA state listeners.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

# States where heat sources for the room must stay at 0.
_OVERRIDE_ACTIVE = frozenset({"open", "pending_closed"})


def tag_is_open(value: Any) -> bool:
    """Return True when a MQTT window-tag value means contact open / on."""

    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) >= 0.5
    text = str(value).strip().lower()
    return text in {"1", "true", "on", "open"}


def build_window_tag_map(rooms_cfg: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Return ``{room_name: [window_tag, ...]}`` from wired ``window_tags``."""

    mapping: dict[str, list[str]] = {}
    for room in rooms_cfg:
        name = room.get("name")
        if not isinstance(name, str) or not name:
            continue
        tags = room.get("window_tags")
        if not isinstance(tags, list):
            continue
        deduped: list[str] = []
        for item in tags:
            if isinstance(item, str) and item and item not in deduped:
                deduped.append(item)
        if deduped:
            mapping[name] = deduped
    return mapping


def build_tag_to_room(window_tags: Mapping[str, list[str]]) -> dict[str, str]:
    """Reverse map ``window_tag → room_name`` (first room wins on duplicates)."""

    out: dict[str, str] = {}
    for room_name, tags in window_tags.items():
        for tag in tags:
            out.setdefault(tag, room_name)
    return out


def set_window_state(
    states: dict[str, str],
    since: dict[str, datetime],
    room_name: str,
    state: str,
    now_utc: datetime,
) -> None:
    """Set per-room window state and timestamp the transition."""

    states[room_name] = state
    since[room_name] = now_utc


def get_window_state(states: Mapping[str, str], room_name: str) -> str:
    """Return the current window override state for a room."""

    return states.get(room_name, "closed")


def is_window_override_active(states: Mapping[str, str], room_name: str) -> bool:
    """Return True while the room's heater must be held off for a window."""

    return get_window_state(states, room_name) in _OVERRIDE_ACTIVE


def any_tag_open(
    tag_values: Mapping[str, Any],
    tags: Iterable[str],
) -> bool:
    """Logical OR of contact-open across a room's window tags."""

    return any(tag_is_open(tag_values.get(tag)) for tag in tags)


def update_window_state_machine(
    *,
    room_names: Iterable[str],
    window_tags: Mapping[str, list[str]],
    tag_values: Mapping[str, Any],
    states: dict[str, str],
    since: dict[str, datetime],
    now_utc: datetime,
    debounce_s: float,
    settle_s: float,
) -> None:
    """Advance the per-room window state machine from current tag values.

    Useful for tests and as a catch-up path; live operation prefers the
    event-driven timer transitions in ``HeatingRuntime``.
    """

    for room_name in room_names:
        tags = window_tags.get(room_name, [])
        if not tags:
            states[room_name] = "closed"
            since.pop(room_name, None)
            continue

        opened = any_tag_open(tag_values, tags)
        state = states.get(room_name, "closed")
        started = since.get(room_name, now_utc)
        elapsed = (now_utc - started).total_seconds()

        if state == "closed":
            if opened:
                set_window_state(states, since, room_name, "pending_open", now_utc)
            continue
        if state == "pending_open":
            if not opened:
                set_window_state(states, since, room_name, "closed", now_utc)
            elif elapsed >= debounce_s:
                set_window_state(states, since, room_name, "open", now_utc)
            continue
        if state == "open":
            if not opened:
                set_window_state(states, since, room_name, "pending_closed", now_utc)
            continue
        if state == "pending_closed":
            if opened:
                set_window_state(states, since, room_name, "open", now_utc)
            elif elapsed >= settle_s:
                set_window_state(states, since, room_name, "closed", now_utc)


def utcnow() -> datetime:
    """Timezone-aware UTC now (test seam)."""

    return datetime.now(tz=timezone.utc)
