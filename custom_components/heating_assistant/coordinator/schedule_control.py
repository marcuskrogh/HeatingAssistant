"""Comfort-schedule resolution and MPC horizon trajectory helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np

from ..const import DEFAULT_COMFORT_OFFSET, DEFAULT_SETPOINT
from ..schedule import (
    EffectiveControlParams,
    build_schedule,
    control_params_at,
    next_transition,
    period_to_dict,
    resolve_effective_control_params,
)
from ..schedule_migration import migrate_period_list
from .types import ControlTrajectory

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator


def apply_schedule(coordinator: HeatingAssistantCoordinator, now: datetime) -> None:
    """Resolve the active schedule for every room and update live state.

    Sets ``room.setpoint`` to the period's effective value and toggles
    ``_schedule_disabled`` so heat sources stop running during ``off``
    periods.  The user's manual on/off toggle (``_room_enabled``) is
    preserved — both flags are AND'ed together by
    :meth:`HeatingAssistantCoordinator.is_room_enabled`.

    Rooms whose schedule has been suspended (``_schedule_enabled`` is
    False) are left at their base setpoint and the schedule-disable is
    cleared.
    """
    for room_name in coordinator.model.room_names:
        schedule = coordinator._room_schedule.get(room_name)
        base = coordinator._base_setpoint.get(
            room_name, coordinator.model.rooms[room_name].setpoint
        )
        measured = coordinator.model.rooms[room_name].temperature
        default_offset = coordinator._room_comfort_offset.get(
            room_name, DEFAULT_COMFORT_OFFSET
        )

        if schedule is None or schedule.is_empty or not coordinator._schedule_enabled.get(
            room_name, True
        ):
            effective = EffectiveControlParams(
                setpoint=base,
                comfort_offset=default_offset,
                tracking_weight=1.0,
                energy_weight=1.0,
                enabled=True,
                period_name=None,
                mode=None,
            )
        else:
            effective = resolve_effective_control_params(
                schedule=schedule,
                base_setpoint=base,
                measured_temp=measured,
                now=now,
                default_comfort_offset=default_offset,
            )

        coordinator.model.rooms[room_name].setpoint = effective.setpoint
        coordinator.model.rooms[room_name].comfort_offset = effective.comfort_offset
        coordinator._schedule_disabled[room_name] = not effective.enabled
        coordinator._effective_setpoint[room_name] = effective


def compute_control_trajectory(
    coordinator: HeatingAssistantCoordinator,
    now_local: datetime,
    N: int,
    dt_seconds: float,
) -> ControlTrajectory:
    """Build per-step control parameters for the full MPC horizon.

    For each room and each horizon step k the resolved comfort setpoint,
    corridor half-width, tracking-weight multiplier and energy-weight
    multiplier are projected forward by consulting the room's schedule at
    ``now_local + k * dt_seconds``.

    Off periods are transparent: when a future step falls in an ``off``
    period the last known comfort values are carried forward unchanged.
    This produces the same reference the static (non-schedule-aware) MPC
    would use while leaving the ``disabled_sources`` zeroing mechanism
    fully responsible for off-period execution.
    """
    traj = ControlTrajectory(
        setpoints={},
        comfort_offsets={},
        q_scales={},
        r_scales={},
        enabled_steps={},
    )

    for room_name in coordinator.model.room_names:
        schedule = coordinator._room_schedule.get(room_name)
        base_sp = coordinator._base_setpoint.get(room_name, DEFAULT_SETPOINT)
        default_offset = coordinator._room_comfort_offset.get(
            room_name, DEFAULT_COMFORT_OFFSET
        )

        sp_seq = np.empty(N, dtype=float)
        off_seq = np.empty(N, dtype=float)
        qw_seq = np.empty(N, dtype=float)
        rw_seq = np.empty(N, dtype=float)
        enabled_seq = np.ones(N, dtype=bool)

        # Anchor: current effective params (already resolved by apply_schedule)
        current = coordinator._effective_setpoint.get(room_name)
        last_sp = current.setpoint if current is not None else base_sp
        last_off = current.comfort_offset if current is not None else default_offset
        last_qw = current.tracking_weight if current is not None else 1.0
        last_rw = current.energy_weight if current is not None else 1.0

        for k in range(N):
            t_k = now_local + timedelta(seconds=k * dt_seconds)
            if not coordinator._schedule_enabled.get(room_name, True):
                # Schedule suspended — use current effective values throughout.
                pass
            else:
                params = control_params_at(
                    schedule=schedule,
                    base_setpoint=base_sp,
                    t_future=t_k,
                    default_comfort_offset=default_offset,
                )
                if params is not None:
                    last_sp = params.setpoint
                    last_off = params.comfort_offset
                    last_qw = params.tracking_weight
                    last_rw = params.energy_weight
                else:
                    # Off period: carry forward values but mark step as disabled.
                    enabled_seq[k] = False

            sp_seq[k] = last_sp
            off_seq[k] = last_off
            qw_seq[k] = last_qw
            rw_seq[k] = last_rw

        # Manual off (user toggle) disables all horizon steps.
        if not coordinator._room_enabled.get(room_name, True):
            enabled_seq[:] = False

        traj.setpoints[room_name] = sp_seq
        traj.comfort_offsets[room_name] = off_seq
        traj.q_scales[room_name] = qw_seq
        traj.r_scales[room_name] = rw_seq
        traj.enabled_steps[room_name] = enabled_seq

    return traj


def has_schedule(coordinator: HeatingAssistantCoordinator, room_name: str) -> bool:
    """Return True when ``room_name`` has at least one schedule period."""
    schedule = coordinator._room_schedule.get(room_name)
    return bool(schedule and not schedule.is_empty)


def is_schedule_enabled(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> bool:
    """Return whether the comfort schedule is active for the room.

    A True result with no configured periods means the schedule "would
    run" if periods were defined; use :func:`has_schedule` to test for
    actual configuration.
    """
    return coordinator._schedule_enabled.get(room_name, True)


def set_schedule_enabled(
    coordinator: HeatingAssistantCoordinator, room_name: str, enabled: bool
) -> None:
    """Suspend or resume the comfort schedule for one room.

    Suspending the schedule restores the room's base setpoint and
    re-enables heating immediately, so e.g. an evening "off" period
    can be skipped without editing YAML.  The configured periods are
    preserved and resume control on the next call to ``set_schedule_enabled``
    or after a Home Assistant restart.
    """
    coordinator._schedule_enabled[room_name] = bool(enabled)
    # Re-apply now so the next tick already reflects the change in the
    # MPC reference; otherwise the user would have to wait one cycle.
    apply_schedule(coordinator, datetime.now())
    _persist_schedule_enabled(coordinator)


def _persist_schedule_enabled(coordinator: HeatingAssistantCoordinator) -> None:
    """Write per-room schedule suspend flags to the config entry."""
    from .enablement import _sync_persisted_key_to_merged_entry
    from ..const import CONF_PERSISTED_SCHEDULE_ENABLED

    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is None:
        return
    persisted = dict(coordinator._schedule_enabled)
    coordinator.hass.config_entries.async_update_entry(
        real_entry,
        data={
            **dict(real_entry.data),
            CONF_PERSISTED_SCHEDULE_ENABLED: persisted,
        },
    )
    _sync_persisted_key_to_merged_entry(
        coordinator, CONF_PERSISTED_SCHEDULE_ENABLED, persisted
    )


def apply_persisted_schedule_enabled(
    coordinator: HeatingAssistantCoordinator, persisted_enabled: dict
) -> None:
    """Overlay dashboard-persisted schedule suspend flags onto coordinator state."""
    for room_name, value in (persisted_enabled or {}).items():
        if room_name in coordinator._schedule_enabled:
            coordinator._schedule_enabled[room_name] = bool(value)


def active_schedule_period(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> Optional[EffectiveControlParams]:
    """Return the most recently resolved effective setpoint for the room."""
    return coordinator._effective_setpoint.get(room_name)


def next_schedule_transition(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> Optional[datetime]:
    """Return the timestamp of the next schedule boundary for the room."""
    schedule = coordinator._room_schedule.get(room_name)
    if schedule is None or schedule.is_empty:
        return None
    return next_transition(schedule, datetime.now())


def reload_room_schedule(
    coordinator: HeatingAssistantCoordinator, room_name: str, periods_raw: list
) -> None:
    """Rebuild a single room's schedule from raw period definitions.

    Called by the ``update_room_schedule`` service after the config entry
    has been persisted with the new periods list.
    """
    new_schedule = build_schedule(periods_raw)
    coordinator._room_schedule[room_name] = new_schedule


def apply_persisted_schedules(
    coordinator: HeatingAssistantCoordinator,
    persisted_schedules: Dict[str, Any],
) -> None:
    """Overlay dashboard-persisted schedules onto ``_room_schedule``.

    Keys in ``persisted_schedules`` may be canonical room names or slugs from
    older saves; resolve via :func:`slugify` so restarts never silently drop
    schedules when the key format drifts.
    """
    from ..naming import slugify

    if not persisted_schedules:
        return

    room_by_slug: Dict[str, str] = {}
    ambiguous_slugs: set[str] = set()
    for name in coordinator._room_schedule:
        slug = slugify(name)
        prior = room_by_slug.get(slug)
        if prior is not None and prior != name:
            ambiguous_slugs.add(slug)
        else:
            room_by_slug[slug] = name
    for slug in ambiguous_slugs:
        room_by_slug.pop(slug, None)

    for key, periods_raw in persisted_schedules.items():
        if key in coordinator._room_schedule:
            canonical = key
        else:
            key_slug = slugify(key)
            canonical = (
                None
                if key_slug in ambiguous_slugs
                else room_by_slug.get(key_slug)
            )
        if canonical is not None:
            coordinator._room_schedule[canonical] = build_schedule(
                migrate_period_list(list(periods_raw))
            )


def migrate_legacy_schedules_to_persisted(hass: Any, entry: Any) -> bool:
    """Copy non-empty ``rooms[].schedule`` into ``persisted_schedules`` when absent.

    Older installs stored dashboard schedules under per-room config; newer saves
    use the dedicated ``persisted_schedules`` key.  Migrating on startup ensures
    a single authoritative path survives restarts and browser refresh.
    """
    from ..const import (
        CONF_PERSISTED_SCHEDULES,
        CONF_ROOM_NAME,
        CONF_ROOMS,
        CONF_SCHEDULE,
    )

    data = dict(entry.data)
    persisted: Dict[str, Any] = dict(data.get(CONF_PERSISTED_SCHEDULES) or {})
    changed = False

    def _ingest_rooms(rooms: Any) -> None:
        nonlocal changed
        for room in rooms or []:
            if not isinstance(room, dict):
                continue
            name = room.get(CONF_ROOM_NAME)
            schedule = room.get(CONF_SCHEDULE)
            if not name or not schedule:
                continue
            if persisted.get(name):
                continue
            persisted[name] = list(schedule)
            changed = True

    _ingest_rooms(data.get(CONF_ROOMS))
    _ingest_rooms(dict(entry.options).get(CONF_ROOMS))

    if not changed:
        return False

    hass.config_entries.async_update_entry(
        entry,
        data={**data, CONF_PERSISTED_SCHEDULES: persisted},
    )
    return True


def serialize_room_schedules(
    coordinator: HeatingAssistantCoordinator,
) -> Dict[str, Any]:
    """Return slug-keyed room schedule payloads for UI / WebSocket consumers."""
    from ..naming import slugify

    schedules: dict = {}
    for room_name in coordinator.model.room_names:
        room_schedule = coordinator._room_schedule.get(room_name)
        periods_payload: list = []
        if room_schedule and not room_schedule.is_empty:
            periods_payload = [
                period_to_dict(p) for p in room_schedule.periods
            ]
        schedules[slugify(room_name)] = {
            "enabled": coordinator._schedule_enabled.get(room_name, True),
            "periods": periods_payload,
        }
    return schedules
