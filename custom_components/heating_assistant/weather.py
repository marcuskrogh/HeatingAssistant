"""Weather-entity readers and forecast parsing for the coordinator.

The coordinator used to inline the entire weather-fetch path: reading the
current outdoor temperature, fetching the forecast (with both the modern
``weather.get_forecasts`` service and the deprecated state-attribute fallback),
parsing per-entry payloads, and interpolating the parsed series to MPC steps.
All of that lives here as pure-ish functions so the coordinator's
``_async_update_data`` is short enough to read top-to-bottom.

The functions are written to accept ``hass`` and entity-id arguments rather
than reach into a coordinator instance, which keeps them callable from tests
without a fake coordinator.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


def smooth_cloud_cover_step(
    prev: Optional[float],
    obs: Optional[float],
    dt: float,
    tau: float,
) -> Optional[float]:
    """One exponential-moving-average step for the live cloud-cover fraction.

    Clouds change gradually, so the instantaneous weather reading is low-pass
    filtered with a time constant ``tau`` (seconds), made dt-aware via
    ``alpha = 1 - exp(-dt / tau)``.

    * ``obs`` is clamped to ``[0, 1]``.
    * The filter is *seeded* on the first valid observation (returns ``obs``),
      so there is no startup transient.
    * A missing observation (``obs is None``) holds the previous value, so a
      briefly-unavailable weather entity never reverts the solar model to an
      unattenuated state.
    """
    if obs is None:
        return prev
    obs = max(0.0, min(1.0, float(obs)))
    if prev is None:
        return obs
    alpha = 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0
    return alpha * obs + (1.0 - alpha) * float(prev)


# Representative cloud-cover fractions for HA's standardised weather
# conditions (see https://www.home-assistant.io/integrations/weather/).
# Used when ``cloud_coverage`` is missing on the entity / forecast entry.
# Values picked conservatively: clearly clear vs clearly overcast, with
# partlycloudy in the middle; conditions that imply heavy precipitation map
# to fully overcast.
CONDITION_CLOUD_COVER: Dict[str, float] = {
    "sunny": 0.0,
    "clear-night": 0.0,
    "windy": 0.0,
    "windy-variant": 0.3,
    "partlycloudy": 0.3,
    "cloudy": 0.85,
    "fog": 1.0,
    "hail": 1.0,
    "lightning": 0.9,
    "lightning-rainy": 1.0,
    "pouring": 1.0,
    "rainy": 0.95,
    "snowy": 1.0,
    "snowy-rainy": 1.0,
    "exceptional": 0.85,
}


# ---------------------------------------------------------------------------
# Coercions
# ---------------------------------------------------------------------------


def coerce_float(value: Any) -> Optional[float]:
    """Return ``float(value)`` or ``None`` when value is missing/unparsable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_cloud_cover_percent(value: Any) -> Optional[float]:
    """Convert a cloud-cover percentage (0–100) to a fraction in [0, 1].

    Returns ``None`` for missing or unparsable input; clamps out-of-range
    values to the valid interval.
    """
    f = coerce_float(value)
    if f is None:
        return None
    return max(0.0, min(1.0, f / 100.0))


def _coerce_condition(value: Any) -> Optional[float]:
    """Map a weather condition string to its representative cloud fraction."""
    if isinstance(value, str):
        return CONDITION_CLOUD_COVER.get(value)
    return None


# ---------------------------------------------------------------------------
# Forecast parsing
# ---------------------------------------------------------------------------


def parse_ts(value: Any) -> Optional[float]:
    """Parse an ISO-8601 string or :class:`datetime` to a UTC epoch [seconds].

    Accepts a trailing ``Z`` and naive datetimes (assumed UTC).  Returns
    ``None`` when the value is missing or unparsable.  Shared by the weather
    and solar-forecast parsers so timestamp handling stays consistent.
    """
    if value is None:
        return None
    try:
        if isinstance(value, str):
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            fc_time = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            fc_time = value
        else:
            return None
        if fc_time.tzinfo is None:
            fc_time = fc_time.replace(tzinfo=timezone.utc)
        return fc_time.timestamp()
    except (ValueError, TypeError):
        return None


def interpolate_forecast(entries: List[Tuple[float, float]], target_ts: float) -> float:
    """Linearly interpolate a sorted (ts, value) series at ``target_ts``.

    Outside the series range, clamps to the nearest endpoint.  Assumes
    ``entries`` is sorted by timestamp ascending and non-empty.
    """
    if not entries:
        return 5.0  # fallback (only reached via the temperature path)

    if target_ts <= entries[0][0]:
        return entries[0][1]
    if target_ts >= entries[-1][0]:
        return entries[-1][1]

    for i in range(len(entries) - 1):
        t0, v0 = entries[i]
        t1, v1 = entries[i + 1]
        if t0 <= target_ts <= t1:
            if t1 == t0:
                return v0
            frac = (target_ts - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)

    return entries[-1][1]


