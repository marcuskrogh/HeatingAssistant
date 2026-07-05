"""System-identification and parameter-estimation service handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
import homeassistant.helpers.config_validation as cv

from ..const import (
    DOMAIN,
    EXCITATION_TYPES,
    SERVICE_CANCEL_EXPERIMENT,
    SERVICE_CREATE_DATASET,
    SERVICE_DELETE_DATASET,
    SERVICE_DELETE_EXPERIMENT,
    SERVICE_ESTIMATE_PARAMETERS_ML,
    SERVICE_SCHEDULE_EXPERIMENT,
)
from ..coordinator import HeatingAssistantCoordinator
from .history_access import (
    dataset_boundaries,
    get_history_for_horizon,
    get_history_for_window,
    get_history_with_leading,
    records_for_dataset,
    records_for_datasets,
)
from .simulation import (
    compute_open_loop_rmse_by_horizon,
    effective_heater_scales,
    estimate_simulation_initial_state,
    extract_sim_room_params,
    inject_identified_t_wall_initial,
    merge_per_room_into_sysid_results,
    open_loop_t_wall_initial_dict,
    patched_heat_sources,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SIMULATE_THERMAL_RESPONSE = "simulate_thermal_response"
SERVICE_ESTIMATE_PARAMETERS = "estimate_parameters"
SERVICE_RUN_SYSID_SIMULATION = "run_sysid_simulation"
SERVICE_APPLY_MANUAL_PARAMETERS = "apply_manual_parameters"
SERVICE_RESET_ESTIMATED_PARAMETERS = "reset_estimated_parameters"
SERVICE_APPLY_HEATER_SCALES = "apply_heater_scales"


def _coordinator(hass: HomeAssistant) -> HeatingAssistantCoordinator:
    import importlib

    return importlib.import_module(
        "custom_components.heating_assistant.__init__"
    )._get_coordinator(hass)


def _resolve_room(coordinator: HeatingAssistantCoordinator, name: str) -> str:
    """Return the canonical room name for a name-or-slug, or raise."""
    from ..naming import slugify as _slugify

    for rn in coordinator.model.room_names:
        if rn == name or _slugify(rn) == name:
            return rn
    raise ValueError(f"Room '{name}' not found in configuration")


async def handle_simulate(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(hass)
    result = coordinator.simulate_thermal_response(
        room_name=call.data["room_name"],
        initial_temp=call.data["initial_temp"],
        outdoor_temp=call.data["outdoor_temp"],
        heating_power=call.data["heating_power"],
        duration_hours=call.data["duration_hours"],
    )
    # Fire an event so automations can consume the result via a trigger,
    # and return it as service-response data so it shows up inline in
    # Developer Tools → Actions rather than as a persistent notification.
    hass.bus.async_fire(
        f"{DOMAIN}_simulation_result",
        result,
    )
    return result


async def handle_estimate(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(hass)
    result = coordinator.estimate_parameters(
        room_name=call.data["room_name"],
        heating_power=call.data["heating_power"],
        outdoor_temp=call.data["outdoor_temp"],
        initial_temp=call.data["initial_temp"],
        final_temp=call.data["final_temp"],
        duration_seconds=call.data["duration_seconds"],
    )
    # Fire an event for automations and return the result as service
    # response data (Developer Tools → Actions); no notification is raised.
    hass.bus.async_fire(
        f"{DOMAIN}_estimation_result",
        result,
    )
    return result


async def handle_estimate_ml(hass: HomeAssistant, call: ServiceCall) -> None:
    """Run ML parameter estimation using the Kalman filter log-likelihood."""
    coordinator = _coordinator(hass)
    apply_params: bool = call.data.get("apply_parameters", False)
    horizon_hours: Optional[float] = call.data.get("horizon_hours")
    locked_params: Optional[Dict] = call.data.get("locked_params")
    window_start_ml: Optional[float] = (
        float(call.data["window_start"]) if "window_start" in call.data else None
    )
    window_end_ml: Optional[float] = (
        float(call.data["window_end"]) if "window_end" in call.data else None
    )

    # Resolve the data the estimator runs over, in priority order:
    #   1. ``dataset_ids`` — joint identification over several stored
    #      datasets (their records are merged into one history).
    #   2. ``dataset_id`` — a single stored dataset's snapshot.
    #   3. an explicit ``window_start``/``window_end`` (JSONL / Recorder).
    # Each takes precedence over the trailing ``horizon_hours`` window.
    dataset_ids = call.data.get("dataset_ids")
    history_override = records_for_datasets(coordinator, dataset_ids)
    # Collect each dataset's first timestamp so the estimator assigns an
    # independent per-dataset wall-temperature parameter block.
    dataset_start_timestamps: Optional[List[float]] = (
        dataset_boundaries(coordinator, dataset_ids)
        if history_override is not None
        else None
    )
    if history_override is None:
        history_override = records_for_dataset(
            coordinator, call.data.get("dataset_id")
        )
    if history_override is None and window_start_ml is not None and window_end_ml is not None:
        history_override = await get_history_for_window(
            hass, coordinator, window_start_ml, window_end_ml
        )

    if history_override is None and horizon_hours is not None:
        history_override = await get_history_for_horizon(
            hass, coordinator, float(horizon_hours)
        )

    result = await coordinator.async_estimate_parameters_ml(
        apply_params=apply_params,
        horizon_hours=horizon_hours if history_override is None else None,
        locked_params=locked_params,
        history_override=history_override,
        dataset_start_timestamps=dataset_start_timestamps,
    )

    # Update sysid_results so that the dashboard sensor entities reflect
    # the newly identified parameters immediately (without requiring a
    # separate sysid simulation run).
    if result.get("success"):
        dt = coordinator.dt
        horizon_steps = (
            max(1, int(float(horizon_hours) * 3600.0 / dt))
            if horizon_hours is not None
            else None
        )
        internal_gains = result.get("estimated_internal_gains", {})
        solar_scales = result.get("estimated_solar_scales", {})
        envelope_splits = result.get("estimated_envelope_splits", {})
        t_wall_initial = result.get("estimated_t_wall_initial", {})
        heater_scales = result.get("estimated_heater_scales", {})
        inter_room_r = result.get("estimated_inter_room_r", {})

        # Map each room to the identified scales of the heaters in it so the
        # per-room identification page can list them like any other param.
        sources_by_room: Dict[str, Dict[str, float]] = {}
        for src in coordinator.heat_sources:
            room = getattr(src, "room", None)
            if room is None or src.name not in heater_scales:
                continue
            sources_by_room.setdefault(room, {})[src.name] = float(
                heater_scales[src.name]
            )

        for room_name, params in result.get("estimated_params", {}).items():
            existing = coordinator.sysid_results.get(room_name, {})
            existing["thermal_mass"] = params.get("thermal_mass")
            existing["r_external"] = params.get("r_external")
            # Surface the full identified parameter set so the room-level
            # identification page can review and apply every parameter
            # (not just C / R_ext) in one place.
            if room_name in internal_gains:
                existing["internal_gain"] = internal_gains[room_name]
            if room_name in solar_scales:
                existing["solar_scale"] = solar_scales[room_name]
            if room_name in envelope_splits:
                splits = envelope_splits[room_name]
                if "c_air_fraction" in splits:
                    existing["c_air_fraction"] = splits["c_air_fraction"]
                if "r_aw_fraction" in splits:
                    existing["r_aw_fraction"] = splits["r_aw_fraction"]
            if room_name in t_wall_initial:
                existing["t_wall_initial"] = t_wall_initial[room_name]
            room_connections = {
                key: val
                for key, val in inter_room_r.items()
                if key.startswith(f"{room_name}:")
                or key.endswith(f":{room_name}")
            }
            if room_connections:
                existing["estimated_inter_room_r"] = room_connections
            if room_name in sources_by_room:
                existing["heater_scales"] = sources_by_room[room_name]
            if horizon_steps is not None:
                existing["horizon_steps"] = horizon_steps
            coordinator.sysid_results[room_name] = existing

        # Cache identified heater scales on the coordinator so the system-wide
        # config sensor keeps exposing them too.
        coordinator._last_identified_heater_scales = dict(heater_scales)

        coordinator.async_update_listeners()

    # Fire an event so automations can consume the full result. The
    # outcome is surfaced in the UI through the per-room parameter and
    # diagnostic sensors, so no persistent notification is raised.
    hass.bus.async_fire(
        f"{DOMAIN}_ml_estimation_result",
        {
            k: v
            for k, v in result.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        },
    )


async def handle_run_open_loop_simulation(hass: HomeAssistant, call: ServiceCall) -> None:
    """Run open-loop simulation diagnostic and report RMSE per room."""
    from ..model_diagnostics import compute_open_loop_predictions
    from ..simulation.model_patch import build_sim_model

    coordinator = _coordinator(hass)
    horizon_hours: Optional[float] = call.data.get("horizon_hours")
    window_start_ol: Optional[float] = (
        float(call.data["window_start"]) if "window_start" in call.data else None
    )
    window_end_ol: Optional[float] = (
        float(call.data["window_end"]) if "window_end" in call.data else None
    )
    sigma_w: float = float(call.data.get(
        "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
    ))
    sigma_v: float = float(call.data.get(
        "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
    ))

    leading_history: Optional[List[Dict[str, Any]]] = None

    # A stored dataset takes precedence over a window / horizon and supplies
    # its snapshotted records directly.
    dataset_records = records_for_dataset(coordinator, call.data.get("dataset_id"))
    if dataset_records is not None:
        history = dataset_records
    # Explicit window: fetch leading calibration data plus the window.
    elif window_start_ol is not None and window_end_ol is not None:
        _full, leading_history, history = await get_history_with_leading(
            hass, coordinator, window_start_ol, window_end_ol,
        )
        from ..history_window import select_window_by_timestamps
        history = select_window_by_timestamps(history, window_start_ol, window_end_ol)
    elif horizon_hours is not None:
        history = await get_history_for_horizon(
            hass, coordinator, float(horizon_hours)
        )
        from ..history_window import history_time_range, select_leading_window
        min_ts, _max_ts = history_time_range(history)
        if min_ts is not None:
            _full, leading_history, _sim = await get_history_with_leading(
                hass,
                coordinator,
                min_ts,
                _max_ts if _max_ts is not None else min_ts,
                leading_hours=6.0,
            )
    else:
        history = list(coordinator.history_buffer)

    # Build per-room parameter overrides from service data (same keys as
    # run_sysid_simulation) so the open-loop diagnostic uses the full
    # parameter set the user configured in the identification panel.
    room_params = extract_sim_room_params(call, coordinator.model.room_names)

    # When room-parameter overrides are given, build a temporary
    # HouseThermalSDE from a patched model copy so the open-loop
    # simulation uses the parameters currently visible in the UI, not
    # the last applied set.  Without overrides the live controller
    # system is used directly (fast path, no copy needed).
    room_names = coordinator.model.room_names
    n_rooms = len(room_names)
    dt = coordinator.dt

    # Patch heat-source copies with the heater power scales currently shown
    # in the UI (falling back to the last auto-identified scales) so the
    # open-loop simulation uses them even when the user hasn't clicked Apply.
    heater_scales = effective_heater_scales(call, coordinator)
    base_heat_sources = patched_heat_sources(coordinator, heater_scales)

    if room_params or heater_scales:
        from ..controller import HouseThermalSDE  # noqa: PLC0415
        try:
            sim_model = build_sim_model(
                coordinator.model, room_params, room_names
            )
            system = HouseThermalSDE(
                sim_model,
                base_heat_sources,
                dt,
                sigma_w=sigma_w,
                sigma_v=sigma_v,
                augment_offsets=False,
            )
        except Exception as exc:
            _LOGGER.error(
                "Open-loop simulation: failed to build patched system: %s",
                exc, exc_info=True,
            )
            return
    else:
        system = coordinator.controller._system

    # Estimate optimal initial air / wall temperatures from a leading
    # calibration window (or a short prefix when no leading data exists).
    t_wall_initial_identified: Dict[str, float] = {}
    try:
        init_state = await estimate_simulation_initial_state(
            hass,
            coordinator,
            history,
            system,
            leading_history=leading_history,
            room_params=room_params if room_params else None,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
        )
        t_wall_initial_identified = {
            name: float(v) for name, v in init_state.get("t_wall", {}).items()
        }
        _LOGGER.debug(
            "Open-loop initial state: method=%s calibration_steps=%s",
            init_state.get("method"),
            init_state.get("calibration_steps"),
        )
    except Exception as exc:
        _LOGGER.warning(
            "Open-loop: initial-state estimation failed (%s); "
            "falling back to cached or air-temperature seed.", exc,
        )
        t_wall_initial_identified = open_loop_t_wall_initial_dict(
            room_params, coordinator
        )

    # Results are surfaced in the UI via the per-room OpenLoopRMSESensor
    # entities, so this service writes its output to the coordinator cache
    # rather than raising a persistent notification.
    #
    # Use continuous mode (segment_length=None) for the main simulation so
    # the plot shows no artificial discontinuities: the wall/envelope state
    # is never re-initialised mid-run, it just evolves freely from the
    # starting condition.  The multi-horizon RMSE analysis below uses fixed
    # segment lengths specifically to measure N-step-ahead accuracy.
    try:
        result = await hass.async_add_executor_job(
            compute_open_loop_predictions,
            history,
            system,
            room_names,
            n_rooms,
            dt,
            None,
            t_wall_initial_identified,
        )

        if "error" not in result:
            per_room = result.get("per_room", {})

            # Multi-horizon open-loop RMSE: re-run the simulation at
            # ~4 h / 12 h / 24 h segment lengths so the diagnostic shows
            # how prediction error grows with horizon — the quantity
            # that matters for price-driven anticipatory heating, where
            # the plan spans many hours.  Each run reuses the same
            # history and model; only the segment slicing differs.
            rmse_by_horizon = await compute_open_loop_rmse_by_horizon(
                hass,
                history,
                system,
                room_names,
                n_rooms,
                dt,
                t_wall_initial_identified,
            )
            for name in room_names:
                if name in per_room:
                    per_room[name]["rmse_by_horizon"] = rmse_by_horizon[name]

            # Write per-room results to coordinator cache so that
            # OpenLoopRMSESensor entities can read them without any
            # blocking computation on the event loop.
            coordinator.open_loop_results.update(per_room)
            coordinator.async_update_listeners()

    except Exception as exc:
        _LOGGER.error("Open-loop simulation failed: %s", exc, exc_info=True)


async def handle_run_sysid_simulation(hass: HomeAssistant, call: ServiceCall) -> None:
    """Run system-identification open-loop simulation with configurable params."""
    from ..sysid import run_sysid_simulation

    coordinator = _coordinator(hass)
    room_name_filter: Optional[str] = call.data.get("room_name")
    horizon_hours: float = float(call.data.get("horizon_hours", 6.0))
    sigma_w: float = float(call.data.get(
        "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
    ))
    sigma_v: float = float(call.data.get(
        "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
    ))

    # Explicit window: if window_start / window_end are supplied (UNIX
    # timestamps), they override the horizon-based trailing window.
    window_start: Optional[float] = (
        float(call.data["window_start"]) if "window_start" in call.data else None
    )
    window_end: Optional[float] = (
        float(call.data["window_end"]) if "window_end" in call.data else None
    )
    window_spec = (
        (window_start, window_end)
        if window_start is not None and window_end is not None
        else None
    )

    dt = coordinator.dt  # seconds; handles runtime overrides correctly
    horizon_steps = max(1, int(horizon_hours * 3600.0 / dt))

    # Build per-room parameter overrides from service data (full parameter
    # set, keyed by ``<param>_<room_slug>``).
    room_params = extract_sim_room_params(call, coordinator.model.room_names)

    leading_history: Optional[List[Dict[str, Any]]] = None

    # Fetch history: a stored dataset (``dataset_id``) supplies its
    # snapshotted records directly; otherwise use the JSONL store / Recorder
    # for out-of-buffer windows.  With a dataset the whole snapshot is used
    # (window_spec cleared) since it was captured for exactly this purpose.
    dataset_records = records_for_dataset(coordinator, call.data.get("dataset_id"))
    if dataset_records is not None:
        history = dataset_records
        window_spec = None
    else:
        if window_spec is not None:
            _full, leading_history, history = await get_history_with_leading(
                hass, coordinator, window_start, window_end,
            )
        else:
            history = await get_history_for_horizon(
                hass, coordinator, horizon_hours
            )
            from ..history_window import history_time_range
            min_ts, max_ts = history_time_range(history)
            if min_ts is not None and max_ts is not None:
                _full, leading_history, _sim = await get_history_with_leading(
                    hass, coordinator, min_ts, max_ts, leading_hours=6.0,
                )

    # Patch heat-source copies with the heater power scales currently shown
    # in the UI (falling back to the last auto-identified scales) so the EKF
    # reconstruction uses them even when the user hasn't clicked Apply yet.
    heater_scales = effective_heater_scales(call, coordinator)
    sim_heat_sources = patched_heat_sources(coordinator, heater_scales)

    # Build the SDE used for initial-state estimation (matches sysid run).
    from ..controller import HouseThermalSDE  # noqa: PLC0415
    from ..simulation.model_patch import build_sim_model  # noqa: PLC0415
    try:
        sim_model = build_sim_model(
            coordinator.model, room_params, coordinator.model.room_names
        )
        init_system = HouseThermalSDE(
            sim_model,
            sim_heat_sources,
            dt,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
            augment_offsets=False,
        )
    except Exception as exc:
        _LOGGER.warning(
            "EKF sim: could not build system for initial-state estimation: %s",
            exc,
        )
        init_system = coordinator.controller._system

    # Estimate optimal initial wall temperatures from a leading calibration
    # window (or a short prefix when no leading data exists).
    try:
        init_state = await estimate_simulation_initial_state(
            hass,
            coordinator,
            history,
            init_system,
            leading_history=leading_history,
            room_params=room_params if room_params else None,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
        )
        for _rn, _tw in init_state.get("t_wall", {}).items():
            room_params.setdefault(_rn, {})["t_wall_initial"] = float(_tw)
        _LOGGER.debug(
            "EKF sim initial state: method=%s calibration_steps=%s",
            init_state.get("method"),
            init_state.get("calibration_steps"),
        )
    except Exception as exc:
        _LOGGER.warning(
            "EKF sim: initial-state estimation failed (%s); "
            "falling back to cached or air-temperature seed.", exc,
        )
        inject_identified_t_wall_initial(room_params, coordinator)

    try:
        result = await hass.async_add_executor_job(
            run_sysid_simulation,
            history,
            coordinator.model,
            sim_heat_sources,
            coordinator.model.room_names,
            dt,
            horizon_steps,
            room_params,
            sigma_w,
            sigma_v,
            window_spec,
        )

        if "error" not in result:
            per_room = result.get("per_room", {})

            # Tag each per-room result with the horizon and window so the
            # sensor can surface them without knowing dt or call.data.
            for room_data in per_room.values():
                room_data["horizon_steps"] = result.get("horizon_steps", horizon_steps)
                if window_start is not None:
                    room_data["window_start"] = window_start
                if window_end is not None:
                    room_data["window_end"] = window_end

            # Filter to requested room if specified.  The frontend
            # sends the room slug (e.g. "bedroom") while per_room keys are
            # the canonical room names (e.g. "Bedroom") stored in the
            # model.  Accept both exact and slug matches so rooms with
            # capital letters or spaces are not silently dropped.
            if room_name_filter:
                from ..naming import slugify as _slugify  # noqa: PLC0415
                per_room = {
                    k: v for k, v in per_room.items()
                    if k == room_name_filter or _slugify(k) == room_name_filter
                }

            # Results are surfaced via the per-room SysID diagnostic
            # sensors, so this service stores them on the coordinator and
            # refreshes listeners instead of raising a notification.
            merge_per_room_into_sysid_results(coordinator.sysid_results, per_room)
            coordinator.async_update_listeners()

    except Exception as exc:
        _LOGGER.error("SysID simulation failed: %s", exc, exc_info=True)


async def handle_apply_manual_parameters(hass: HomeAssistant, call: ServiceCall) -> None:
    """Apply manually tuned thermal parameters for a single room."""
    coordinator = _coordinator(hass)
    room_name: str = call.data["room_name"]
    thermal_mass: float = call.data["thermal_mass"]
    r_external: float = call.data["r_external"]
    coordinator.apply_manual_parameters(room_name, thermal_mass, r_external)
    coordinator.async_update_listeners()


async def handle_apply_heater_scales(hass: HomeAssistant, call: ServiceCall) -> None:
    """Apply heater power-scale factors identified by ML estimation.

    When called with no ``scales`` argument the last identified scales
    cached on the coordinator (from the most-recent ``estimate_parameters_ml``
    run) are used.  An explicit ``scales`` dict can override this for
    testing or manual correction.
    """
    coordinator = _coordinator(hass)
    scales: Optional[Dict[str, float]] = call.data.get("scales")
    if not scales:
        scales = getattr(coordinator, "_last_identified_heater_scales", {})
    if not scales:
        _LOGGER.warning(
            "apply_heater_scales: no scales provided and none identified yet; "
            "run estimate_parameters_ml first."
        )
        return
    coordinator.apply_heater_scales(scales)
    coordinator.async_update_listeners()


async def handle_reset_estimated_parameters(hass: HomeAssistant, call: ServiceCall) -> None:
    """Reset the active model back to configured (YAML) default parameters."""
    coordinator = _coordinator(hass)
    coordinator.reset_estimated_parameters()
    coordinator.async_update_listeners()


async def handle_store_identified_parameters(hass: HomeAssistant, call: ServiceCall) -> None:
    """Store identified parameters with history tracking."""
    coordinator = _coordinator(hass)
    room_name: str = _resolve_room(coordinator, call.data["room_name"])
    thermal_mass: float = call.data["thermal_mass"]
    r_external: float = call.data["r_external"]
    source: str = call.data.get("source", "manual")
    rmse: Optional[float] = call.data.get("rmse")
    heater_scales = call.data.get("heater_scales")
    coordinator.store_identified_parameters(
        room_name, thermal_mass, r_external, source=source, rmse=rmse,
        internal_gain=call.data.get("internal_gain"),
        solar_scale=call.data.get("solar_scale"),
        c_air_fraction=call.data.get("c_air_fraction"),
        r_aw_fraction=call.data.get("r_aw_fraction"),
        heater_scales=dict(heater_scales) if heater_scales else None,
    )
    coordinator.async_update_listeners()


async def handle_revert_parameters(hass: HomeAssistant, call: ServiceCall) -> None:
    """Revert parameters to a previous history entry."""
    coordinator = _coordinator(hass)
    room_name: str = _resolve_room(coordinator, call.data["room_name"])
    history_index: int = call.data["history_index"]
    coordinator.revert_parameters(room_name, history_index)
    coordinator.async_update_listeners()


async def handle_delete_parameter_history(hass: HomeAssistant, call: ServiceCall) -> None:
    """Delete a single entry from the parameter history."""
    coordinator = _coordinator(hass)
    history_index: int = call.data["history_index"]
    coordinator.delete_parameter_history(history_index)
    coordinator.async_update_listeners()


async def handle_schedule_experiment(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Schedule a system-identification experiment for one room/time window."""
    from ..naming import slugify as _slugify
    from ..experiments import Experiment, validate_signal_params
    from ..const import (
        DEFAULT_EXCITATION_PERIOD_S,
        DEFAULT_EXCITATION_STEP_PCT,
        DEFAULT_EXCITATION_TYPE,
        DEFAULT_EXPERIMENT_MAX_TEMP,
        DEFAULT_EXPERIMENT_MIN_TEMP,
        DEFAULT_EXPERIMENT_SETTLE_S,
        MAX_EXPERIMENT_DURATION_S,
    )

    coordinator = _coordinator(hass)
    canonical = _resolve_room(coordinator, call.data["room_name"])

    start_ts = float(call.data["start"])
    end_ts = float(call.data["end"])
    if end_ts <= start_ts:
        raise ValueError("Experiment end must be after start")
    duration = end_ts - start_ts
    if duration > MAX_EXPERIMENT_DURATION_S:
        raise ValueError("Experiment duration exceeds the 7-day maximum")

    signal_type = str(call.data.get("signal_type", DEFAULT_EXCITATION_TYPE))
    step_pct = float(call.data.get("step_pct", DEFAULT_EXCITATION_STEP_PCT))
    period_s = float(call.data.get("period_s", DEFAULT_EXCITATION_PERIOD_S))
    validate_signal_params(signal_type, step_pct, period_s)

    # Settle / response buffer: excitation stops this long before the window
    # ends so the heater's influence is absorbed within the captured data.
    # Default to the configured buffer but never more than half the window so
    # a short experiment still gets meaningful excitation; an explicit value
    # must leave at least some excitation time.
    settle_raw = call.data.get("settle_s")
    if settle_raw is None:
        settle_s = min(DEFAULT_EXPERIMENT_SETTLE_S, duration / 2.0)
    else:
        settle_s = float(settle_raw)
        if settle_s < 0.0:
            raise ValueError("settle_s must be non-negative")
        if settle_s >= duration:
            raise ValueError("settle_s must be shorter than the experiment duration")

    exp = Experiment(
        room_name=canonical,
        room_slug=_slugify(canonical),
        start_ts=start_ts,
        end_ts=end_ts,
        name=str(call.data.get("name", "")),
        signal_type=signal_type,
        step_pct=step_pct,
        period_s=period_s,
        settle_s=settle_s,
        min_temp=float(call.data.get("min_temp", DEFAULT_EXPERIMENT_MIN_TEMP)),
        max_temp=float(call.data.get("max_temp", DEFAULT_EXPERIMENT_MAX_TEMP)),
        auto_save=bool(call.data.get("auto_save", True)),
    )
    coordinator.schedule_experiment(exp)
    coordinator.async_update_listeners()
    return {"experiment_id": exp.id}


