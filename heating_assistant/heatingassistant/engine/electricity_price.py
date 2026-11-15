"""Electricity price forecast readers for the MPC coordinator.

Reads spot-price data from a configured Home Assistant sensor and aligns it
to the MPC horizon steps.  Supports multiple upstream integration formats:

* **Nord Pool HACS** — ``raw_today`` / ``raw_tomorrow`` with ``start`` +
  ``value`` (hourly or 15-minute resolution), or plain ``today`` / ``tomorrow``
  lists.
* **Energi Data Service (EDS)** — ``raw_today`` / ``raw_tomorrow`` with
  ``hour`` + ``price`` (hourly mean or full quarterly resolution), plain
  ``today`` / ``tomorrow`` lists, and the optional five-day ``forecast``
  attribute (Carnot predictions).
* **Generic** — any sensor whose state is a numeric current price.

Known day-ahead prices (today and tomorrow) always take precedence over the
extended ``forecast`` predictions.  When tomorrow's known prices are not yet
published, tomorrow slots are filled from ``forecast`` until ``tomorrow`` or
``raw_tomorrow`` become available.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_STATES = ("unknown", "unavailable")

# Priority tiers — higher wins when multiple sources cover the same instant.
_PRIORITY_KNOWN = 2
_PRIORITY_FORECAST = 1

# Source buckets used to scope known prices to the correct calendar day.
_SOURCE_TODAY = "today"
_SOURCE_TOMORROW = "tomorrow"
_SOURCE_FORECAST = "forecast"

_PRICE_VALUE_KEYS = ("value", "price", "total")
_TIME_KEYS = ("start", "hour", "time", "from", "datetime", "period_start")

# Recognised list lengths → interval length [seconds].
_LIST_INTERVALS: Tuple[Tuple[int, float], ...] = (
    (24, 3600.0),    # hourly mean
    (25, 3600.0),    # DST spring-forward day
    (23, 3600.0),    # DST fall-back day
    (48, 1800.0),    # half-hourly
    (96, 900.0),     # quarterly (15 min)
    (100, 900.0),    # 25 h at 15 min (DST)
    (92, 900.0),     # 23 h at 15 min (DST)
)

# (start_dt, price, priority, source)
TimedPrice = Tuple[datetime, float, int, str]


def build_price_forecast(
    state: Any,
    *,
    now: datetime,
    horizon: int,
    dt_s: float,
    adder: float = 0.0,
) -> Optional[List[float]]:
    """Build an MPC-aligned price forecast from a HA entity state.

    Returns a list of ``horizon`` prices [currency/kWh] with ``adder`` applied
    and negative values clamped to zero, or ``None`` when the entity is
    missing or carries no usable price data.
    """
    if state is None or getattr(state, "state", None) in _UNAVAILABLE_STATES:
        return None

    attrs = getattr(state, "attributes", None) or {}
    series = collect_price_series(attrs, now)
    current_price = _sensor_price(getattr(state, "state", None))
    if series:
        return align_prices_to_horizon(
            series,
            now,
            horizon,
            dt_s,
            adder,
            current_price=current_price,
        )

    # Fallback: repeat the current sensor state over the full horizon.
    if current_price is None:
        return None
    clamped = max(0.0, current_price + adder)
    return [clamped] * horizon


def _sensor_price(state: Any) -> Optional[float]:
    """Return the sensor's numeric state, or None when it is not a price."""

    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def collect_price_series(
    attrs: dict,
    now: datetime,
) -> List[TimedPrice]:
    """Merge today, tomorrow, and forecast attributes into one timed series."""
    series: List[TimedPrice] = []

    raw_today = _as_price_entries(attrs.get("raw_today"))
    raw_tomorrow = _as_price_entries(attrs.get("raw_tomorrow"))
    today_raw = _as_price_entries(attrs.get("today") or attrs.get("prices_today"))
    tomorrow_raw = _as_price_entries(attrs.get("tomorrow") or attrs.get("prices_tomorrow"))
    forecast_raw = _as_price_entries(attrs.get("forecast"))

    if raw_today:
        series.extend(
            parse_timestamped_entries(raw_today, _PRIORITY_KNOWN, _SOURCE_TODAY)
        )
    if raw_tomorrow:
        series.extend(
            parse_timestamped_entries(
                raw_tomorrow, _PRIORITY_KNOWN, _SOURCE_TOMORROW
            )
        )
    if today_raw:
        series.extend(
            parse_price_list(
                today_raw, _local_day_start(now, 0), _PRIORITY_KNOWN, _SOURCE_TODAY
            )
        )
    if tomorrow_raw:
        series.extend(
            parse_price_list(
                tomorrow_raw,
                _local_day_start(now, 1),
                _PRIORITY_KNOWN,
                _SOURCE_TOMORROW,
            )
        )
    if forecast_raw:
        series.extend(
            parse_timestamped_entries(
                forecast_raw, _PRIORITY_FORECAST, _SOURCE_FORECAST
            )
        )

    if not series:
        return []

    series.sort(key=lambda tp: (tp[0], tp[2]))
    return series


