"""Thermal-parameter persistence and ML estimation lifecycle for the App engine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .const import (
    CONF_ESTIMATED_PARAMS,
    CONF_PE_MAX_COMPUTE_S,
    CONF_UPDATE_INTERVAL,
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_PARAMETER_ESTIMATION_HORIZON_HOURS,
    DEFAULT_PE_MAX_COMPUTE_S,
    DEFAULT_UPDATE_INTERVAL,
    ESTIMATION_HISTORY_SIZE,
)
from .history.window import select_recent_window
from .nmpc_timing import timing_from_options

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
        "ua_open": float(getattr(room, "ua_open", 0.0)),
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


_STRUCTURAL_KEYS = (
    "thermal_mass",
    "r_external",
    "internal_gain",
    "solar_scale",
    "c_air_fraction",
    "r_aw_fraction",
)


def _float_map(values: Mapping[str, Any] | None) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for key, value in dict(values or {}).items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def structural_param_fingerprint(
    estimated_params: Mapping[str, Mapping[str, Any]] | None,
    *,
    estimated_internal_gains: Mapping[str, Any] | None = None,
    estimated_solar_scales: Mapping[str, Any] | None = None,
    estimated_envelope_splits: Mapping[str, Mapping[str, Any]] | None = None,
) -> Dict[str, Dict[str, float]]:
    """Round structural θ per room so later Simulate can match this PE fit."""

    fingerprint: Dict[str, Dict[str, float]] = {}
    for room_name, params in dict(estimated_params or {}).items():
        entry: Dict[str, float] = {}
        if isinstance(params, Mapping):
            for key in ("thermal_mass", "r_external"):
                if key in params:
                    entry[key] = round(float(params[key]), 6)
        if estimated_internal_gains and room_name in estimated_internal_gains:
            entry["internal_gain"] = round(float(estimated_internal_gains[room_name]), 6)
        if estimated_solar_scales and room_name in estimated_solar_scales:
            entry["solar_scale"] = round(float(estimated_solar_scales[room_name]), 6)
        splits = (estimated_envelope_splits or {}).get(room_name)
        if isinstance(splits, Mapping):
            for key in ("c_air_fraction", "r_aw_fraction"):
                if key in splits:
                    entry[key] = round(float(splits[key]), 6)
        fingerprint[str(room_name)] = entry
    return fingerprint


def fingerprints_match(
    fit_fp: Mapping[str, Mapping[str, Any]] | None,
    room_params: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    """True when Simulate overrides still match the PE structural fingerprint."""

    if not fit_fp:
        return False
    overrides = room_params or {}
    if not overrides:
        return True
    for room_name, room_overrides in overrides.items():
        expected = fit_fp.get(str(room_name)) or {}
        if not isinstance(room_overrides, Mapping):
            continue
        for key in _STRUCTURAL_KEYS:
            if key not in room_overrides:
                continue
            if key not in expected:
                return False
            got = float(room_overrides[key])
            want = float(expected[key])
            if abs(got - want) > 1e-4 * max(1.0, abs(want)):
                return False
    return True


def t_wall_initial_by_dataset(
    dataset_ids: Sequence[str] | None,
    estimated_t_wall_initial: Mapping[str, Any] | None,
    estimated_t_wall_per_dataset: Sequence[Mapping[str, Any]] | None,
) -> Dict[str, Dict[str, float]]:
    ids = [str(item) for item in (dataset_ids or []) if item]
    if not ids:
        return {}
    per_ds = list(estimated_t_wall_per_dataset or [])
    by_dataset: Dict[str, Dict[str, float]] = {}
    if per_ds and len(per_ds) == len(ids):
        for dataset_id, block in zip(ids, per_ds):
            by_dataset[dataset_id] = _float_map(block)
        return by_dataset
    shared = _float_map(estimated_t_wall_initial)
    if shared:
        for dataset_id in ids:
            by_dataset[dataset_id] = dict(shared)
    return by_dataset


def pe_fit_record(
    estimated_params: Mapping[str, Mapping[str, Any]] | None,
    *,
    estimated_internal_gains: Mapping[str, Any] | None = None,
    estimated_solar_scales: Mapping[str, Any] | None = None,
    estimated_envelope_splits: Mapping[str, Mapping[str, Any]] | None = None,
    dataset_ids: Sequence[str] | None = None,
    estimated_t_wall_initial: Mapping[str, Any] | None = None,
    estimated_t_wall_per_dataset: Sequence[Mapping[str, Any]] | None = None,
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
) -> Dict[str, Any]:
    """Session / snapshot payload tying fitted Tw0 to the θ and data that produced it."""

    ids = [str(item) for item in (dataset_ids or []) if item]
    record: Dict[str, Any] = {
        "param_fingerprint": structural_param_fingerprint(
            estimated_params,
            estimated_internal_gains=estimated_internal_gains,
            estimated_solar_scales=estimated_solar_scales,
            estimated_envelope_splits=estimated_envelope_splits,
        ),
        "t_wall_initial": _float_map(estimated_t_wall_initial),
        "t_wall_initial_by_dataset": t_wall_initial_by_dataset(
            ids,
            estimated_t_wall_initial,
            estimated_t_wall_per_dataset,
        ),
        "dataset_ids": ids,
    }
    if window_start is not None:
        record["window_start"] = float(window_start)
    if window_end is not None:
        record["window_end"] = float(window_end)
    return record


def _lookup_tw0_by_dataset(
    fit: Mapping[str, Any],
    ids: Sequence[str],
    stored_ids: Sequence[str],
) -> Optional[Dict[str, float]]:
    if not stored_ids:
        return None
    by_dataset = fit.get("t_wall_initial_by_dataset") or {}
    if len(ids) == 1 and ids[0] in stored_ids:
        block = by_dataset.get(ids[0]) if isinstance(by_dataset, Mapping) else None
        mapped = _float_map(block if isinstance(block, Mapping) else None)
        if mapped:
            return mapped
        if len(stored_ids) == 1:
            return _float_map(fit.get("t_wall_initial")) or None
        return None
    if set(ids) == set(stored_ids):
        return _float_map(fit.get("t_wall_initial")) or None
    return None


def _lookup_tw0_by_window(
    fit: Mapping[str, Any],
    window_start: Optional[float],
    window_end: Optional[float],
) -> Optional[Dict[str, float]]:
    if window_start is None or window_end is None:
        return None
    try:
        if abs(float(fit.get("window_start")) - float(window_start)) > 1.0:
            return None
        if abs(float(fit.get("window_end")) - float(window_end)) > 1.0:
            return None
    except (TypeError, ValueError):
        return None
    return _float_map(fit.get("t_wall_initial")) or None


def lookup_fitted_t_wall_initial(
    fit: Mapping[str, Any] | None,
    *,
    dataset_ids: Sequence[str] | None = None,
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
    room_params: Mapping[str, Mapping[str, Any]] | None = None,
) -> Optional[Dict[str, float]]:
    """Return fitted Tw0 when this plot uses the same θ and the same PE data."""

    if not isinstance(fit, Mapping):
        return None
    if not fingerprints_match(fit.get("param_fingerprint"), room_params):
        return None
    ids = [str(item) for item in (dataset_ids or []) if item]
    stored_ids = [str(item) for item in (fit.get("dataset_ids") or []) if item]
    if ids:
        return _lookup_tw0_by_dataset(fit, ids, stored_ids)
    return _lookup_tw0_by_window(fit, window_start, window_end)


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
            "ua_open",
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
    ua_open: Optional[float] = None,
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
    if ua_open is not None:
        room.ua_open = max(0.0, float(ua_open))
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
    estimated_ua_open: Optional[Mapping[str, float]] = None,
    estimated_heater_scales: Optional[Mapping[str, float]] = None,
    estimated_solar_scales: Optional[Mapping[str, float]] = None,
    estimated_envelope_splits: Optional[Mapping[str, Mapping[str, float]]] = None,
    log_likelihood: Optional[float] = None,
    dataset_ids: Optional[Sequence[str]] = None,
    estimated_t_wall_initial: Optional[Mapping[str, float]] = None,
    estimated_t_wall_per_dataset: Optional[Sequence[Mapping[str, float]]] = None,
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
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
        if estimated_ua_open and room_name in estimated_ua_open:
            room.ua_open = max(0.0, float(estimated_ua_open[room_name]))
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

    tw_fit = pe_fit_record(
        estimated_params,
        estimated_internal_gains=estimated_internal_gains,
        estimated_solar_scales=estimated_solar_scales,
        estimated_envelope_splits=estimated_envelope_splits,
        dataset_ids=dataset_ids,
        estimated_t_wall_initial=estimated_t_wall_initial,
        estimated_t_wall_per_dataset=estimated_t_wall_per_dataset,
        window_start=window_start,
        window_end=window_end,
    )
    active: Dict[str, Any] = {
        "rooms": _snapshot_rooms(model, prev_rooms, set(estimated_params), now_iso, "ml"),
        "sources": _source_snapshot(heat_sources),
        "connections": dict(estimated_inter_room_r or {}),
        "estimated_at": now_iso,
        "source": "ml",
        "log_likelihood": log_likelihood,
        **tw_fit,
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
    dataset_ids: Optional[Sequence[str]] = None,
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
    executor: Any | None = None,
) -> Dict[str, Any]:
    """Run ML parameter estimation and optionally apply the result."""

    dt = float(options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL) or DEFAULT_UPDATE_INTERVAL)
    eff_horizon_hours = (
        float(horizon_hours)
        if horizon_hours is not None
        else float(options.get("parameter_estimation_horizon_hours", DEFAULT_PARAMETER_ESTIMATION_HORIZON_HOURS))
    )
    if history_override is not None:
        selected_history = list(history_override)
    else:
        selected_history = [dict(item) for item in history]
        if eff_horizon_hours > 0 and selected_history:
            selected_history = select_recent_window(selected_history, eff_horizon_hours * 3600.0, dt)

    from .parameter_estimator import KalmanMLEstimator

    timing = timing_from_options(
        options,
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    cap_s = float(options.get(CONF_PE_MAX_COMPUTE_S, DEFAULT_PE_MAX_COMPUTE_S) or DEFAULT_PE_MAX_COMPUTE_S)

    estimator = KalmanMLEstimator(
        rooms=list((getattr(model, "rooms", {}) or {}).values()),
        sources=list(heat_sources),
        dt=timing.dt_s,
        n_horizon_steps=timing.n_fast,
        origin_stride=timing.fast_substeps,
        max_compute_s=cap_s,
        use_nstep_pem=True,
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
            estimated_ua_open=result.get("estimated_ua_open", {}),
            estimated_heater_scales=result.get("estimated_heater_scales", {}),
            estimated_solar_scales=result.get("estimated_solar_scales", {}),
            estimated_envelope_splits=result.get("estimated_envelope_splits", {}),
            log_likelihood=result.get("log_likelihood"),
            dataset_ids=dataset_ids,
            estimated_t_wall_initial=result.get("estimated_t_wall_initial"),
            estimated_t_wall_per_dataset=result.get("estimated_t_wall_per_dataset"),
            window_start=window_start,
            window_end=window_end,
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
    "fingerprints_match",
    "lookup_fitted_t_wall_initial",
    "pe_fit_record",
    "restore_estimated_parameters",
    "structural_param_fingerprint",
    "t_wall_initial_by_dataset",
    "revert_parameters",
    "store_identified_parameters",
]
