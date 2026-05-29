"""Solar-forecast entity readers and GHI derivation.

Home Assistant's solar-forecast integrations come in two flavours:

* **PV-power forecasts** (Forecast.Solar, Solcast, Open-Meteo Solar): watts for a
  single configured panel plane.
* **Irradiance forecasts**: Global Horizontal Irradiance (GHI) in W/m².

This module turns either into a **GHI series** [W/m²] aligned to the MPC steps.
GHI is the swappable *intensity* the solar model consumes: the model then
decomposes it into beam/diffuse (Erbs) and transposes it onto each window using
geometry alone — so the forecast supplies the magnitude and the model supplies
only the geometry.  No clear-sky transmittance assumption enters this path.

For PV-power sensors the panel power is first converted to a plane-of-array
irradiance (``1000 · P / peak``) and then de-projected to GHI using the panel
geometry (:func:`poa_to_ghi`) — again geometry + the Erbs split only.  When the
panel plane is unknown the panel is treated as a horizontal GHI proxy; when the
peak power is unknown the forecast cannot be scaled to absolute irradiance and
the caller falls back to the analytical clear-sky model.

Like :mod:`weather`, the functions take ``hass`` / state objects and avoid Home
Assistant imports so they test under the conftest stubs.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from .solar_model import (
    angle_of_incidence,
    erbs_diffuse_fraction,
    extraterrestrial_normal_irradiance,
    solar_angles,
)
from .weather import coerce_float, interpolate_forecast, parse_ts

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_STATES = ("unknown", "unavailable")

#: Default age beyond which a forecast entity is considered stale.
STALE_SECONDS_DEFAULT = 3 * 3600

#: STC reference irradiance — converts a PV plant's rated peak power [Wp] and a
#: forecast power [W] to a plane-of-array irradiance [W/m²]: POA = 1000 · P / peak.
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
_GENERIC_ATTR_NAMES = (
    "forecast", "watts", "detailedForecast", "power_forecast",
    "shortwave_radiation", "ghi",
)
_GENERIC_TS_KEYS = ("datetime", "period_start", "time", "timestamp")
_GENERIC_VALUE_KEYS = (
    "watts", "power", "pv_estimate", "ghi", "shortwave_radiation", "value", "w",
)


def _parse_generic_forecast(attrs: dict) -> Optional[List[Tuple[float, float]]]:
    """Open-Meteo and any other sensor with a forecast series (power or GHI).

    Accepts both ``{ISO-ts: value}`` dicts and lists of dicts, trying a set of
    common timestamp / value key names.  Unit interpretation (power vs.
    irradiance, kW vs. W) is handled by the caller via :func:`detect_value_kind`
    and the kW→W rule above for the known schemas.
    """
    for name in _GENERIC_ATTR_NAMES:
        payload = attrs.get(name)

        if isinstance(payload, dict) and payload:
            out: List[Tuple[float, float]] = []
            for ts_raw, val in payload.items():
                ts = parse_ts(ts_raw)
                v = coerce_float(val)
                if ts is not None and v is not None:
                    out.append((ts, v))
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
                    out.append((ts, v))
            if out:
                return out
    return None


def parse_pv_power_forecast(
    state: Any,
) -> Tuple[Optional[List[Tuple[float, float]]], str]:
    """Parse a forecast entity state into ``(sorted [(epoch, value)], provider)``.

    Values are watts for PV-power providers (Solcast already converted kW→W) or
    W/m² for irradiance providers — :func:`detect_value_kind` tells them apart.
    Returns ``(None, "none")`` when nothing usable parses.
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


def detect_value_kind(state: Any) -> str:
    """Return ``"irradiance"`` when the entity reports W/m², else ``"power"``.

    Looks at the entity's ``unit_of_measurement`` (and ``native_unit_…``).  An
    irradiance sensor's series is treated as GHI directly; anything else is
    treated as PV power for a plane.
    """
    attrs = getattr(state, "attributes", None) or {}
    for key in ("unit_of_measurement", "native_unit_of_measurement"):
        unit = attrs.get(key)
        if isinstance(unit, str):
            u = unit.lower().replace(" ", "")
            if "w/m2" in u or "w/m²" in u:
                return "irradiance"
    return "power"


# ---------------------------------------------------------------------------
# Entity readers
# ---------------------------------------------------------------------------


