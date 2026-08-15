"""Hass-free sysid and open-loop simulation helpers."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional

from ..naming import slugify

_SIM_ROOM_PARAM_KEYS = (
    "thermal_mass",
    "r_external",
    "internal_gain",
    "solar_scale",
    "c_air_fraction",
    "r_aw_fraction",
    "t_wall_initial",
)


def _mapping_data(data: Any) -> Mapping[str, Any]:
    if isinstance(data, Mapping):
        return data
    call_data = getattr(data, "data", None)
    if isinstance(call_data, Mapping):
        return call_data
    return {}


def extract_sim_room_params(
    data: Mapping[str, Any] | Any,
    room_names: Sequence[str],
) -> Dict[str, Dict[str, float]]:
    """Collect per-room simulation parameter overrides from request data."""

    values = _mapping_data(data)
    room_params: Dict[str, Dict[str, float]] = {}
    for room_name in room_names:
        room_key = slugify(str(room_name))
        overrides: Dict[str, float] = {}
        for param in _SIM_ROOM_PARAM_KEYS:
            key = f"{param}_{room_key}"
            if key in values:
                overrides[param] = float(values[key])
        if overrides:
            room_params[str(room_name)] = overrides
    return room_params


def merge_per_room_into_sysid_results(
    sysid_results: Dict[str, Any],
    per_room: Mapping[str, Any],
) -> None:
    """Merge per-room diagnostics into a mutable sysid result cache."""

    for room_name, room_data in per_room.items():
        existing = dict(sysid_results.get(room_name, {}))
        if isinstance(room_data, Mapping):
            existing.update(room_data)
        else:
            existing["value"] = room_data
        sysid_results[room_name] = existing


def inject_identified_t_wall_initial(
    room_params: Dict[str, Dict[str, float]],
    room_names: Sequence[str],
    sysid_results: Mapping[str, Any] | None,
) -> None:
    """Add identified ``t_wall_initial`` values to room params in-place."""

    cache = sysid_results or {}
    for room_name in room_names:
        room_data = cache.get(room_name, {})
        t_wall = room_data.get("t_wall_initial") if isinstance(room_data, Mapping) else None
        if t_wall is not None:
            room_params.setdefault(str(room_name), {}).setdefault(
                "t_wall_initial",
                float(t_wall),
            )


def open_loop_t_wall_initial_dict(
    room_params: Dict[str, Dict[str, float]],
    room_names: Sequence[str],
    sysid_results: Mapping[str, Any] | None = None,
    fast_estimated: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Resolve wall initial temperatures for open-loop simulation."""

    inject_identified_t_wall_initial(room_params, room_names, sysid_results)
    result: Dict[str, float] = {
        str(name): float(room_params[str(name)]["t_wall_initial"])
        for name in room_names
        if room_params.get(str(name), {}).get("t_wall_initial") is not None
    }
    if fast_estimated:
        for name in room_names:
            name_s = str(name)
            if name_s not in result and name_s in fast_estimated:
                result[name_s] = float(fast_estimated[name_s])
    return result


def effective_heater_scales(
    data: Mapping[str, Any] | Any,
    fallback_scales: Mapping[str, float] | None = None,
) -> Dict[str, float]:
    """Resolve heater power scales from explicit request data or fallback cache."""

    values = _mapping_data(data)
    ui_scales = values.get("heater_scales") or {}
    if isinstance(ui_scales, Mapping) and ui_scales:
        return {str(key): float(value) for key, value in ui_scales.items()}
    return {str(key): float(value) for key, value in dict(fallback_scales or {}).items()}


def patched_heat_sources(
    heat_sources: Sequence[Any],
    scales: Mapping[str, float] | None,
) -> List[Any]:
    """Return heat sources with scale overrides applied to shallow copies."""

    if not scales:
        return list(heat_sources)
    patched = [copy.copy(source) for source in heat_sources]
    for source in patched:
        name = str(getattr(source, "name", ""))
        if name in scales:
            source.power_scale = float(scales[name])
    return patched


async def compute_open_loop_rmse_by_horizon(
    history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    n_rooms: int,
    dt: float,
    t_wall_initial: Optional[Dict[str, float]] = None,
    *,
    executor: Any | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Run segmented open-loop RMSE at roughly 4 h, 12 h, and 24 h horizons."""

    from ..model_diagnostics import compute_open_loop_predictions

    rmse_by_horizon: Dict[str, Dict[str, Any]] = {name: {} for name in room_names}

    async def _run(*args: Any) -> Dict[str, Any]:
        if executor is not None:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(executor, lambda: compute_open_loop_predictions(*args))
        return await asyncio.to_thread(compute_open_loop_predictions, *args)

    for hours in (4, 12, 24):
        steps = max(2, int(round(hours * 3600.0 / float(dt))))
        result = await _run(
            history,
            system,
            room_names,
            n_rooms,
            dt,
            steps,
            t_wall_initial or None,
        )
        if "error" in result:
            continue
        for name, room_result in result.get("per_room", {}).items():
            rmse_by_horizon.setdefault(name, {})[f"{hours}h"] = room_result.get("rmse")
    return rmse_by_horizon


async def estimate_simulation_initial_state(
    simulation_history: List[Dict[str, Any]],
    system: Any,
    model: Any,
    heat_sources: Sequence[Any],
    room_names: Sequence[str],
    dt: float,
    *,
    leading_history: Optional[List[Dict[str, Any]]] = None,
    room_params: Optional[Dict[str, Dict[str, float]]] = None,
    sigma_w: float = 0.1,
    sigma_v: float = 0.5,
    leading_hours: float = 6.0,
    executor: Any | None = None,
) -> Dict[str, Any]:
    """Estimate optimal air/wall temperatures at a simulation window start."""

    from ..initial_state_estimator import estimate_simulation_initial_state as _estimate
    from ..parameter_estimator import KalmanMLEstimator

    fast_estimator = KalmanMLEstimator(
        rooms=list(model.rooms.values()),
        sources=list(heat_sources),
        dt=float(dt),
    )

    def _run() -> Dict[str, Any]:
        return _estimate(
            simulation_history,
            system,
            list(room_names),
            float(dt),
            leading_history=leading_history,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
            room_params=room_params,
            wall_optimizer=fast_estimator,
            leading_hours=leading_hours,
        )

    if executor is not None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, _run)
    return await asyncio.to_thread(_run)


def optimal_t_wall_for_window(
    history: List[Dict[str, Any]],
    model: Any,
    heat_sources: Sequence[Any],
    dt: float,
    room_params: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, float]:
    """Wall-only Tw0 on the history being simulated (diagnostic IC)."""

    from ..parameter_estimator import KalmanMLEstimator

    rooms = getattr(model, "rooms", {}) or {}
    room_list = list(rooms.values()) if isinstance(rooms, dict) else list(rooms)
    estimator = KalmanMLEstimator(
        rooms=room_list,
        sources=list(heat_sources),
        dt=float(dt),
    )
    return estimator.estimate_wall_initial_only(
        list(history or []),
        room_params=room_params,
        prior_mean="air",
        min_lam=1.0,
    )


__all__ = [
    "compute_open_loop_rmse_by_horizon",
    "effective_heater_scales",
    "estimate_simulation_initial_state",
    "extract_sim_room_params",
    "inject_identified_t_wall_initial",
    "merge_per_room_into_sysid_results",
    "open_loop_t_wall_initial_dict",
    "optimal_t_wall_for_window",
    "patched_heat_sources",
]