def parse_timestamped_entries(
    entries: Any,
    priority: int,
    source: str,
) -> List[TimedPrice]:
    """Parse a list of dicts carrying an explicit timestamp and price."""
    if not isinstance(entries, (list, tuple)):
        return []

    out: List[TimedPrice] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = _extract_timestamp(entry)
        price = _extract_price_value(entry)
        if ts is None or price is None:
            continue
        out.append((ts, price, priority, source))
    return out


def parse_price_list(
    entries: Sequence[Any],
    day_start: datetime,
    priority: int,
    source: str,
) -> List[TimedPrice]:
    """Assign timestamps to a plain price list using inferred resolution."""
    prices = [_extract_price_value(e) for e in entries]
    prices = [p for p in prices if p is not None]
    if not prices:
        return []

    interval_s = infer_list_interval_seconds(len(prices))
    out: List[TimedPrice] = []
    for i, price in enumerate(prices):
        start_dt = day_start + timedelta(seconds=interval_s * i)
        out.append((start_dt, price, priority, source))
    return out


def infer_list_interval_seconds(n_entries: int) -> float:
    """Return the interval [s] implied by a day-ahead price list length."""
    for count, interval in _LIST_INTERVALS:
        if n_entries == count:
            return interval
    if n_entries <= 25:
        return 3600.0
    if n_entries <= 50:
        return 1800.0
    return 900.0


def align_prices_to_horizon(
    series: Sequence[TimedPrice],
    now: datetime,
    horizon: int,
    dt_s: float,
    adder: float,
    current_price: Optional[float] = None,
) -> List[float]:
    """Map a merged timed series onto ``horizon`` MPC steps via zero-order hold."""
    if not series:
        return []

    sorted_series = sorted(series, key=lambda tp: _as_utc(tp[0]))
    has_known_tomorrow = any(tp[3] == _SOURCE_TOMORROW for tp in sorted_series)
    aligned: List[float] = []
    for k in range(horizon):
        step_time = now + timedelta(seconds=dt_s * k)
        price = _lookup_price(
            sorted_series,
            step_time,
            now,
            has_known_tomorrow,
            current_price=current_price,
        )
        aligned.append(max(0.0, price + adder))
    return aligned


def _lookup_price(
    series: Sequence[TimedPrice],
    step_time: datetime,
    now: datetime,
    has_known_tomorrow: bool,
    current_price: Optional[float] = None,
) -> float:
    """Return the best price at ``step_time`` respecting day-scoped sources."""
    allowed = _allowed_sources(now, step_time, has_known_tomorrow)
    best_price: Optional[float] = None
    best_priority = -1
    best_start: Optional[datetime] = None

    step_time = _as_utc(step_time)
    for start_dt, price, priority, source in series:
        if source not in allowed:
            continue
        if _as_utc(start_dt) > step_time:
            break
        later = best_start is not None and _as_utc(start_dt) > _as_utc(best_start)
        if priority > best_priority or (priority == best_priority and (best_start is None or later)):
            best_price = price
            best_priority = priority
            best_start = start_dt

    if best_price is not None:
        return best_price

    # No matching entry in the allowed window — hold the last allowed price
    # if one exists before step_time (covers horizon beyond published data).
    for start_dt, price, priority, source in reversed(series):
        if source in allowed and _as_utc(start_dt) <= step_time:
            return price

    # Horizon tail beyond all published days: hold the most recent known price.
    for start_dt, price, priority, source in reversed(series):
        if _as_utc(start_dt) <= step_time:
            return price

    # The step is before every published stamp. The last entry is the furthest
    # future hour, so a forecast that starts tomorrow must not fill today.
    # Hold the caller-supplied price until the series actually starts.
    if current_price is not None:
        return float(current_price)
    return series[0][1]