def read_value_now(
    hass: Any,
    solar_forecast_entity: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Return the current sensor value (W or W/m²) or ``None``.

    Prefers the instantaneous state; falls back to interpolating the parsed
    forecast series at ``now``.
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
# POA → GHI de-projection (geometry + Erbs only; no clear-sky atmosphere)
# ---------------------------------------------------------------------------


def poa_to_ghi(
    poa: float,
    dt: datetime,
    latitude: float,
    longitude: float,
    surface_tilt: float,
    surface_azimuth: float,
) -> float:
    """
    Recover Global Horizontal Irradiance [W/m²] from a tilted-plane POA value.

    Solves ``POA = GHI·[(1−df)·cosθ/sinα + df·svf]`` for GHI, where the diffuse
    fraction ``df`` itself depends on GHI through the clearness index (Erbs).  A
    short fixed-point iteration handles that coupling.  Uses only sun geometry
    and the extra-terrestrial reference — no clear-sky transmittance model.

    Returns ``0.0`` at night or when the sun is too low to invert robustly.
    """
    altitude, sun_az = solar_angles(dt, latitude, longitude)
    if altitude <= 0.0 or poa <= 0.0:
        return 0.0
    sin_alt = math.sin(altitude)
    if sin_alt <= 1e-3:
        return 0.0
    n = dt.timetuple().tm_yday
    ghi_extra = extraterrestrial_normal_irradiance(n) * sin_alt
    if ghi_extra <= 0.0:
        return 0.0

    cos_theta = math.cos(
        angle_of_incidence(altitude, sun_az, surface_tilt, surface_azimuth)
    )
    svf = (1.0 + math.cos(math.radians(surface_tilt))) / 2.0

    ghi = min(poa, ghi_extra)  # initial guess
    for _ in range(16):
        kt = max(0.0, min(1.0, ghi / ghi_extra))
        df = erbs_diffuse_fraction(kt)
        coef = (1.0 - df) * cos_theta / sin_alt + df * svf
        if coef <= 1e-6:
            break
        ghi_new = max(0.0, min(poa / coef, ghi_extra))
        if abs(ghi_new - ghi) < 0.5:
            ghi = ghi_new
            break
        ghi = ghi_new
    return ghi


# ---------------------------------------------------------------------------
# GHI series builder
# ---------------------------------------------------------------------------


def _value_to_ghi(
    value: float,
    ts: float,
    kind: str,
    latitude: float,
    longitude: float,
    plane_tilt: Optional[float],
    plane_azimuth: Optional[float],
    peak_power: Optional[float],
) -> Optional[float]:
    """Convert one forecast value to GHI [W/m²], or ``None`` if not possible."""
    if kind == "irradiance":
        return max(0.0, value)
    # PV power → plane-of-array irradiance needs the rated peak power.
    if not peak_power or peak_power <= 0.0:
        return None
    poa = _STC_IRRADIANCE * value / peak_power
    if plane_tilt is not None and plane_azimuth is not None:
        return poa_to_ghi(
            poa,
            datetime.fromtimestamp(ts, tz=timezone.utc),
            latitude,
            longitude,
            plane_tilt,
            plane_azimuth,
        )
    # No plane geometry: treat the panel as a horizontal GHI proxy.
    return max(0.0, poa)


def compute_ghi_series(
    pv_series: Optional[List[Tuple[float, float]]],
    value_now: Optional[float],
    kind: str,
    horizon: int,
    dt: float,
    latitude: float,
    longitude: float,
    now: datetime,
    plane_tilt: Optional[float] = None,
    plane_azimuth: Optional[float] = None,
    peak_power: Optional[float] = None,
) -> Tuple[Optional[List[Optional[float]]], Optional[float]]:
    """Build a per-MPC-step GHI series [W/m²] from a parsed forecast.

    Returns ``(series, ghi_now)`` where ``series`` has length ``horizon``,
    aligned to ``now + dt·(k+1)``, with ``None`` for steps outside the forecast's
    time coverage (so the controller falls back to the clear-sky model there).
    Returns ``(None, None)`` when no absolute GHI can be derived (e.g. a
    PV-power sensor with no configured peak power).
    """
    if not pv_series and value_now is None:
        return None, None

    def _to_ghi(ts: float, val: float) -> Optional[float]:
        return _value_to_ghi(
            val, ts, kind, latitude, longitude,
            plane_tilt, plane_azimuth, peak_power,
        )

    now_ts = now.timestamp()
    entries: List[Tuple[float, float]] = []
    for ts, val in pv_series or []:
        g = _to_ghi(ts, val)
        if g is not None:
            entries.append((ts, g))

    ghi_now: Optional[float] = None
    if value_now is not None:
        ghi_now = _to_ghi(now_ts, value_now)
        if ghi_now is not None:
            entries.append((now_ts, ghi_now))

    if not entries:
        return None, ghi_now
    entries.sort(key=lambda e: e[0])

    first_ts, last_ts = entries[0][0], entries[-1][0]
    series: List[Optional[float]] = []
    for k in range(horizon):
        t_k = now_ts + dt * (k + 1)
        if first_ts <= t_k <= last_ts:
            series.append(interpolate_forecast(entries, t_k))
        else:
            # Outside forecast coverage → no data; fall back per-step.
            series.append(None)
    return series, ghi_now


def select_ghi_for_step(
    ghi_forecast: Optional[List[Optional[float]]],
    k: int,
    fallback: Optional[float] = None,
) -> Optional[float]:
    """Pick the GHI for horizon step ``k``.

    Returns ``ghi_forecast[k]`` when present and non-``None``, else ``fallback``.
    Unlike cloud cover, GHI is **not** persisted past the forecast's coverage
    (a stale daytime value must not leak into the night) — out-of-range steps
    fall back to the analytical model.
    """
    if not ghi_forecast:
        return fallback
    if k < len(ghi_forecast):
        val = ghi_forecast[k]
        return val if val is not None else fallback
    return fallback


def is_stale(state: Any, now: datetime, max_age: float = STALE_SECONDS_DEFAULT) -> bool:
    """Return True when the entity's ``last_updated`` is older than ``max_age``."""
    last = getattr(state, "last_updated", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) > timedelta(seconds=max_age)
