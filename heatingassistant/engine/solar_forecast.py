"""Solar-radiation (irradiance) forecast readers.

Reads a **solar irradiance forecast** — Global Horizontal Irradiance (GHI) in
W/m² — from a configured Home Assistant sensor and aligns it to the MPC steps.
This is the *intensity* the solar model consumes in place of its modelled
clear-sky irradiance: the model then decomposes the GHI into beam/diffuse
(Erbs) and transposes it onto each window using geometry alone (see
:mod:`solar_model`).  The forecast therefore supplies the magnitude of the
sun's radiation — including real cloud, haze, and the cloud-driven shift toward
diffuse light — while the model supplies only the geometry that distributes it.

This has nothing to do with solar panels or PV power: the input is the sun's
radiation in W/m², not panel production.  Sources include the Open-Meteo
``shortwave_radiation`` variable, a pyranometer, or any sensor reporting solar
irradiance, ideally with an hourly forecast in its attributes.

Like :mod:`weather`, the functions take ``hass`` / state objects and avoid Home
Assistant imports so they test under the conftest stubs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from .weather import coerce_float, interpolate_forecast, parse_ts

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_STATES = ("unknown", "unavailable")

#: Default age beyond which a forecast entity is considered stale.
STALE_SECONDS_DEFAULT = 3 * 3600

# Attribute names scanned for an irradiance forecast series, and the per-entry
# key candidates for list-of-dict payloads.  All values are interpreted as
# solar irradiance in W/m².
_FORECAST_ATTR_NAMES = (
    "forecast",
    "forecasts",
    "shortwave_radiation",
    "ghi",
    "irradiance",
    "radiation",
    "detailedForecast",
    "data",
    "entries",
)
_TS_KEYS = ("datetime", "period_start", "time", "timestamp")
_VALUE_KEYS = ("ghi", "shortwave_radiation", "irradiance", "radiation", "value")


# ---------------------------------------------------------------------------
# Forecast parsing
# ---------------------------------------------------------------------------


def parse_irradiance_forecast(
    state: Any,
) -> Optional[List[Tuple[float, float]]]:
    """Parse an irradiance forecast series ``[(epoch, W/m²)]`` from a state.

    Accepts a forecast exposed as a ``{ISO-timestamp: value}`` dict or a list of
    ``{<time-key>: ..., <value-key>: ...}`` entries under any of the recognised
    attribute names.  Returns a sorted series, or ``None`` when nothing usable
    is present (the entity's current state is read separately).
    """
    attrs = getattr(state, "attributes", None) or {}
    for name in _FORECAST_ATTR_NAMES:
        payload = attrs.get(name)

        if isinstance(payload, dict) and payload:
            out: List[Tuple[float, float]] = []
            for ts_raw, val in payload.items():
                ts = parse_ts(ts_raw)
                v = coerce_float(val)
                if ts is not None and v is not None:
                    out.append((ts, max(0.0, v)))
            if out:
                out.sort(key=lambda e: e[0])
                return out

        if isinstance(payload, list) and payload:
            out = []
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                ts = next(
                    (parse_ts(entry[k]) for k in _TS_KEYS if k in entry), None
                )
                v = next(
                    (coerce_float(entry[k]) for k in _VALUE_KEYS if k in entry), None
                )
                if ts is not None and v is not None:
                    out.append((ts, max(0.0, v)))
            if out:
                out.sort(key=lambda e: e[0])
                return out
    return None


# ---------------------------------------------------------------------------
# Entity readers
# ---------------------------------------------------------------------------


def read_irradiance_now(
    hass: Any,
    entity_id: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Return the current solar irradiance [W/m²] or ``None``.

    Prefers the instantaneous state; falls back to interpolating the parsed
    forecast series at ``now``.
    """
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None

    val = coerce_float(state.state)
    if val is not None:
        return max(0.0, val)

    series = parse_irradiance_forecast(state)
    if series:
        if now is None:
            now = datetime.now(tz=timezone.utc)
        return max(0.0, interpolate_forecast(series, now.timestamp()))
    return None


# ---------------------------------------------------------------------------
# GHI series builder
# ---------------------------------------------------------------------------


def compute_ghi_series(
    series: Optional[List[Tuple[float, float]]],
    value_now: Optional[float],
    horizon: int,
    dt: float,
    now: datetime,
) -> Tuple[Optional[List[Optional[float]]], Optional[float]]:
    """Align an irradiance forecast to the MPC steps.

    Returns ``(ghi_series, ghi_now)`` where ``ghi_series`` has length
    ``horizon``, aligned to ``now + dt·(k+1)`` [W/m²], with ``None`` for steps
    outside the forecast's time coverage (so the controller falls back to the
    clear-sky model there).  Returns ``(None, None)`` when no usable data.

    GHI is unit-agnostic to geometry here — the beam/diffuse decomposition and
    transposition onto windows happen later in :mod:`solar_model`, where the sun
    position is known.
    """
    if not series and value_now is None:
        return None, None

    now_ts = now.timestamp()
    entries: List[Tuple[float, float]] = list(series or [])

    ghi_now: Optional[float] = None
    if value_now is not None:
        ghi_now = max(0.0, value_now)
        entries.append((now_ts, ghi_now))

    if not entries:
        return None, ghi_now
    entries.sort(key=lambda e: e[0])

    first_ts, last_ts = entries[0][0], entries[-1][0]
    out: List[Optional[float]] = []
    for k in range(horizon):
        t_k = now_ts + dt * (k + 1)
        if first_ts <= t_k <= last_ts:
            out.append(interpolate_forecast(entries, t_k))
        else:
            # Outside forecast coverage → no data; fall back per-step.
            out.append(None)
    return out, ghi_now


def select_ghi_for_step(
    ghi_forecast: Optional[List[Optional[float]]],
    k: int,
    fallback: Optional[float] = None,
) -> Optional[float]:
    """Pick the GHI for horizon step ``k``.

    Returns ``ghi_forecast[k]`` when present and non-``None``. Missing slots,
    out-of-range indices, and an empty forecast return ``None`` so the caller
    takes the analytical cloud/clear-sky path. Unlike cloud cover, GHI is
    **not** persisted past the forecast's coverage — a stale daytime value
    must not leak into the night.

    ``fallback`` is accepted for call-site compatibility and is ignored.
    Passing a numeric fallback would leak current GHI into later steps.
    """
    del fallback
    if not ghi_forecast:
        return None
    if 0 <= k < len(ghi_forecast):
        return ghi_forecast[k]
    return None


def is_stale(state: Any, now: datetime, max_age: float = STALE_SECONDS_DEFAULT) -> bool:
    """Return True when the entity's ``last_updated`` is older than ``max_age``."""
    last = getattr(state, "last_updated", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) > timedelta(seconds=max_age)