def _allowed_sources(
    now: datetime,
    step_time: datetime,
    has_known_tomorrow: bool,
) -> frozenset[str]:
    """Return which source buckets may supply ``step_time`` relative to ``now``."""
    day_offset = _local_day_offset(now, step_time)
    if day_offset <= 0:
        return frozenset({_SOURCE_TODAY, _SOURCE_FORECAST})
    if day_offset == 1:
        if has_known_tomorrow:
            return frozenset({_SOURCE_TOMORROW})
        return frozenset({_SOURCE_FORECAST})
    return frozenset({_SOURCE_FORECAST})


def _local_day_offset(now: datetime, step_time: datetime) -> int:
    return (_local_date(step_time) - _local_date(now)).days


def _local_date(dt: datetime) -> date:
    return dt.astimezone().date()


def _as_utc(moment: datetime) -> datetime:
    """Return an aware UTC timestamp. A naive value is UTC, matching weather."""

    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _as_price_entries(raw: Any) -> list:
    """Normalise a price attribute to a list of entries.

    Nord Pool publishes lists. A map of timestamp to price, or timestamp to
    an entry, is the same series and must not be dropped.
    """

    if isinstance(raw, Mapping):
        rows = []
        for key, value in raw.items():
            if isinstance(value, Mapping):
                row = dict(value)
                if not any(row.get(time_key) is not None for time_key in _TIME_KEYS):
                    row["start"] = key
                rows.append(row)
            else:
                rows.append({"start": key, "value": value})
        return rows
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def _extract_timestamp(entry: dict) -> Optional[datetime]:
    for key in _TIME_KEYS:
        raw = entry.get(key)
        if raw is None:
            continue
        if isinstance(raw, datetime):
            return _as_utc(raw)
        try:
            text = str(raw)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return _as_utc(datetime.fromisoformat(text))
        except (TypeError, ValueError):
            continue
    return None


def _extract_price_value(entry: Any) -> Optional[float]:
    if isinstance(entry, (int, float)):
        try:
            return float(entry)
        except (TypeError, ValueError):
            return None
    if isinstance(entry, dict):
        for key in _PRICE_VALUE_KEYS:
            if key in entry and entry[key] is not None:
                try:
                    return float(entry[key])
                except (TypeError, ValueError):
                    continue
    return None


def _local_day_start(now: datetime, day_offset: int) -> datetime:
    local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=day_offset)


# A stamp older than this does not cover the current quarter.
PRICE_TARIFF_COVER_S = 3600.0
# Published consumer prices are DKK/kWh. A residual outside this band is not a spot price.
_SPOT_MIN = -0.05
_SPOT_MAX = 15.0
# Systemtariff notes, slugified the same way the integration publishes them.
_FIXED_ADDITION_KEYS = ("transmissions_nettarif", "systemtarif", "elafgift")
_COPENHAGEN = ZoneInfo("Europe/Copenhagen")
PRICE_LATCH_STATE_KEY = "price_day_latch"

# consider() results
PRICE_LATCH_PASSTHROUGH = "passthrough"
PRICE_LATCH_LATCHED = "latched"
PRICE_LATCH_IGNORED = "ignored"
PRICE_LATCH_AWAITING = "awaiting"


