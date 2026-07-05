"""Simulation helpers used by sysid and open-loop service handlers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.core import ServiceCall

from ..coordinator import HeatingAssistantCoordinator

# Per-room thermal-model parameters the System Identification panel can preview
# in the EKF reconstruction / open-loop simulation.  Each is passed as a
# ``<param>_<room_slug>`` service-data key (e.g. ``thermal_mass_living_room``)
# so a parameter set can be tried without applying it to the live model.
_SIM_ROOM_PARAM_KEYS = (
    "thermal_mass",
    "r_external",
    "internal_gain",
    "solar_scale",
    "c_air_fraction",
    "r_aw_fraction",
    # t_wall_initial is identified per-dataset but never shown in the UI
    # parameter list. It is injected automatically from sysid_results by the
    # EKF and open-loop handlers below so reconstruction uses the last
    # identified value without any user interaction.
)


def extract_sim_room_params(
    call: ServiceCall,
    room_names: Any,
) -> Dict[str, Dict[str, float]]:
    """Collect per-room parameter overrides from a simulation service call.

    Reads every ``<param>_<room_slug>`` key present in ``call.data`` into a
    ``{room_name: {param: value}}`` mapping so the EKF reconstruction and
    open-loop simulation use the full set of values currently shown in the UI,
    not just ``thermal_mass`` / ``r_external``.
    """
    from ..naming import slugify  # noqa: PLC0415

    room_params: Dict[str, Dict[str, float]] = {}
    for room_name in room_names:
        room_key = slugify(room_name)
        overrides: Dict[str, float] = {}
        for param in _SIM_ROOM_PARAM_KEYS:
            key = f"{param}_{room_key}"
            if key in call.data:
                overrides[param] = float(call.data[key])
        if overrides:
            room_params[room_name] = overrides
    return room_params


def merge_per_room_into_sysid_results(
    sysid_results: Dict[str, Any],
    per_room: Dict[str, Any],
) -> None:
    """Merge per-room simulation results into the coordinator cache.

    Preserves fields already stored for a room (e.g. ML-identified
    t_wall_initial, internal_gain, heater_scales) while adding or
    updating simulation diagnostics (simulation, rmse, mae).
    """
    for room_name, room_data in per_room.items():
        existing = sysid_results.get(room_name, {})
        existing.update(room_data)
        sysid_results[room_name] = existing


def inject_identified_t_wall_initial(
    room_params: Dict[str, Dict[str, float]],
    coordinator: HeatingAssistantCoordinator,
) -> None:
    """Add identified t_wall_initial from sysid_results into room_params.

    The wall envelope initial temperature is identified per dataset but is
    not shown in the UI parameter list.  This function ensures that every
    EKF/open-loop reconstruction run automatically uses the last identified
    value for each room, falling back to the steady-state seed when no
    identified value is available.  Explicit overrides already present in
    room_params (e.g. from a direct service call) are never overwritten.
    """
    for room_name in coordinator.model.room_names:
        sysid = coordinator.sysid_results.get(room_name, {})
        t_wall = sysid.get("t_wall_initial")
        if t_wall is not None:
            room_params.setdefault(room_name, {}).setdefault(
                "t_wall_initial", float(t_wall)
            )


def open_loop_t_wall_initial_dict(
    room_params: Dict[str, Dict[str, float]],
    coordinator: HeatingAssistantCoordinator,
    fast_estimated: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Resolve per-room wall initial temperatures for open-loop simulation.

    Injects cached ML-identified values from ``sysid_results`` first, then
    merges *fast_estimated* only for rooms that still lack ``t_wall_initial``.
    """
    inject_identified_t_wall_initial(room_params, coordinator)
    room_names = coordinator.model.room_names
    result: Dict[str, float] = {
        name: float(room_params[name]["t_wall_initial"])
        for name in room_names
        if room_params.get(name, {}).get("t_wall_initial") is not None
    }
    if fast_estimated:
        for name in room_names:
            if name not in result and name in fast_estimated:
                result[name] = float(fast_estimated[name])
    return result


async def compute_open_loop_rmse_by_horizon(
    hass: Any,
    history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    n_rooms: int,
    dt: float,
    t_wall_initial: Optional[Dict[str, float]],
) -> Dict[str, Dict[str, Any]]:
    """Run segmented open-loop RMSE at ~4 h / 12 h / 24 h horizons."""
    from ..model_diagnostics import compute_open_loop_predictions

    rmse_by_horizon: Dict[str, Dict[str, Any]] = {name: {} for name in room_names}
    t_wall = t_wall_initial or None
    for hours in (4, 12, 24):
        steps = max(2, int(round(hours * 3600.0 / dt)))
        res_h = await hass.async_add_executor_job(
            compute_open_loop_predictions,
            history,
            system,
            room_names,
            n_rooms,
            dt,
            steps,
            t_wall,
        )
        if "error" in res_h:
            continue
        for name, room_res in res_h.get("per_room", {}).items():
            rmse_by_horizon[name][f"{hours}h"] = room_res.get("rmse")
    return rmse_by_horizon


def effective_heater_scales(
    call: ServiceCall,
    coordinator: HeatingAssistantCoordinator,
) -> Dict[str, float]:
    """Resolve the heater power scales a simulation should use.

    Prefers the explicit ``heater_scales`` mapping supplied by the
    identification panel (so manual edits take effect without clicking Apply);
    falls back to the last auto-identified scales cached on the coordinator for
    direct service calls that omit it.
    """
    ui_scales = call.data.get("heater_scales") or {}
    if ui_scales:
        return {str(k): float(v) for k, v in ui_scales.items()}
    return dict(getattr(coordinator, "_last_identified_heater_scales", {}))


def patched_heat_sources(
    coordinator: HeatingAssistantCoordinator,
    scales: Dict[str, float],
) -> Any:
    """Return heat sources with *scales* applied to shallow copies.

    The live heat sources are never mutated — when ``scales`` is empty the live
    list is returned unchanged (fast path), otherwise each affected source is
    shallow-copied and its ``power_scale`` overridden for the simulation only.
    """
    if not scales:
        return coordinator.heat_sources
    import copy as _copy  # noqa: PLC0415

    patched = [_copy.copy(src) for src in coordinator.heat_sources]
    for src in patched:
        if src.name in scales:
            src.power_scale = float(scales[src.name])
    return patched


async def estimate_simulation_initial_state(
    hass: Any,
    coordinator: HeatingAssistantCoordinator,
    simulation_history: List[Dict[str, Any]],
    system: Any,
    *,
    leading_history: Optional[List[Dict[str, Any]]] = None,
    room_params: Optional[Dict[str, Dict[str, float]]] = None,
    sigma_w: float = 0.1,
    sigma_v: float = 0.5,
    leading_hours: float = 6.0,
) -> Dict[str, Any]:
    """Estimate optimal air / wall temperatures at a simulation-window start."""
    from ..initial_state_estimator import (
        DEFAULT_LEADING_HOURS,
        estimate_simulation_initial_state as _estimate,
    )
    from ..parameter_estimator import KalmanMLEstimator

    dt = coordinator.dt
    fast_estimator = KalmanMLEstimator(
        rooms=list(coordinator.model.rooms.values()),
        sources=coordinator.heat_sources,
        dt=dt,
    )

    def _run() -> Dict[str, Any]:
        return _estimate(
            simulation_history,
            system,
            coordinator.model.room_names,
            dt,
            leading_history=leading_history,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
            room_params=room_params,
            wall_optimizer=fast_estimator,
            leading_hours=leading_hours or DEFAULT_LEADING_HOURS,
        )

    return await hass.async_add_executor_job(_run)
