"""System-identification experiment lifecycle and MPC input clamps."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import numpy as np

from ..const import EXPERIMENT_RELAXED_COMFORT_OFFSET
from ..datasets import build_dataset
from ..experiments import apply_safety_bounds, ceil_to_grid, excitation_fraction

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def is_experiment_active(
    coordinator: HeatingAssistantCoordinator, room_name: str
) -> bool:
    """Return whether an identification experiment is exciting ``room_name``."""
    from ..dashboard import slugify as _slugify

    active = getattr(coordinator, "_experiment_active_rooms", None)
    if not active:
        return False
    return _slugify(room_name) in active


def build_experiment_clamps(
    coordinator: HeatingAssistantCoordinator, now: datetime
) -> Dict[str, np.ndarray]:
    """Advance experiments and build per-source horizon input clamps for the MPC."""
    from ..dashboard import slugify as _slugify

    active_rooms: Set[str] = set()
    manager = coordinator.experiment_manager
    now_ts = now.timestamp()

    transitions = manager.advance(now_ts)
    for finished in transitions.get("completed", []):
        on_experiment_completed(coordinator, finished)
    if transitions.get("started") or transitions.get("completed"):
        persist_experiments(coordinator)

    clamps: Dict[str, np.ndarray] = {}
    horizon_steps: Dict[str, List[bool]] = {}
    N = int(coordinator._horizon)
    dt = float(coordinator.dt)
    horizon_end = now_ts + N * dt
    if not any(
        e.start_ts < horizon_end and now_ts < e.end_ts and not e.is_terminal()
        for e in manager.experiments
    ):
        coordinator._experiment_active_rooms = active_rooms
        coordinator._experiment_horizon_steps = horizon_steps
        return clamps

    slug_to_room = {_slugify(rn): rn for rn in coordinator.model.room_names}

    for src in coordinator.heat_sources:
        room_slug = _slugify(src.room)
        if coordinator.is_window_override_active(src.room):
            continue

        room_name = slug_to_room.get(room_slug, src.room)
        can_cool = bool(getattr(src, "can_cool", False))
        arr = np.full(N, np.nan, dtype=float)
        clamped_any = False
        measured = coordinator.measured_temperatures.get(room_name)
        for k in range(N):
            t_k = now_ts + k * dt
            exp = manager.active_for_room(room_slug, t_k)
            if exp is None:
                continue
            frac = excitation_fraction(exp, t_k, can_cool=can_cool)
            if k == 0:
                frac = apply_safety_bounds(
                    frac, measured, exp.min_temp, exp.max_temp, exp.step_pct,
                )
                active_rooms.add(room_slug)
            arr[k] = frac
            clamped_any = True
        if clamped_any:
            clamps[src.name] = arr
            mask = horizon_steps.setdefault(room_name, [False] * N)
            for k in range(N):
                if not np.isnan(arr[k]):
                    mask[k] = True

    coordinator._experiment_active_rooms = active_rooms
    coordinator._experiment_horizon_steps = horizon_steps
    return clamps


def relax_experiment_comfort(
    coordinator: HeatingAssistantCoordinator, control_traj: Any
) -> None:
    """Neutralise comfort penalties on the steps an experiment governs."""
    if control_traj is None or not coordinator._experiment_horizon_steps:
        return
    for room_name, mask in coordinator._experiment_horizon_steps.items():
        q_seq = control_traj.q_scales.get(room_name)
        off_seq = control_traj.comfort_offsets.get(room_name)
        for k, governed in enumerate(mask):
            if not governed:
                continue
            if q_seq is not None and k < len(q_seq):
                q_seq[k] = 0.0
            if off_seq is not None and k < len(off_seq):
                off_seq[k] = EXPERIMENT_RELAXED_COMFORT_OFFSET


def on_experiment_completed(
    coordinator: HeatingAssistantCoordinator, exp: Any
) -> None:
    """React to an experiment finishing: auto-save its window as a dataset."""
    if not getattr(exp, "auto_save", False) or exp.dataset_id is not None:
        return
    if coordinator.dataset_store is None:
        return
    try:
        coordinator.hass.async_create_task(async_autosave_experiment(coordinator, exp))
    except Exception:  # pragma: no cover - defensive
        _LOGGER.warning(
            "Experiment %s: failed to schedule dataset auto-save", exp.id,
            exc_info=True,
        )


async def async_autosave_experiment(
    coordinator: HeatingAssistantCoordinator, exp: Any
) -> None:
    """Snapshot a completed experiment's window into a stored dataset."""
    records = await async_collect_window_records(
        coordinator, exp.start_ts, exp.end_ts
    )
    if not records:
        _LOGGER.info(
            "Experiment %s finished with no captured records; no dataset saved",
            exp.id,
        )
        return
    name = exp.name or f"Experiment {exp.room_name}"
    dataset = build_dataset(
        name,
        records,
        room_name=exp.room_name,
        room_slug=exp.room_slug,
        source="experiment",
        notes=f"Auto-saved {exp.signal_type} experiment",
        window_start=exp.start_ts,
        window_end=exp.end_ts,
        experiment=exp.to_dict(),
    )
    await coordinator.dataset_store.async_add(dataset)
    exp.dataset_id = dataset["id"]
    persist_experiments(coordinator)
    coordinator.async_update_listeners()
    _LOGGER.info(
        "Experiment %s auto-saved as dataset '%s' (%d records)",
        exp.id, name, dataset["record_count"],
    )


