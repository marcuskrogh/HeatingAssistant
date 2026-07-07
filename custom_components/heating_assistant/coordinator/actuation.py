"""Heat-source actuation: read delivered power and write MPC actions to HA."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from ..const import ACTUATION_WATCHDOG_MIN_INTERVAL_S, DEFAULT_IDLE_OFFSET
from ..heat_sources import HeatPump, HeatSource
from .types import _coerce_opt_float

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def _heater_should_be_off(
    coordinator: HeatingAssistantCoordinator, src: HeatSource
) -> bool:
    """Return True when ``apply_actions`` would turn this source off."""
    room_enabled = coordinator.is_room_enabled(src.room)
    window_override_active = coordinator.is_window_override_active(src.room)
    experiment_active = coordinator.is_experiment_active(src.room)
    return not (
        coordinator._system_enabled
        and (room_enabled or experiment_active)
        and not window_override_active
    )


def heater_entity_matches_expected(
    coordinator: HeatingAssistantCoordinator, src: HeatSource
) -> bool:
    """Return True when the heater entity state/mode matches what we expect."""
    entity_id = src.heater_entity
    if not entity_id:
        return True

    ha_state = coordinator.hass.states.get(entity_id)
    if ha_state is None or ha_state.state in ("unknown", "unavailable"):
        return True

    actual = str(ha_state.state).lower()
    domain = entity_id.split(".")[0]

    if _heater_should_be_off(coordinator, src):
        return actual == "off"

    fraction = float(coordinator.actions.get(src.name, 0.0))
    if domain == "switch":
        return (actual == "on") == (fraction > 0.5)
    if domain == "number":
        value = _coerce_opt_float(ha_state.state)
        expected = round(fraction * 100)
        return value is not None and int(round(value)) == expected
    if domain == "climate":
        return actual != "off"
    return True


async def async_verify_heater_entities(
    coordinator: HeatingAssistantCoordinator, outdoor_temp: float
) -> bool:
    """Re-apply commands when a heater entity state/mode does not match intent.

    Compares each configured heater entity's live HA state against the
    setting ``apply_actions`` would write (on/off for switches, value for
    numbers, off vs active mode for climate).  Runs on the fast UI cadence
    so a missed command — e.g. after a restart during window override — is
    corrected without waiting for the next MPC tick.

    Returns True when a corrective ``apply_actions`` call was made.
    """
    if not coordinator._system_enabled or not coordinator.actions:
        return False

    mismatched = [
        src.name
        for src in coordinator.heat_sources
        if not heater_entity_matches_expected(coordinator, src)
    ]
    if not mismatched:
        return False

    now_ts = coordinator.now_utc.timestamp()
    last_ts = float(getattr(coordinator, "_actuation_watchdog_last_ts", 0.0))
    if now_ts - last_ts < ACTUATION_WATCHDOG_MIN_INTERVAL_S:
        return False

    coordinator._actuation_watchdog_last_ts = now_ts
    _LOGGER.warning(
        "Heater watchdog: entity state mismatch for %s — re-applying commands",
        ", ".join(mismatched),
    )
    try:
        await apply_actions(coordinator, outdoor_temp)
    except Exception:
        _LOGGER.warning(
            "Heater watchdog: failed to re-apply commands",
            exc_info=True,
        )
        return False
    return True


def read_delivered_actions(
    coordinator: HeatingAssistantCoordinator, outdoor_temp: float
) -> Dict[str, float]:
    """Return the *actually delivered* control fraction for every source.

    Used when the dashboard stop switch is off: instead of the controller's
    freshly computed (but un-applied) optimal actions, read each heater
    entity's real state back from Home Assistant and derive the fraction it
    is currently delivering.  Each source's ``current_power`` is updated to
    match so the power sensors report what the hardware is doing, and the
    controller's EKF is notified of the true applied input via
    :meth:`HeatingController.notify_applied_u` so predictions stay
    consistent on the next cycle.

    ``switch`` and ``number`` heaters report an exact delivered fraction.
    For ``climate`` heaters the instantaneous modulation is not observable,
    so it is estimated from the entity's reported ``hvac_action`` and the
    gap between its target and current temperature (inverting the
    proportional setpoint this integration writes when commanding it).
    """
    controller = getattr(coordinator, "controller", None)
    applied: Dict[str, float] = {}
    for src in coordinator.heat_sources:
        frac = read_delivered_fraction(coordinator, src)
        set_source_power(coordinator, src, frac, outdoor_temp)
        applied[src.name] = frac
        if controller is not None:
            controller.notify_applied_u(src.name, frac)
    return applied


def read_delivered_fraction(
    coordinator: HeatingAssistantCoordinator, src: HeatSource
) -> float:
    """Read one heat source's actually-delivered control fraction.

    Returns a value in ``[-1, 1]`` (negative = active cooling).  Returns
    ``0.0`` when the heater entity is missing, unavailable, or off.
    """
    entity_id = src.heater_entity
    if not entity_id:
        return 0.0
    state = coordinator.hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return 0.0
    domain = entity_id.split(".")[0]
    if domain == "switch":
        return 1.0 if state.state == "on" else 0.0
    if domain == "number":
        value = _coerce_opt_float(state.state)
        if value is None:
            return 0.0
        return max(0.0, min(1.0, value / 100.0))
    if domain == "climate":
        return read_delivered_fraction_climate(coordinator, src, state)
    return 0.0


def read_delivered_fraction_climate(
    coordinator: HeatingAssistantCoordinator, src: HeatSource, state: Any
) -> float:
    """Best-effort delivered fraction for a climate heater.

    Modulating climate entities do not report instantaneous power, so the
    magnitude is inferred from the gap between the entity's own target and
    current temperature — the inverse of the proportional setpoint this
    integration writes when commanding the unit — gated by the reported
    ``hvac_action`` so an idle/off unit reads as zero.
    """
    if state.state == "off":
        return 0.0
    attrs = getattr(state, "attributes", {}) or {}
    action = attrs.get("hvac_action")
    if action in ("off", "idle"):
        return 0.0

    target = _coerce_opt_float(attrs.get("temperature"))
    current = _coerce_opt_float(attrs.get("current_temperature"))

    # Heat pumps command a logit offset from the room comfort setpoint, not
    # a linear offset from the entity's internal temperature.  Invert that
    # mapping so readback reaches ±1 at saturation instead of capping at
    # delta_sat / max_temp_offset (≈ 0.6 with the defaults).
    if isinstance(src, HeatPump):
        base_temp: Optional[float] = None
        if hasattr(coordinator, "model") and src.room in getattr(
            coordinator.model, "rooms", {}
        ):
            base_temp = coordinator.get_room_setpoint(src.room)
        elif current is not None:
            base_temp = current
        if target is not None and base_temp is not None:
            gap = src.fraction_from_setpoint_offset(target - base_temp)
        else:
            gap = None
    else:
        offset = float(getattr(src, "max_temp_offset", 0.0) or 0.0)
        gap = None
        if target is not None and current is not None and offset > 0.0:
            gap = (target - current) / offset

    # Cooling: only meaningful for sources that can actively cool.
    if action == "cooling" or (gap is not None and gap < 0.0):
        if getattr(src, "can_cool", False):
            return gap if gap is not None else -1.0
        return 0.0

    # Heating.
    if gap is not None:
        return (
            max(0.0, min(1.0, gap))
            if not isinstance(src, HeatPump)
            else max(0.0, gap)
        )
    # No usable temperature telemetry: trust hvac_action if it reports the
    # unit is heating, otherwise assume it is delivering nothing.
    return 1.0 if action == "heating" else 0.0


def set_source_power(
    coordinator: HeatingAssistantCoordinator,
    src: HeatSource,
    frac: float,
    outdoor_temp: float,
) -> None:
    """Set ``src.current_power`` from a delivered fraction in ``[-1, 1]``."""
    if frac >= 0.0:
        src.set_power(frac, outdoor_temp)
    elif getattr(src, "can_cool", False) and hasattr(
        src, "display_smooth_thermal_power"
    ):
        src._current_power = src.display_smooth_thermal_power(
            frac,
            outdoor_temp,
        )
    else:
        src._current_power = 0.0


def climate_internal_temp(
    coordinator: HeatingAssistantCoordinator, src: HeatSource, state: Any
) -> float:
    """Best estimate of a climate unit's own internal temperature [°C].

    Prefers the entity's ``current_temperature`` attribute (the sensor the
    unit's built-in thermostat regulates against); falls back to the
    modelled room air temperature when the attribute is missing or
    unparsable.
    """
    attrs = getattr(state, "attributes", {})
    raw_temp = attrs.get("current_temperature")
    if raw_temp is not None:
        try:
            return float(raw_temp)
        except (ValueError, TypeError):
            pass
    return coordinator.model.rooms[src.room].temperature


def climate_hp_command(
    coordinator: HeatingAssistantCoordinator,
    src: HeatPump,
    fraction: float,
    internal_temp: float,
    outdoor_temp: float,
    state: Any,
) -> Tuple[str, float]:
    """``(hvac_mode, target_temp)`` for an enabled heat-pump climate source.

    The target is the unit's internal temperature plus the logit-mapped
    offset for the held MPC ``fraction``, so re-applying it as the internal
    temperature drifts keeps the commanded gap — and the power the HP
    modulates to deliver for that gap — constant over the interval.  The HA
    mode string is resolved against the modes the entity advertises.
    """
    attrs = getattr(state, "attributes", {})
    supported_modes = attrs.get("hvac_modes", [])
    if src.hvac_mode == "heat_cool":
        if "heat_cool" in supported_modes:
            hvac_mode_str = "heat_cool"
        elif "auto" in supported_modes:
            hvac_mode_str = "auto"
        else:
            hvac_mode_str = "heat_cool"
    elif src.hvac_mode == "cool":
        if "cool" in supported_modes:
            hvac_mode_str = "cool"
        elif "dry" in supported_modes:
            hvac_mode_str = "dry"
        else:
            hvac_mode_str = "fan_only"
    else:
        hvac_mode_str = "heat"
    target_temp = src.target_temperature(fraction, internal_temp, outdoor_temp)
    return hvac_mode_str, target_temp


def climate_thermostat_command(
    coordinator: HeatingAssistantCoordinator,
    src: HeatSource,
    fraction: float,
    internal_temp: float,
    room_temp: float,
    room_setpoint: float,
) -> Tuple[str, float]:
    """``(hvac_mode, target_temp)`` for an enabled non-HP climate source.

    Heating (``fraction > 0`` and room at/below setpoint): the target is the
    unit's internal temperature plus the fraction-scaled offset, floored at
    the room comfort setpoint.  Otherwise (idle or cooling protection): the
    target is pushed below the internal temperature by
    ``DEFAULT_IDLE_OFFSET`` plus any room overshoot so the unit's own
    thermostat does not fire.  Anchoring to ``internal_temp`` keeps the gap
    — and therefore the delivered power — constant when the command is
    re-asserted between MPC ticks.
    """
    if fraction > 0.0 and room_temp <= room_setpoint:
        target_temp = max(
            room_setpoint,
            src.target_temperature(fraction, internal_temp),
        )
        return "heat", target_temp
    overshoot = max(0.0, room_temp - room_setpoint)
    target_temp = internal_temp - (DEFAULT_IDLE_OFFSET + overshoot)
    return "heat", target_temp


async def reapply_climate_setpoints(
    coordinator: HeatingAssistantCoordinator, outdoor_temp: float
) -> None:
    """Re-assert climate setpoints between MPC ticks to hold the gap constant.

    The MPC commands a constant input over each ``update_interval`` (a ZOH
    hold), but a climate unit regulates to an *absolute* target using its own
    internal sensor: written once per tick, that target lets the delivered
    power sag as the unit warms toward it.  Re-anchoring the target to the
    unit's current internal temperature on the fast UI cadence keeps
    (target − internal_temp), and hence the modulating power, ~constant
    across the interval — which is what the MPC plant model and the
    parameter identification assume.

    Only ``set_temperature`` is re-issued, and only for ``climate`` entities:
    the MPC ``fraction`` is held fixed (no EKF/optimiser work and no
    ``notify_applied_u`` — the applied-input record must carry one value per
    tick), the hvac mode was already set by the last full tick, and
    ``number`` / ``switch`` units are commanded as power/duty directly so
    they need no re-anchoring.
    """
    if not coordinator._system_enabled or not coordinator.actions:
        return
    for src in coordinator.heat_sources:
        entity_id = src.heater_entity
        if not entity_id or entity_id.split(".")[0] != "climate":
            continue
        room_enabled = coordinator.is_room_enabled(src.room)
        window_override_active = coordinator.is_window_override_active(src.room)
        experiment_active = coordinator.is_experiment_active(src.room)
        effective_room_enabled = (
            coordinator._system_enabled
            and (room_enabled or experiment_active)
            and not window_override_active
        )
        if not effective_room_enabled:
            # Unit is held off by the last full tick / window path; there is
            # nothing to re-anchor while it is not heating.
            continue
        state = coordinator.hass.states.get(entity_id)
        if state is None:
            continue
        fraction = float(coordinator.actions.get(src.name, 0.0))
        internal_temp = climate_internal_temp(coordinator, src, state)
        if isinstance(src, HeatPump):
            _mode, target_temp = climate_hp_command(
                coordinator,
                src,
                fraction,
                internal_temp,
                outdoor_temp,
                state,
            )
        else:
            room_temp = coordinator.model.rooms[src.room].temperature
            room_setpoint = coordinator.get_room_setpoint(src.room)
            _mode, target_temp = climate_thermostat_command(
                coordinator,
                src,
                fraction,
                internal_temp,
                room_temp,
                room_setpoint,
            )
        try:
            await coordinator.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, "temperature": target_temp},
                blocking=False,
            )
        except Exception:
            _LOGGER.debug(
                "Fast setpoint re-apply failed for %s",
                entity_id,
                exc_info=True,
            )



async def apply_actions(
    coordinator: HeatingAssistantCoordinator, outdoor_temp: float
) -> None:
    """Write the computed set-point fractions to heater entities via HA services."""
    for src in coordinator.heat_sources:
        fraction = coordinator.actions.get(src.name, 0.0)
        room_enabled = coordinator.is_room_enabled(src.room)
        window_override_active = coordinator.is_window_override_active(src.room)
        # A running identification experiment may deliberately drive heaters
        # in a room the comfort schedule has switched off (e.g. overnight),
        # so an active experiment counts as "enabled" here.  Window overrides
        # and the global stop switch still force the room off for safety.
        experiment_active = coordinator.is_experiment_active(src.room)
        effective_room_enabled = (
            coordinator._system_enabled
            and (room_enabled or experiment_active)
            and not window_override_active
        )
        controller = getattr(coordinator, "controller", None)

        # If the room is disabled, force fraction to 0 (turn off).
        if not effective_room_enabled:
            fraction = 0.0
            if controller is not None:
                controller.notify_applied_u(src.name, 0.0)

        entity_id = src.heater_entity
        if not entity_id:
            continue
        # Map fraction to 0–100 % and call climate/number/switch service
        state = coordinator.hass.states.get(entity_id)
        if state is None:
            continue
        domain = entity_id.split(".")[0]
        if domain == "climate":
            # For climate entities we need to ensure the heat pump
            # actually modulates to the desired power output.
            #
            # Heat pump climate entities use a three-state strategy:
            #   - fraction < 0  → cool mode (active cooling), with
            #     "cool" preferred, then "dry", then "fan_only"
            #   - fraction == 0 AND room_temp > setpoint
            #     → cool mode to remove heat (same mode preference order)
            #   - fraction == 0 AND room_temp ≤ setpoint
            #     → stay in heat mode, set target below internal temp
            #       (HP idles with no temperature gap)
            #   - fraction > 0  → heat mode, offset-based setpoint
            if isinstance(src, HeatPump):
                if not effective_room_enabled:
                    src.set_power(0.0, outdoor_temp)
                    if controller is not None:
                        controller.notify_applied_u(src.name, 0.0)
                    await coordinator.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": "off"},
                        blocking=False,
                    )
                    continue

                # Anchor the logit offset to the unit's current internal
                # temperature so the commanded gap (target − internal_temp)
                # — and therefore the power the HP modulates to deliver for
                # that gap — stays constant when the setpoint is re-asserted
                # on the fast cadence between MPC ticks.  Holding the gap is
                # what makes the delivered power honour the MPC's constant-
                # input (ZOH) assumption across the whole interval; a fixed
                # absolute target instead lets the power sag as the room
                # warms toward it.
                internal_temp = climate_internal_temp(coordinator, src, state)
                hvac_mode_str, target_temp = climate_hp_command(
                    coordinator,
                    src,
                    fraction,
                    internal_temp,
                    outdoor_temp,
                    state,
                )

                await coordinator.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": entity_id, "hvac_mode": hvac_mode_str},
                    blocking=False,
                )
                await coordinator.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity_id, "temperature": target_temp},
                    blocking=False,
                )
            else:
                # Non-heat-pump climate entity (e.g. electric heater with a
                # built-in thermostat).  Disabled rooms are turned off;
                # otherwise the target is computed by the same internal-
                # temperature-anchored helper the fast re-apply path uses, so
                # the commanded gap — and the delivered power — is held
                # constant across the MPC interval rather than sagging as the
                # unit warms toward a once-per-tick absolute setpoint.  The
                # idle/cooling-protection branch likewise tracks the internal
                # temperature so the thermostat never fires while idle.
                if not effective_room_enabled:
                    # Room explicitly disabled – turn the entity off.
                    await coordinator.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": "off"},
                        blocking=False,
                    )
                else:
                    internal_temp = climate_internal_temp(coordinator, src, state)
                    room_temp = coordinator.model.rooms[src.room].temperature
                    room_setpoint = coordinator.get_room_setpoint(src.room)
                    hvac_mode_str, target_temp = climate_thermostat_command(
                        coordinator,
                        src,
                        fraction,
                        internal_temp,
                        room_temp,
                        room_setpoint,
                    )
                    await coordinator.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": hvac_mode_str},
                        blocking=False,
                    )
                    await coordinator.hass.services.async_call(
                        "climate",
                        "set_temperature",
                        {"entity_id": entity_id, "temperature": target_temp},
                        blocking=False,
                    )
        elif domain == "number":
            await coordinator.hass.services.async_call(
                "number",
                "set_value",
                {
                    "entity_id": entity_id,
                    # Disabled/off-scheduled room should always apply zero output.
                    "value": 0
                    if not effective_room_enabled
                    else round(fraction * 100),
                },
                blocking=False,
            )
        elif domain == "switch":
            # Disabled/off-scheduled room should always switch the unit off.
            if not effective_room_enabled:
                service = "turn_off"
            else:
                service = "turn_on" if fraction > 0.5 else "turn_off"
            await coordinator.hass.services.async_call(
                "switch",
                service,
                {"entity_id": entity_id},
                blocking=False,
            )
