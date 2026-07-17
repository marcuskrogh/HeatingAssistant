"""One-shot migration from legacy recurring/all_day periods to schedule_type."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from .const import (
    CONF_COMFORT_OFFSET,
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SETPOINTS,
    CONF_ROOMS,
    CONF_ROOM_NAME,
    CONF_SCHEDULE_ALL_DAY,
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_END_AT,
    CONF_SCHEDULE_END_DATE,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_RECURRING,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_START_AT,
    CONF_SCHEDULE_START_DATE,
    CONF_SCHEDULE_TIME_MODE,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SCHEDULE_TYPE,
    CONF_SETPOINT,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_FROST_PROTECTION,
    DEFAULT_SETPOINT,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SCHEDULE_TIME_MODE_ALL_DAY,
    SCHEDULE_TIME_MODE_WINDOW,
    SCHEDULE_TYPE_DATE_RANGE_DAILY,
    SCHEDULE_TYPE_WEEKLY_RECURRING,
)

_LEGACY_KEYS = frozenset({CONF_SCHEDULE_RECURRING, CONF_SCHEDULE_ALL_DAY})
_COMFORT_OVERRIDE_FIELDS = frozenset(
    {
        CONF_SCHEDULE_SETPOINT,
        CONF_SCHEDULE_COMFORT_OFFSET,
        CONF_SCHEDULE_TRACKING_WEIGHT,
        CONF_SCHEDULE_ENERGY_WEIGHT,
    }
)
_OFF_OVERRIDE_FIELDS = frozenset({CONF_SCHEDULE_FROST_PROTECTION})


def is_legacy_period(period: Dict[str, Any]) -> bool:
    """Return True when a stored period lacks explicit ``schedule_type``."""
    return CONF_SCHEDULE_TYPE not in period


def migrate_period_dict(period: Dict[str, Any]) -> Dict[str, Any]:
    """Map a legacy period dict to the explicit schedule_type model.

    Already-migrated periods are stripped of legacy keys only.
    """
    if not isinstance(period, dict):
        raise TypeError("schedule period must be a mapping")

    if is_legacy_period(period):
        recurring = bool(period.get(CONF_SCHEDULE_RECURRING, True))
        all_day = bool(period.get(CONF_SCHEDULE_ALL_DAY, False))
        schedule_type = (
            SCHEDULE_TYPE_WEEKLY_RECURRING
            if recurring
            else SCHEDULE_TYPE_DATE_RANGE_DAILY
        )
        time_mode = SCHEDULE_TIME_MODE_ALL_DAY if all_day else SCHEDULE_TIME_MODE_WINDOW
        migrated: Dict[str, Any] = {
            key: deepcopy(value)
            for key, value in period.items()
            if key not in _LEGACY_KEYS
        }
        migrated[CONF_SCHEDULE_TYPE] = schedule_type
        migrated[CONF_SCHEDULE_TIME_MODE] = time_mode
        return migrated

    cleaned = {
        key: deepcopy(value)
        for key, value in period.items()
        if key not in _LEGACY_KEYS
    }
    return cleaned


def migrate_period_list(periods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Migrate a list of period dicts, preserving order."""
    return [migrate_period_dict(period) for period in periods]


def persisted_schedules_need_migration(
    persisted: Dict[str, Any] | None,
) -> bool:
    """Return True when any room schedule still uses legacy period fields."""
    if not persisted:
        return False
    for periods_raw in persisted.values():
        if not isinstance(periods_raw, list):
            continue
        for period in periods_raw:
            if isinstance(period, dict) and is_legacy_period(period):
                return True
            if isinstance(period, dict) and any(
                key in period for key in _LEGACY_KEYS
            ):
                return True
    return False


def migrate_persisted_schedules_dict(
    persisted: Dict[str, Any],
) -> tuple[Dict[str, Any], bool]:
    """Return migrated schedules and whether any period changed."""
    if not persisted:
        return {}, False

    migrated: Dict[str, Any] = {}
    changed = False
    for room_key, periods_raw in persisted.items():
        if not isinstance(periods_raw, list):
            migrated[room_key] = periods_raw
            continue
        next_periods: List[Dict[str, Any]] = []
        for period in periods_raw:
            if not isinstance(period, dict):
                next_periods.append(period)
                continue
            converted = migrate_period_dict(period)
            if converted != period:
                changed = True
            next_periods.append(converted)
        migrated[room_key] = next_periods
    return migrated, changed


