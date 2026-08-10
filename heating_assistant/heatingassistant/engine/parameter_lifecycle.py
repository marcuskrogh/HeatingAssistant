"""Thermal-parameter persistence and ML estimation lifecycle for the App engine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .const import (
    CONF_ESTIMATED_PARAMS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_IDENTIFICATION_HORIZON_HOURS,
    DEFAULT_UPDATE_INTERVAL,
    ESTIMATION_HISTORY_SIZE,
)
from .history.window import select_recent_window

_LOGGER = logging.getLogger(__name__)
PARAMETER_HISTORY_KEY = "parameter_history"
ESTIMATION_HISTORY_KEY = "estimation_history"
_MAX_PARAMETER_HISTORY = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _room_names(model: Any) -> List[str]:
    names = getattr(model, "room_names", None)
    if names is not None:
        return list(names)
    rooms = getattr(model, "rooms", {}) or {}
    return list(rooms.keys())


def _rebuild_model(model: Any) -> None:
    rebuild = getattr(model, "rebuild_derived_parameters", None)
    if callable(rebuild):
        rebuild()
    build_matrices = getattr(model, "_build_matrices", None)
    if callable(build_matrices):
        try:
            model._C, model._A, model._B_ext = build_matrices()
        except ValueError:
            pass


def _source_snapshot(heat_sources: Sequence[Any]) -> Dict[str, Dict[str, float]]:
    return {
        str(source.name): {"power_scale": float(getattr(source, "power_scale", 1.0))}
        for source in heat_sources
        if getattr(source, "name", None) is not None
    }


def _connection_snapshot(model: Any) -> Dict[str, float]:
    rooms = getattr(model, "rooms", {}) or {}
    snapshot: Dict[str, float] = {}
    seen: set[tuple[str, str]] = set()
    for room_name, room in rooms.items():
        for conn in getattr(room, "connections", []) or []:
            other = getattr(conn, "connected_room", None)
            if other is None:
                continue
            pair = tuple(sorted((str(room_name), str(other))))
            if pair in seen:
                continue
            seen.add(pair)
            snapshot[f"{pair[0]}:{pair[1]}"] = float(getattr(conn, "r_value"))
    return snapshot


def _room_snapshot_entry(
    room_name: str,
    room: Any,
    *,
    previous: Mapping[str, Any] | None = None,
    just_estimated: bool = False,
    estimated_at: str | None = None,
    source: str | None = None,
) -> Dict[str, Any]:
    prev = previous if isinstance(previous, Mapping) else {}
    entry: Dict[str, Any] = {
        "thermal_mass": float(getattr(room, "thermal_mass")),
        "r_external": float(getattr(room, "r_external")),
        "internal_gain": float(getattr(room, "internal_gain", 0.0)),
        "solar_scale": float(getattr(room, "solar_scale", 1.0)),
        "c_air_fraction": float(getattr(room, "c_air_fraction", 0.05)),
        "r_aw_fraction": float(getattr(room, "r_aw_fraction", 0.05)),
        "is_estimated": bool(just_estimated or prev.get("is_estimated", False)),
    }
    if just_estimated:
        entry["estimated_at"] = estimated_at
        entry["estimation_source"] = source
    elif prev.get("estimated_at"):
        entry["estimated_at"] = prev.get("estimated_at")
        if prev.get("estimation_source"):
            entry["estimation_source"] = prev.get("estimation_source")
    return entry


def _active_from_snapshot(snapshot: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        return {}
    active = snapshot.get("active")
    if isinstance(active, Mapping):
        return dict(active)
    rooms = snapshot.get("rooms")
    if isinstance(rooms, Mapping):
        return {
            "rooms": dict(rooms),
            "estimated_at": snapshot.get("estimated_at"),
            "source": snapshot.get("source", "ml"),
            "rmse": snapshot.get("rmse"),
        }
    return {}


def _history_from_options(
    options: MutableMapping[str, Any],
    existing_snapshot: Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    explicit = options.get(PARAMETER_HISTORY_KEY)
    if isinstance(explicit, list):
        return [dict(item) for item in explicit if isinstance(item, Mapping)]
    if isinstance(existing_snapshot, Mapping):
        history = existing_snapshot.get("history")
        if isinstance(history, list):
            return [dict(item) for item in history if isinstance(item, Mapping)]
    return []


def _persist_snapshot(
    options: MutableMapping[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    history = list(snapshot.get("history", []))
    active = snapshot.get("active")
    if isinstance(active, Mapping):
        options[PARAMETER_HISTORY_KEY] = [dict(active), *history]
    else:
        options[PARAMETER_HISTORY_KEY] = history
    options[CONF_ESTIMATED_PARAMS] = snapshot
    return snapshot


def estimated_params_snapshot(options: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the persisted estimated-parameter snapshot from options."""

    snapshot = options.get(CONF_ESTIMATED_PARAMS)
    return dict(snapshot) if isinstance(snapshot, Mapping) else None


