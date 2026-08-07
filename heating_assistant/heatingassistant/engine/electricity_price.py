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
    if series:
        return align_prices_to_horizon(series, now, horizon, dt_s, adder)

    # Fallback: repeat the current sensor state over the full horizon.
    try:
        current_price = float(state.state)
    except (TypeError, ValueError):
        return None
    clamped = max(0.0, current_price + adder)
    return [clamped] * horizon


def collect_price_series(
    attrs: dict,
    now: datetime,
) -> List[TimedPrice]:
    """Merge today, tomorrow, and forecast attributes into one timed series."""
    series: List[TimedPrice] = []

    raw_today = attrs.get("raw_today") or []
    raw_tomorrow = attrs.get("raw_tomorrow") or []
    today_raw = attrs.get("today") or attrs.get("prices_today") or []
    tomorrow_raw = attrs.get("tomorrow") or attrs.get("prices_tomorrow") or []
    forecast_raw = attrs.get("forecast") or []

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
) -> List[float]:
    """Map a merged timed series onto ``horizon`` MPC steps via zero-order hold."""
    if not series:
        return []

    sorted_series = sorted(series, key=lambda tp: tp[0])
    has_known_tomorrow = any(tp[3] == _SOURCE_TOMORROW for tp in sorted_series)
    aligned: List[float] = []
    for k in range(horizon):
        step_time = now + timedelta(seconds=dt_s * k)
        price = _lookup_price(sorted_series, step_time, now, has_known_tomorrow)
        aligned.append(max(0.0, price + adder))
    return aligned


def _lookup_price(
    series: Sequence[TimedPrice],
    step_time: datetime,
    now: datetime,
    has_known_tomorrow: bool,
) -> float:
    """Return the best price at ``step_time`` respecting day-scoped sources."""
    allowed = _allowed_sources(now, step_time, has_known_tomorrow)
    best_price: Optional[float] = None
    best_priority = -1
    best_start: Optional[datetime] = None

    for start_dt, price, priority, source in series:
        if source not in allowed:
            continue
        if start_dt > step_time:
            break
        if priority > best_priority or (
            priority == best_priority
            and (best_start is None or start_dt > best_start)
        ):
            best_price = price
            best_priority = priority
            best_start = start_dt

    if best_price is not None:
        return best_price

    # No matching entry in the allowed window — hold the last allowed price
    # if one exists before step_time (covers horizon beyond published data).
    for start_dt, price, priority, source in reversed(series):
        if source in allowed and start_dt <= step_time:
            return price

    # Horizon tail beyond all published days: hold the most recent known price.
    for start_dt, price, priority, source in reversed(series):
        if start_dt <= step_time:
            return price

    return series[-1][1]


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


def _extract_timestamp(entry: dict) -> Optional[datetime]:
    for key in _TIME_KEYS:
        raw = entry.get(key)
        if raw is None:
            continue
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        try:
            text = str(raw)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text)
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