async def async_collect_window_records(
    coordinator: HeatingAssistantCoordinator,
    start_ts: float,
    end_ts: float,
) -> List[Dict[str, Any]]:
    """Return observation records covering ``[start_ts, end_ts]``."""
    records: List[Dict[str, Any]] = []
    if coordinator.id_history_store is not None:
        try:
            records = await coordinator.id_history_store.async_query_range(
                start_ts, end_ts
            )
        except Exception:
            _LOGGER.warning(
                "Dataset snapshot: JSONL query failed for [%s, %s]",
                start_ts, end_ts, exc_info=True,
            )
    if not records:
        records = [
            dict(r)
            for r in coordinator._history_buffer
            if start_ts <= float(r.get("timestamp", 0.0)) < end_ts
        ]
    return records


def schedule_experiment(
    coordinator: HeatingAssistantCoordinator, exp: Any
) -> Any:
    """Register a new experiment and persist the experiment list."""
    interval_s = int(coordinator._update_interval_s)
    ref_ts = (
        coordinator.now_utc.timestamp()
        if getattr(coordinator, "now_utc", None) is not None
        else time.time()
    )
    exp.start_ts = ceil_to_grid(exp.start_ts, ref_ts, interval_s)
    exp.end_ts = ceil_to_grid(exp.end_ts, ref_ts, interval_s)
    coordinator.experiment_manager.add(exp)
    coordinator.experiment_manager.prune_terminal()
    persist_experiments(coordinator)
    return exp


def cancel_experiment(
    coordinator: HeatingAssistantCoordinator, experiment_id: str
) -> bool:
    """Cancel a scheduled/running experiment.  Returns True if it changed."""
    cancelled = coordinator.experiment_manager.cancel(experiment_id)
    if cancelled is None:
        removed = coordinator.experiment_manager.remove(experiment_id)
        if removed:
            persist_experiments(coordinator)
        return removed
    coordinator._experiment_active_rooms.discard(cancelled.room_slug)
    persist_experiments(coordinator)
    return True


def delete_experiment(
    coordinator: HeatingAssistantCoordinator, experiment_id: str
) -> bool:
    """Remove an experiment from the list outright, regardless of status."""
    exp = coordinator.experiment_manager.get(experiment_id)
    if exp is not None and not exp.is_terminal():
        coordinator.experiment_manager.cancel(experiment_id)
        coordinator._experiment_active_rooms.discard(exp.room_slug)
    removed = coordinator.experiment_manager.remove(experiment_id)
    if removed:
        persist_experiments(coordinator)
    return removed


def persist_experiments(coordinator: HeatingAssistantCoordinator) -> None:
    """Persist the experiment list (best-effort, never blocks a cycle)."""
    if coordinator.experiment_store is None:
        return
    try:
        coordinator.hass.async_create_task(
            coordinator.experiment_store.async_save(coordinator.experiment_manager)
        )
    except Exception:  # pragma: no cover - defensive
        _LOGGER.debug("Failed to persist experiments", exc_info=True)
