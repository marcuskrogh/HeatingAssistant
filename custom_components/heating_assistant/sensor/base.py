"""Shared sensor mixins and helpers for Heating Assistant sensors."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..coordinator import HeatingAssistantCoordinator
from ..kpi import RoomSnapshot
from ..naming import slugify

_LOGGER = logging.getLogger(__name__)

class _LiveValueSensorMixin:
    """Keep a sensor available even when a coordinator update cycle fails.

    The live readout sensors (measured / filtered temperature, setpoint, solar
    gain, power, model-fit KPIs, …) read their value from coordinator *instance
    attributes* that persist between cycles and are refreshed by BOTH the
    scheduled MPC tick and the fast UI refresh.  By default ``CoordinatorEntity``
    ties ``available`` to ``coordinator.last_update_success``, so a single failed
    update (e.g. a transient sensor/weather/solver hiccup) makes every one of
    these entities report ``unavailable`` — the dashboard then shows no KPIs,
    setpoints, or measurements until the next *successful* full update, even
    though the last-known values are perfectly usable.

    Mirroring the forecast sensors (which already pin ``available`` to ``True``),
    this mixin keeps the cached live value on screen.  ``native_value`` still
    returns ``None`` — surfaced as ``unknown`` — when there is genuinely no data
    yet, so nothing fabricated is ever displayed.
    """

    @property
    def available(self) -> bool:
        return True


class _ConstraintSensorBase(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Shared base for the MPC soft-constraint bound sensors.

    Exposes both the current scalar (used by entities cards) and a
    timestamped ``forecast`` attribute that spans the MPC horizon.  The
    band ``setpoint ± offset`` is constant unless the setpoint changes, so
    the line is flat by construction — but it stays anchored to the
    forecast time window, matching the predicted-temperature trace.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _sign: float = 1.0
    _attr_field: str = "constraint"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator

    @property
    def native_value(self) -> Optional[float]:
        if not self._coordinator.is_room_enabled(self._room_name):
            return None
        bound = _constraint_bound(
            self._coordinator, self._room_name, self._sign,
        )
        return None if bound is None else round(bound, 2)

    @property
    def extra_state_attributes(self) -> dict:
        sign = self._sign
        field = self._attr_field

        def _value(coord: Any, room_name: str) -> Optional[float]:
            return _constraint_bound(coord, room_name, sign)

        traj = getattr(self._coordinator, "_control_trajectory", None)
        step_bounds = None
        if traj is not None:
            sp_seq = traj.setpoints.get(self._room_name)
            off_seq = traj.comfort_offsets.get(self._room_name)
            if sp_seq is not None and off_seq is not None:
                import numpy as np
                step_bounds = sp_seq + sign * off_seq

        return _build_horizon_forecast(
            self._coordinator,
            self._room_name,
            field=field,
            value=_value,
            step_values=step_bounds,
        )


# ---------------------------------------------------------------------------
# Helpers for setpoint / constraint forecast attributes
# ---------------------------------------------------------------------------

def _setpoint_value(
    coordinator: HeatingAssistantCoordinator, room_name: str,
) -> Optional[float]:
    room = coordinator.model.rooms.get(room_name)
    if room is None:
        return None
    return round(float(room.setpoint), 2)


def _constraint_bound(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    sign: float,
) -> Optional[float]:
    """Compute effective comfort-region bound for a room, or None if unknown.

    Precedence:
      1. Explicit ``room.comfort_corridor_high`` / ``comfort_corridor_low``
         (set externally or by occupancy/mode overrides).
      2. Derived bound ``room.setpoint ± room.comfort_offset``.
    """
    room = coordinator.model.rooms.get(room_name)
    if room is None:
        return None
    explicit_attr = "comfort_corridor_high" if sign > 0 else "comfort_corridor_low"
    explicit = getattr(room, explicit_attr, None)
    if explicit is not None:
        return float(explicit)
    offset = getattr(room, "comfort_offset", None)
    if offset is None:
        return None
    return float(room.setpoint) + sign * float(offset)


def _kpi_room_snapshot(
    coordinator: HeatingAssistantCoordinator, room_name: str,
) -> RoomSnapshot:
    """Build a :class:`~.kpi.RoomSnapshot` from live coordinator state."""
    room = coordinator.model.rooms.get(room_name)
    setpoint = float(room.setpoint) if room is not None else None
    return RoomSnapshot(
        slug=slugify(room_name),
        room_active=coordinator.is_room_enabled(room_name),
        temperature_filtered=coordinator.filtered_temperatures.get(room_name),
        temperature_measured=coordinator.measured_temperatures.get(room_name),
        constraint_lower=_constraint_bound(coordinator, room_name, -1.0),
        constraint_upper=_constraint_bound(coordinator, room_name, 1.0),
        setpoint=setpoint,
    )


def _kpi_room_snapshots(
    coordinator: HeatingAssistantCoordinator,
) -> list[RoomSnapshot]:
    """Per-room KPI inputs for all model rooms."""
    return [
        _kpi_room_snapshot(coordinator, name)
        for name in coordinator.model.room_names
    ]


def _build_horizon_forecast(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    field: str,
    value: Any,
    step_values: "Optional[np.ndarray]" = None,
) -> Dict[str, Any]:
    """Build a per-step ``forecast`` attribute that spans the MPC horizon.

    Produces ``horizon + 1`` entries (bridge at ``now`` + one per step)
    so dashboard ``data_generator`` blocks can plot the value as a line
    in the forecast region.  When ``value`` returns ``None`` we still emit
    the entries so the chart renders a continuous trace — these scalars
    (setpoint, constraint bounds) are not MPC outputs, so they remain
    valid even on solver failure.

    When ``step_values`` is provided (a per-step array of shape ``(N,)``
    from the schedule-projected control trajectory) it is used instead of
    repeating the scalar ``value`` result, so the dashboard shows a
    time-varying comfort corridor that reflects scheduled setpoint /
    comfort-offset changes over the horizon.
    """
    horizon = getattr(coordinator, "_horizon", None)
    dt = getattr(coordinator, "dt", None)
    if horizon is None or dt is None:
        return {
            "forecast": [],
            "horizon_steps": 0,
            "step_seconds": None,
        }

    now = getattr(coordinator, "now_utc", None) or datetime.now(tz=timezone.utc)
    current = value(coordinator, room_name)
    forecast: List[Dict[str, Any]] = []
    # Bridge at "now" (k=0) plus one entry per OCP step (k=1…N), so the
    # line spans the same window as the temperature_forecast trace.
    # step_values[k] corresponds to the OCP step starting at now + k*dt,
    # matching the indexing used by _compute_control_trajectory.
    n_steps = len(step_values) if step_values is not None else 0
    for k in range(int(horizon) + 1):
        step_time = now + timedelta(seconds=float(dt) * k)
        entry: Dict[str, Any] = {"time": step_time.isoformat()}
        if step_values is not None and n_steps > 0:
            idx = min(k, n_steps - 1)
            entry[field] = round(float(step_values[idx]), 2)
        elif current is not None:
            entry[field] = round(current, 2)
        forecast.append(entry)

    return {
        "forecast": forecast,
        "horizon_steps": int(horizon),
        "step_seconds": float(dt),
    }


def _closed_loop_fit_for_room(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Return (r_squared, rmse, n_samples) from the history buffer, if available."""
    from .model_diagnostics import compute_model_fit_metrics

    try:
        room_idx = coordinator.model.room_names.index(room_name)
    except ValueError:
        return None, None, None

    predictions: list[float] = []
    measurements: list[float] = []
    history = getattr(coordinator, "history_buffer", None) or []
    for record in history:
        y = record.get("y", [])
        y_pred = record.get("y_pred")
        if y_pred is None:
            continue
        if room_idx < len(y) and room_idx < len(y_pred):
            predictions.append(y_pred[room_idx])
            measurements.append(y[room_idx])

    if len(predictions) < 2:
        return None, None, len(predictions)

    try:
        metrics = compute_model_fit_metrics(predictions, measurements, room_name)
        return metrics.r_squared, metrics.rmse, metrics.n_samples
    except Exception:
        return None, None, len(predictions)


def _room_estimation_provenance(
    snapshot: Optional[dict],
    room_name: str,
) -> tuple[bool, Optional[str]]:
    """Return per-room estimation flags from the persisted snapshot."""
    if not snapshot:
        return False, None
    room_snap = snapshot.get("rooms", {}).get(room_name)
    if not isinstance(room_snap, dict):
        return False, None
    if "is_estimated" in room_snap:
        is_estimated = bool(room_snap.get("is_estimated"))
        estimated_at = room_snap.get("estimated_at") if is_estimated else None
        return is_estimated, estimated_at
    # Legacy snapshots listed every room without a provenance flag.
    return False, None
