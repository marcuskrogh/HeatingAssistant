"""Unit tests for the comfort schedule module."""

from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.const import (
    DEFAULT_FROST_PROTECTION,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
)
from custom_components.heating_assistant.schedule import (
    RoomSchedule,
    SchedulePeriod,
    build_schedule,
    next_transition,
    parse_time,
    resolve_effective_setpoint,
)


# A fixed Monday (weekday() == 0) used as a base for time arithmetic.
MONDAY = datetime(2026, 1, 5)


def _at(hour: int, minute: int = 0, day_offset: int = 0) -> datetime:
    """Return a datetime at the given hour:minute on Monday + offset days."""
    return MONDAY + timedelta(days=day_offset, hours=hour, minutes=minute)


# ---------------------------------------------------------------------------
# parse_time
# ---------------------------------------------------------------------------


class TestParseTime:
    def test_parses_hh_mm(self):
        assert parse_time("22:00") == time(22, 0)
        assert parse_time("04:30") == time(4, 30)

    def test_parses_hh_mm_ss(self):
        assert parse_time("06:15:45") == time(6, 15, 45)

    def test_passes_through_existing_time(self):
        t = time(7, 30)
        assert parse_time(t) is t

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_time("not-a-time")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            parse_time("25:00")
        with pytest.raises(ValueError):
            parse_time("12:75")


# ---------------------------------------------------------------------------
# SchedulePeriod.matches
# ---------------------------------------------------------------------------


class TestSchedulePeriodMatches:
    def test_matches_inside_normal_window(self):
        period = SchedulePeriod(
            name="evening", start=time(18, 0), end=time(22, 0),
        )
        assert period.matches(_at(18, 0)) is True
        assert period.matches(_at(20, 30)) is True

    def test_does_not_match_at_end(self):
        period = SchedulePeriod(
            name="evening", start=time(18, 0), end=time(22, 0),
        )
        assert period.matches(_at(22, 0)) is False
        assert period.matches(_at(22, 30)) is False

    def test_does_not_match_before_start(self):
        period = SchedulePeriod(
            name="evening", start=time(18, 0), end=time(22, 0),
        )
        assert period.matches(_at(17, 59)) is False

    def test_wraps_past_midnight_first_half(self):
        period = SchedulePeriod(
            name="night", start=time(22, 0), end=time(4, 0),
        )
        # Monday 22:30 is in the night period (start-side).
        assert period.matches(_at(22, 30)) is True
        # Monday 23:59 still in.
        assert period.matches(_at(23, 59)) is True

    def test_wraps_past_midnight_second_half(self):
        period = SchedulePeriod(
            name="night", start=time(22, 0), end=time(4, 0),
        )
        # Tuesday 02:00 (1 day after Monday) is still in the wrap.
        assert period.matches(_at(2, 0, day_offset=1)) is True
        # Tuesday 03:59 in.
        assert period.matches(_at(3, 59, day_offset=1)) is True
        # Tuesday 04:00 out.
        assert period.matches(_at(4, 0, day_offset=1)) is False

    def test_wraps_past_midnight_outside_window(self):
        period = SchedulePeriod(
            name="night", start=time(22, 0), end=time(4, 0),
        )
        # Monday 12:00 is well outside.
        assert period.matches(_at(12, 0)) is False

    def test_day_filter_excludes_unlisted_weekdays(self):
        period = SchedulePeriod(
            name="workday", start=time(9, 0), end=time(17, 0),
            days=frozenset({0, 1, 2, 3, 4}),  # Mon-Fri
        )
        # Monday → in.
        assert period.matches(_at(10, 0)) is True
        # Saturday (offset 5) → out.
        assert period.matches(_at(10, 0, day_offset=5)) is False

    def test_wrap_around_uses_start_day_for_filter(self):
        """A night period starting Sunday should still match Monday early hours."""
        period = SchedulePeriod(
            name="weekend_night", start=time(23, 0), end=time(8, 0),
            days=frozenset({6}),  # Sunday only (weekday=6)
        )
        sunday_evening = _at(23, 30, day_offset=6)  # Monday + 6 = Sunday next week
        # Verify weekday assumption.
        assert sunday_evening.weekday() == 6
        assert period.matches(sunday_evening) is True
        # Monday 03:00 — previous day was Sunday → still in wrap.
        monday_morning = _at(3, 0, day_offset=7)
        assert monday_morning.weekday() == 0
        assert period.matches(monday_morning) is True
        # Tuesday 03:00 — previous day was Monday (not in filter) → out.
        tuesday_morning = _at(3, 0, day_offset=8)
        assert period.matches(tuesday_morning) is False


# ---------------------------------------------------------------------------
# RoomSchedule.active
# ---------------------------------------------------------------------------


class TestRoomScheduleActive:
    def test_returns_none_when_empty(self):
        sched = RoomSchedule()
        assert sched.active(_at(10, 0)) is None

    def test_returns_none_when_no_period_matches(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0)),
        ])
        assert sched.active(_at(12, 0)) is None

    def test_returns_first_matching_period(self):
        # Two overlapping periods — declaration order wins.
        sched = RoomSchedule(periods=[
            SchedulePeriod("workday_eco", time(9, 0), time(17, 0),
                           days=frozenset({0})),
            SchedulePeriod("daytime", time(8, 0), time(18, 0)),
        ])
        active = sched.active(_at(10, 0))
        assert active is not None
        assert active.name == "workday_eco"