def tariff_hour_map(attrs: Mapping[str, Any]) -> Optional[dict]:
    """Return Energi Data Service hourly tariffs, or None when not forwarded.

    The integration publishes ``tariffs`` only while ``tariff_data`` is set.
    The shape is ``{"tariffs": {hour: price}, "additional_tariffs": {...}}``.
    An empty ``tariffs`` map is the "empty tariff dataset" path: prices are
    published with no tariff added. A missing attribute is not that signal.
    """

    raw = attrs.get("tariffs")
    if not isinstance(raw, Mapping):
        return None
    if "tariffs" in raw or "additional_tariffs" in raw:
        inner = raw.get("tariffs")
        if isinstance(inner, Mapping):
            return dict(inner)
        return {}
    return dict(raw)


def _prices_by_stamp(attrs: Mapping[str, Any], now: datetime) -> dict[datetime, float]:
    series = collect_price_series(dict(attrs), now)
    chosen: dict[datetime, tuple[int, float]] = {}
    for start_dt, price, priority, _source in series:
        key = _as_utc(start_dt).replace(microsecond=0)
        previous = chosen.get(key)
        if previous is None or priority >= previous[0]:
            chosen[key] = (priority, float(price))
    return {key: price for key, (_priority, price) in chosen.items()}


def series_covers_now(attrs: Mapping[str, Any], now: datetime) -> bool:
    """True when a published stamp is at or before ``now`` and still current."""

    now_utc = _as_utc(now)
    latest: Optional[datetime] = None
    for start_dt in _prices_by_stamp(attrs, now):
        if start_dt <= now_utc and (latest is None or start_dt > latest):
            latest = start_dt
    if latest is None:
        return False
    return (now_utc - latest).total_seconds() <= PRICE_TARIFF_COVER_S


def live_price_from_series(attrs: Mapping[str, Any], now: datetime) -> Optional[float]:
    """Price at ``now`` from ``attrs``, without using the sensor state."""

    if not series_covers_now(attrs, now):
        return None
    series = collect_price_series(dict(attrs), now)
    if not series:
        return None
    aligned = align_prices_to_horizon(series, now, 1, 900.0, 0.0, current_price=None)
    return float(aligned[0]) if aligned else None


def _copenhagen_date(stamp: datetime) -> date:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_COPENHAGEN)
    return stamp.astimezone(_COPENHAGEN).date()


def _row_date(row: Mapping[str, Any]) -> Optional[date]:
    stamp = _extract_timestamp(dict(row))
    if stamp is None:
        return None
    return _copenhagen_date(stamp)


def _dated_rows(raw: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw, list):
        return grouped
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        day = _row_date(row)
        if day is None:
            continue
        grouped.setdefault(day.isoformat(), []).append(dict(row))
    return grouped


def _hourly_tariffs(attrs: Mapping[str, Any]) -> Optional[dict[int, float]]:
    """24 hourly tariffs, or None. An empty map is the empty-dataset path."""

    hours = tariff_hour_map(attrs)
    if not hours:
        return None
    parsed: dict[int, float] = {}
    for key, value in hours.items():
        try:
            hour = int(str(key))
            amount = float(value)
        except (TypeError, ValueError):
            return None
        if hour < 0 or hour > 23 or amount <= 0.0:
            return None
        parsed[hour] = amount
    if set(parsed) != set(range(24)):
        return None
    return parsed


def _fixed_additions(attrs: Mapping[str, Any]) -> Optional[float]:
    """Sum of VAT-inclusive fixed additions, or None when one is missing."""

    raw = attrs.get("tariffs")
    if not isinstance(raw, Mapping):
        return None
    additions = raw.get("additional_tariffs")
    if not isinstance(additions, Mapping) or not additions:
        return None
    total = 0.0
    for key in _FIXED_ADDITION_KEYS:
        if key not in additions:
            return None
    for value in additions.values():
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        if amount <= 0.0:
            return None
        total += amount
    return total


def _sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_hour: dict[int, dict[str, Any]] = {}
    for row in rows:
        stamp = _extract_timestamp(row)
        if stamp is None:
            continue
        by_hour.setdefault(_local_date_hour(stamp), row)
    chosen = [by_hour[hour] for hour in (2, 12, 18) if hour in by_hour]
    if chosen:
        return chosen
    return list(by_hour.values())[:8]