async def handle_cancel_experiment(hass: HomeAssistant, call: ServiceCall) -> None:
    """Cancel (or remove) a scheduled / running experiment."""
    coordinator = _coordinator(hass)
    coordinator.cancel_experiment(call.data["experiment_id"])
    coordinator.async_update_listeners()


async def handle_delete_experiment(hass: HomeAssistant, call: ServiceCall) -> None:
    """Delete an experiment outright, regardless of its status."""
    coordinator = _coordinator(hass)
    coordinator.delete_experiment(call.data["experiment_id"])
    coordinator.async_update_listeners()


async def handle_create_dataset(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Snapshot a custom data window into a named, permanent dataset."""
    from ..datasets import build_dataset
    from ..history_window import select_window_by_timestamps
    from ..naming import slugify as _slugify
    from ..const import DATASET_SOURCE_MANUAL

    coordinator = _coordinator(hass)
    if coordinator.dataset_store is None:
        raise ValueError("Dataset store is not available")

    name = str(call.data["name"]).strip()
    if not name:
        raise ValueError("Dataset name must not be empty")
    window_start = float(call.data["window_start"])
    window_end = float(call.data["window_end"])
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    canonical: Optional[str] = None
    room_name = call.data.get("room_name")
    if room_name:
        canonical = _resolve_room(coordinator, room_name)
    room_slug = _slugify(canonical) if canonical else None

    # Pull the actual records for the window (JSONL store → Recorder
    # fallback), then clip to the exact bounds.
    records = await get_history_for_window(
        hass, coordinator, window_start, window_end
    )
    records = select_window_by_timestamps(records, window_start, window_end)
    if not records:
        raise ValueError("No observation data found in the selected window")

    dataset = build_dataset(
        name,
        records,
        room_name=canonical,
        room_slug=room_slug,
        source=DATASET_SOURCE_MANUAL,
        notes=str(call.data.get("notes", "")),
        window_start=window_start,
        window_end=window_end,
    )
    await coordinator.dataset_store.async_add(dataset)
    coordinator.async_update_listeners()
    return {
        "dataset_id": dataset["id"],
        "record_count": dataset["record_count"],
    }


async def handle_delete_dataset(hass: HomeAssistant, call: ServiceCall) -> None:
    """Delete a stored dataset by id."""
    coordinator = _coordinator(hass)
    if coordinator.dataset_store is None:
        return
    await coordinator.dataset_store.async_delete(call.data["dataset_id"])
    coordinator.async_update_listeners()


def register_identification_services(hass: HomeAssistant) -> None:
    """Register identification and simulation domain services."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULATE_THERMAL_RESPONSE,
        lambda call: handle_simulate(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("duration_hours"): vol.Coerce(float),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS,
        lambda call: handle_estimate(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("final_temp"): vol.Coerce(float),
                vol.Required("duration_seconds"): vol.Coerce(float),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS_ML,
        lambda call: handle_estimate_ml(hass, call),
        schema=vol.Schema(
            {
                vol.Optional("apply_parameters", default=False): cv.boolean,
                vol.Optional("horizon_hours"): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0)
                ),
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                vol.Optional("locked_params"): dict,
                # Identify from a stored dataset's snapshotted records, taking
                # precedence over window_start/window_end and horizon_hours.
                vol.Optional("dataset_id"): cv.string,
                # Joint identification over several stored datasets (their
                # records are merged); takes precedence over dataset_id.
                vol.Optional("dataset_ids"): [cv.string],
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "run_open_loop_simulation",
        lambda call: handle_run_open_loop_simulation(hass, call),
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("segment_length", default=30): vol.All(
                    vol.Coerce(int), vol.Range(min=5)
                ),
                vol.Optional("horizon_hours"): vol.Coerce(float),
                # Explicit window overrides horizon when both start and end are
                # provided. Values are UNIX timestamps (seconds since epoch).
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                vol.Optional("sigma_w"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("sigma_v"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            },
            # Allow per-room parameter overrides keyed as <param>_<room_slug>
            # (thermal_mass_<room>, r_external_<room>, internal_gain_<room>,
            # solar_scale_<room>, c_air_fraction_<room>, r_aw_fraction_<room>).
            extra=vol.ALLOW_EXTRA,
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_SYSID_SIMULATION,
        lambda call: handle_run_sysid_simulation(hass, call),
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("horizon_hours", default=6.0): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5)
                ),
                vol.Optional("sigma_w"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("sigma_v"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                # Explicit identification window as UNIX timestamps [s].
                # When both are provided, horizon_hours is ignored and only data
                # in [window_start, window_end] is used for identification.
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            },
            # Allow per-room parameter overrides keyed as <param>_<room_slug>
            # (thermal_mass_<room>, r_external_<room>, internal_gain_<room>,
            # solar_scale_<room>, c_air_fraction_<room>, r_aw_fraction_<room>).
            extra=vol.ALLOW_EXTRA,
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_MANUAL_PARAMETERS,
        lambda call: handle_apply_manual_parameters(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("thermal_mass"): vol.All(
                    vol.Coerce(float), vol.Range(min=1000.0)
                ),
                vol.Required("r_external"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0001)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_HEATER_SCALES,
        lambda call: handle_apply_heater_scales(hass, call),
        schema=vol.Schema(
            {
                vol.Optional("scales"): {cv.string: vol.Coerce(float)},
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ESTIMATED_PARAMETERS,
        lambda call: handle_reset_estimated_parameters(hass, call),
        schema=vol.Schema({}),
    )

    hass.services.async_register(
        DOMAIN,
        "store_identified_parameters",
        lambda call: handle_store_identified_parameters(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("thermal_mass"): vol.All(
                    vol.Coerce(float), vol.Range(min=1000.0)
                ),
                vol.Required("r_external"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0001)
                ),
                vol.Optional("source", default="manual"): vol.In(["ml", "manual"]),
                vol.Optional("rmse"): vol.Coerce(float),
                vol.Optional("internal_gain"): vol.Coerce(float),
                vol.Optional("solar_scale"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("c_air_fraction"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("r_aw_fraction"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "revert_parameters",
        lambda call: handle_revert_parameters(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("history_index"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=9)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "delete_parameter_history",
        lambda call: handle_delete_parameter_history(hass, call),
        schema=vol.Schema(
            {
                vol.Required("history_index"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=9)
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCHEDULE_EXPERIMENT,
        lambda call: handle_schedule_experiment(hass, call),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("start"): vol.Coerce(float),
                vol.Required("end"): vol.Coerce(float),
                vol.Optional("name"): cv.string,
                vol.Optional("signal_type"): vol.In(list(EXCITATION_TYPES)),
                vol.Optional("step_pct"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("period_s"): vol.All(
                    vol.Coerce(float), vol.Range(min=60.0)
                ),
                vol.Optional("settle_s"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("min_temp"): vol.Coerce(float),
                vol.Optional("max_temp"): vol.Coerce(float),
                vol.Optional("auto_save"): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_EXPERIMENT,
        lambda call: handle_cancel_experiment(hass, call),
        schema=vol.Schema({vol.Required("experiment_id"): cv.string}),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_EXPERIMENT,
        lambda call: handle_delete_experiment(hass, call),
        schema=vol.Schema({vol.Required("experiment_id"): cv.string}),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_DATASET,
        lambda call: handle_create_dataset(hass, call),
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Required("window_start"): vol.Coerce(float),
                vol.Required("window_end"): vol.Coerce(float),
                vol.Optional("room_name"): cv.string,
                vol.Optional("notes"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_DATASET,
        lambda call: handle_delete_dataset(hass, call),
        schema=vol.Schema({vol.Required("dataset_id"): cv.string}),
    )