def parse_forecast_field(
    forecast_data: list,
    horizon: int,
    dt: float,
    field: str,
    coerce: Callable[[Any], Optional[float]],
    fallback_field: Optional[str] = None,
    fallback_coerce: Optional[Callable[[Any], Optional[float]]] = None,
    now: Optional[datetime] = None,
    current_value: Optional[float] = None,
) -> Optional[List[float]]:
    """Parse a forecast field into ``horizon`` values interpolated to MPC steps.

    Parameters
    ----------
    forecast_data : list of dict
        Raw forecast entries.  Each entry must have a ``datetime`` key
        (ISO-8601 string or :class:`datetime`) plus the requested ``field``.
    horizon : int
        Number of MPC prediction steps.
    dt : float
        MPC time step in seconds.
    field : str
        Primary field name to read from each entry (e.g. ``"temperature"``,
        ``"cloud_coverage"``).
    coerce : callable
        Function mapping the raw field value to a float in the desired unit,
        or returning ``None`` when the value is missing / unparsable.
    fallback_field, fallback_coerce : optional
        When provided and the primary field is missing/unparsable for an
        entry, the parser tries this fallback field with this coerce function
        before discarding the entry.
    now : datetime, optional
        Reference time for interpolation.  Defaults to current UTC.
    current_value : float, optional
        Observed value at ``now``.  When provided it is prepended to the
        forecast entries as an anchor point at ``t = now``, so the
        interpolated series starts from the current observation and ramps
        smoothly toward the first forecast entry rather than jumping.
        When no forecast entries are available but ``current_value`` is
        given, the function returns a persistence forecast
        (all steps = ``current_value``) instead of ``None``.

    Returns
    -------
    list of float or None
        ``horizon`` values aligned to MPC steps, or ``None`` when no usable
        entries are found.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    now_ts = now.timestamp()
    entries: List[Tuple[float, float]] = []
    for entry in forecast_data:
        dt_str = entry.get("datetime")
        if dt_str is None:
            continue
        raw = entry.get(field)
        value = coerce(raw)
        if value is None and fallback_field is not None and fallback_coerce is not None:
            value = fallback_coerce(entry.get(fallback_field))
        if value is None:
            continue
        ts = parse_ts(dt_str)
        if ts is None:
            continue
        entries.append((ts, float(value)))

    # Anchor at the current observation so the interpolated series starts
    # from the measured value and ramps smoothly into the forecast rather
    # than jumping at t=now.  When no forecast entries were parsed, this
    # single point turns the function into a persistence forecast.
    if current_value is not None:
        entries.append((now_ts, float(current_value)))

    if not entries:
        return None

    entries.sort(key=lambda e: e[0])

    return [
        interpolate_forecast(entries, now_ts + dt * (k + 1)) for k in range(horizon)
    ]


def parse_temperature_forecast(
    forecast_data: list,
    horizon: int,
    dt: float,
    now: Optional[datetime] = None,
) -> Optional[List[float]]:
    """Parse raw weather forecast entries into interpolated horizon temperatures."""
    return parse_forecast_field(
        forecast_data,
        horizon,
        dt,
        field="temperature",
        coerce=coerce_float,
        now=now,
    )


def parse_cloud_forecast(
    forecast_data: list,
    horizon: int,
    dt: float,
    now: Optional[datetime] = None,
    current_cloud_cover: Optional[float] = None,
) -> Optional[List[float]]:
    """Parse raw weather forecast entries into interpolated horizon cloud-cover.

    Returns fractions in [0, 1].  Prefers the numeric ``cloud_coverage`` field;
    falls back to mapping the per-entry ``condition`` string when the
    percentage is missing on individual entries.

    Parameters
    ----------
    current_cloud_cover : float, optional
        Observed cloud-cover fraction at ``now``.  When provided it is
        prepended as an anchor so the series transitions smoothly from the
        current observation.  When no forecast entries are available it
        produces a persistence forecast instead of returning ``None``.
    """
    return parse_forecast_field(
        forecast_data,
        horizon,
        dt,
        field="cloud_coverage",
        coerce=coerce_cloud_cover_percent,
        fallback_field="condition",
        fallback_coerce=_coerce_condition,
        now=now,
        current_value=current_cloud_cover,
    )


# ---------------------------------------------------------------------------
# Entity readers
# ---------------------------------------------------------------------------

_UNAVAILABLE_STATES = ("unknown", "unavailable")


def read_outdoor_temp(
    hass: Any,
    outdoor_entity: Optional[str],
    weather_entity: Optional[str],
) -> Optional[float]:
    """Return the current outdoor temperature [°C], or None if unavailable.

    Tries the dedicated ``outdoor_entity`` first; if it's missing or
    unavailable, falls back to the ``temperature`` attribute on the weather
    entity.  Returns ``None`` when neither source has a valid reading so the
    coordinator can skip MPC execution instead of seeding the EKF with a
    fabricated fallback value.
    """
    if outdoor_entity:
        state = hass.states.get(outdoor_entity)
        if state and state.state not in _UNAVAILABLE_STATES:
            try:
                return float(state.state)
            except ValueError:
                pass

    if weather_entity:
        state = hass.states.get(weather_entity)
        if state and state.state not in _UNAVAILABLE_STATES:
            temp = state.attributes.get("temperature")
            if temp is not None:
                try:
                    return float(temp)
                except (ValueError, TypeError):
                    pass

    return None


def read_wind_speed_now(hass: Any, weather_entity: Optional[str]) -> Optional[float]:
    """Return the current outdoor wind speed [m/s] or ``None``.

    Reads the ``wind_speed`` attribute on the configured weather entity.
    Home Assistant weather entities expose wind in whatever unit the
    integration provider chose, with the canonical ``wind_speed_unit``
    attribute telling us what it is.  We normalise to m/s here so the
    Sherman–Grimsrud infiltration overlay (Phase 1 C1) can use the value
    directly.  Returns ``None`` when no entity is configured, the entity
    is unavailable, or the value can't be parsed — the model then stays
    on its typical-conditions external conductance.
    """
    if not weather_entity:
        return None
    state = hass.states.get(weather_entity)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None
    raw = state.attributes.get("wind_speed")
    val = coerce_float(raw)
    if val is None:
        return None

    # Normalise common HA wind-speed units to m/s.  HA's canonical
    # ``wind_speed_unit`` attribute is exposed by integrations such as
    # Met.no, OpenWeatherMap and Tomorrow.io; older entities may omit it,
    # in which case we assume m/s (HA's internal canonical unit).
    unit = (state.attributes.get("wind_speed_unit") or "m/s").lower()
    if unit in ("m/s", "ms", "meter/second", "meter_per_second"):
        factor = 1.0
    elif unit in ("km/h", "kph", "kilometer/hour", "kilometre/hour"):
        factor = 1.0 / 3.6
    elif unit in ("mph", "mi/h", "mile/hour"):
        factor = 0.44704
    elif unit in ("ft/s", "fps", "foot/second", "feet/second"):
        factor = 0.3048
    elif unit in ("kn", "kt", "knot", "knots"):
        factor = 0.514444
    else:
        # Unrecognised unit — silently fall back to assuming m/s rather
        # than risking a 3-orders-of-magnitude error.  Logged so that
        # surprising unit conventions surface in diagnostics.
        _LOGGER.debug(
            "Unrecognised wind_speed_unit %r on %s; assuming m/s",
            unit, weather_entity,
        )
        factor = 1.0
    return max(0.0, val * factor)


def read_cloud_cover_now(hass: Any, weather_entity: Optional[str]) -> Optional[float]:
    """Return the current cloud-cover fraction in [0, 1] or ``None``.

    Prefers the numeric ``cloud_coverage`` attribute (percent) when present;
    falls back to mapping the entity's ``condition`` / state string to a
    representative fraction.  Returns ``None`` when no entity is configured
    or neither signal is available — the solar model then stays on its
    clear-sky default.
    """
    if not weather_entity:
        return None
    state = hass.states.get(weather_entity)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None

    frac = coerce_cloud_cover_percent(state.attributes.get("cloud_coverage"))
    if frac is not None:
        return frac

    # Weather entities expose the condition as the entity state.
    return CONDITION_CLOUD_COVER.get(state.state)


async def fetch_forecast_entries(
    hass: Any,
    weather_entity: Optional[str],
) -> Tuple[Optional[list], Optional[str]]:
    """Fetch raw forecast entries from the configured weather entity.

    Tries the modern ``weather.get_forecasts`` service first (HA 2023.9+),
    then falls back to the deprecated ``forecast`` state attribute.

    Returns
    -------
    (entries, error)
        ``entries`` is the raw forecast list (typically dicts with
        ``datetime`` / ``temperature`` / ``cloud_coverage`` keys), or ``None``
        when no usable data could be obtained.  ``error`` is ``None`` on
        success, a human-readable description otherwise.  When no weather
        entity is configured both return ``None`` — that's neither success
        nor failure.
    """
    if not weather_entity:
        return None, None

    service_exc: Optional[str] = None

    # ── Modern approach: weather.get_forecasts service (HA 2023.9+) ──────
    if hass.services.has_service("weather", "get_forecasts"):
        try:
            response = await hass.services.async_call(
                "weather",
                "get_forecasts",
                service_data={"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            if response and weather_entity in response:
                forecast_data = response[weather_entity].get("forecast", [])
                if forecast_data:
                    return forecast_data, None
        except Exception as exc:  # noqa: BLE001 — propagate as text for diagnostics
            service_exc = f"{type(exc).__name__}: {exc}"
            _LOGGER.debug(
                "weather.get_forecasts service call failed for %s, "
                "falling back to state attribute: %s",
                weather_entity,
                exc,
            )

    # ── Fallback: read from the deprecated state attribute ───────────────
    state = hass.states.get(weather_entity)
    if state is None:
        return None, service_exc or f"weather entity {weather_entity!r} not found"

    forecast_data = state.attributes.get("forecast")
    if not forecast_data:
        return None, service_exc or "no forecast data on weather entity"

    return forecast_data, None