def migrate_schedule_types_in_persisted(hass: Any, entry: Any) -> bool:
    """Rewrite ``persisted_schedules`` to the schedule_type model on upgrade."""
    data = dict(entry.data)
    persisted: Dict[str, Any] = dict(data.get(CONF_PERSISTED_SCHEDULES) or {})
    migrated, changed = migrate_persisted_schedules_dict(persisted)
    if not changed:
        return False

    hass.config_entries.async_update_entry(
        entry,
        data={**data, CONF_PERSISTED_SCHEDULES: migrated},
    )
    return True


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _room_override_baselines(data: Dict[str, Any], room_key: str) -> Dict[str, float]:
    baselines: Dict[str, float] = {
        CONF_SCHEDULE_SETPOINT: DEFAULT_SETPOINT,
        CONF_SCHEDULE_COMFORT_OFFSET: DEFAULT_COMFORT_OFFSET,
        CONF_SCHEDULE_TRACKING_WEIGHT: 1.0,
        CONF_SCHEDULE_ENERGY_WEIGHT: 1.0,
        CONF_SCHEDULE_FROST_PROTECTION: DEFAULT_FROST_PROTECTION,
    }

    rooms = data.get(CONF_ROOMS) or []
    if isinstance(rooms, list):
        for room in rooms:
            if not isinstance(room, dict) or room.get(CONF_ROOM_NAME) != room_key:
                continue
            baselines[CONF_SCHEDULE_SETPOINT] = _float_or_default(
                room.get(CONF_SETPOINT), DEFAULT_SETPOINT
            )
            baselines[CONF_SCHEDULE_COMFORT_OFFSET] = _float_or_default(
                room.get(CONF_COMFORT_OFFSET), DEFAULT_COMFORT_OFFSET
            )
            break

    persisted_setpoints = data.get(CONF_PERSISTED_SETPOINTS) or {}
    if isinstance(persisted_setpoints, dict) and room_key in persisted_setpoints:
        baselines[CONF_SCHEDULE_SETPOINT] = _float_or_default(
            persisted_setpoints.get(room_key), baselines[CONF_SCHEDULE_SETPOINT]
        )

    persisted_offsets = data.get(CONF_PERSISTED_COMFORT_OFFSETS) or {}
    if isinstance(persisted_offsets, dict) and room_key in persisted_offsets:
        baselines[CONF_SCHEDULE_COMFORT_OFFSET] = _float_or_default(
            persisted_offsets.get(room_key), baselines[CONF_SCHEDULE_COMFORT_OFFSET]
        )

    return baselines


def _stored_float_equals(value: Any, baseline: float) -> bool:
    try:
        return float(value) == baseline
    except (TypeError, ValueError):
        return False


def strip_inherited_overrides_from_period(
    period: Dict[str, Any],
    baselines: Dict[str, float],
) -> Dict[str, Any]:
    """Remove upgrade-time redundant override keys from one stored period."""
    migrated = {key: deepcopy(value) for key, value in period.items()}
    mode = str(migrated.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_COMFORT)).lower()

    if mode == SCHEDULE_MODE_COMFORT:
        for key in _OFF_OVERRIDE_FIELDS:
            migrated.pop(key, None)
    elif mode == SCHEDULE_MODE_OFF:
        for key in _COMFORT_OVERRIDE_FIELDS:
            migrated.pop(key, None)

    for key, baseline in baselines.items():
        if key in migrated and _stored_float_equals(migrated[key], baseline):
            migrated.pop(key, None)

    return migrated


def migrate_inherited_overrides_persisted_schedules_dict(
    persisted: Dict[str, Any],
    data: Dict[str, Any],
) -> tuple[Dict[str, Any], bool]:
    """Return persisted schedules with inherited/mode-irrelevant overrides stripped."""
    if not persisted:
        return {}, False

    migrated: Dict[str, Any] = {}
    changed = False
    for room_key, periods_raw in persisted.items():
        if not isinstance(periods_raw, list):
            migrated[room_key] = periods_raw
            continue
        baselines = _room_override_baselines(data, room_key)
        next_periods: List[Dict[str, Any]] = []
        for period in periods_raw:
            if not isinstance(period, dict):
                next_periods.append(period)
                continue
            converted = strip_inherited_overrides_from_period(period, baselines)
            if converted != period:
                changed = True
            next_periods.append(converted)
        migrated[room_key] = next_periods
    return migrated, changed


def migrate_inherited_overrides_in_persisted(hass: Any, entry: Any) -> bool:
    """One-shot cleanup for old persisted schedule override snapshots."""
    data = dict(entry.data)
    persisted: Dict[str, Any] = dict(data.get(CONF_PERSISTED_SCHEDULES) or {})
    migrated, changed = migrate_inherited_overrides_persisted_schedules_dict(
        persisted, data
    )
    if not changed:
        return False

    hass.config_entries.async_update_entry(
        entry,
        data={**data, CONF_PERSISTED_SCHEDULES: migrated},
    )
    return True