def _local_date_hour(stamp: datetime) -> int:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_COPENHAGEN)
    return stamp.astimezone(_COPENHAGEN).hour


def _spot_matches(rows: list[dict[str, Any]], hourly: dict[int, float], additions: float, scale: float) -> bool:
    sampled = _sample_rows(rows)
    if not sampled:
        return False
    for row in sampled:
        stamp = _extract_timestamp(row)
        price = _extract_price_value(row)
        if stamp is None or price is None:
            return False
        spot = float(price) / scale - hourly[_local_date_hour(stamp)] - additions
        if spot < _SPOT_MIN or spot > _SPOT_MAX:
            return False
    return True


def validate_price_update(attrs: Mapping[str, Any]) -> tuple[bool, str]:
    """Return whether an Energi Data Service payload includes its tariffs.

    The published price is ``(spot + hourly tariff + fixed additions) * (1 + VAT)``.
    ``show_with_vat`` puts that same VAT on ``tariffs`` and ``additional_tariffs``,
    so the check is ``price - hourly[hour] - sum(additional_tariffs)`` on the
    attributes the App receives. The default cost template adds nothing.
    """

    hourly = _hourly_tariffs(attrs)
    if hourly is None:
        hours = tariff_hour_map(attrs)
        if hours is not None and len(hours) == 0:
            return False, "empty tariffs"
        return False, "tariffs"
    additions = _fixed_additions(attrs)
    if additions is None:
        return False, "additions"
    scale = 100.0 if attrs.get("use_cent") else 1.0
    blocks: list[list[dict[str, Any]]] = []
    for key in ("raw_today", "raw_tomorrow"):
        grouped = _dated_rows(attrs.get(key))
        for rows in grouped.values():
            blocks.append(rows)
    if not blocks:
        forecast_rows = [
            dict(row)
            for row in attrs.get("forecast") or []
            if isinstance(row, Mapping)
        ]
        if forecast_rows:
            blocks.append(forecast_rows)
    if not blocks:
        return False, "series"
    for rows in blocks:
        if not _spot_matches(rows, hourly, additions, scale):
            return False, "spot"
    today_rows = [
        row
        for rows in _dated_rows(attrs.get("raw_today")).values()
        for row in rows
    ]
    if today_rows and isinstance(attrs.get("today_mean"), (int, float)):
        prices = [price for row in today_rows if (price := _extract_price_value(row)) is not None]
        if prices and abs(sum(prices) / len(prices) - float(attrs["today_mean"])) > 0.05:
            return False, "today_mean"
    return True, ""


class LatchDecision:
    """What to store after one price update."""

    def __init__(self, attrs: Optional[dict[str, Any]], event: str, *, warn: bool, reason: str) -> None:
        self.attrs = attrs
        self.event = event
        self.warn = warn
        self.reason = reason


