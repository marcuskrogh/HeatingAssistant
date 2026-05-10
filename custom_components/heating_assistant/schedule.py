"""
Comfort schedule (time-of-day setpoint / setback) for the Heating Assistant.

Each room may declare a ``schedule`` of named periods.  At every coordinator
update tick the active period (if any) is resolved and used to derive the
room's *effective* setpoint and *enabled* flag.  Two modes are supported:

* ``comfort`` – the period contributes a setpoint (defaults to the room's
  base setpoint when omitted).  Heat sources keep running, the MPC just
  tracks the new reference.  Use this for setback values such as a daytime
  "eco" period where you want a cooler-but-controlled room.

* ``off`` – heat sources for the room are switched off for the duration of
  the period.  An optional ``frost_protection`` floor re-enables heating if
  the measured temperature drops below the configured value, so the room
  cannot freeze.  Use this for sleep / away periods where you want the
  heating source to genuinely stop running.

Example
-------
.. code-block:: yaml

    rooms:
      - name: living_room
        setpoint: 21.0
        schedule:
          - name: night
            start: "22:00"
            end: "04:00"
            mode: off
            frost_protection: 12.0
          - name: workday_eco
            start: "08:30"
            end: "16:00"
            days: [mon, tue, wed, thu, fri]
            setpoint: 18.0

The MPC's prediction horizon naturally produces a *preheat* trajectory:
when an upcoming period has a higher comfort setpoint than the current one,
the controller starts heating before the transition so the room reaches
the comfort temperature on time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .const import (
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_START,
    DEFAULT_FROST_PROTECTION,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
)


# Weekday short names accepted in the YAML ``days`` list (case-insensitive).
# Index matches Python's ``datetime.weekday()`` (Monday = 0).
WEEKDAY_NAMES: Tuple[str, ...] = (
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
)


def parse_time(value: str | time) -> time:
    """Parse a HH:MM (or HH:MM:SS) string into a ``datetime.time``.

    Accepts an existing ``time`` instance unchanged.
    """
    if isinstance(value, time):
        return value
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError(f"Schedule time must be HH:MM or HH:MM:SS; got {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"Schedule time must be numeric; got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"Schedule time out of range: {value!r}")
    return time(hour=hour, minute=minute, second=second)


def _parse_days(value: Optional[Sequence[str]]) -> frozenset[int]:
    """Convert a list of weekday short names into a set of weekday indices.

    ``None`` or empty means "every day".
    """
    if not value:
        return frozenset(range(7))
    days: set[int] = set()
    for raw in value:
        key = str(raw).strip().lower()[:3]
        if key not in WEEKDAY_NAMES:
            raise ValueError(
                f"Unknown weekday {raw!r}; expected one of {WEEKDAY_NAMES}"
            )
        days.add(WEEKDAY_NAMES.index(key))
    return frozenset(days)


@dataclass(frozen=True)
class SchedulePeriod:
    """A single time window in a room's comfort schedule."""

    name: str
    start: time
    end: time
    mode: str = SCHEDULE_MODE_COMFORT
    setpoint: Optional[float] = None
    frost_protection: float = DEFAULT_FROST_PROTECTION
    days: frozenset[int] = field(default_factory=lambda: frozenset(range(7)))

    @property
    def wraps_midnight(self) -> bool:
        """True when ``end`` is earlier-or-equal to ``start`` (e.g. 22:00 → 04:00)."""
        return self.end <= self.start

    @property
    def is_off(self) -> bool:
        """True when this period requests the heat sources to be switched off."""
        return self.mode == SCHEDULE_MODE_OFF

    def matches(self, now: datetime) -> bool:
        """Return True if ``now`` falls inside this period.

        Periods are inclusive on the start and exclusive on the end.  When the
        end is earlier than the start the period is interpreted as wrapping
        past midnight.  For wrapping periods, the ``days`` filter is applied
        to the *start* day, so a "night" period beginning Sunday 22:00 covers
        Monday 03:59 too.
        """
        wall = now.time()
        weekday = now.weekday()

        if self.wraps_midnight:
            # First half: from start until midnight on weekdays in ``days``.
            in_first_half = wall >= self.start and weekday in self.days
            # Second half: from midnight until end on the *following* weekday.
            prev_weekday = (weekday - 1) % 7
            in_second_half = wall < self.end and prev_weekday in self.days
            return in_first_half or in_second_half

        if weekday not in self.days:
            return False
        return self.start <= wall < self.end


@dataclass
class RoomSchedule:
    """A list of :class:`SchedulePeriod` objects attached to a room."""

    periods: List[SchedulePeriod] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.periods

    def active(self, now: datetime) -> Optional[SchedulePeriod]:
        """Return the first matching period, or ``None`` when none apply.

        Periods are evaluated in declaration order so users can put more
        specific rules (e.g. a workday eco period) ahead of more general
        ones (e.g. an everyday night period).
        """
        for period in self.periods:
            if period.matches(now):
                return period
        return None


