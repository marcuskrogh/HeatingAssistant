"""Tests for legacy schedule period migration to schedule_type."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.heating_assistant.const import (
    CONF_PERSISTED_SCHEDULES,
    CONF_SCHEDULE_TIME_MODE,
    CONF_SCHEDULE_TYPE,
    SCHEDULE_TIME_MODE_ALL_DAY,
    SCHEDULE_TIME_MODE_WINDOW,
    SCHEDULE_TYPE_DATE_RANGE_DAILY,
    SCHEDULE_TYPE_WEEKLY_RECURRING,
)
from custom_components.heating_assistant.schedule import build_schedule
from custom_components.heating_assistant.schedule_migration import (
    migrate_period_dict,
    migrate_period_list,
    migrate_persisted_schedules_dict,
    migrate_schedule_types_in_persisted,
)


def test_migrate_recurring_window_period():
    legacy = {
        "name": "Morning",
        "start": "06:00",
        "end": "09:00",
        "recurring": True,
        "all_day": False,
    }
    migrated = migrate_period_dict(legacy)
    assert migrated[CONF_SCHEDULE_TYPE] == SCHEDULE_TYPE_WEEKLY_RECURRING
    assert migrated[CONF_SCHEDULE_TIME_MODE] == SCHEDULE_TIME_MODE_WINDOW
    assert "recurring" not in migrated
    assert "all_day" not in migrated


def test_migrate_one_off_all_day_period():
    legacy = {
        "name": "Trip",
        "start": "00:00",
        "end": "23:59",
        "recurring": False,
        "all_day": True,
        "start_date": "2026-07-20",
        "end_date": "2026-07-27",
    }
    migrated = migrate_period_dict(legacy)
    assert migrated[CONF_SCHEDULE_TYPE] == SCHEDULE_TYPE_DATE_RANGE_DAILY
    assert migrated[CONF_SCHEDULE_TIME_MODE] == SCHEDULE_TIME_MODE_ALL_DAY


def test_migrated_period_builds_and_matches_legacy_behavior():
    legacy = {
        "name": "Evening",
        "start": "18:00",
        "end": "22:00",
        "recurring": True,
        "all_day": False,
    }
    sched = build_schedule([migrate_period_dict(legacy)])
    period = sched.periods[0]
    assert period.schedule_type == SCHEDULE_TYPE_WEEKLY_RECURRING
    assert period.time_mode == SCHEDULE_TIME_MODE_WINDOW


def test_migrate_persisted_schedules_dict_strips_legacy_keys():
    persisted = {
        "Living Room": [
            {
                "name": "Eco",
                "start": "08:00",
                "end": "16:00",
                "recurring": True,
                "all_day": False,
                CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
                CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
            }
        ]
    }
    migrated, changed = migrate_persisted_schedules_dict(persisted)
    assert changed is True
    assert "recurring" not in migrated["Living Room"][0]


def test_migrate_schedule_types_in_persisted_rewrites_entry():
    entry = MagicMock()
    entry.data = {
        CONF_PERSISTED_SCHEDULES: {
            "Living Room": [
                {
                    "name": "Night",
                    "start": "22:00",
                    "end": "04:00",
                    "recurring": True,
                    "all_day": False,
                }
            ]
        }
    }
    hass = MagicMock()
    changed = migrate_schedule_types_in_persisted(hass, entry)
    assert changed is True
    hass.config_entries.async_update_entry.assert_called_once()
    update_kwargs = hass.config_entries.async_update_entry.call_args.kwargs
    room_periods = update_kwargs["data"][CONF_PERSISTED_SCHEDULES]["Living Room"]
    assert room_periods[0][CONF_SCHEDULE_TYPE] == SCHEDULE_TYPE_WEEKLY_RECURRING


def test_migrate_period_list_strips_legacy_keys_from_new_shape():
    period = {
        "name": "Eco",
        "schedule_type": SCHEDULE_TYPE_WEEKLY_RECURRING,
        "time_mode": SCHEDULE_TIME_MODE_WINDOW,
        "start": "08:00",
        "end": "16:00",
        "recurring": True,
        "all_day": False,
    }
    migrated = migrate_period_list([period])[0]
    assert "recurring" not in migrated
    assert "all_day" not in migrated
