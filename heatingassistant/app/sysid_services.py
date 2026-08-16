"""App-owned system-identification service implementations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from heatingassistant.engine import const
from heatingassistant.engine.datasets import build_dataset
from heatingassistant.engine.history.datasets import (
    dataset_boundaries,
    records_for_dataset,
    records_for_datasets,
)
from heatingassistant.engine.history.window import (
    select_recent_window,
    select_window_by_timestamps,
)
from heatingassistant.engine.model_diagnostics import (
    build_identification_warnings,
    compute_model_fit_metrics,
    compute_open_loop_predictions,
    validate_parameters,
)
from heatingassistant.engine.parameter_lifecycle import (
    PARAMETER_HISTORY_KEY,
    async_estimate_parameters_ml,
    delete_parameter_history,
    estimated_params_snapshot,
    restore_estimated_parameters,
    store_identified_parameters,
)
from heatingassistant.engine.simulation.model_patch import build_sim_model
from heatingassistant.engine.history.plot_series import identification_aux_series
from heatingassistant.engine.simulation.sysid_helpers import (
    compute_open_loop_rmse_by_horizon,
    effective_heater_scales,
    extract_sim_room_params,
    merge_per_room_into_sysid_results,
    open_loop_t_wall_initial_dict,
    optimal_t_wall_for_window,
    patched_heat_sources,
)
from heatingassistant.engine.sysid import run_sysid_simulation
from heatingassistant.persistence import save_config

_LOGGER = logging.getLogger(__name__)
HouseThermalSDE: Any | None = None


def _payload(data: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(data, Mapping):
        return dict(data)
    call_data = getattr(data, "data", None)
    return dict(call_data) if isinstance(call_data, Mapping) else {}


def _dt(runtime: Any) -> float:
    return float(
        getattr(runtime, "options", {}).get(
            const.CONF_UPDATE_INTERVAL, const.DEFAULT_UPDATE_INTERVAL
        )
        or const.DEFAULT_UPDATE_INTERVAL
    )


def _model(runtime: Any) -> Any:
    return runtime.control_engine.model


def _heat_sources(runtime: Any) -> list[Any]:
    return list(runtime.control_engine.heat_sources)


def _house_thermal_sde(*args: Any, **kwargs: Any) -> Any:
    global HouseThermalSDE
    if HouseThermalSDE is None:
        from heatingassistant.engine.controller import HouseThermalSDE as _HouseThermalSDE

        HouseThermalSDE = _HouseThermalSDE
    return HouseThermalSDE(*args, **kwargs)


def _room_names(runtime: Any) -> list[str]:
    names = getattr(_model(runtime), "room_names", None)
    if names is not None:
        return list(names)
    return list((getattr(_model(runtime), "rooms", {}) or {}).keys())


def _slug(runtime: Any, room_name: str) -> str:
    slug_fn = getattr(runtime, "_room_slug", None)
    if callable(slug_fn):
        return str(slug_fn(room_name))
    from heatingassistant.engine.naming import room_slug

    return room_slug(room_name)


def _resolve_room(runtime: Any, name_or_slug: str | None) -> str:
    value = str(name_or_slug or "")
    for room_name in _room_names(runtime):
        if room_name == value or _slug(runtime, room_name) == value:
            return room_name
    raise ValueError(f"Room '{value}' not found in configuration")


def _dataset_id_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value else None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        ids = [str(item) for item in value if item]
        return ids or None
    return None


def _normalised(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return select_recent_window([dict(record) for record in records], 0.0, 0.0)


async def resolve_history(
    runtime: Any,
    *,
    dataset_id: str | None = None,
    dataset_ids: Sequence[str] | None = None,
    window_start: float | None = None,
    window_end: float | None = None,
    horizon_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Resolve App identification history without Home Assistant Recorder."""

    ids = _dataset_id_list(dataset_ids)
    if ids:
        records = await asyncio.to_thread(records_for_datasets, runtime, ids)
        return _normalised(records or [])

    if dataset_id:
        records = await asyncio.to_thread(records_for_dataset, runtime, str(dataset_id))
        return _normalised(records or [])

    buffer = _normalised(list(getattr(runtime, "_history_buffer", []) or []))
    if window_start is not None and window_end is not None:
        start_ts = float(window_start)
        end_ts = float(window_end)
        records = select_window_by_timestamps(buffer, start_ts, end_ts)
        store = getattr(runtime, "id_history_store", None)
        if store is not None and hasattr(store, "async_query_range"):
            stored = await store.async_query_range(start_ts, end_ts)
            records = _normalised([*stored, *records])
            records = select_window_by_timestamps(records, start_ts, end_ts)
        return records

    if horizon_hours is not None:
        # Option A (SWD-320): reuse the window merge path so durable JSONL is
        # always consulted — same coverage as an equivalent custom window.
        horizon = float(horizon_hours)
        if horizon <= 0:
            return buffer
        if buffer:
            try:
                end_ts = float(buffer[-1].get("timestamp") or 0.0)
            except (TypeError, ValueError):
                end_ts = 0.0
            if end_ts <= 0:
                end_ts = time.time()
        else:
            end_ts = time.time()
        start_ts = end_ts - horizon * 3600.0
        return await resolve_history(
            runtime,
            window_start=start_ts,
            window_end=end_ts,
        )

    return buffer


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _locked_t_wall_from_request(
    values: Mapping[str, Any],
    room_params: Mapping[str, Mapping[str, Any]],
    room_names: Sequence[str],
) -> dict[str, float]:
    if not _truthy(values.get("t_wall_locked")):
        return {}
    result: dict[str, float] = {}
    for name in room_names:
        raw = room_params.get(str(name), {}).get("t_wall_initial")
        if raw is None:
            continue
        try:
            result[str(name)] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def _resolve_simulation_t_wall(
    history: list[dict[str, Any]],
    runtime: Any,
    heat_sources: Sequence[Any],
    room_names: Sequence[str],
    dt: float,
    room_params: dict[str, dict[str, float]],
    values: Mapping[str, Any],
) -> dict[str, float]:
    locked = _locked_t_wall_from_request(values, room_params, room_names)
    if locked:
        return locked
    try:
        estimated = optimal_t_wall_for_window(
            history,
            _model(runtime),
            heat_sources,
            dt,
            room_params=room_params or None,
        )
    except Exception as exc:
        _LOGGER.warning("Diagnostic wall-initial optimisation failed: %s", exc)
        estimated = {}
    result = {str(name): float(val) for name, val in dict(estimated or {}).items()}
    if not result:
        result = open_loop_t_wall_initial_dict(
            room_params,
            room_names,
            getattr(runtime, "sysid_results", None),
        )
    for name, value in result.items():
        room_params.setdefault(str(name), {})["t_wall_initial"] = float(value)
    return result


