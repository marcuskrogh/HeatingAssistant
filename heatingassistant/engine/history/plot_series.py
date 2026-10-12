"""Identification-history series for PE Heating Input / Disturbances charts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


def _iso_time(value: Any) -> str | None:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _u_fraction(raw: Any) -> float:
    """Map a stored heater command to a thermal-model fraction.

    Identification records store commanded output as 0–1. A few fixtures
    used 0–100 percent; values outside ±1.5 are treated as percent.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if abs(value) > 1.5:
        return value / 100.0
    return value


def _call_thermal(fn: Any, u_frac: float, outdoor: float) -> float | None:
    try:
        return float(fn(u_frac, outdoor))
    except TypeError:
        try:
            return float(fn(u_frac))
        except Exception:
            return None
    except Exception:
        return None


def _source_thermal_power(source: Any, u_frac: float, outdoor: float) -> float:
    """Convert a stored command with the configured model power map [W].

    Parameter-estimation Heating Input uses the same map as room view:
    ``display_smooth_thermal_power`` / ``display_thermal_power``. Those
    evaluate rated heating and cooling capacity at the stored command, so
    a half command is half of the configured capacity and a full command
    is that capacity. Identified ``power_scale`` stays in the plant model.
    Applying it here moved the whole trace, including mid-range commands,
    above the heat pump the user configured.

    This is not a cap on an already-scaled trace. Cooling-capable sources
    still use the piecewise display map, so a negative command is cooling
    capacity, not ``−heating_capacity``.
    """
    if bool(getattr(source, "can_cool", False)):
        smooth = getattr(source, "display_smooth_thermal_power", None)
        if callable(smooth):
            power = _call_thermal(smooth, u_frac, outdoor)
            if power is not None:
                return power
        fn = getattr(source, "display_thermal_power", None)
        if callable(fn):
            power = _call_thermal(fn, u_frac, outdoor)
            if power is not None:
                return power
        # Never map cooling through heating capacity if the display
        # call failed — that is the SWD-459 bug.
        if u_frac < 0.0:
            return 0.0
    fn = getattr(source, "display_thermal_power", None)
    if callable(fn):
        power = _call_thermal(fn, u_frac, outdoor)
        if power is not None:
            return power
    rated = getattr(source, "rated_heating_capacity", None)
    if callable(rated):
        try:
            return float(rated(outdoor)) * u_frac
        except (TypeError, ValueError):
            pass
    try:
        gain = float(getattr(source, "max_power", 0.0) or 0.0)
    except (TypeError, ValueError):
        gain = 0.0
    efficiency = getattr(source, "efficiency", None)
    if efficiency is not None:
        try:
            gain *= float(efficiency)
        except (TypeError, ValueError):
            pass
    return gain * u_frac


def identification_aux_series(
    history: Sequence[Mapping[str, Any]] | None,
    heat_sources: Sequence[Any],
    room_name: str,
    *,
    iso_time: Callable[[Any], str | None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build heater-power / outdoor / solar series for one room from ID history."""

    time_fn = iso_time or _iso_time
    sources = list(heat_sources or [])
    room_idxs = [
        idx
        for idx, source in enumerate(sources)
        if str(getattr(source, "room", "")) == str(room_name)
    ]
    heating: list[dict[str, Any]] = []
    outdoor: list[dict[str, Any]] = []
    solar: list[dict[str, Any]] = []
    for record in list(history or []):
        if not isinstance(record, Mapping):
            continue
        stamp = time_fn(record.get("timestamp"))
        if stamp is None:
            continue
        u_raw = record.get("u")
        if isinstance(u_raw, (int, float)):
            u_seq: Sequence[Any] = [u_raw]
        elif isinstance(u_raw, (str, bytes, bytearray)):
            u_seq = []
        else:
            try:
                u_seq = list(u_raw or [])
            except TypeError:
                u_seq = []
        try:
            t_out = float(record.get("d_outdoor", 0.0) or 0.0)
        except (TypeError, ValueError):
            t_out = 0.0
        power = 0.0
        for idx in room_idxs:
            frac = _u_fraction(u_seq[idx] if idx < len(u_seq) else 0.0)
            power += _source_thermal_power(sources[idx], frac, t_out)
        solar_map = record.get("d_solar") or {}
        if not isinstance(solar_map, Mapping):
            solar_map = {}
        try:
            q_solar = float(solar_map.get(room_name, 0.0) or 0.0)
        except (TypeError, ValueError):
            q_solar = 0.0
        heating.append({"time": stamp, "value": round(power, 2)})
        outdoor.append({"time": stamp, "value": round(t_out, 3)})
        solar.append({"time": stamp, "value": round(q_solar, 2)})
    return {
        "heating_power": heating,
        "outdoor_temp": outdoor,
        "solar_gain": solar,
    }


__all__ = ["identification_aux_series"]
