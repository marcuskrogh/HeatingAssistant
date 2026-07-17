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

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .const import (
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_END_AT,
    CONF_SCHEDULE_END_DATE,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_START_AT,
    CONF_SCHEDULE_START_DATE,
    CONF_SCHEDULE_TIME_MODE,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_WHEN_BY_TYPE,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_FROST_PROTECTION,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SCHEDULE_TIME_MODE_ALL_DAY,
    SCHEDULE_TIME_MODE_WINDOW,
    SCHEDULE_TYPE_CONTINUOUS_SPAN,
    SCHEDULE_TYPE_DATE_RANGE_DAILY,
    SCHEDULE_TYPE_WEEKLY_RECURRING,
    SCHEDULE_TIME_MODES,
    SCHEDULE_TYPES,
)


_ALL_DAYS = frozenset(range(7))
_WHEN_FIELD_KEYS = frozenset(
    {
        CONF_SCHEDULE_DAYS,
        CONF_SCHEDULE_START,
        CONF_SCHEDULE_END,
        CONF_SCHEDULE_START_DATE,
        CONF_SCHEDULE_END_DATE,
        CONF_SCHEDULE_START_AT,
        CONF_SCHEDULE_END_AT,
        CONF_SCHEDULE_TIME_MODE,
    }
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


def _parse_date(value: str | date) -> date:
    """Parse an ISO date string (YYYY-MM-DD) into a ``date``."""
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Schedule date must be YYYY-MM-DD; got {value!r}") from exc


def _as_naive_local(value: datetime) -> datetime:
    """Return a naive local wall-clock ``datetime`` for schedule comparisons.

    Persisted continuous-span bounds are naive local ISO strings. Coordinator
    ``now`` is typically timezone-aware (``now_utc.astimezone()``). Comparing
    naive to aware raises ``TypeError`` in Python 3, which the schedule apply
    path swallows — continuous spans would never match.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _parse_datetime(value: str | datetime) -> datetime:
    """Parse a local ISO datetime string into a naive ``datetime``."""
    if isinstance(value, datetime):
        return _as_naive_local(value)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Schedule datetime must be ISO-8601 local time; got {value!r}"
        ) from exc
    return _as_naive_local(parsed)


def _parse_days(value: Optional[Sequence[str]]) -> frozenset[int]:
    """Convert a list of weekday short names or indices into a set of weekday indices.

    ``None`` or empty means "every day".
    Accepts both string names ("mon", "tue") and integer indices (0=Mon, 6=Sun).
    """
    if not value:
        return frozenset(range(7))
    days: set[int] = set()
    for raw in value:
        if isinstance(raw, int):
            if 0 <= raw <= 6:
                days.add(raw)
            else:
                raise ValueError(
                    f"Weekday index {raw} out of range; expected 0-6"
                )
        else:
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
    schedule_type: str
    mode: str = SCHEDULE_MODE_COMFORT
    setpoint: Optional[float] = None
    frost_protection: Optional[float] = None
    days: frozenset[int] = field(default_factory=lambda: _ALL_DAYS)
    comfort_offset: Optional[float] = None
    tracking_weight: Optional[float] = None
    energy_weight: Optional[float] = None
    enabled: bool = True
    time_mode: Optional[str] = SCHEDULE_TIME_MODE_WINDOW
    start: Optional[time] = None
    end: Optional[time] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    when_by_type: Optional[Dict] = field(default=None, repr=False, compare=False)
    preserved_when_fields: frozenset[str] = field(
        default_factory=frozenset, repr=False, compare=False
    )

    @property
    def wraps_midnight(self) -> bool:
        """True when ``end`` is earlier-or-equal to ``start`` (e.g. 22:00 → 04:00)."""
        if self.start is None or self.end is None:
            return False
        return self.end <= self.start

    @property
    def is_off(self) -> bool:
        """True when this period requests the heat sources to be switched off."""
        return self.mode == SCHEDULE_MODE_OFF

    def _date_in_range(self, when: date) -> bool:
        """Return True when ``when`` falls inside the date-range window."""
        if self.start_date is None or self.end_date is None:
            return False
        return self.start_date <= when <= self.end_date

    def _matches_time_of_day(self, now: datetime, *, apply_weekday_filter: bool) -> bool:
        """Evaluate the time-of-day window."""
        if self.start is None or self.end is None:
            return False
        wall = now.time()
        weekday = now.weekday()

        if self.wraps_midnight:
            if not apply_weekday_filter:
                return wall >= self.start or wall < self.end
            in_first_half = wall >= self.start and weekday in self.days
            prev_weekday = (weekday - 1) % 7
            in_second_half = wall < self.end and prev_weekday in self.days
            return in_first_half or in_second_half

        if apply_weekday_filter and weekday not in self.days:
            return False
        return self.start <= wall < self.end

    def _matches_weekly_recurring(self, now: datetime) -> bool:
        if self.time_mode == SCHEDULE_TIME_MODE_ALL_DAY:
            return now.weekday() in self.days
        return self._matches_time_of_day(now, apply_weekday_filter=True)

    def _matches_date_range_daily(self, now: datetime) -> bool:
        if not self._date_in_range(now.date()):
            return False
        if self.time_mode == SCHEDULE_TIME_MODE_ALL_DAY:
            return True
        return self._matches_time_of_day(now, apply_weekday_filter=False)

    def _matches_continuous_span(self, now: datetime) -> bool:
        if self.start_at is None or self.end_at is None:
            return False
        local_now = _as_naive_local(now)
        start_at = _as_naive_local(self.start_at)
        end_at = _as_naive_local(self.end_at)
        return start_at <= local_now < end_at

    def matches(self, now: datetime) -> bool:
        """Return True if ``now`` falls inside this period.

        Periods are inclusive on the start and exclusive on the end.
        """
        if not self.enabled:
            return False

        if self.schedule_type == SCHEDULE_TYPE_WEEKLY_RECURRING:
            return self._matches_weekly_recurring(now)
        if self.schedule_type == SCHEDULE_TYPE_DATE_RANGE_DAILY:
            return self._matches_date_range_daily(now)
        if self.schedule_type == SCHEDULE_TYPE_CONTINUOUS_SPAN:
            return self._matches_continuous_span(now)
        return False


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


def _parse_optional_weights(
    entry: Dict,
    name: str,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    comfort_offset = entry.get(CONF_SCHEDULE_COMFORT_OFFSET)
    if comfort_offset is not None:
        comfort_offset = float(comfort_offset)
        if comfort_offset <= 0:
            raise ValueError(
                f"Schedule entry {name!r}: comfort_offset must be > 0; "
                f"got {comfort_offset}"
            )

    tracking_weight = entry.get(CONF_SCHEDULE_TRACKING_WEIGHT)
    if tracking_weight is not None:
        tracking_weight = float(tracking_weight)
        if tracking_weight < 0:
            raise ValueError(
                f"Schedule entry {name!r}: tracking_weight must be >= 0; "
                f"got {tracking_weight}"
            )

    energy_weight = entry.get(CONF_SCHEDULE_ENERGY_WEIGHT)
    if energy_weight is not None:
        energy_weight = float(energy_weight)
        if energy_weight < 0:
            raise ValueError(
                f"Schedule entry {name!r}: energy_weight must be >= 0; "
                f"got {energy_weight}"
            )

    return comfort_offset, tracking_weight, energy_weight


def period_to_dict(period: SchedulePeriod) -> Dict:
    """Serialize a :class:`SchedulePeriod` for persistence / UI consumers."""
    payload: Dict = {
        CONF_SCHEDULE_NAME: period.name,
        CONF_SCHEDULE_TYPE: period.schedule_type,
        CONF_SCHEDULE_MODE: period.mode,
        CONF_SCHEDULE_ENABLED: period.enabled,
    }
    if period.setpoint is not None:
        payload[CONF_SCHEDULE_SETPOINT] = period.setpoint
    if period.frost_protection is not None:
        payload[CONF_SCHEDULE_FROST_PROTECTION] = period.frost_protection
    if period.comfort_offset is not None:
        payload[CONF_SCHEDULE_COMFORT_OFFSET] = period.comfort_offset
    if period.tracking_weight is not None:
        payload[CONF_SCHEDULE_TRACKING_WEIGHT] = period.tracking_weight
    if period.energy_weight is not None:
        payload[CONF_SCHEDULE_ENERGY_WEIGHT] = period.energy_weight

    if period.schedule_type in (
        SCHEDULE_TYPE_WEEKLY_RECURRING,
        SCHEDULE_TYPE_DATE_RANGE_DAILY,
    ):
        if period.time_mode == SCHEDULE_TIME_MODE_WINDOW:
            if period.start is None or period.end is None:
                raise ValueError(
                    f"Schedule entry {period.name!r}: window mode requires "
                    f"{CONF_SCHEDULE_START!r} and {CONF_SCHEDULE_END!r}"
                )
        if period.schedule_type == SCHEDULE_TYPE_DATE_RANGE_DAILY:
            if period.start_date is None or period.end_date is None:
                raise ValueError(
                    f"Schedule entry {period.name!r}: date_range_daily requires "
                    f"{CONF_SCHEDULE_START_DATE!r} and {CONF_SCHEDULE_END_DATE!r}"
                )
    elif period.schedule_type == SCHEDULE_TYPE_CONTINUOUS_SPAN:
        if period.start_at is None or period.end_at is None:
            raise ValueError(
                f"Schedule entry {period.name!r}: continuous_span requires "
                f"{CONF_SCHEDULE_START_AT!r} and {CONF_SCHEDULE_END_AT!r}"
            )
    if (
        period.schedule_type
        in (SCHEDULE_TYPE_WEEKLY_RECURRING, SCHEDULE_TYPE_DATE_RANGE_DAILY)
        or CONF_SCHEDULE_TIME_MODE in period.preserved_when_fields
    ) and period.time_mode is not None:
        payload[CONF_SCHEDULE_TIME_MODE] = period.time_mode
    if period.start is not None:
        payload[CONF_SCHEDULE_START] = period.start.strftime("%H:%M")
    if period.end is not None:
        payload[CONF_SCHEDULE_END] = period.end.strftime("%H:%M")
    if (
        period.schedule_type == SCHEDULE_TYPE_WEEKLY_RECURRING
        or CONF_SCHEDULE_DAYS in period.preserved_when_fields
    ):
        payload[CONF_SCHEDULE_DAYS] = sorted(period.days)
    if period.start_date is not None:
        payload[CONF_SCHEDULE_START_DATE] = period.start_date.isoformat()
    if period.end_date is not None:
        payload[CONF_SCHEDULE_END_DATE] = period.end_date.isoformat()
    if period.start_at is not None:
        payload[CONF_SCHEDULE_START_AT] = period.start_at.strftime("%Y-%m-%dT%H:%M:%S")
    if period.end_at is not None:
        payload[CONF_SCHEDULE_END_AT] = period.end_at.strftime("%Y-%m-%dT%H:%M:%S")
    if period.when_by_type:
        payload[CONF_SCHEDULE_WHEN_BY_TYPE] = deepcopy(period.when_by_type)

    return payload


def _parse_optional_time(entry: Dict, key: str) -> Optional[time]:
    if key not in entry or entry.get(key) is None:
        return None
    return parse_time(entry[key])


def _parse_optional_date(entry: Dict, key: str) -> Optional[date]:
    if key not in entry or entry.get(key) is None:
        return None
    return _parse_date(entry[key])


def _parse_optional_datetime(entry: Dict, key: str) -> Optional[datetime]:
    if key not in entry or entry.get(key) is None:
        return None
    return _parse_datetime(entry[key])


def _parse_time_mode(
    entry: Dict,
    name: str,
    schedule_type: str,
) -> Optional[str]:
    if (
        schedule_type
        not in (SCHEDULE_TYPE_WEEKLY_RECURRING, SCHEDULE_TYPE_DATE_RANGE_DAILY)
        and CONF_SCHEDULE_TIME_MODE not in entry
    ):
        return None

    time_mode = entry.get(CONF_SCHEDULE_TIME_MODE, SCHEDULE_TIME_MODE_WINDOW)
    if time_mode not in SCHEDULE_TIME_MODES:
        raise ValueError(
            f"Schedule entry {name!r}: {CONF_SCHEDULE_TIME_MODE!r} must be "
            f"one of {SCHEDULE_TIME_MODES}; got {time_mode!r}"
        )
    return time_mode


def build_schedule(raw: Optional[Sequence[Dict]]) -> RoomSchedule:
    """Build a :class:`RoomSchedule` from persisted period definitions.

    Every period must include ``schedule_type``.  Legacy ``recurring`` /
    ``all_day`` payloads must be migrated before calling this function.
    """
    if not raw:
        return RoomSchedule()

    periods: List[SchedulePeriod] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Schedule entry #{idx} must be a mapping")
        name = str(entry.get(CONF_SCHEDULE_NAME) or f"period_{idx + 1}")

        schedule_type = entry.get(CONF_SCHEDULE_TYPE)
        if schedule_type not in SCHEDULE_TYPES:
            raise ValueError(
                f"Schedule entry {name!r} requires {CONF_SCHEDULE_TYPE!r} "
                f"to be one of {SCHEDULE_TYPES}; got {schedule_type!r}"
            )

        mode = str(entry.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_COMFORT)).lower()
        if mode not in (SCHEDULE_MODE_COMFORT, SCHEDULE_MODE_OFF):
            raise ValueError(
                f"Schedule entry {name!r} has unknown mode {mode!r}; "
                f"expected {SCHEDULE_MODE_COMFORT!r} or {SCHEDULE_MODE_OFF!r}"
            )

        setpoint = entry.get(CONF_SCHEDULE_SETPOINT)
        if setpoint is not None:
            setpoint = float(setpoint)

        frost_raw = entry.get(CONF_SCHEDULE_FROST_PROTECTION)
        frost = float(frost_raw) if frost_raw is not None else None
        comfort_offset, tracking_weight, energy_weight = _parse_optional_weights(
            entry, name
        )
        enabled = bool(entry.get(CONF_SCHEDULE_ENABLED, True))

        common = dict(
            name=name,
            schedule_type=schedule_type,
            mode=mode,
            setpoint=setpoint,
            frost_protection=frost,
            comfort_offset=comfort_offset,
            tracking_weight=tracking_weight,
            energy_weight=energy_weight,
            enabled=enabled,
        )

        preserved_when_fields = frozenset(
            key for key in _WHEN_FIELD_KEYS if key in entry
        )
        time_mode = _parse_time_mode(entry, name, schedule_type)
        days = (
            _parse_days(entry.get(CONF_SCHEDULE_DAYS))
            if schedule_type == SCHEDULE_TYPE_WEEKLY_RECURRING
            or CONF_SCHEDULE_DAYS in entry
            else _ALL_DAYS
        )
        start = _parse_optional_time(entry, CONF_SCHEDULE_START)
        end = _parse_optional_time(entry, CONF_SCHEDULE_END)
        start_date = _parse_optional_date(entry, CONF_SCHEDULE_START_DATE)
        end_date = _parse_optional_date(entry, CONF_SCHEDULE_END_DATE)
        start_at = _parse_optional_datetime(entry, CONF_SCHEDULE_START_AT)
        end_at = _parse_optional_datetime(entry, CONF_SCHEDULE_END_AT)

        if schedule_type in (
            SCHEDULE_TYPE_WEEKLY_RECURRING,
            SCHEDULE_TYPE_DATE_RANGE_DAILY,
        ) and time_mode == SCHEDULE_TIME_MODE_WINDOW:
            missing = [
                key
                for key, value in (
                    (CONF_SCHEDULE_START, start),
                    (CONF_SCHEDULE_END, end),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"Schedule entry {name!r} is missing required key {missing[0]!r}"
                )

        if schedule_type == SCHEDULE_TYPE_DATE_RANGE_DAILY:
            missing = [
                key
                for key, value in (
                    (CONF_SCHEDULE_START_DATE, start_date),
                    (CONF_SCHEDULE_END_DATE, end_date),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"Schedule entry {name!r} is missing required key {missing[0]!r}"
                )
            if end_date < start_date:
                raise ValueError(
                    f"Schedule entry {name!r}: end_date must be on or after start_date"
                )

        if schedule_type == SCHEDULE_TYPE_CONTINUOUS_SPAN:
            missing = [
                key
                for key, value in (
                    (CONF_SCHEDULE_START_AT, start_at),
                    (CONF_SCHEDULE_END_AT, end_at),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"Schedule entry {name!r} is missing required key {missing[0]!r}"
                )
            if end_at <= start_at:
                raise ValueError(
                    f"Schedule entry {name!r}: end_at must be after start_at"
                )

        periods.append(
            SchedulePeriod(
                **common,
                time_mode=time_mode,
                days=days,
                start=start,
                end=end,
                start_date=start_date,
                end_date=end_date,
                start_at=start_at,
                end_at=end_at,
                when_by_type=(
                    deepcopy(entry[CONF_SCHEDULE_WHEN_BY_TYPE])
                    if isinstance(entry.get(CONF_SCHEDULE_WHEN_BY_TYPE), dict)
                    else None
                ),
                preserved_when_fields=preserved_when_fields,
            )
        )

    return RoomSchedule(periods=periods)


@dataclass(frozen=True)
class EffectiveControlParams:
    """The result of resolving a schedule for a single room at a single time.

    Attributes
    ----------
    setpoint : float
        The temperature [°C] the controller should track at this instant.
    comfort_offset : float
        Soft constraint corridor half-width [°C] for this period.
    tracking_weight : float
        Multiplier on the global Q (setpoint-tracking aggressiveness).
        1.0 means unchanged; 2.0 means twice as aggressive.
    energy_weight : float
        Multiplier on the global R (energy-use penalty).
        1.0 means unchanged; 2.0 means twice as expensive.
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
    comfort_offset: float
    tracking_weight: float
    energy_weight: float
    enabled: bool
    period_name: Optional[str] = None
    mode: Optional[str] = None


# Backward-compatible alias used by existing callers that only need setpoint/enabled.
EffectiveSetpoint = EffectiveControlParams


def resolve_effective_control_params(
    schedule: RoomSchedule,
    base_setpoint: float,
    measured_temp: Optional[float],
    now: datetime,
    default_comfort_offset: float = DEFAULT_COMFORT_OFFSET,
    default_tracking_weight: float = 1.0,
    default_energy_weight: float = 1.0,
) -> EffectiveControlParams:
    """Resolve the full set of effective control parameters for a single room.

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
    default_comfort_offset : float
        Room-level comfort corridor half-width to fall back to when the
        active period does not specify one.
    default_tracking_weight : float
        Global Q multiplier to use when the active period does not specify
        one.  Defaults to 1.0 (no change from global setting).
    default_energy_weight : float
        Global R multiplier to use when the active period does not specify
        one.  Defaults to 1.0 (no change from global setting).
    """
    if schedule.is_empty:
        return EffectiveControlParams(
            setpoint=base_setpoint,
            comfort_offset=default_comfort_offset,
            tracking_weight=default_tracking_weight,
            energy_weight=default_energy_weight,
            enabled=True,
            period_name=None,
            mode=None,
        )

    period = schedule.active(now)
    if period is None:
        return EffectiveControlParams(
            setpoint=base_setpoint,
            comfort_offset=default_comfort_offset,
            tracking_weight=default_tracking_weight,
            energy_weight=default_energy_weight,
            enabled=True,
            period_name=None,
            mode=None,
        )

    if period.is_off:
        frost_floor = (
            period.frost_protection
            if period.frost_protection is not None
            else DEFAULT_FROST_PROTECTION
        )
        # Frost protection: keep the room enabled and target the floor when
        # the measurement drops below it.  This way an "off" period never
        # lets pipes freeze.
        if measured_temp is not None and measured_temp <= frost_floor:
            return EffectiveControlParams(
                setpoint=frost_floor,
                comfort_offset=default_comfort_offset,
                tracking_weight=default_tracking_weight,
                energy_weight=default_energy_weight,
                enabled=True,
                period_name=period.name,
                mode=period.mode,
            )
        return EffectiveControlParams(
            setpoint=frost_floor,
            comfort_offset=default_comfort_offset,
            tracking_weight=default_tracking_weight,
            energy_weight=default_energy_weight,
            enabled=False,
            period_name=period.name,
            mode=period.mode,
        )

    setpoint = period.setpoint if period.setpoint is not None else base_setpoint
    comfort_offset = (
        period.comfort_offset
        if period.comfort_offset is not None
        else default_comfort_offset
    )
    tracking_weight = (
        period.tracking_weight
        if period.tracking_weight is not None
        else default_tracking_weight
    )
    energy_weight = (
        period.energy_weight
        if period.energy_weight is not None
        else default_energy_weight
    )
    return EffectiveControlParams(
        setpoint=setpoint,
        comfort_offset=comfort_offset,
        tracking_weight=tracking_weight,
        energy_weight=energy_weight,
        enabled=True,
        period_name=period.name,
        mode=period.mode,
    )


def resolve_effective_setpoint(
    schedule: RoomSchedule,
    base_setpoint: float,
    measured_temp: Optional[float],
    now: datetime,
    default_comfort_offset: float = DEFAULT_COMFORT_OFFSET,
    default_tracking_weight: float = 1.0,
    default_energy_weight: float = 1.0,
) -> EffectiveControlParams:
    """Backward-compatible wrapper around :func:`resolve_effective_control_params`."""
    return resolve_effective_control_params(
        schedule=schedule,
        base_setpoint=base_setpoint,
        measured_temp=measured_temp,
        now=now,
        default_comfort_offset=default_comfort_offset,
        default_tracking_weight=default_tracking_weight,
        default_energy_weight=default_energy_weight,
    )


def control_params_at(
    schedule: RoomSchedule,
    base_setpoint: float,
    t_future: datetime,
    default_comfort_offset: float = DEFAULT_COMFORT_OFFSET,
    default_tracking_weight: float = 1.0,
    default_energy_weight: float = 1.0,
) -> Optional[EffectiveControlParams]:
    """Return effective comfort control parameters at a future time.

    Returns ``None`` when ``t_future`` falls in an ``off`` period — the
    caller is responsible for carry-forward logic in that case.  No frost
    protection evaluation is performed (measured temperature is unknown
    for future instants).
    """
    if schedule is None or schedule.is_empty:
        return EffectiveControlParams(
            setpoint=base_setpoint,
            comfort_offset=default_comfort_offset,
            tracking_weight=default_tracking_weight,
            energy_weight=default_energy_weight,
            enabled=True,
        )

    period = schedule.active(t_future)
    if period is None:
        return EffectiveControlParams(
            setpoint=base_setpoint,
            comfort_offset=default_comfort_offset,
            tracking_weight=default_tracking_weight,
            energy_weight=default_energy_weight,
            enabled=True,
        )

    if period.is_off:
        return None  # caller applies carry-forward

    setpoint = period.setpoint if period.setpoint is not None else base_setpoint
    comfort_offset = (
        period.comfort_offset
        if period.comfort_offset is not None
        else default_comfort_offset
    )
    tracking_weight = (
        period.tracking_weight
        if period.tracking_weight is not None
        else default_tracking_weight
    )
    energy_weight = (
        period.energy_weight
        if period.energy_weight is not None
        else default_energy_weight
    )
    return EffectiveControlParams(
        setpoint=setpoint,
        comfort_offset=comfort_offset,
        tracking_weight=tracking_weight,
        energy_weight=energy_weight,
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