def _attach_aux_and_tw0(
    per_room: Mapping[str, Any],
    history: list[dict[str, Any]],
    heat_sources: Sequence[Any],
    t_wall: Mapping[str, float],
) -> None:
    for room_name, room_data in per_room.items():
        if not isinstance(room_data, dict):
            continue
        aux = identification_aux_series(history, heat_sources, str(room_name), iso_time=_iso_time)
        room_data.update(aux)
        if room_name in t_wall:
            room_data["t_wall_initial"] = float(t_wall[room_name])


def _iso_series(entries: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in list(entries or []):
        if not isinstance(entry, Mapping):
            continue
        stamp = entry.get("time")
        if stamp is None:
            continue
        if not isinstance(stamp, str):
            stamp = _iso_time(stamp)
        if stamp is None:
            continue
        out.append({"time": stamp, "value": entry.get("value")})
    return out


def _persist_runtime_config(runtime: Any) -> None:
    save_config(runtime.data_dir, runtime.options)
    runtime.control_engine.update_config(runtime.options)
    snapshot = estimated_params_snapshot(runtime.options)
    if snapshot:
        restore_estimated_parameters(
            runtime.control_engine.model,
            runtime.control_engine.heat_sources,
            snapshot,
        )
    sync_retention = getattr(runtime, "_sync_history_retention", None)
    if callable(sync_retention):
        sync_retention()
    save_state = getattr(runtime, "_save_runtime_state", None)
    if callable(save_state):
        save_state()


def _sources_by_room(runtime: Any, heater_scales: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    by_room: dict[str, dict[str, float]] = {}
    for source in _heat_sources(runtime):
        room = getattr(source, "room", None)
        name = getattr(source, "name", None)
        if room is None or name is None or name not in heater_scales:
            continue
        by_room.setdefault(str(room), {})[str(name)] = float(heater_scales[name])
    return by_room


def _merge_ml_result(runtime: Any, result: Mapping[str, Any], horizon_hours: float | None) -> None:
    dt = _dt(runtime)
    horizon_steps = max(1, int(float(horizon_hours) * 3600.0 / dt)) if horizon_hours else None
    internal_gains = result.get("estimated_internal_gains", {}) or {}
    solar_scales = result.get("estimated_solar_scales", {}) or {}
    envelope_splits = result.get("estimated_envelope_splits", {}) or {}
    t_wall_initial = result.get("estimated_t_wall_initial", {}) or {}
    heater_scales = result.get("estimated_heater_scales", {}) or {}
    inter_room_r = result.get("estimated_inter_room_r", {}) or {}
    sources_by_room = _sources_by_room(runtime, heater_scales)

    for room_name, params in dict(result.get("estimated_params", {}) or {}).items():
        existing = dict(getattr(runtime, "sysid_results", {}).get(room_name, {}))
        if isinstance(params, Mapping):
            existing["thermal_mass"] = params.get("thermal_mass")
            existing["r_external"] = params.get("r_external")
        if room_name in internal_gains:
            existing["internal_gain"] = internal_gains[room_name]
        ua_open = result.get("estimated_ua_open", {}) or {}
        if room_name in ua_open:
            existing["ua_open"] = ua_open[room_name]
        if room_name in solar_scales:
            existing["solar_scale"] = solar_scales[room_name]
        if room_name in envelope_splits and isinstance(envelope_splits[room_name], Mapping):
            splits = envelope_splits[room_name]
            if "c_air_fraction" in splits:
                existing["c_air_fraction"] = splits["c_air_fraction"]
            if "r_aw_fraction" in splits:
                existing["r_aw_fraction"] = splits["r_aw_fraction"]
        if room_name in t_wall_initial:
            existing["t_wall_initial"] = t_wall_initial[room_name]
        room_connections = {
            key: value
            for key, value in dict(inter_room_r).items()
            if str(key).startswith(f"{room_name}:") or str(key).endswith(f":{room_name}")
        }
        if room_connections:
            existing["estimated_inter_room_r"] = room_connections
        if room_name in sources_by_room:
            existing["heater_scales"] = sources_by_room[room_name]
        if horizon_steps is not None:
            existing["horizon_steps"] = horizon_steps
        runtime.sysid_results[room_name] = existing
    runtime._last_identified_heater_scales = {
        str(name): float(value) for name, value in dict(heater_scales).items()
    }


async def handle_estimate_parameters_ml(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    apply_params = bool(values.get("apply_parameters", False))
    horizon_hours = values.get("horizon_hours")
    horizon = float(horizon_hours) if horizon_hours is not None else None
    dataset_ids = _dataset_id_list(values.get("dataset_ids"))

    history = await resolve_history(
        runtime,
        dataset_ids=dataset_ids,
        dataset_id=None if dataset_ids else values.get("dataset_id"),
        window_start=values.get("window_start"),
        window_end=values.get("window_end"),
        horizon_hours=horizon,
    )
    boundaries = (
        await asyncio.to_thread(dataset_boundaries, runtime, dataset_ids)
        if dataset_ids
        else None
    )
    result = await async_estimate_parameters_ml(
        _model(runtime),
        _heat_sources(runtime),
        runtime.options,
        history,
        apply_params=apply_params,
        horizon_hours=None,
        locked_params=values.get("locked_params"),
        history_override=history,
        dataset_start_timestamps=boundaries,
    )
    if result.get("success"):
        _merge_ml_result(runtime, result, horizon)
        if apply_params:
            _persist_runtime_config(runtime)
    else:
        save_config(runtime.data_dir, runtime.options)
    return dict(result)


async def handle_run_sysid_simulation(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    horizon_hours = float(values.get("horizon_hours", const.DEFAULT_PARAMETER_ESTIMATION_HORIZON_HOURS))
    dt = _dt(runtime)
    horizon_steps = max(1, int(horizon_hours * 3600.0 / dt))
    window_start = values.get("window_start")
    window_end = values.get("window_end")
    window_spec = None

    dataset_id = values.get("dataset_id")
    if dataset_id:
        history = await resolve_history(runtime, dataset_id=str(dataset_id))
    elif window_start is not None and window_end is not None:
        window_spec = (float(window_start), float(window_end))
        history = await resolve_history(
            runtime, window_start=float(window_start), window_end=float(window_end)
        )
    else:
        history = await resolve_history(runtime, horizon_hours=horizon_hours)

    room_names = _room_names(runtime)
    room_params = extract_sim_room_params(values, room_names)
    heater_scales = effective_heater_scales(
        values, getattr(runtime, "_last_identified_heater_scales", {})
    )
    sim_heat_sources = patched_heat_sources(_heat_sources(runtime), heater_scales)

    sigma_w = float(values.get("sigma_w", runtime.options.get(const.CONF_SIGMA_W, const.DEFAULT_SIGMA_W)))
    sigma_v = float(values.get("sigma_v", runtime.options.get(const.CONF_SIGMA_V, const.DEFAULT_SIGMA_V)))
    t_wall_initial = _resolve_simulation_t_wall(
        history,
        runtime,
        sim_heat_sources,
        room_names,
        dt,
        room_params,
        values,
    )

    result = await asyncio.to_thread(
        run_sysid_simulation,
        history,
        _model(runtime),
        sim_heat_sources,
        room_names,
        dt,
        horizon_steps,
        room_params,
        sigma_w,
        sigma_v,
        window_spec,
    )
    if "error" not in result:
        per_room = dict(result.get("per_room", {}) or {})
        _attach_aux_and_tw0(per_room, history, sim_heat_sources, t_wall_initial)
        for room_data in per_room.values():
            if isinstance(room_data, dict):
                room_data["horizon_steps"] = result.get("horizon_steps", horizon_steps)
                if window_start is not None:
                    room_data["window_start"] = float(window_start)
                if window_end is not None:
                    room_data["window_end"] = float(window_end)
        room_filter = values.get("room_name")
        if room_filter:
            canonical = _resolve_room(runtime, str(room_filter))
            per_room = {canonical: per_room[canonical]} if canonical in per_room else {}
        merge_per_room_into_sysid_results(runtime.sysid_results, per_room)
    return dict(result)


async def handle_run_open_loop_simulation(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    horizon_hours = values.get("horizon_hours")
    window_start = values.get("window_start")
    window_end = values.get("window_end")

    dataset_id = values.get("dataset_id")
    if dataset_id:
        history = await resolve_history(runtime, dataset_id=str(dataset_id))
    elif window_start is not None and window_end is not None:
        history = await resolve_history(
            runtime, window_start=float(window_start), window_end=float(window_end)
        )
    elif horizon_hours is not None:
        history = await resolve_history(runtime, horizon_hours=float(horizon_hours))
    else:
        history = await resolve_history(runtime)

    room_names = _room_names(runtime)
    n_rooms = len(room_names)
    dt = _dt(runtime)
    sigma_w = float(values.get("sigma_w", runtime.options.get(const.CONF_SIGMA_W, const.DEFAULT_SIGMA_W)))
    sigma_v = float(values.get("sigma_v", runtime.options.get(const.CONF_SIGMA_V, const.DEFAULT_SIGMA_V)))
    room_params = extract_sim_room_params(values, room_names)
    heater_scales = effective_heater_scales(
        values, getattr(runtime, "_last_identified_heater_scales", {})
    )
    base_heat_sources = patched_heat_sources(_heat_sources(runtime), heater_scales)

    system = None
    if room_params or heater_scales:
        sim_model = build_sim_model(_model(runtime), room_params, room_names)
        system = _house_thermal_sde(
            sim_model,
            base_heat_sources,
            dt,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
            augment_offsets=False,
        )
    else:
        controller = getattr(runtime.control_engine, "_controller", None)
        system = getattr(controller, "_system", None)
        if system is None:
            system = _house_thermal_sde(
                _model(runtime),
                base_heat_sources,
                dt,
                sigma_w=sigma_w,
                sigma_v=sigma_v,
                augment_offsets=False,
            )

    t_wall_initial = _resolve_simulation_t_wall(
        history,
        runtime,
        base_heat_sources,
        room_names,
        dt,
        room_params,
        values,
    )

    result = await asyncio.to_thread(
        compute_open_loop_predictions,
        history,
        system,
        room_names,
        n_rooms,
        dt,
        None,
        t_wall_initial,
    )
    if "error" not in result:
        per_room = dict(result.get("per_room", {}) or {})
        _attach_aux_and_tw0(per_room, history, base_heat_sources, t_wall_initial)
        rmse_by_horizon = await compute_open_loop_rmse_by_horizon(
            history,
            system,
            room_names,
            n_rooms,
            dt,
            t_wall_initial,
        )
        for room_name in room_names:
            if room_name in per_room:
                per_room[room_name]["rmse_by_horizon"] = rmse_by_horizon.get(room_name, {})
        room_filter = values.get("room_name")
        if room_filter:
            canonical = _resolve_room(runtime, str(room_filter))
            per_room = {canonical: per_room[canonical]} if canonical in per_room else {}
        runtime.open_loop_results.update(per_room)
    return dict(result)


def _pe_coverage_kwargs(runtime: Any, room_name: str) -> dict[str, Any]:
    dt = _dt(runtime)
    from heatingassistant.engine.estimation.constants import _MIN_HISTORY_TIME_S

    min_steps = max(10, int((_MIN_HISTORY_TIME_S / dt) + 0.999)) if dt > 0 else 10
    return {
        "room_name": room_name,
        "room_names": _room_names(runtime),
        "sources": _heat_sources(runtime),
        "dt": dt,
        "has_contact_entity": _room_has_contact(runtime, room_name),
        "min_history_steps": min_steps,
    }


def _categorise_records(runtime: Any, records: Sequence[Mapping[str, Any]], room_name: str) -> dict[str, Any]:
    from heatingassistant.engine.estimation.coverage import categorise_pe_coverage

    return categorise_pe_coverage(list(records or []), **_pe_coverage_kwargs(runtime, room_name))


def annotate_datasets_with_coverage(runtime: Any, metas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach per-dataset PE category tags for the stored-dataset summary."""
    from heatingassistant.engine.estimation.coverage import coverage_tags

    out: list[dict[str, Any]] = []
    engine = getattr(runtime, "control_engine", None)
    can_categorise = engine is not None and getattr(engine, "model", None) is not None
    for meta in metas:
        item = dict(meta)
        if not can_categorise:
            out.append(item)
            continue
        dataset_id = item.get("id")
        records = records_for_dataset(runtime, str(dataset_id)) if dataset_id else None
        room_value = item.get("room_name") or item.get("room_slug")
        try:
            room_name = _resolve_room(runtime, str(room_value or ""))
            cov = _categorise_records(runtime, records or [], room_name)
            item["coverage_categories"] = coverage_tags(cov)
        except Exception:
            _LOGGER.debug("PE coverage tags skipped for dataset %s", dataset_id, exc_info=True)
        out.append(item)
    return out


async def handle_get_pe_coverage(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    """Return recommended-data coverage for selected datasets or the live window."""
    values = _payload(data)
    room_name = _resolve_room(runtime, str(values.get("room_name") or values.get("room_slug") or ""))
    dataset_ids = _dataset_id_list(values.get("dataset_ids"))
    from heatingassistant.engine.estimation.coverage import union_pe_coverage

    if dataset_ids:
        coverages = []
        for dataset_id in dataset_ids:
            records = await asyncio.to_thread(records_for_dataset, runtime, dataset_id)
            coverages.append(_categorise_records(runtime, records or [], room_name))
        return union_pe_coverage(coverages, room_name=room_name)

    horizon_hours = values.get("horizon_hours")
    history = await resolve_history(
        runtime,
        dataset_id=values.get("dataset_id"),
        window_start=values.get("window_start"),
        window_end=values.get("window_end"),
        horizon_hours=float(horizon_hours) if horizon_hours is not None else None,
    )
    return _categorise_records(runtime, history, room_name)


async def handle_get_pe_inputs(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    """Return heater / outdoor / solar series for the PE window or dataset."""
    values = _payload(data)
    room_name = _resolve_room(runtime, str(values.get("room_name") or values.get("room_slug") or ""))
    horizon_hours = values.get("horizon_hours")
    history = await resolve_history(
        runtime,
        dataset_id=values.get("dataset_id"),
        dataset_ids=values.get("dataset_ids"),
        window_start=values.get("window_start"),
        window_end=values.get("window_end"),
        horizon_hours=float(horizon_hours) if horizon_hours is not None else None,
    )
    heater_scales = effective_heater_scales(
        values, getattr(runtime, "_last_identified_heater_scales", {})
    )
    sources = patched_heat_sources(_heat_sources(runtime), heater_scales)
    return identification_aux_series(
        history,
        sources,
        room_name,
        iso_time=_iso_time,
    )


def _room_has_contact(runtime: Any, room_name: str) -> bool:
    rooms = getattr(runtime, "options", {}).get("rooms") or []
    slug = _slug(runtime, room_name)
    for room in rooms:
        if not isinstance(room, Mapping):
            continue
        name = str(room.get("name") or "")
        if name != room_name and _slug(runtime, name) != slug:
            continue
        sensors = room.get("window_sensors") or []
        if isinstance(sensors, str):
            return bool(sensors.strip())
        return bool(sensors)
    return False


async def handle_store_identified_parameters(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    room_name = _resolve_room(runtime, str(values.get("room_name") or ""))
    snapshot = store_identified_parameters(
        _model(runtime),
        _heat_sources(runtime),
        runtime.options,
        room_name,
        float(values["thermal_mass"]),
        float(values["r_external"]),
        source=str(values.get("source", "manual")),
        rmse=float(values["rmse"]) if values.get("rmse") is not None else None,
        internal_gain=float(values["internal_gain"]) if values.get("internal_gain") is not None else None,
        solar_scale=float(values["solar_scale"]) if values.get("solar_scale") is not None else None,
        c_air_fraction=float(values["c_air_fraction"]) if values.get("c_air_fraction") is not None else None,
        r_aw_fraction=float(values["r_aw_fraction"]) if values.get("r_aw_fraction") is not None else None,
        heater_scales=dict(values["heater_scales"]) if isinstance(values.get("heater_scales"), Mapping) else None,
        ua_open=float(values["ua_open"]) if values.get("ua_open") not in (None, "") else None,
    )
    _persist_runtime_config(runtime)
    return {"stored": True, "estimated_params": snapshot}


async def handle_update_estimation_params(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    updates: dict[str, Any] = {}
    for key in (
        const.CONF_SIGMA_W,
        const.CONF_SIGMA_V,
        const.CONF_PARAMETER_ESTIMATION_HORIZON_HOURS,
    ):
        if key in values:
            updates[key] = float(values[key])
    if updates:
        return {"config": await runtime.update_config(updates)}
    return {"config": runtime.config()}


async def handle_delete_parameter_history(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    snapshot = delete_parameter_history(runtime.options, int(values["history_index"]))
    _persist_runtime_config(runtime)
    return {"deleted": True, "estimated_params": snapshot}


async def handle_create_dataset(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    name = str(values.get("name") or "").strip()
    if not name:
        raise ValueError("Dataset name must not be empty")
    window_start = float(values["window_start"])
    window_end = float(values["window_end"])
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    room_name_value = values.get("room_name")
    canonical = _resolve_room(runtime, str(room_name_value)) if room_name_value else None
    records = await resolve_history(runtime, window_start=window_start, window_end=window_end)
    records = select_window_by_timestamps(records, window_start, window_end)
    if not records:
        raise ValueError("No observation data found in the selected window")

    dataset = build_dataset(
        name,
        records,
        room_name=canonical,
        room_slug=_slug(runtime, canonical) if canonical else None,
        source=const.DATASET_SOURCE_MANUAL,
        notes=str(values.get("notes", "")),
        window_start=window_start,
        window_end=window_end,
    )
    await runtime.dataset_store.async_add(dataset)
    return {"dataset_id": dataset["id"], "record_count": dataset["record_count"]}


async def handle_delete_dataset(runtime: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    values = _payload(data)
    deleted = await runtime.dataset_store.async_delete(str(values["dataset_id"]))
    return {"deleted": bool(deleted)}


def _iso_time(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _formatted_sysid_simulation(room_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for entry in list(room_data.get("simulation", []) or []):
        if not isinstance(entry, Mapping):
            continue
        sim_entry: dict[str, Any] = {
            "time": _iso_time(entry.get("time")),
            "measured": entry.get("measured"),
            "predicted": entry.get("predicted"),
            "cov_upper": entry.get("cov_upper"),
            "cov_lower": entry.get("cov_lower"),
        }
        if entry.get("predicted_wall") is not None:
            sim_entry["predicted_wall"] = entry.get("predicted_wall")
            sim_entry["wall_cov_upper"] = entry.get("wall_cov_upper")
            sim_entry["wall_cov_lower"] = entry.get("wall_cov_lower")
        formatted.append(sim_entry)
    return formatted


def sysid_sensor_attrs(runtime: Any, room_name: str) -> dict[str, Any]:
    room_data = dict(getattr(runtime, "sysid_results", {}).get(room_name, {}) or {})
    horizon_steps = room_data.get("horizon_steps", 0)
    horizon_hours = round(float(horizon_steps) * _dt(runtime) / 3600.0, 2) if horizon_steps else None
    return {
        "simulation": _formatted_sysid_simulation(room_data),
        "thermal_mass": room_data.get("thermal_mass"),
        "r_external": room_data.get("r_external"),
        "internal_gain": room_data.get("internal_gain"),
        "ua_open": room_data.get("ua_open"),
        "solar_scale": room_data.get("solar_scale"),
        "c_air_fraction": room_data.get("c_air_fraction"),
        "r_aw_fraction": room_data.get("r_aw_fraction"),
        "t_wall_initial": room_data.get("t_wall_initial"),
        "estimated_inter_room_r": room_data.get("estimated_inter_room_r"),
        "heater_scales": room_data.get("heater_scales"),
        "sigma_w": room_data.get("sigma_w"),
        "sigma_v": room_data.get("sigma_v"),
        "rmse": room_data.get("rmse"),
        "mae": room_data.get("mae"),
        "horizon_hours": horizon_hours,
        "window_start": room_data.get("window_start"),
        "window_end": room_data.get("window_end"),
        "heating_power": _iso_series(room_data.get("heating_power")),
        "outdoor_temp": _iso_series(room_data.get("outdoor_temp")),
        "solar_gain": _iso_series(room_data.get("solar_gain")),
    }


def open_loop_sensor_attrs(runtime: Any, room_name: str) -> dict[str, Any]:
    room_data = dict(getattr(runtime, "open_loop_results", {}).get(room_name, {}) or {})
    formatted = []
    for entry in list(room_data.get("simulation", []) or []):
        if not isinstance(entry, Mapping):
            continue
        ol_entry: dict[str, Any] = {
            "time": _iso_time(entry.get("time")),
            "measured": entry.get("measured"),
            "predicted": entry.get("predicted"),
        }
        if entry.get("predicted_wall") is not None:
            ol_entry["predicted_wall"] = entry.get("predicted_wall")
        formatted.append(ol_entry)
    attrs = {
        "open_loop_rmse": room_data.get("rmse"),
        "open_loop_mae": room_data.get("mae"),
        "rmse_by_horizon": room_data.get("rmse_by_horizon"),
        "simulation": formatted,
        "t_wall_initial": room_data.get("t_wall_initial"),
        "heating_power": _iso_series(room_data.get("heating_power")),
        "outdoor_temp": _iso_series(room_data.get("outdoor_temp")),
        "solar_gain": _iso_series(room_data.get("solar_gain")),
    }
    if "error" in room_data:
        attrs["error"] = room_data["error"]
    return attrs


def _room_index(runtime: Any, room_name: str) -> int | None:
    room_names = list(getattr(getattr(runtime.control_engine, "model", None), "room_names", []) or [])
    try:
        return room_names.index(room_name)
    except ValueError:
        return None


def closed_loop_fit_for_room(
    runtime: Any, room_name: str
) -> tuple[float | None, float | None, int | None]:
    """Return ``(r_squared, rmse, n_samples)`` from aligned history ``y`` / ``y_pred``."""

    room_idx = _room_index(runtime, room_name)
    if room_idx is None:
        return None, None, None

    predictions: list[float] = []
    measurements: list[float] = []
    for record in list(getattr(runtime, "_history_buffer", []) or []):
        if not isinstance(record, Mapping):
            continue
        y = record.get("y", []) or []
        y_pred = record.get("y_pred")
        if y_pred is None:
            continue
        if room_idx < len(y) and room_idx < len(y_pred):
            predictions.append(float(y_pred[room_idx]))
            measurements.append(float(y[room_idx]))

    if len(predictions) < 2:
        return None, None, len(predictions)

    try:
        metrics = compute_model_fit_metrics(predictions, measurements, room_name)
    except Exception:
        _LOGGER.exception("Failed to compute closed-loop fit for %s", room_name)
        return None, None, len(predictions)
    return float(metrics.r_squared), float(metrics.rmse), int(metrics.n_samples)


def _room_estimation_provenance(
    snapshot: Mapping[str, Any] | None, room_name: str
) -> tuple[bool, str | None]:
    if not snapshot:
        return False, None
    rooms = snapshot.get("rooms") if isinstance(snapshot, Mapping) else None
    room_snap = rooms.get(room_name) if isinstance(rooms, Mapping) else None
    if not isinstance(room_snap, Mapping):
        return False, None
    if "is_estimated" in room_snap:
        is_estimated = bool(room_snap.get("is_estimated"))
        estimated_at = room_snap.get("estimated_at") if is_estimated else None
        return is_estimated, estimated_at if isinstance(estimated_at, str) else None
    return False, None


def model_fit_quality_sensor(runtime: Any, room_name: str) -> tuple[Any, dict[str, Any]]:
    """Return ``(state, attributes)`` for ``*_model_fit_quality``."""

    room_idx = _room_index(runtime, room_name)
    if room_idx is None:
        return "unknown", {"error": "Unknown room", "n_samples": 0}

    predictions: list[float] = []
    measurements: list[float] = []
    for record in list(getattr(runtime, "_history_buffer", []) or []):
        if not isinstance(record, Mapping):
            continue
        y = record.get("y", []) or []
        y_pred = record.get("y_pred")
        if y_pred is None:
            continue
        if room_idx < len(y) and room_idx < len(y_pred):
            predictions.append(float(y_pred[room_idx]))
            measurements.append(float(y[room_idx]))

    if len(predictions) < 2:
        return "unknown", {"error": "Insufficient data", "n_samples": len(predictions)}

    try:
        metrics = compute_model_fit_metrics(predictions, measurements, room_name)
    except Exception as exc:
        _LOGGER.exception("Failed to compute model fit quality for %s", room_name)
        return "unknown", {"error": str(exc), "n_samples": len(predictions)}

    return round(float(metrics.r_squared), 4), {
        "r_squared": round(float(metrics.r_squared), 4),
        "rmse": round(float(metrics.rmse), 3),
        "mae": round(float(metrics.mae), 3),
        "bias": round(float(metrics.bias), 3),
        "max_error": round(float(metrics.max_error), 2),
        "residual_std": round(float(metrics.residual_std), 3),
        "residual_autocorr_lag1": (
            round(float(metrics.residual_autocorr_lag1), 3)
            if metrics.residual_autocorr_lag1 is not None
            else None
        ),
        "n_samples": int(metrics.n_samples),
        "room": room_name,
    }


def parameter_confidence_sensor(runtime: Any, room_name: str) -> tuple[Any, dict[str, Any]]:
    """Return ``(state, attributes)`` for ``*_parameter_confidence``."""

    rooms = getattr(getattr(runtime.control_engine, "model", None), "rooms", {}) or {}
    room = rooms.get(room_name)
    if room is None:
        return "unknown", {"error": "Unknown room"}

    try:
        fit_r2, fit_rmse, n_samples = closed_loop_fit_for_room(runtime, room_name)
        validation = validate_parameters(
            room_name,
            float(getattr(room, "thermal_mass", 0.0) or 0.0),
            float(getattr(room, "r_external", 0.0) or 0.0),
            model_r_squared=fit_r2,
            model_rmse=fit_rmse,
        )
        score = 0.0
        if validation.mass_valid:
            score += 33.3
        if validation.r_external_valid:
            score += 33.3
        if validation.time_constant_valid:
            score += 33.4

        snapshot = estimated_params_snapshot(runtime.options)
        is_estimated, estimated_at = _room_estimation_provenance(snapshot, room_name)
        ol_rmse = (
            getattr(runtime, "open_loop_results", {}).get(room_name, {}) or {}
        ).get("rmse")
        card_warnings = build_identification_warnings(
            room_name,
            validation,
            model_r_squared=fit_r2,
            model_rmse=fit_rmse,
            open_loop_rmse=float(ol_rmse) if ol_rmse is not None else None,
            n_samples=n_samples,
        )
        return round(score, 1), {
            "thermal_mass": validation.thermal_mass,
            "r_external": validation.r_external,
            "internal_gain": round(float(getattr(room, "internal_gain", 0.0) or 0.0), 2),
            "ua_open": round(float(getattr(room, "ua_open", 0.0) or 0.0), 3),
            "time_constant_hours": round(float(validation.time_constant_hours), 2),
            "mass_valid": validation.mass_valid,
            "r_external_valid": validation.r_external_valid,
            "time_constant_valid": validation.time_constant_valid,
            "warnings": list(validation.warnings),
            "card_warnings": [
                {"code": w.code, "message": w.message, "severity": w.severity}
                for w in card_warnings
            ],
            "is_estimated": is_estimated,
            "estimated_at": estimated_at,
            "room": room_name,
        }
    except Exception as exc:
        _LOGGER.exception("Failed to validate parameters for %s", room_name)
        return "unknown", {"error": str(exc)}


__all__ = [
    "closed_loop_fit_for_room",
    "handle_create_dataset",
    "handle_delete_dataset",
    "handle_delete_parameter_history",
    "handle_estimate_parameters_ml",
    "annotate_datasets_with_coverage",
    "handle_get_pe_coverage",
    "handle_get_pe_inputs",
    "handle_run_open_loop_simulation",
    "handle_run_sysid_simulation",
    "handle_store_identified_parameters",
    "handle_update_estimation_params",
    "model_fit_quality_sensor",
    "open_loop_sensor_attrs",
    "parameter_confidence_sensor",
    "resolve_history",
    "sysid_sensor_attrs",
]
