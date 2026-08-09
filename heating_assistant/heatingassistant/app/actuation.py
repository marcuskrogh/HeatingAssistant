"""Convert MPC control fractions into Home Assistant heater write payloads.

The thin MQTT bridge is intentionally dumb: it applies domain-specific HA
services from the App's ``tag/out`` value.  Climate heat pumps need
``set_hvac_mode`` + ``set_temperature`` derived from the HeatPump logit
setpoint map (ported from the pre-SWD-262 coordinator actuation path).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from heatingassistant.engine import const
from heatingassistant.engine.heat_sources import HeatPump, HeatSource


def resolve_hp_hvac_mode(
    configured_mode: str,
    supported_modes: Sequence[str] | None,
) -> str:
    """Pick an HA climate mode string the entity advertises."""

    modes = [str(m).lower() for m in (supported_modes or [])]
    configured = str(configured_mode or const.DEFAULT_SOURCE_HVAC_MODE).lower()
    if configured == "heat_cool":
        if "heat_cool" in modes:
            return "heat_cool"
        if "auto" in modes:
            return "auto"
        return "heat_cool"
    if configured == "cool":
        if "cool" in modes:
            return "cool"
        if "dry" in modes:
            return "dry"
        return "fan_only"
    return "heat"


def climate_hp_command(
    source: HeatPump,
    fraction: float,
    internal_temp: float,
    outdoor_temp: float = 0.0,
    supported_modes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return ``{hvac_mode, temperature}`` for an enabled heat-pump climate."""

    hvac_mode = resolve_hp_hvac_mode(source.hvac_mode, supported_modes)
    target = source.target_temperature(fraction, internal_temp, outdoor_temp)
    return {"hvac_mode": hvac_mode, "temperature": float(target)}


def climate_thermostat_command(
    source: HeatSource,
    fraction: float,
    internal_temp: float,
    room_temp: float,
    room_setpoint: float,
) -> dict[str, Any]:
    """Return ``{hvac_mode, temperature}`` for a non-HP climate heater."""

    idle_offset = float(const.DEFAULT_IDLE_OFFSET)
    if fraction > 0.0 and room_temp <= room_setpoint:
        target = max(
            room_setpoint,
            source.target_temperature(fraction, internal_temp),
        )
        return {"hvac_mode": "heat", "temperature": float(target)}
    overshoot = max(0.0, room_temp - room_setpoint)
    target = internal_temp - (idle_offset + overshoot)
    return {"hvac_mode": "heat", "temperature": float(target)}


def climate_write_payload(
    source: HeatSource,
    fraction: float,
    *,
    enabled: bool,
    internal_temp: float | None,
    outdoor_temp: float,
    room_temp: float | None,
    room_setpoint: float | None,
    supported_modes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build the climate MQTT ``value`` object for the thin bridge."""

    if not enabled:
        return {"hvac_mode": "off"}

    base = internal_temp
    if base is None:
        base = room_temp if room_temp is not None else 21.0

    if isinstance(source, HeatPump):
        return climate_hp_command(
            source,
            float(fraction),
            float(base),
            outdoor_temp,
            supported_modes,
        )

    rt = float(room_temp) if room_temp is not None else float(base)
    sp = float(room_setpoint) if room_setpoint is not None else float(base)
    return climate_thermostat_command(
        source,
        float(fraction),
        float(base),
        rt,
        sp,
    )


def number_write_payload(fraction: float, *, enabled: bool) -> float:
    """Map control fraction to a 0–100 ``number.set_value`` payload."""

    if not enabled:
        return 0.0
    return float(round(max(0.0, min(1.0, float(fraction))) * 100.0))


def switch_write_payload(fraction: float, *, enabled: bool) -> bool:
    """Map control fraction to on/off for a switch heater."""

    if not enabled:
        return False
    return float(fraction) > 0.5


def coerce_climate_attrs(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract climate feedback fields from a tag/in attribute blob."""

    attrs = dict(attributes or {})
    out: dict[str, Any] = {}
    for key in (
        "current_temperature",
        "temperature",
        "hvac_modes",
        "hvac_action",
        "min_temp",
        "max_temp",
    ):
        if key in attrs and attrs[key] is not None:
            out[key] = attrs[key]
    return out
