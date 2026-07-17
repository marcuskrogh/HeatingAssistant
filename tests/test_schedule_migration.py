"""Tests for legacy schedule period migration to schedule_type."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.heating_assistant.const import (
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SETPOINTS,
    CONF_ROOMS,
    CONF_ROOM_NAME,
    CONF_COMFORT_OFFSET,
    CONF_SETPOINT,
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_TIME_MODE,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SCHEDULE_TYPE,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SCHEDULE_TIME_MODE_ALL_DAY,
    SCHEDULE_TIME_MODE_WINDOW,
    SCHEDULE_TYPE_DATE_RANGE_DAILY,
    SCHEDULE_TYPE_WEEKLY_RECURRING,
)
from custom_components.heating_assistant.schedule import build_schedule
from custom_components.heating_assistant.schedule_migration import (
    migrate_period_dict,
    migrate_period_list,
    migrate_inherited_overrides_in_persisted,
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


def test_migrate_inherited_overrides_strips_defaults_and_mode_irrelevant_fields():
    entry = MagicMock()
    entry.data = {
        CONF_ROOMS: [
            {
                CONF_ROOM_NAME: "Living Room",
                CONF_SETPOINT: 21.0,
                CONF_COMFORT_OFFSET: 1.5,
            },
            {CONF_ROOM_NAME: "Bedroom"},
        ],
        CONF_PERSISTED_SETPOINTS: {"Living Room": 20.0},
        CONF_PERSISTED_COMFORT_OFFSETS: {"Living Room": 1.0},
        CONF_PERSISTED_SCHEDULES: {
            "Living Room": [
                {
                    "name": "Comfort",
                    CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
                    CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
                    CONF_SCHEDULE_MODE: SCHEDULE_MODE_COMFORT,
                    "start": "08:00",
                    "end": "12:00",
                    "enabled": False,
                    CONF_SCHEDULE_SETPOINT: 20.0,
                    CONF_SCHEDULE_COMFORT_OFFSET: 1.0,
                    CONF_SCHEDULE_TRACKING_WEIGHT: 1.0,
                    CONF_SCHEDULE_ENERGY_WEIGHT: 1.0,
                    CONF_SCHEDULE_FROST_PROTECTION: 9.0,
                },
                {
                    "name": "Off default",
                    CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
                    CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
                    CONF_SCHEDULE_MODE: SCHEDULE_MODE_OFF,
                    "start": "22:00",
                    "end": "06:00",
                    CONF_SCHEDULE_SETPOINT: 18.0,
                    CONF_SCHEDULE_COMFORT_OFFSET: 0.5,
                    CONF_SCHEDULE_TRACKING_WEIGHT: 2.0,
                    CONF_SCHEDULE_ENERGY_WEIGHT: 2.0,
                    CONF_SCHEDULE_FROST_PROTECTION: 12.0,
                },
                {
                    "name": "Off custom",
                    CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
                    CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
                    CONF_SCHEDULE_MODE: SCHEDULE_MODE_OFF,
                    "start": "12:00",
                    "end": "13:00",
                    CONF_SCHEDULE_FROST_PROTECTION: 10.0,
                },
            ],
            "Bedroom": [
                {
                    "name": "Default comfort",
                    CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
                    CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
                    CONF_SCHEDULE_MODE: SCHEDULE_MODE_COMFORT,
                    "start": "07:00",
                    "end": "09:00",
                    CONF_SCHEDULE_SETPOINT: 22.0,
                    CONF_SCHEDULE_COMFORT_OFFSET: 2.0,
                }
            ],
        },
    }
    hass = MagicMock()

    changed = migrate_inherited_overrides_in_persisted(hass, entry)

    assert changed is True
    hass.config_entries.async_update_entry.assert_called_once()
    update_kwargs = hass.config_entries.async_update_entry.call_args.kwargs
    migrated = update_kwargs["data"][CONF_PERSISTED_SCHEDULES]
    living_periods = migrated["Living Room"]
    assert living_periods[0] == {
        "name": "Comfort",
        CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
        CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_COMFORT,
        "start": "08:00",
        "end": "12:00",
        "enabled": False,
    }
    assert living_periods[1] == {
        "name": "Off default",
        CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
        CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_OFF,
        "start": "22:00",
        "end": "06:00",
    }
    assert living_periods[2][CONF_SCHEDULE_FROST_PROTECTION] == 10.0
    assert migrated["Bedroom"][0] == {
        "name": "Default comfort",
        CONF_SCHEDULE_TYPE: SCHEDULE_TYPE_WEEKLY_RECURRING,
        CONF_SCHEDULE_TIME_MODE: SCHEDULE_TIME_MODE_WINDOW,
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_COMFORT,
        "start": "07:00",
        "end": "09:00",
    }