# ---------------------------------------------------------------------------
# resolve_effective_setpoint
# ---------------------------------------------------------------------------


class TestResolveEffectiveSetpoint:
    def test_no_schedule_returns_base(self):
        sched = RoomSchedule()
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=20.0, now=_at(10, 0))
        assert result.setpoint == 21.0
        assert result.enabled is True
        assert result.period_name is None
        assert result.mode is None

    def test_no_active_period_returns_base(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0)),
        ])
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=20.0, now=_at(12, 0))
        assert result.setpoint == 21.0
        assert result.enabled is True

    def test_comfort_period_with_explicit_setpoint(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("workday_eco", time(9, 0), time(17, 0),
                           setpoint=18.0),
        ])
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=20.0, now=_at(12, 0))
        assert result.setpoint == 18.0
        assert result.enabled is True
        assert result.period_name == "workday_eco"
        assert result.mode == SCHEDULE_MODE_COMFORT

    def test_comfort_period_falls_back_to_base_setpoint(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("evening", time(18, 0), time(22, 0)),
        ])
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=20.0, now=_at(20, 0))
        assert result.setpoint == 21.0
        assert result.enabled is True

    def test_off_period_disables_room(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0),
                           mode=SCHEDULE_MODE_OFF, frost_protection=12.0),
        ])
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=18.0, now=_at(23, 0))
        assert result.enabled is False
        assert result.setpoint == 12.0  # frost-protection value reported
        assert result.period_name == "night"
        assert result.mode == SCHEDULE_MODE_OFF

    def test_off_period_frost_protection_trips(self):
        """Below the floor, the room is re-enabled to defend the floor."""
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0),
                           mode=SCHEDULE_MODE_OFF, frost_protection=12.0),
        ])
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=11.5, now=_at(23, 0))
        assert result.enabled is True
        assert result.setpoint == 12.0
        assert result.mode == SCHEDULE_MODE_OFF

    def test_off_period_with_missing_temperature_stays_off(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0),
                           mode=SCHEDULE_MODE_OFF, frost_protection=12.0),
        ])
        result = resolve_effective_setpoint(sched, base_setpoint=21.0,
                                            measured_temp=None, now=_at(23, 0))
        assert result.enabled is False


# ---------------------------------------------------------------------------
# build_schedule
# ---------------------------------------------------------------------------


class TestBuildSchedule:
    def test_empty_input_yields_empty_schedule(self):
        sched = build_schedule(None)
        assert sched.is_empty
        sched = build_schedule([])
        assert sched.is_empty

    def test_builds_minimal_period(self):
        sched = build_schedule([
            {"start": "22:00", "end": "04:00"},
        ])
        assert len(sched.periods) == 1
        period = sched.periods[0]
        assert period.start == time(22, 0)
        assert period.end == time(4, 0)
        assert period.mode == SCHEDULE_MODE_COMFORT
        assert period.frost_protection == DEFAULT_FROST_PROTECTION
        assert period.days == frozenset(range(7))
        assert period.setpoint is None
        # Auto-generated name when omitted.
        assert period.name == "period_1"

    def test_builds_full_off_period_with_days_filter(self):
        sched = build_schedule([
            {
                "name": "night",
                "start": "22:00",
                "end": "04:00",
                "mode": "off",
                "frost_protection": 10.0,
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
        ])
        period = sched.periods[0]
        assert period.name == "night"
        assert period.is_off
        assert period.frost_protection == 10.0
        assert period.days == frozenset({0, 1, 2, 3, 4})

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError):
            build_schedule([{"start": "08:00", "end": "10:00", "mode": "boost"}])

    def test_rejects_unknown_day(self):
        with pytest.raises(ValueError):
            build_schedule([
                {"start": "08:00", "end": "10:00", "days": ["monday", "funday"]},
            ])

    def test_rejects_missing_start(self):
        with pytest.raises(ValueError):
            build_schedule([{"end": "10:00"}])


# ---------------------------------------------------------------------------
# next_transition
# ---------------------------------------------------------------------------


class TestNextTransition:
    def test_returns_none_when_empty(self):
        assert next_transition(RoomSchedule(), _at(10, 0)) is None

    def test_finds_upcoming_start(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0)),
        ])
        # At 21:00 the next transition is the start of "night" at 22:00.
        target = next_transition(sched, _at(21, 0))
        assert target is not None
        assert target.hour == 22
        assert target.minute == 0

    def test_finds_upcoming_end(self):
        sched = RoomSchedule(periods=[
            SchedulePeriod("night", time(22, 0), time(4, 0)),
        ])
        # Already inside the period at 23:00 → next transition is the 04:00
        # end on the next day.
        target = next_transition(sched, _at(23, 0))
        assert target is not None
        assert target.hour == 4
        assert target.minute == 0
