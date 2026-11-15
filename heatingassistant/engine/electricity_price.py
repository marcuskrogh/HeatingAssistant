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
import statistics
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Tuple

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


# A tariff-less republish drops every overlapping slot at once. One changed
# hour must not trip the guard, so require a handful of shared stamps and a
# majority of them.
PRICE_TARIFF_MIN_OVERLAP = 8
PRICE_TARIFF_MOST_FRACTION = 0.5
# Hourly slots still cover "now". A stamp older than this does not.
PRICE_TARIFF_COVER_S = 3600.0

# consider() results
PRICE_GUARD_ACCEPT = "accept"
PRICE_GUARD_ENGAGE = "engage"
PRICE_GUARD_HOLD = "hold"
PRICE_GUARD_CLEAR = "clear"
PRICE_GUARD_EXPIRE = "expire"


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

    series = collect_price_series(dict(attrs), now)
    if not series:
        return None
    if series_covers_now(attrs, now):
        aligned = align_prices_to_horizon(series, now, 1, 900.0, 0.0, current_price=None)
        return float(aligned[0]) if aligned else None
    sorted_series = sorted(series, key=lambda tp: _as_utc(tp[0]))
    return float(sorted_series[0][1])


def _median_drop_is_tariff_strip(
    good: Mapping[str, Any],
    incoming: Mapping[str, Any],
    now: datetime,
    drop_fraction: float,
) -> bool:
    previous = _prices_by_stamp(good, now)
    current = _prices_by_stamp(incoming, now)
    drops: list[float] = []
    for stamp, old in previous.items():
        if stamp not in current or old <= 1e-9:
            continue
        drops.append((old - current[stamp]) / old)
    if len(drops) < PRICE_TARIFF_MIN_OVERLAP:
        return False
    over = sum(1 for drop in drops if drop > drop_fraction)
    median = float(statistics.median(drops))
    return median > drop_fraction and over / len(drops) >= PRICE_TARIFF_MOST_FRACTION


def _explicit_tariff_strip(good: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    previous = tariff_hour_map(good)
    current = tariff_hour_map(incoming)
    if previous is None or current is None:
        return False
    return len(previous) > 0 and len(current) == 0


class PriceTariffGuard:
    """Hold the last good price series across a tariff-less republish."""

    def __init__(self, *, drop_fraction: float, hold_s: float) -> None:
        self.drop_fraction = float(drop_fraction)
        self.hold_s = float(hold_s)
        self.good_attrs: Optional[dict[str, Any]] = None
        self.holding = False
        self.hold_started: Optional[float] = None
        self.engagements = 0

    def consider(
        self,
        attrs: Mapping[str, Any],
        now_ts: float,
        now: datetime,
    ) -> tuple[dict[str, Any], str]:
        """Return the attributes to store and one of the PRICE_GUARD_* events."""

        incoming = dict(attrs)
        if self.good_attrs is None:
            self.good_attrs = dict(incoming)
            self.holding = False
            self.hold_started = None
            return incoming, PRICE_GUARD_ACCEPT

        suspect = _explicit_tariff_strip(self.good_attrs, incoming) or _median_drop_is_tariff_strip(
            self.good_attrs,
            incoming,
            now,
            self.drop_fraction,
        )
        if not suspect:
            was_holding = self.holding
            self.holding = False
            self.hold_started = None
            self.good_attrs = dict(incoming)
            return incoming, PRICE_GUARD_CLEAR if was_holding else PRICE_GUARD_ACCEPT

        if not series_covers_now(self.good_attrs, now):
            self.holding = False
            self.hold_started = None
            self.good_attrs = dict(incoming)
            return incoming, PRICE_GUARD_EXPIRE

        if (
            self.holding
            and self.hold_started is not None
            and float(now_ts) - float(self.hold_started) >= self.hold_s
        ):
            self.holding = False
            self.hold_started = None
            self.good_attrs = dict(incoming)
            return incoming, PRICE_GUARD_EXPIRE

        if not self.holding:
            self.holding = True
            self.hold_started = float(now_ts)
            self.engagements += 1
            return dict(self.good_attrs), PRICE_GUARD_ENGAGE
        return dict(self.good_attrs), PRICE_GUARD_HOLD
