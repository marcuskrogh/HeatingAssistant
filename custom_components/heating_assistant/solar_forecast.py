"""Solar-forecast entity readers and clearness-index derivation.

Home Assistant's dedicated solar-forecast integrations (Forecast.Solar,
Solcast PV Forecast, Open-Meteo Solar) publish *PV array power* forecasts for
a single configured panel plane — not per-window irradiance.  This module turns
such a forecast into a dimensionless **clearness index** (sky transmittance)
that the geometric solar model (:mod:`solar_model`) can use in place of its
coarse Kasten–Czeplak cloud factor, while leaving all per-window geometry
intact.

The index is derived as ``forecast_power / modelled_clear-sky_power`` for the
same plane, so the unknown array size/efficiency cancels.  When the plane
geometry is unknown it is auto-calibrated against the forecast's own clear-sky
envelope (a robust rolling peak).

Like :mod:`weather`, the functions take ``hass`` / state objects rather than a
coordinator instance, and avoid Home Assistant imports so they test under the
conftest stubs.  Timestamp and value coercion are shared with :mod:`weather`
(``parse_ts``, ``coerce_float``, ``interpolate_forecast``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from .solar_model import clear_sky_plane_poa
from .weather import coerce_float, interpolate_forecast, parse_ts

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_STATES = ("unknown", "unavailable")

#: Clamp bounds for the clearness index.  A small headroom above 1.0 allows for
#: cloud-edge enhancement / over-irradiance without letting parse glitches blow
#: the gain up.
CLEARNESS_MIN = 0.0
CLEARNESS_MAX = 1.1

#: Modelled clear-sky POA below this [W/m²] is treated as night / too-low-sun:
#: the index is undefined there (small-denominator guard), so the controller
#: falls back per-step rather than dividing by a tiny number.
_MIN_CLEAR_SKY_W = 5.0

#: Default age beyond which a forecast entity is considered stale.
STALE_SECONDS_DEFAULT = 3 * 3600

#: Generic reference plane used when the PV plane geometry is not configured.
_GENERIC_PLANE_TILT = 30.0
_GENERIC_PLANE_AZIMUTH = 180.0

#: STC reference irradiance — converts modelled POA [W/m²] + rated peak power
#: [Wp] to an expected clear-sky plant power [W] for the physically-anchored path.
_STC_IRRADIANCE = 1000.0


# ---------------------------------------------------------------------------
# Forecast parsing — one parser per known provider schema + a generic fallback
# ---------------------------------------------------------------------------


def _parse_watts_dict(attrs: dict) -> Optional[List[Tuple[float, float]]]:
    """Forecast.Solar: ``attrs["watts"]`` = ``{ISO-timestamp: watts}``."""
    watts = attrs.get("watts")
    if not isinstance(watts, dict) or not watts:
        return None
    out: List[Tuple[float, float]] = []
    for ts_raw, val in watts.items():
        ts = parse_ts(ts_raw)
        v = coerce_float(val)
        if ts is not None and v is not None:
            out.append((ts, v))
    return out or None


def _parse_solcast_list(attrs: dict) -> Optional[List[Tuple[float, float]]]:
    """Solcast: ``detailedForecast``/``detailedHourly`` = list of
    ``{period_start, pv_estimate}`` with ``pv_estimate`` in **kW**."""
    for key in ("detailedForecast", "detailedHourly"):
        lst = attrs.get(key)
        if not isinstance(lst, list) or not lst:
            continue
        out: List[Tuple[float, float]] = []
        for entry in lst:
            if not isinstance(entry, dict):
                continue
            ts = parse_ts(entry.get("period_start"))
            v = coerce_float(entry.get("pv_estimate"))
            if ts is not None and v is not None:
                out.append((ts, v * 1000.0))  # kW → W
        if out:
            return out
    return None


# Attribute names scanned by the generic parser, and the per-entry key
# candidates for list-of-dict payloads.
_GENERIC_ATTR_NAMES = ("forecast", "watts", "detailedForecast", "power_forecast")
_GENERIC_TS_KEYS = ("datetime", "period_start", "time", "timestamp")
_GENERIC_VALUE_KEYS = ("watts", "power", "pv_estimate", "value", "w")


def _generic_unit_scale(attrs: dict, attr_name: str) -> float:
    """Return a W-conversion multiplier from a unit hint, defaulting to 1.0 (W).

    Looks for a ``<attr>_unit`` / ``unit_of_measurement`` sibling mentioning
    kilowatts.  Anything unrecognised assumes watts (HA's canonical power unit)
    with a debug log, mirroring :func:`weather.read_wind_speed_now`.
    """
    for key in (f"{attr_name}_unit", "unit_of_measurement", "power_unit"):
        unit = attrs.get(key)
        if isinstance(unit, str):
            u = unit.strip().lower()
            if u in ("kw", "kilowatt", "kilowatts"):
                return 1000.0
            if u in ("w", "watt", "watts"):
                return 1.0
            _LOGGER.debug("Unrecognised solar-forecast power unit %r; assuming W", unit)
            return 1.0
    return 1.0


def _parse_generic_forecast(attrs: dict) -> Optional[List[Tuple[float, float]]]:
    """Open-Meteo Solar and any other sensor with a power-forecast series.

    Accepts both ``{ISO-ts: value}`` dicts and lists of dicts, trying a set of
    common timestamp / value key names.  Applies a unit heuristic (defaults to
    watts).
    """
    for name in _GENERIC_ATTR_NAMES:
        payload = attrs.get(name)
        scale = _generic_unit_scale(attrs, name)

        if isinstance(payload, dict) and payload:
            out: List[Tuple[float, float]] = []
            for ts_raw, val in payload.items():
                ts = parse_ts(ts_raw)
                v = coerce_float(val)
                if ts is not None and v is not None:
                    out.append((ts, v * scale))
            if out:
                return out

        if isinstance(payload, list) and payload:
            out = []
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                ts = next(
                    (parse_ts(entry[k]) for k in _GENERIC_TS_KEYS if k in entry),
                    None,
                )
                v = next(
                    (coerce_float(entry[k]) for k in _GENERIC_VALUE_KEYS if k in entry),
                    None,
                )
                if ts is not None and v is not None:
                    out.append((ts, v * scale))
            if out:
                return out
    return None


def parse_pv_power_forecast(
    state: Any,
) -> Tuple[Optional[List[Tuple[float, float]]], str]:
    """Parse a forecast entity state into ``(sorted [(epoch, watts)], provider)``.

    Tries the known provider schemas first, then a generic fallback.  Returns
    ``(None, "none")`` when nothing usable parses.  ``provider`` is one of
    ``forecast_solar`` / ``solcast`` / ``open_meteo`` / ``generic`` / ``none``.
    """
    attrs = getattr(state, "attributes", None) or {}
    for parser, label in (
        (_parse_watts_dict, "forecast_solar"),
        (_parse_solcast_list, "solcast"),
        (_parse_generic_forecast, "generic"),
    ):
        series = parser(attrs)
        if series:
            series.sort(key=lambda e: e[0])
            return series, label
    return None, "none"


# ---------------------------------------------------------------------------
# Entity readers
# ---------------------------------------------------------------------------


def read_pv_power_now(
    hass: Any,
    solar_forecast_entity: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Return the current PV power [W] from the forecast entity, or ``None``.

    Many of these integrations expose instantaneous power as the entity state.
    When the state is not a usable number (e.g. an energy-sensor state), falls
    back to interpolating the parsed forecast series at ``now``.
    """
    if not solar_forecast_entity:
        return None
    state = hass.states.get(solar_forecast_entity)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None

    val = coerce_float(state.state)
    if val is not None:
        return val

    series, _ = parse_pv_power_forecast(state)
    if series:
        if now is None:
            now = datetime.now(tz=timezone.utc)
        return interpolate_forecast(series, now.timestamp())
    return None


# ---------------------------------------------------------------------------
# Clearness-index derivation
# ---------------------------------------------------------------------------


def _percentile(values: List[float], pct: float) -> float:
    """Linear-interpolated percentile of a non-empty list (pct in [0, 100])."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (pct / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + frac * (s[hi] - s[lo])


def _clamp_clearness(x: float) -> float:
    return max(CLEARNESS_MIN, min(CLEARNESS_MAX, x))


def compute_clearness_series(
    pv_series: Optional[List[Tuple[float, float]]],
    horizon: int,
    dt: float,
    latitude: float,
    longitude: float,
    now: datetime,
    plane_tilt: Optional[float] = None,
    plane_azimuth: Optional[float] = None,
    pv_power_now: Optional[float] = None,
    pv_peak_power: Optional[float] = None,
) -> Tuple[Optional[List[Optional[float]]], Optional[float]]:
    """Derive a per-MPC-step clearness index from a PV-power forecast.

    Returns ``(series, clearness_now)`` where ``series`` is a list of length
    ``horizon`` aligned to ``now + dt*(k+1)`` (mirroring
    :func:`weather.parse_cloud_forecast`), with ``None`` for steps that fall in
    night / too-low-sun (where the index is undefined and the controller should
    fall back to the cloud/clear path).  ``clearness_now`` is the index at
    ``now`` (or ``None``).  Returns ``(None, None)`` when no usable data.

    Normalisation:

    * **Physically anchored** when both plane geometry *and* ``pv_peak_power``
      are given: ``index = power / (peak * POA / 1000)``.
    * **Auto-calibrated** otherwise: ``index = (power / POA) / S`` where ``S`` is
      a robust (90th-percentile) rolling peak of ``power / POA`` over the
      horizon — the unknown plant scale cancels.
    """
    if not pv_series and pv_power_now is None:
        return None, None

    tilt = plane_tilt if plane_tilt is not None else _GENERIC_PLANE_TILT
    azimuth = plane_azimuth if plane_azimuth is not None else _GENERIC_PLANE_AZIMUTH
    have_geometry = plane_tilt is not None and plane_azimuth is not None
    physical = have_geometry and pv_peak_power is not None and pv_peak_power > 0.0

    def _ref(ts: float) -> float:
        return clear_sky_plane_poa(
            datetime.fromtimestamp(ts, tz=timezone.utc),
            latitude,
            longitude,
            tilt,
            azimuth,
        )

    # Defined raw ratios (power / clear-sky POA) at each forecast timestamp.
    raw: List[Tuple[float, float]] = []
    for ts, watts in pv_series or []:
        ref = _ref(ts)
        if ref > _MIN_CLEAR_SKY_W:
            raw.append((ts, watts / ref))

    # Determine the normalisation scale for the auto-calibrated path.
    scale: Optional[float] = None
    if not physical:
        defined = [r for _, r in raw]
        if defined:
            scale = _percentile(defined, 90.0)
        if not scale:  # all-zero or empty → cannot calibrate
            scale = None

    def _index(ts: float, watts: float) -> Optional[float]:
        ref = _ref(ts)
        if ref <= _MIN_CLEAR_SKY_W:
            return None
        if physical:
            return _clamp_clearness(watts / (pv_peak_power * ref / _STC_IRRADIANCE))
        if scale:
            return _clamp_clearness((watts / ref) / scale)
        return None

    # Build interpolation entries from defined index points, anchored at ``now``.
    now_ts = now.timestamp()
    clearness_now: Optional[float] = None
    if pv_power_now is not None:
        clearness_now = _index(now_ts, pv_power_now)

    entries: List[Tuple[float, float]] = []
    if clearness_now is not None:
        entries.append((now_ts, clearness_now))
    for ts, watts in pv_series or []:
        idx = _index(ts, watts)
        if idx is not None:
            entries.append((ts, idx))

    if not entries:
        return None, clearness_now
    entries.sort(key=lambda e: e[0])

    series: List[Optional[float]] = []
    for k in range(horizon):
        t_k = now_ts + dt * (k + 1)
        # Night / too-low-sun steps stay undefined so the controller falls back.
        if _ref(t_k) <= _MIN_CLEAR_SKY_W:
            series.append(None)
        else:
            series.append(interpolate_forecast(entries, t_k))

    return series, clearness_now


def select_clearness_for_step(
    clearness_forecast: Optional[List[Optional[float]]],
    k: int,
    fallback: Optional[float] = None,
) -> Optional[float]:
    """Pick the clearness index for horizon step ``k``.

    Mirrors :func:`controller._select_cloud_for_step` but treats ``None``
    entries as "no data": returns ``clearness_forecast[k]`` when present and
    non-``None``, the last non-``None`` value when ``k`` runs past the forecast
    (persistence), else ``fallback``.
    """
    if not clearness_forecast:
        return fallback
    if k < len(clearness_forecast):
        val = clearness_forecast[k]
        return val if val is not None else fallback
    # persistence: last defined value, else fallback
    for val in reversed(clearness_forecast):
        if val is not None:
            return val
    return fallback


def is_stale(state: Any, now: datetime, max_age: float = STALE_SECONDS_DEFAULT) -> bool:
    """Return True when the entity's ``last_updated`` is older than ``max_age``."""
    last = getattr(state, "last_updated", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) > timedelta(seconds=max_age)
