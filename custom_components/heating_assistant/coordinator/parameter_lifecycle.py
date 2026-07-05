"""Thermal-parameter persistence, application, and ML estimation lifecycle."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..const import (
    CONF_ESTIMATED_PARAMS,
    CONF_HEAT_SOURCES,
    CONF_ROOMS,
    DEFAULT_IDENTIFICATION_HORIZON_HOURS,
)
from .model_builders import build_heat_sources, build_house_model
from .types import _coerce_interval_seconds

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def restore_estimated_parameters(
    coordinator: HeatingAssistantCoordinator, snapshot: Dict[str, Any]
) -> None:
    """Apply a persisted estimation snapshot to the in-memory model objects."""
    if "rooms" not in snapshot and isinstance(snapshot.get("active"), dict):
        snapshot = snapshot["active"]
    for room_name, params in snapshot.get("rooms", {}).items():
        if room_name not in coordinator.model.rooms:
            continue
        room = coordinator.model.rooms[room_name]
        if "thermal_mass" in params:
            room.thermal_mass = float(params["thermal_mass"])
        if "r_external" in params:
            room.r_external = float(params["r_external"])
        if "internal_gain" in params:
            room.internal_gain = float(params["internal_gain"])
        if "solar_scale" in params:
            room.solar_scale = float(params["solar_scale"])
        if "c_air_fraction" in params:
            room.c_air_fraction = float(params["c_air_fraction"])
        if "r_aw_fraction" in params:
            room.r_aw_fraction = float(params["r_aw_fraction"])

    for src_name, src_params in snapshot.get("sources", {}).items():
        for src in coordinator.heat_sources:
            if src.name == src_name:
                src.power_scale = float(src_params.get("power_scale", 1.0))

    for key, r_val in snapshot.get("connections", {}).items():
        parts = key.split(":", 1)
        if len(parts) != 2:
            continue
        room_a, room_b = parts
        for name in (room_a, room_b):
            if name not in coordinator.model.rooms:
                continue
            other = room_b if name == room_a else room_a
            for conn in coordinator.model.rooms[name].connections:
                if conn.connected_room == other:
                    conn.r_value = float(r_val)

    coordinator.model.rebuild_derived_parameters()
    (
        coordinator.model._C,
        coordinator.model._A,
        coordinator.model._B_ext,
    ) = coordinator.model._build_matrices()

    coordinator._estimation_timestamp = snapshot.get("estimated_at")
    coordinator._estimation_log_likelihood = snapshot.get("log_likelihood")
    _LOGGER.info("Restored persisted estimated parameters from entry.data")


def reset_estimated_parameters(coordinator: HeatingAssistantCoordinator) -> None:
    """Discard persisted estimation and revert the live model to configured defaults."""
    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is not None:
        new_data = {
            k: v for k, v in real_entry.data.items() if k != CONF_ESTIMATED_PARAMS
        }
        coordinator.hass.config_entries.async_update_entry(real_entry, data=new_data)

    rooms_cfg: List[Dict[str, Any]] = coordinator._entry.data.get(CONF_ROOMS, [])
    sources_cfg: List[Dict[str, Any]] = coordinator._entry.data.get(CONF_HEAT_SOURCES, [])
    coordinator.model = build_house_model(rooms_cfg)
    coordinator.heat_sources = coordinator._drop_orphaned_sources(
        build_heat_sources(sources_cfg)
    )

    for _room_name, _sp in coordinator._base_setpoint.items():
        if _room_name in coordinator.model.rooms:
            coordinator.model.rooms[_room_name].setpoint = _sp
    coordinator._rebuild_sources_by_room()
    coordinator._build_controller()

    coordinator._estimation_timestamp = None
    coordinator._estimation_log_likelihood = None
    _LOGGER.info("Estimated parameters reset to configured defaults")


def apply_manual_parameters(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    thermal_mass: float,
    r_external: float,
) -> None:
    store_identified_parameters(
        coordinator, room_name, thermal_mass, r_external, source="manual"
    )


def apply_heater_scales(
    coordinator: HeatingAssistantCoordinator,
    heater_scales: Dict[str, float],
) -> None:
    if not heater_scales:
        return

    applied: Dict[str, float] = {}
    for src in coordinator.heat_sources:
        if src.name in heater_scales:
            src.power_scale = float(heater_scales[src.name])
            applied[src.name] = src.power_scale

    if not applied:
        return

    coordinator._build_controller()

    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is not None:
        snap = dict(coordinator.estimated_params_snapshot or {})
        snap["sources"] = {
            src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
            for src in coordinator.heat_sources
        }
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snap},
        )

    _LOGGER.info("Applied heater power scales: %s", applied)


def store_identified_parameters(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    thermal_mass: float,
    r_external: float,
    source: str = "manual",
    rmse: Optional[float] = None,
    internal_gain: Optional[float] = None,
    solar_scale: Optional[float] = None,
    c_air_fraction: Optional[float] = None,
    r_aw_fraction: Optional[float] = None,
    heater_scales: Optional[Dict[str, float]] = None,
) -> None:
    if room_name not in coordinator.model.rooms:
        raise ValueError(f"Room '{room_name}' not found in model")

    now_iso = datetime.now(timezone.utc).isoformat()

    existing_snap = {}
    try:
        existing_snap = coordinator.estimated_params_snapshot or {}
    except Exception:
        pass

    if "active" not in existing_snap:
        old_active = {
            "rooms": existing_snap.get("rooms", {}),
            "estimated_at": existing_snap.get("estimated_at", now_iso),
            "source": "manual",
        }
        history: list = []
    else:
        old_active = dict(existing_snap["active"])
        history = list(existing_snap.get("history", []))

    if old_active.get("rooms"):
        history.insert(0, old_active)
        history = history[:10]

    room = coordinator.model.rooms[room_name]
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
        for src in coordinator.heat_sources:
            if src.name in heater_scales:
                src.power_scale = float(heater_scales[src.name])

    coordinator.model.rebuild_derived_parameters()
    (
        coordinator.model._C,
        coordinator.model._A,
        coordinator.model._B_ext,
    ) = coordinator.model._build_matrices()
    coordinator._build_controller()

    prev_rooms = old_active.get("rooms", {}) or existing_snap.get("rooms", {})

    def _room_snapshot_entry(r_name: str, r_obj: Any) -> Dict[str, Any]:
        prev = prev_rooms.get(r_name, {}) if isinstance(prev_rooms.get(r_name), dict) else {}
        just_estimated = r_name == room_name
        entry: Dict[str, Any] = {
            "thermal_mass": r_obj.thermal_mass,
            "r_external": r_obj.r_external,
            "internal_gain": float(getattr(r_obj, "internal_gain", 0.0)),
            "solar_scale": float(getattr(r_obj, "solar_scale", 1.0)),
            "c_air_fraction": float(getattr(r_obj, "c_air_fraction", 0.05)),
            "r_aw_fraction": float(getattr(r_obj, "r_aw_fraction", 0.05)),
            "is_estimated": bool(just_estimated or prev.get("is_estimated", False)),
        }
        if just_estimated:
            entry["estimated_at"] = now_iso
            entry["estimation_source"] = source
        elif prev.get("estimated_at"):
            entry["estimated_at"] = prev.get("estimated_at")
            if prev.get("estimation_source"):
                entry["estimation_source"] = prev.get("estimation_source")
        return entry

    new_active: Dict[str, Any] = {
        "rooms": {
            name: _room_snapshot_entry(name, r)
            for name, r in coordinator.model.rooms.items()
        },
        "estimated_at": now_iso,
        "source": source,
    }
    if rmse is not None:
        new_active["rmse"] = rmse

    coordinator._estimation_timestamp = now_iso

    snapshot: Dict[str, Any] = {
        "active": new_active,
        "history": history,
        "rooms": new_active["rooms"],
        "sources": {
            src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
            for src in coordinator.heat_sources
        },
        "connections": {},
        "estimated_at": now_iso,
        "log_likelihood": None,
    }

    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is not None:
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snapshot},
        )
    _LOGGER.info(
        "Stored identified parameters for room '%s': thermal_mass=%.0f J/K, "
        "r_external=%.5f K/W (source=%s)",
        room_name,
        thermal_mass,
        r_external,
        source,
    )


def revert_parameters(
    coordinator: HeatingAssistantCoordinator, room_name: str, history_index: int
) -> None:
    if room_name not in coordinator.model.rooms:
        raise ValueError(f"Room '{room_name}' not found in model")

    existing_snap = {}
    try:
        existing_snap = coordinator.estimated_params_snapshot or {}
    except Exception:
        pass

    if "active" not in existing_snap:
        raise ValueError("No parameter history available (old format)")

    current_active = dict(existing_snap["active"])
    history = list(existing_snap.get("history", []))

    if history_index < 0 or history_index >= len(history):
        raise ValueError(
            f"history_index {history_index} out of range "
            f"(0..{len(history) - 1})"
        )

    target_entry = history.pop(history_index)
    history.insert(0, current_active)
    history = history[:10]

    target_rooms = target_entry.get("rooms", {})
    if room_name in target_rooms:
        params = target_rooms[room_name]
        room = coordinator.model.rooms[room_name]
        room.thermal_mass = float(params["thermal_mass"])
        room.r_external = float(params["r_external"])
    else:
        raise ValueError(f"Room '{room_name}' not found in history entry")

    coordinator.model.rebuild_derived_parameters()
    (
        coordinator.model._C,
        coordinator.model._A,
        coordinator.model._B_ext,
    ) = coordinator.model._build_matrices()
    coordinator._build_controller()

    now_iso = datetime.now(timezone.utc).isoformat()
    new_active: Dict[str, Any] = {
        "rooms": {
            name: {
                "thermal_mass": r.thermal_mass,
                "r_external": r.r_external,
                "internal_gain": float(getattr(r, "internal_gain", 0.0)),
            }
            for name, r in coordinator.model.rooms.items()
        },
        "estimated_at": now_iso,
        "source": target_entry.get("source", "reverted"),
    }
    coordinator._estimation_timestamp = now_iso

    snapshot: Dict[str, Any] = {
        "active": new_active,
        "history": history,
        "rooms": new_active["rooms"],
        "sources": {
            src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
            for src in coordinator.heat_sources
        },
        "connections": {},
        "estimated_at": now_iso,
        "log_likelihood": None,
    }
    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is not None:
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snapshot},
        )
    _LOGGER.info(
        "Reverted parameters for room '%s' to history index %d",
        room_name,
        history_index,
    )


def delete_parameter_history(
    coordinator: HeatingAssistantCoordinator, history_index: int
) -> None:
    existing_snap = {}
    try:
        existing_snap = coordinator.estimated_params_snapshot or {}
    except Exception:
        pass

    history = list(existing_snap.get("history", []))
    if history_index < 0 or history_index >= len(history):
        raise ValueError(
            f"history_index {history_index} out of range "
            f"(0..{len(history) - 1})"
        )

    history.pop(history_index)
    new_snap = dict(existing_snap)
    new_snap["history"] = history

    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is not None:
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: new_snap},
        )
    _LOGGER.info("Deleted parameter history entry at index %d", history_index)


def apply_estimated_parameters(
    coordinator: HeatingAssistantCoordinator,
    estimated_params: Dict[str, Dict[str, float]],
    estimated_inter_room_r: Optional[Dict[str, float]] = None,
    estimated_internal_gains: Optional[Dict[str, float]] = None,
    estimated_heater_scales: Optional[Dict[str, float]] = None,
    estimated_solar_scales: Optional[Dict[str, float]] = None,
    estimated_envelope_splits: Optional[Dict[str, Dict[str, float]]] = None,
    log_likelihood: Optional[float] = None,
) -> None:
    for room_name, params in estimated_params.items():
        if room_name not in coordinator.model.rooms:
            continue
        room = coordinator.model.rooms[room_name]
        room.thermal_mass = float(params["thermal_mass"])
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
        for src in coordinator.heat_sources:
            if src.name in estimated_heater_scales:
                src.power_scale = float(estimated_heater_scales[src.name])

    if estimated_inter_room_r:
        for key, r_val in estimated_inter_room_r.items():
            parts = key.split(":")
            if len(parts) != 2:
                continue
            room_a, room_b = parts
            for name in (room_a, room_b):
                if name not in coordinator.model.rooms:
                    continue
                other = room_b if name == room_a else room_a
                for conn in coordinator.model.rooms[name].connections:
                    if conn.connected_room == other:
                        conn.r_value = float(r_val)
        _LOGGER.info(
            "Applied estimated inter-room resistances: %s", estimated_inter_room_r
        )

    coordinator.model.rebuild_derived_parameters()
    (
        coordinator.model._C,
        coordinator.model._A,
        coordinator.model._B_ext,
    ) = coordinator.model._build_matrices()
    coordinator._build_controller()

    _LOGGER.info(
        "Applied estimated thermal parameters: %s",
        {
            name: {
                "thermal_mass": coordinator.model.rooms[name].thermal_mass,
                "r_external": coordinator.model.rooms[name].r_external,
            }
            for name in estimated_params
            if name in coordinator.model.rooms
        },
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    prev_rooms: Dict[str, Any] = {}
    try:
        prev_rooms = (coordinator.estimated_params_snapshot or {}).get("rooms", {})
    except Exception:
        pass

    snapshot_rooms: Dict[str, Any] = {}
    for name in coordinator.model.room_names:
        prev = prev_rooms.get(name, {}) if isinstance(prev_rooms.get(name), dict) else {}
        just_estimated = name in estimated_params
        entry: Dict[str, Any] = {
            "thermal_mass": coordinator.model.rooms[name].thermal_mass,
            "r_external": coordinator.model.rooms[name].r_external,
            "internal_gain": float(
                getattr(coordinator.model.rooms[name], "internal_gain", 0.0)
            ),
            "solar_scale": float(
                getattr(coordinator.model.rooms[name], "solar_scale", 1.0)
            ),
            "c_air_fraction": float(
                getattr(coordinator.model.rooms[name], "c_air_fraction", 0.05)
            ),
            "r_aw_fraction": float(
                getattr(coordinator.model.rooms[name], "r_aw_fraction", 0.05)
            ),
            "is_estimated": bool(just_estimated or prev.get("is_estimated", False)),
        }
        if just_estimated:
            entry["estimated_at"] = now_iso
            entry["estimation_source"] = "ml"
        elif prev.get("estimated_at"):
            entry["estimated_at"] = prev.get("estimated_at")
            if prev.get("estimation_source"):
                entry["estimation_source"] = prev.get("estimation_source")
        snapshot_rooms[name] = entry

    snapshot: Dict[str, Any] = {
        "rooms": snapshot_rooms,
        "sources": {
            src.name: {"power_scale": float(getattr(src, "power_scale", 1.0))}
            for src in coordinator.heat_sources
        },
        "connections": dict(estimated_inter_room_r) if estimated_inter_room_r else {},
        "estimated_at": now_iso,
        "log_likelihood": log_likelihood,
    }
    coordinator._estimation_timestamp = now_iso
    coordinator._estimation_log_likelihood = log_likelihood

    real_entry = coordinator.hass.config_entries.async_get_entry(
        coordinator._entry.entry_id
    )
    if real_entry is not None:
        coordinator.hass.config_entries.async_update_entry(
            real_entry,
            data={**dict(real_entry.data), CONF_ESTIMATED_PARAMS: snapshot},
        )
        _LOGGER.debug("Persisted estimated parameter snapshot to entry.data")


async def async_estimate_parameters_ml(
    coordinator: HeatingAssistantCoordinator,
    apply_params: bool = True,
    horizon_hours: Optional[float] = None,
    locked_params: Optional[Dict[str, Any]] = None,
    history_override: Optional[List[Dict[str, Any]]] = None,
    dataset_start_timestamps: Optional[List[float]] = None,
) -> Dict[str, Any]:
    from ..history_window import select_recent_window
    from ..parameter_estimator import KalmanMLEstimator

    dt = _coerce_interval_seconds(coordinator._update_interval_s)

    eff_horizon_hours = (
        float(horizon_hours)
        if horizon_hours is not None
        else float(
            getattr(
                coordinator,
                "_identification_horizon_hours",
                DEFAULT_IDENTIFICATION_HORIZON_HOURS,
            )
        )
    )

    if history_override is not None:
        history = list(history_override)
    else:
        history = list(coordinator._history_buffer)
        if eff_horizon_hours > 0 and history:
            history = select_recent_window(history, eff_horizon_hours * 3600.0)

    estimator = KalmanMLEstimator(
        rooms=list(coordinator.model.rooms.values()),
        sources=coordinator.heat_sources,
        dt=dt,
    )

    result: Dict[str, Any] = await coordinator.hass.async_add_executor_job(
        lambda: estimator.estimate(
            history,
            locked_params=locked_params,
            dataset_start_timestamps=dataset_start_timestamps,
        )
    )

    if result["success"] and apply_params:
        apply_estimated_parameters(
            coordinator,
            result["estimated_params"],
            result.get("estimated_inter_room_r", {}),
            estimated_internal_gains=result.get("estimated_internal_gains", {}),
            estimated_heater_scales=result.get("estimated_heater_scales", {}),
            estimated_solar_scales=result.get("estimated_solar_scales", {}),
            estimated_envelope_splits=result.get("estimated_envelope_splits", {}),
            log_likelihood=result.get("log_likelihood"),
        )

    history_buf = getattr(coordinator, "_estimation_history", None)
    if history_buf is not None:
        history_buf.append(
            {
                "estimated_at": datetime.now(timezone.utc).isoformat(),
                "success": bool(result.get("success")),
                "log_likelihood": (
                    float(result.get("log_likelihood"))
                    if isinstance(result.get("log_likelihood"), (int, float))
                    else None
                ),
                "applied": bool(result.get("success")) and apply_params,
                "n_rooms": len(coordinator.model.room_names),
                "n_sources": len(coordinator.heat_sources),
            }
        )

    return result