class PriceDayLatch:
    """Keep one validated price series per local day. Never overwrite it."""

    def __init__(self) -> None:
        self.days: dict[str, list[dict[str, Any]]] = {}
        self.forecast: list[dict[str, Any]] = []
        self.tariffs: Optional[dict[str, Any]] = None
        self.ignored = 0
        self.eds = False
        self.invalid_streak = False
        self.awaiting_valid = False
        self.live_fallback = False

    @classmethod
    def from_dict(cls, raw: Any) -> "PriceDayLatch":
        latch = cls()
        if not isinstance(raw, Mapping):
            return latch
        days = raw.get("days")
        if isinstance(days, Mapping):
            for key, rows in days.items():
                if isinstance(key, str) and isinstance(rows, list):
                    latch.days[key] = [dict(row) for row in rows if isinstance(row, Mapping)]
        forecast = raw.get("forecast")
        if isinstance(forecast, list):
            latch.forecast = [dict(row) for row in forecast if isinstance(row, Mapping)]
        tariffs = raw.get("tariffs")
        if isinstance(tariffs, Mapping):
            latch.tariffs = dict(tariffs)
        try:
            latch.ignored = int(raw.get("ignored") or 0)
        except (TypeError, ValueError):
            latch.ignored = 0
        latch.eds = bool(raw.get("eds")) or bool(latch.days) or bool(latch.forecast)
        latch.awaiting_valid = bool(latch.eds and not latch.days and not latch.forecast)
        return latch

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": {key: [dict(row) for row in rows] for key, rows in self.days.items()},
            "forecast": [dict(row) for row in self.forecast],
            "tariffs": dict(self.tariffs) if isinstance(self.tariffs, dict) else None,
            "ignored": int(self.ignored),
            "eds": bool(self.eds),
        }

    def _prune(self, now: datetime) -> None:
        cutoff = (_copenhagen_date(now) - timedelta(days=1)).isoformat()
        self.days = {key: rows for key, rows in self.days.items() if key >= cutoff}
        kept: list[dict[str, Any]] = []
        for row in self.forecast:
            day = _row_date(row)
            if day is not None and day.isoformat() < cutoff:
                continue
            kept.append(row)
        self.forecast = kept

    def compose(self, now: datetime) -> dict[str, Any]:
        """Attributes the planner should read. Latched days replace forecast days."""

        today = _copenhagen_date(now)
        tomorrow = today + timedelta(days=1)
        attrs: dict[str, Any] = {}
        today_rows = self.days.get(today.isoformat())
        tomorrow_rows = self.days.get(tomorrow.isoformat())
        if today_rows:
            attrs["raw_today"] = [dict(row) for row in today_rows]
        if tomorrow_rows:
            attrs["raw_tomorrow"] = [dict(row) for row in tomorrow_rows]
            attrs["tomorrow_valid"] = True
        if self.forecast:
            attrs["forecast"] = [dict(row) for row in self.forecast]
        if isinstance(self.tariffs, dict):
            attrs["tariffs"] = dict(self.tariffs)
        return attrs

    def covers(self, now: datetime) -> bool:
        return series_covers_now(self.compose(now), now)

    def consider(self, attrs: Mapping[str, Any], now: datetime) -> LatchDecision:
        """Accept a new day only when the payload validates. Latched days stay."""

        self._prune(now)
        if "tariffs" not in attrs and not self.eds:
            self.awaiting_valid = False
            self.live_fallback = False
            return LatchDecision(dict(attrs), PRICE_LATCH_PASSTHROUGH, warn=False, reason="")

        self.eds = True
        valid, reason = validate_price_update(attrs)
        if not valid:
            self.ignored += 1
            warn = not self.invalid_streak
            self.invalid_streak = True
            if self.days or self.forecast:
                self.awaiting_valid = False
                self.live_fallback = not self.covers(now)
                return LatchDecision(
                    self.compose(now),
                    PRICE_LATCH_IGNORED,
                    warn=warn,
                    reason=reason,
                )
            self.awaiting_valid = True
            self.live_fallback = False
            return LatchDecision(None, PRICE_LATCH_AWAITING, warn=warn, reason=reason)

        self.invalid_streak = False
        self.awaiting_valid = False
        if attrs.get("tomorrow_valid") is False:
            sources = ("raw_today",)
        else:
            sources = ("raw_today", "raw_tomorrow")
        for source in sources:
            for day, rows in _dated_rows(attrs.get(source)).items():
                if day not in self.days and rows:
                    self.days[day] = rows
        if isinstance(attrs.get("forecast"), list):
            self.forecast = []
            for row in attrs["forecast"]:
                if not isinstance(row, Mapping):
                    continue
                day = _row_date(row)
                if day is not None and day.isoformat() in self.days:
                    continue
                self.forecast.append(dict(row))
        if isinstance(attrs.get("tariffs"), Mapping):
            self.tariffs = dict(attrs["tariffs"])
        self._prune(now)
        self.live_fallback = not self.covers(now)
        return LatchDecision(self.compose(now), PRICE_LATCH_LATCHED, warn=False, reason="")