def restore_estimated_parameters(
    model: Any,
    heat_sources: Sequence[Any],
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply a persisted estimation snapshot to in-memory model objects."""

    active = _active_from_snapshot(snapshot) or dict(snapshot)
    rooms = getattr(model, "rooms", {}) or {}
    for room_name, params in dict(active.get("rooms", {})).items():
        room = rooms.get(room_name)
        if room is None or not isinstance(params, Mapping):
            continue
        for attr in (
            "thermal_mass",
            "r_external",
            "internal_gain",
            "solar_scale",
            "c_air_fraction",
            "r_aw_fraction",
        ):
            if attr in params:
                setattr(room, attr, float(params[attr]))

    sources = active.get("sources") or snapshot.get("sources") or {}
    if isinstance(sources, Mapping):
        apply_heater_scales(heat_sources, None, {
            name: params.get("power_scale", 1.0) if isinstance(params, Mapping) else params
            for name, params in sources.items()
        })

    connections = active.get("connections") or snapshot.get("connections") or {}
    if isinstance(connections, Mapping):
        apply_inter_room_resistances(model, connections)

    _rebuild_model(model)
    return active


def apply_heater_scales(
    heat_sources: Sequence[Any],
    options: Optional[MutableMapping[str, Any]],
    heater_scales: Mapping[str, float],
    *,
    model: Any | None = None,
) -> Optional[Dict[str, Any]]:
    """Apply heater power scales and optionally persist a source snapshot."""

    if not heater_scales:
        return None
    applied: Dict[str, float] = {}
    for source in heat_sources:
        name = str(getattr(source, "name", ""))
        if name in heater_scales:
            source.power_scale = float(heater_scales[name])
            applied[name] = float(source.power_scale)
    if not applied or options is None:
        return None
    existing = estimated_params_snapshot(options) or {}
    active = _active_from_snapshot(existing)
    snapshot = dict(existing)
    if active:
        snapshot["active"] = active
    snapshot["sources"] = _source_snapshot(heat_sources)
    if model is not None:
        snapshot["rooms"] = _snapshot_rooms(model, active.get("rooms", {}), set(), _now_iso(), "manual")
        snapshot["connections"] = _connection_snapshot(model)
    return _persist_snapshot(options, snapshot)


def apply_inter_room_resistances(
    model: Any,
    estimated_inter_room_r: Mapping[str, float],
) -> None:
    """Apply inter-room thermal resistance estimates to model connections."""

    rooms = getattr(model, "rooms", {}) or {}
    for key, r_value in estimated_inter_room_r.items():
        parts = str(key).split(":", 1)
        if len(parts) != 2:
            continue
        room_a, room_b = parts
        for name, other in ((room_a, room_b), (room_b, room_a)):
            room = rooms.get(name)
            if room is None:
                continue
            for conn in getattr(room, "connections", []) or []:
                if getattr(conn, "connected_room", None) == other:
                    conn.r_value = float(r_value)


def _snapshot_rooms(
    model: Any,
    prev_rooms: Mapping[str, Any] | None,
    just_estimated_rooms: set[str],
    estimated_at: str,
    source: str,
) -> Dict[str, Any]:
    rooms = getattr(model, "rooms", {}) or {}
    return {
        name: _room_snapshot_entry(
            name,
            rooms[name],
            previous=prev_rooms.get(name, {}) if isinstance(prev_rooms, Mapping) else {},
            just_estimated=name in just_estimated_rooms,
            estimated_at=estimated_at,
            source=source,
        )
        for name in _room_names(model)
        if name in rooms
    }


def store_identified_parameters(
    model: Any,
    heat_sources: Sequence[Any],
    options: MutableMapping[str, Any],
    room_name: str,
    thermal_mass: float,
    r_external: float,
    *,
    source: str = "manual",
    rmse: Optional[float] = None,
    internal_gain: Optional[float] = None,
    solar_scale: Optional[float] = None,
    c_air_fraction: Optional[float] = None,
    r_aw_fraction: Optional[float] = None,
    heater_scales: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Apply one room's identified parameters and persist a full snapshot."""

    rooms = getattr(model, "rooms", {}) or {}
    if room_name not in rooms:
        raise ValueError(f"Room '{room_name}' not found in model")

    now_iso = _now_iso()
    existing = estimated_params_snapshot(options) or {}
    old_active = _active_from_snapshot(existing)
    existing_history = _history_from_options(options, existing)
    if existing_history and old_active and existing_history[0].get("rooms") == old_active.get("rooms"):
        history = existing_history[1:]
    else:
        history = existing_history
    if old_active.get("rooms"):
        history.insert(0, old_active)
    history = history[:_MAX_PARAMETER_HISTORY]

    room = rooms[room_name]
    room.thermal_mass = float(thermal_mass)
    room.r_external = float(r_external)
    if internal_gain is not None:
        room.internal_gain = float(internal_gain)
    if solar_scale is not None:
        room.solar_scale = float(solar_scale)
    if c_air_fraction is not None:
        room.c_air_fraction = float(c_air_fraction)
    if r_aw_fraction is not None:
        room.r_aw_fraction = float(r_aw_fraction)
    if heater_scales:
        for src in heat_sources:
            name = str(getattr(src, "name", ""))
            if name in heater_scales:
                src.power_scale = float(heater_scales[name])

    _rebuild_model(model)
    prev_rooms = old_active.get("rooms", {}) or existing.get("rooms", {})
    active: Dict[str, Any] = {
        "rooms": _snapshot_rooms(model, prev_rooms, {room_name}, now_iso, source),
        "estimated_at": now_iso,
        "source": source,
    }
    if rmse is not None:
        active["rmse"] = float(rmse)

    snapshot: Dict[str, Any] = {
        "active": active,
        "history": history,
        "rooms": active["rooms"],
        "sources": _source_snapshot(heat_sources),
        "connections": _connection_snapshot(model),
        "estimated_at": now_iso,
        "log_likelihood": None,
    }
    return _persist_snapshot(options, snapshot)


def apply_manual_parameters(
    model: Any,
    heat_sources: Sequence[Any],
    options: MutableMapping[str, Any],
    room_name: str,
    thermal_mass: float,
    r_external: float,
) -> Dict[str, Any]:
    """Store manually-entered room thermal parameters."""

    return store_identified_parameters(
        model,
        heat_sources,
        options,
        room_name,
        thermal_mass,
        r_external,
        source="manual",
    )


def apply_estimated_parameters(
    model: Any,
    heat_sources: Sequence[Any],
    options: MutableMapping[str, Any],
    estimated_params: Mapping[str, Mapping[str, float]],
    estimated_inter_room_r: Optional[Mapping[str, float]] = None,
    *,
    estimated_internal_gains: Optional[Mapping[str, float]] = None,
    estimated_heater_scales: Optional[Mapping[str, float]] = None,
    estimated_solar_scales: Optional[Mapping[str, float]] = None,
    estimated_envelope_splits: Optional[Mapping[str, Mapping[str, float]]] = None,
    log_likelihood: Optional[float] = None,
) -> Dict[str, Any]:
    """Apply ML-estimated parameters and persist them to the options dict."""

    rooms = getattr(model, "rooms", {}) or {}
    for room_name, params in estimated_params.items():
        room = rooms.get(room_name)
        if room is None:
            continue
        if "thermal_mass" in params:
            room.thermal_mass = float(params["thermal_mass"])
        if "r_external" in params:
            room.r_external = float(params["r_external"])
        if estimated_internal_gains and room_name in estimated_internal_gains:
            room.internal_gain = float(estimated_internal_gains[room_name])
        if estimated_solar_scales and room_name in estimated_solar_scales:
            room.solar_scale = float(estimated_solar_scales[room_name])
        if estimated_envelope_splits and room_name in estimated_envelope_splits:
            splits = estimated_envelope_splits[room_name]
            if "c_air_fraction" in splits:
                room.c_air_fraction = float(splits["c_air_fraction"])
            if "r_aw_fraction" in splits:
                room.r_aw_fraction = float(splits["r_aw_fraction"])

    if estimated_heater_scales:
        for source in heat_sources:
            name = str(getattr(source, "name", ""))
            if name in estimated_heater_scales:
                source.power_scale = float(estimated_heater_scales[name])

    if estimated_inter_room_r:
        apply_inter_room_resistances(model, estimated_inter_room_r)

    _rebuild_model(model)
    now_iso = _now_iso()
    existing = estimated_params_snapshot(options) or {}
    old_active = _active_from_snapshot(existing)
    existing_history = _history_from_options(options, existing)
    if existing_history and old_active and existing_history[0].get("rooms") == old_active.get("rooms"):
        existing_history = existing_history[1:]
    if old_active.get("rooms"):
        existing_history.insert(0, old_active)
    history = existing_history[:_MAX_PARAMETER_HISTORY]
    prev_rooms = old_active.get("rooms", {}) or existing.get("rooms", {})

    active: Dict[str, Any] = {
        "rooms": _snapshot_rooms(model, prev_rooms, set(estimated_params), now_iso, "ml"),
        "sources": _source_snapshot(heat_sources),
        "connections": dict(estimated_inter_room_r or {}),
        "estimated_at": now_iso,
        "source": "ml",
        "log_likelihood": log_likelihood,
    }
    snapshot: Dict[str, Any] = {
        "active": active,
        "history": history,
        "rooms": active["rooms"],
        "sources": active["sources"],
        "connections": active["connections"],
        "estimated_at": now_iso,
        "log_likelihood": log_likelihood,
    }
    return _persist_snapshot(options, snapshot)


def revert_parameters(
    model: Any,
    heat_sources: Sequence[Any],
    options: MutableMapping[str, Any],
    history_index: int,
) -> Dict[str, Any]:
    """Promote a parameter-history entry to active and persist the new order."""

    entries = _history_from_options(options, estimated_params_snapshot(options) or {})
    if history_index < 0 or history_index >= len(entries):
        raise ValueError(
            f"history_index {history_index} out of range (0..{len(entries) - 1})"
        )
    target = dict(entries.pop(history_index))
    current = _active_from_snapshot(estimated_params_snapshot(options) or {})
    if current.get("rooms"):
        entries.insert(0, current)
    restore_estimated_parameters(model, heat_sources, target)
    now_iso = _now_iso()
    target["estimated_at"] = now_iso
    target["source"] = target.get("source", "reverted")
    snapshot = {
        "active": target,
        "history": entries[:_MAX_PARAMETER_HISTORY],
        "rooms": target.get("rooms", {}),
        "sources": _source_snapshot(heat_sources),
        "connections": _connection_snapshot(model),
        "estimated_at": now_iso,
        "log_likelihood": target.get("log_likelihood"),
    }
    return _persist_snapshot(options, snapshot)


def delete_parameter_history(
    options: MutableMapping[str, Any],
    history_index: int,
) -> Dict[str, Any]:
    """Delete a parameter-history entry from the UI-facing list.

    The panel shows ``options[parameter_history]`` as
    ``[active, *history]`` (newest / current first) and deletes by that
    displayed index. Index 0 removes the active snapshot (promoting the next
    entry when present); later indices remove past history rows only.
    """

    snapshot = estimated_params_snapshot(options) or {}
    displayed = _history_from_options(options, snapshot)
    if not displayed and isinstance(snapshot.get("history"), list):
        active = _active_from_snapshot(snapshot)
        history_only = [
            dict(item) for item in snapshot["history"] if isinstance(item, Mapping)
        ]
        displayed = ([dict(active)] if active.get("rooms") else []) + history_only
    if history_index < 0 or history_index >= len(displayed):
        raise ValueError(
            f"history_index {history_index} out of range (0..{len(displayed) - 1})"
        )
    displayed.pop(history_index)
    if not displayed:
        options[PARAMETER_HISTORY_KEY] = []
        options.pop(CONF_ESTIMATED_PARAMS, None)
        return {}

    active = dict(displayed[0])
    history = [dict(item) for item in displayed[1:]]
    new_snapshot = dict(snapshot)
    new_snapshot["active"] = active
    new_snapshot["rooms"] = active.get("rooms", new_snapshot.get("rooms", {}))
    new_snapshot["history"] = history
    new_snapshot["estimated_at"] = active.get("estimated_at", new_snapshot.get("estimated_at"))
    new_snapshot["source"] = active.get("source", new_snapshot.get("source"))
    if "rmse" in active:
        new_snapshot["rmse"] = active.get("rmse")
    return _persist_snapshot(options, new_snapshot)


async def async_estimate_parameters_ml(
    model: Any,
    heat_sources: Sequence[Any],
    options: MutableMapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    *,
    apply_params: bool = True,
    horizon_hours: Optional[float] = None,
    locked_params: Optional[Dict[str, Any]] = None,
    history_override: Optional[List[Dict[str, Any]]] = None,
    dataset_start_timestamps: Optional[List[float]] = None,
    executor: Any | None = None,
) -> Dict[str, Any]:
    """Run ML parameter estimation and optionally apply the result."""

    dt = float(options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL) or DEFAULT_UPDATE_INTERVAL)
    eff_horizon_hours = (
        float(horizon_hours)
        if horizon_hours is not None
        else float(options.get("identification_horizon_hours", DEFAULT_IDENTIFICATION_HORIZON_HOURS))
    )
    if history_override is not None:
        selected_history = list(history_override)
    else:
        selected_history = [dict(item) for item in history]
        if eff_horizon_hours > 0 and selected_history:
            selected_history = select_recent_window(selected_history, eff_horizon_hours * 3600.0, dt)

    from .parameter_estimator import KalmanMLEstimator

    estimator = KalmanMLEstimator(
        rooms=list((getattr(model, "rooms", {}) or {}).values()),
        sources=list(heat_sources),
        dt=dt,
    )

    def _run_estimate() -> Dict[str, Any]:
        return estimator.estimate(
            selected_history,
            locked_params=locked_params,
            dataset_start_timestamps=dataset_start_timestamps,
        )

    if executor is not None:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, _run_estimate)
    else:
        result = await asyncio.to_thread(_run_estimate)

    if result.get("success") and apply_params:
        apply_estimated_parameters(
            model,
            heat_sources,
            options,
            result["estimated_params"],
            result.get("estimated_inter_room_r", {}),
            estimated_internal_gains=result.get("estimated_internal_gains", {}),
            estimated_heater_scales=result.get("estimated_heater_scales", {}),
            estimated_solar_scales=result.get("estimated_solar_scales", {}),
            estimated_envelope_splits=result.get("estimated_envelope_splits", {}),
            log_likelihood=result.get("log_likelihood"),
        )

    estimation_history = list(options.get(ESTIMATION_HISTORY_KEY, []))
    estimation_history.append(
        {
            "estimated_at": _now_iso(),
            "success": bool(result.get("success")),
            "log_likelihood": (
                float(result["log_likelihood"])
                if isinstance(result.get("log_likelihood"), (int, float))
                else None
            ),
            "applied": bool(result.get("success")) and apply_params,
            "n_rooms": len(_room_names(model)),
            "n_sources": len(heat_sources),
        }
    )
    options[ESTIMATION_HISTORY_KEY] = estimation_history[-ESTIMATION_HISTORY_SIZE:]
    return result


__all__ = [
    "ESTIMATION_HISTORY_KEY",
    "PARAMETER_HISTORY_KEY",
    "apply_estimated_parameters",
    "apply_heater_scales",
    "apply_inter_room_resistances",
    "apply_manual_parameters",
    "async_estimate_parameters_ml",
    "delete_parameter_history",
    "estimated_params_snapshot",
    "restore_estimated_parameters",
    "revert_parameters",
    "store_identified_parameters",
]
