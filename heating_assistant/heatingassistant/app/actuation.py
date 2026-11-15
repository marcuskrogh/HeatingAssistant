"""Convert MPC control fractions into Home Assistant heater write payloads.

The thin MQTT bridge is intentionally dumb: it applies domain-specific HA
services from the App's ``tag/out`` value.  Climate heat pumps need
``set_hvac_mode`` + ``set_temperature`` derived from the HeatPump logit
setpoint map (ported from the pre-SWD-262 coordinator actuation path).

Between planner samples the offset hold may publish ``set_temperature``
again. That publish is not the sample's actuator write: it restores the
offset the planner already commanded, and it does not solve or step the
estimator. See ``climate_offset_hold_decision``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
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


#: Climate entities accept targets on a 0.5 °C grid.
CLIMATE_TARGET_STEP_C = 0.5
#: At most one offset-hold write per zone in this window.
OFFSET_HOLD_MIN_INTERVAL_S = 60.0
#: A command older than this many sample periods is not restored.
OFFSET_HOLD_STALE_PERIODS = 2.0


def commanded_climate_offset(
    target: float,
    internal_temp: float,
    delta_sat: float,
) -> float:
    """Return the planner gap, clamped to the sigmoid saturation bound.

    The hold restores this gap. It does not compute a new, larger one.
    """

    offset = float(target) - float(internal_temp)
    limit = abs(float(delta_sat))
    if abs(offset) > limit:
        return math.copysign(limit, offset)
    return offset


def quantize_hold_target(
    internal_temp: float,
    offset: float,
    *,
    step: float = CLIMATE_TARGET_STEP_C,
) -> float:
    """Snap ``internal + offset`` onto ``step`` without exceeding the offset."""

    limit = float(internal_temp) + float(offset)
    grid = float(step)
    if grid <= 0.0:
        return limit
    if offset >= 0.0:
        ticks = math.floor((limit + 1e-9) / grid)
    else:
        ticks = math.ceil((limit - 1e-9) / grid)
    return ticks * grid


@dataclass(frozen=True)
class ClimateOffsetHold:
    """One decision from the between-sample climate offset hold."""

    write: bool
    reason: str
    target: float | None = None
    offset: float | None = None
    internal_temp: float | None = None
    zone: str | None = None


def climate_offset_hold_decision(
    *,
    enabled_flag: bool,
    system_enabled: bool,
    in_fallback: bool,
    now: float,
    period_s: float,
    entity_state: Any,
    entity_status: str,
    internal_temp: float | None,
    hold: Mapping[str, Any] | None,
) -> ClimateOffsetHold:
    """Decide whether to re-write a climate target between planner samples.

    The write is ``internal_temp + commanded_offset``, on the 0.5 °C step,
    and never past the offset stored at the last planner write. A missing
    flag, a disabled controller, fallback, a stale command, an unavailable
    or unknown entity, and HVAC ``off`` or ``cool`` all refuse the write.
    """

    zone = None if hold is None else hold.get("zone")
    zone_name = zone if isinstance(zone, str) else None
    if not enabled_flag:
        return ClimateOffsetHold(False, "flag_off", zone=zone_name)
    if not system_enabled:
        return ClimateOffsetHold(False, "disabled", zone=zone_name)
    if in_fallback:
        return ClimateOffsetHold(False, "fallback", zone=zone_name)
    if not hold or hold.get("commanded_offset") is None or hold.get("command_ts") is None:
        return ClimateOffsetHold(False, "no_command", zone=zone_name)

    state_text = "" if entity_state is None else str(entity_state).strip().lower()
    status = str(entity_status or "").upper()
    if status != "GOOD" or state_text in {"", "unavailable", "unknown", "none"}:
        reason = "unknown" if state_text == "unknown" else "unavailable"
        return ClimateOffsetHold(False, reason, zone=zone_name)
    if state_text in {"off", "cool"}:
        return ClimateOffsetHold(False, f"hvac_{state_text}", zone=zone_name)

    try:
        command_ts = float(hold["command_ts"])
        offset = float(hold["commanded_offset"])
        period = max(1.0, float(period_s))
    except (TypeError, ValueError):
        return ClimateOffsetHold(False, "no_command", zone=zone_name)
    if float(now) - command_ts > OFFSET_HOLD_STALE_PERIODS * period:
        return ClimateOffsetHold(False, "stale", zone=zone_name, offset=offset)
    if internal_temp is None:
        return ClimateOffsetHold(False, "unavailable", zone=zone_name, offset=offset)

    internal = float(internal_temp)
    last_internal = hold.get("last_internal")
    try:
        unchanged = last_internal is not None and abs(internal - float(last_internal)) <= 1e-6
    except (TypeError, ValueError):
        unchanged = False
    if unchanged:
        return ClimateOffsetHold(
            False, "internal_unchanged", offset=offset, internal_temp=internal, zone=zone_name
        )

    last_hold = hold.get("last_hold_ts")
    try:
        if last_hold is not None and float(now) - float(last_hold) < OFFSET_HOLD_MIN_INTERVAL_S:
            return ClimateOffsetHold(
                False, "rate_cap", offset=offset, internal_temp=internal, zone=zone_name
            )
    except (TypeError, ValueError):
        pass

    target = quantize_hold_target(internal, offset)
    last_target = hold.get("last_written_target")
    try:
        if last_target is not None and abs(target - float(last_target)) <= 1e-6:
            return ClimateOffsetHold(
                False,
                "target_unchanged",
                target=target,
                offset=offset,
                internal_temp=internal,
                zone=zone_name,
            )
    except (TypeError, ValueError):
        pass
    # The snapped gap must not be larger than the planner's offset.
    gap = target - internal
    if offset >= 0.0 and gap > offset + 1e-9:
        return ClimateOffsetHold(
            False, "offset_bound", target=target, offset=offset, internal_temp=internal, zone=zone_name
        )
    if offset < 0.0 and gap < offset - 1e-9:
        return ClimateOffsetHold(
            False, "offset_bound", target=target, offset=offset, internal_temp=internal, zone=zone_name
        )
    return ClimateOffsetHold(
        True,
        "internal_changed",
        target=target,
        offset=offset,
        internal_temp=internal,
        zone=zone_name,
    )


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
