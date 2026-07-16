"""One-shot migration from legacy recurring/all_day periods to schedule_type."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from .const import (
    CONF_PERSISTED_SCHEDULES,
    CONF_SCHEDULE_ALL_DAY,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_END_AT,
    CONF_SCHEDULE_END_DATE,
    CONF_SCHEDULE_RECURRING,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_START_AT,
    CONF_SCHEDULE_START_DATE,
    CONF_SCHEDULE_TIME_MODE,
    CONF_SCHEDULE_TYPE,
    SCHEDULE_TIME_MODE_ALL_DAY,
    SCHEDULE_TIME_MODE_WINDOW,
    SCHEDULE_TYPE_DATE_RANGE_DAILY,
    SCHEDULE_TYPE_WEEKLY_RECURRING,
)

_LEGACY_KEYS = frozenset({CONF_SCHEDULE_RECURRING, CONF_SCHEDULE_ALL_DAY})


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
