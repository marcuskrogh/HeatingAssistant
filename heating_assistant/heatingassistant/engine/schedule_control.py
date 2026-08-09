"""Schedule resolution and MPC horizon trajectory helpers (App / engine).

Coordinator-free port of the classic ``schedule_control`` helpers removed in
SWD-262. Used by the HAOS App runtime to keep live sensors, forecast plots,
and MPC solves schedule-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from .const import DEFAULT_COMFORT_OFFSET, DEFAULT_SETPOINT
from .schedule import (
    EffectiveControlParams,
    RoomSchedule,
    build_schedule,
    control_params_at,
    resolve_effective_control_params,
)
from .schedule_migration import migrate_period_list


@dataclass
class ControlTrajectory:
    """Per-step schedule-projected control parameters for the MPC horizon.

    All arrays have shape ``(N,)`` where N is the prediction horizon.
    Room-keyed dicts contain one array per configured room (canonical name).
    """

    setpoints: dict[str, np.ndarray]
    comfort_offsets: dict[str, np.ndarray]
    q_scales: dict[str, np.ndarray]
    r_scales: dict[str, np.ndarray]
    enabled_steps: dict[str, np.ndarray]


def schedule_from_payload(payload: Mapping[str, Any] | Sequence[Any] | None) -> RoomSchedule:
    """Build a :class:`RoomSchedule` from a UI / options schedule payload."""

    if payload is None:
        return build_schedule([])
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        periods = [dict(p) for p in payload if isinstance(p, Mapping)]
        return build_schedule(migrate_period_list(periods))
    if isinstance(payload, Mapping):
        periods = payload.get("periods", [])
        if not isinstance(periods, list):
            periods = []
        return build_schedule(
            migrate_period_list([dict(p) for p in periods if isinstance(p, Mapping)])
        )
    return build_schedule([])


def schedule_enabled_flag(payload: Mapping[str, Any] | Sequence[Any] | None) -> bool:
    """Return whether the comfort schedule is active (not suspended)."""

    if isinstance(payload, Mapping):
        return bool(payload.get("enabled", True))
    return True


def resolve_room_effective_params(
    *,
    schedule_payload: Mapping[str, Any] | Sequence[Any] | None,
    base_setpoint: float,
    measured_temp: float | None,
    now: datetime,
    default_comfort_offset: float = DEFAULT_COMFORT_OFFSET,
    default_tracking_weight: float = 1.0,
    default_energy_weight: float = 1.0,
) -> EffectiveControlParams:
    """Resolve live effective control params for one room at ``now``."""

    schedule = schedule_from_payload(schedule_payload)
    if schedule.is_empty or not schedule_enabled_flag(schedule_payload):
        return EffectiveControlParams(
            setpoint=float(base_setpoint),
            comfort_offset=float(default_comfort_offset),
            tracking_weight=float(default_tracking_weight),
            energy_weight=float(default_energy_weight),
            enabled=True,
            period_name=None,
            mode=None,
        )
    return resolve_effective_control_params(
        schedule=schedule,
        base_setpoint=float(base_setpoint),
        measured_temp=measured_temp,
        now=now,
        default_comfort_offset=float(default_comfort_offset),
        default_tracking_weight=float(default_tracking_weight),
        default_energy_weight=float(default_energy_weight),
    )


def compute_control_trajectory(
    *,
    rooms: Sequence[Mapping[str, Any]],
    schedules_by_slug: Mapping[str, Mapping[str, Any] | Sequence[Any]],
    room_slug_fn,
    base_setpoints: Mapping[str, float],
    default_comfort_offsets: Mapping[str, float],
    room_enabled: Mapping[str, bool],
    now_local: datetime,
    n_steps: int,
    dt_seconds: float,
    current_effective: Mapping[str, EffectiveControlParams] | None = None,
) -> ControlTrajectory:
    """Build per-step setpoints / comfort corridors for the MPC horizon.

    Off periods carry forward the last comfort values (transparent reference)
    while marking ``enabled_steps`` False so callers can zero actuation and
    hide corridors on the plot.
    """

    traj = ControlTrajectory(
        setpoints={},
        comfort_offsets={},
        q_scales={},
        r_scales={},
        enabled_steps={},
    )
    n = max(0, int(n_steps))
    if n <= 0:
        return traj

    current_effective = current_effective or {}

    for room in rooms:
        name = room.get("name")
        if not isinstance(name, str) or not name:
            continue
        slug = room_slug_fn(name)
        payload = schedules_by_slug.get(slug)
        schedule = schedule_from_payload(payload)
        schedule_on = schedule_enabled_flag(payload)
        base_sp = float(base_setpoints.get(name, DEFAULT_SETPOINT))
        default_offset = float(
            default_comfort_offsets.get(name, DEFAULT_COMFORT_OFFSET)
        )

        sp_seq = np.empty(n, dtype=float)
        off_seq = np.empty(n, dtype=float)
        qw_seq = np.empty(n, dtype=float)
        rw_seq = np.empty(n, dtype=float)
        enabled_seq = np.ones(n, dtype=bool)

        current = current_effective.get(name)
        last_sp = current.setpoint if current is not None else base_sp
        last_off = current.comfort_offset if current is not None else default_offset
        last_qw = current.tracking_weight if current is not None else 1.0
        last_rw = current.energy_weight if current is not None else 1.0

        for k in range(n):
            t_k = now_local + timedelta(seconds=k * dt_seconds)
            if schedule_on and not schedule.is_empty:
                params = control_params_at(
                    schedule=schedule,
                    base_setpoint=base_sp,
                    t_future=t_k,
                    default_comfort_offset=default_offset,
                )
                if params is not None:
                    last_sp = params.setpoint
                    last_off = params.comfort_offset
                    last_qw = params.tracking_weight
                    last_rw = params.energy_weight
                else:
                    enabled_seq[k] = False
            sp_seq[k] = last_sp
            off_seq[k] = last_off
            qw_seq[k] = last_qw
            rw_seq[k] = last_rw

        if not bool(room_enabled.get(name, True)):
            enabled_seq[:] = False

        traj.setpoints[name] = sp_seq
        traj.comfort_offsets[name] = off_seq
        traj.q_scales[name] = qw_seq
        traj.r_scales[name] = rw_seq
        traj.enabled_steps[name] = enabled_seq

    return traj


def step_setpoint_offset(
    traj: ControlTrajectory | None,
    room_name: str,
    step_index: int,
    *,
    fallback_setpoint: float,
    fallback_offset: float,
    fallback_enabled: bool = True,
) -> tuple[float, float, bool]:
    """Return ``(setpoint, comfort_offset, enabled)`` for a forecast step."""

    if traj is None:
        return float(fallback_setpoint), float(fallback_offset), bool(fallback_enabled)
    sp = traj.setpoints.get(room_name)
    off = traj.comfort_offsets.get(room_name)
    en = traj.enabled_steps.get(room_name)
    if sp is None or off is None or step_index < 0 or step_index >= len(sp):
        return float(fallback_setpoint), float(fallback_offset), bool(fallback_enabled)
    enabled = bool(en[step_index]) if en is not None and step_index < len(en) else True
    return float(sp[step_index]), float(off[step_index]), enabled