def build_schedule(raw: Optional[Sequence[Dict]]) -> RoomSchedule:
    """Build a :class:`RoomSchedule` from the YAML / config-entry list.

    Unknown modes raise ``ValueError`` so configuration mistakes surface
    early rather than producing surprising runtime behaviour.
    """
    if not raw:
        return RoomSchedule()

    periods: List[SchedulePeriod] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Schedule entry #{idx} must be a mapping")
        name = str(entry.get(CONF_SCHEDULE_NAME) or f"period_{idx + 1}")
        try:
            start = parse_time(entry[CONF_SCHEDULE_START])
            end = parse_time(entry[CONF_SCHEDULE_END])
        except KeyError as exc:
            raise ValueError(
                f"Schedule entry {name!r} is missing required key {exc.args[0]!r}"
            ) from exc

        mode = str(entry.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_COMFORT)).lower()
        if mode not in (SCHEDULE_MODE_COMFORT, SCHEDULE_MODE_OFF):
            raise ValueError(
                f"Schedule entry {name!r} has unknown mode {mode!r}; "
                f"expected {SCHEDULE_MODE_COMFORT!r} or {SCHEDULE_MODE_OFF!r}"
            )

        setpoint = entry.get(CONF_SCHEDULE_SETPOINT)
        if setpoint is not None:
            setpoint = float(setpoint)

        frost = float(entry.get(CONF_SCHEDULE_FROST_PROTECTION, DEFAULT_FROST_PROTECTION))
        days = _parse_days(entry.get(CONF_SCHEDULE_DAYS))

        periods.append(
            SchedulePeriod(
                name=name,
                start=start,
                end=end,
                mode=mode,
                setpoint=setpoint,
                frost_protection=frost,
                days=days,
            )
        )

    return RoomSchedule(periods=periods)


@dataclass(frozen=True)
class EffectiveSetpoint:
    """The result of resolving a schedule for a single room at a single time.

    Attributes
    ----------
    setpoint : float
        The temperature [°C] the controller should track at this instant.
    enabled : bool
        Whether the room's heat sources are allowed to run.  ``False`` when
        an ``off`` period is active and frost protection is not triggered.
    period_name : str | None
        Name of the matched period for diagnostics, or ``None`` when no
        period matches.
    mode : str | None
        ``"comfort"`` / ``"off"`` for the matched period, or ``None``.
    """

    setpoint: float
    enabled: bool
    period_name: Optional[str] = None
    mode: Optional[str] = None


def resolve_effective_setpoint(
    schedule: RoomSchedule,
    base_setpoint: float,
    measured_temp: Optional[float],
    now: datetime,
) -> EffectiveSetpoint:
    """Resolve the effective setpoint and enabled flag for a single room.

    Parameters
    ----------
    schedule : RoomSchedule
        The room's schedule (may be empty).
    base_setpoint : float
        The room's user-chosen setpoint when no schedule period applies.
    measured_temp : float | None
        Most recent measured room temperature.  Used to evaluate the frost
        protection floor when an ``off`` period is active.  ``None`` is
        treated as "above the floor" so frost protection does not trip on
        a missing reading.
    now : datetime
        Reference time-of-day used to look up the active period.
    """
    if schedule.is_empty:
        return EffectiveSetpoint(
            setpoint=base_setpoint, enabled=True, period_name=None, mode=None,
        )

    period = schedule.active(now)
    if period is None:
        return EffectiveSetpoint(
            setpoint=base_setpoint, enabled=True, period_name=None, mode=None,
        )

    if period.is_off:
        # Frost protection: keep the room enabled and target the floor when
        # the measurement drops below it.  This way an "off" period never
        # lets pipes freeze.
        if measured_temp is not None and measured_temp <= period.frost_protection:
            return EffectiveSetpoint(
                setpoint=period.frost_protection,
                enabled=True,
                period_name=period.name,
                mode=period.mode,
            )
        return EffectiveSetpoint(
            setpoint=period.frost_protection,
            enabled=False,
            period_name=period.name,
            mode=period.mode,
        )

    setpoint = period.setpoint if period.setpoint is not None else base_setpoint
    return EffectiveSetpoint(
        setpoint=setpoint,
        enabled=True,
        period_name=period.name,
        mode=period.mode,
    )


def next_transition(
    schedule: RoomSchedule,
    now: datetime,
    horizon: timedelta = timedelta(hours=24),
) -> Optional[datetime]:
    """Return the next time any schedule boundary is crossed within ``horizon``.

    Useful for diagnostics — it tells the user when the next setback or
    comfort transition is going to occur.  Returns ``None`` if the schedule
    is empty or no transition is found in the search window.
    """
    if schedule.is_empty:
        return None

    # Probe every minute for up to ``horizon``.  This is bounded (1440 ticks
    # for the 24-hour default) and keeps the implementation independent of
    # the period semantics, including wrap-around.
    current_active = schedule.active(now)
    current_name = current_active.name if current_active else None
    step = timedelta(minutes=1)
    probe = now
    end = now + horizon
    while probe < end:
        probe += step
        active = schedule.active(probe)
        new_name = active.name if active else None
        if new_name != current_name:
            return probe
    return None
