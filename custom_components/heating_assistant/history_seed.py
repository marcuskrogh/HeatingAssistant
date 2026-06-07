"""
Rebuild the identification history buffer from Home Assistant's recorder.

The integration keeps a small, in-memory ``history_buffer`` (a count-bounded
deque) that the EKF reconstruction, open-loop simulation and parameter
estimator replay.  That buffer is fragile: it only survives a *clean* unload,
so an unclean restart loses recent entries and leaves gaps — which is why the
diagnostics could not "see" the same continuous data the overview pages show.

The overview/room pages read directly from the **recorder**, which persists
every state change of the integration's own sensors across restarts.  Every
input the model needs is published as a recordable sensor:

    y         sensor.heating_assistant_<room>_temperature_measured   [°C]
    u         sensor.heating_assistant_<source>_control_action       [%]   (/100)
    d_outdoor sensor.heating_assistant_outdoor_temperature_measured  [°C]
    d_solar   sensor.heating_assistant_<room>_solar_gain_measured    [W]

``async_rebuild_history_from_recorder`` fetches those series, resamples them
onto the coordinator's uniform ``dt`` grid with a zero-order hold (matching the
controller's ZOH inputs), and returns buffer records byte-compatible with what
``sysid.run_sysid_ekf`` and ``model_diagnostics.compute_open_loop_predictions``
consume.  The result mirrors the recorder, so the diagnostics use exactly the
data the overviews display.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .recorder_resample import build_uniform_grid, zoh_resample

_LOGGER = logging.getLogger(__name__)


def _slug(name: str) -> str:
    # Local import keeps this module importable without the HA-heavy dashboard
    # dependency during unit tests that exercise the pure helpers.
    from .dashboard import slugify  # noqa: PLC0415

    return slugify(name)


def _entity_ids(coordinator: Any) -> Dict[str, Any]:
    """Return the recorder entity_ids needed to rebuild the buffer."""
    room_names = list(coordinator.model.room_names)
    source_names = [src.name for src in coordinator.heat_sources]
    return {
        "room_names": room_names,
        "source_names": source_names,
        "temp": [
            f"sensor.heating_assistant_{_slug(r)}_temperature_measured"
            for r in room_names
        ],
        "solar": [
            f"sensor.heating_assistant_{_slug(r)}_solar_gain_measured"
            for r in room_names
        ],
        "control": [
            f"sensor.heating_assistant_{_slug(s)}_control_action"
            for s in source_names
        ],
        "outdoor": "sensor.heating_assistant_outdoor_temperature_measured",
    }


def _samples_from_states(states: Optional[List[Any]]) -> List[tuple]:
    """Convert a recorder state list into ``(timestamp, float)`` samples,
    dropping ``unknown``/``unavailable``/non-numeric states."""
    out: List[tuple] = []
    for st in states or []:
        raw = getattr(st, "state", None)
        if raw in (None, "unknown", "unavailable", ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        ts = getattr(st, "last_updated", None) or getattr(st, "last_changed", None)
        if ts is None:
            continue
        out.append((ts.timestamp(), value))
    return out


def build_records_from_history(
    history: Dict[str, List[Any]],
    ids: Dict[str, Any],
    grid: List[float],
) -> List[Dict[str, Any]]:
    """Assemble buffer records from per-entity recorder state lists on ``grid``.

    A grid point is emitted only when every room temperature *and* the outdoor
    temperature are known at that instant (the essentials for a meaningful
    reconstruction).  Missing control / solar samples default to 0 (no heating /
    no solar gain), matching the buffer's own convention for absent inputs.
    """
    n_rooms = len(ids["room_names"])

    temp_series = [
        zoh_resample(_samples_from_states(history.get(e)), grid) for e in ids["temp"]
    ]
    solar_series = [
        zoh_resample(_samples_from_states(history.get(e)), grid) for e in ids["solar"]
    ]
    control_series = [
        zoh_resample(_samples_from_states(history.get(e)), grid)
        for e in ids["control"]
    ]
    outdoor_series = zoh_resample(
        _samples_from_states(history.get(ids["outdoor"])), grid
    )

    records: List[Dict[str, Any]] = []
    for i, ts in enumerate(grid):
        temps = [temp_series[r][i] for r in range(n_rooms)]
        outdoor = outdoor_series[i]
        if outdoor is None or any(t is None for t in temps):
            continue  # not enough data yet at this instant
        d_solar = {
            ids["room_names"][r]: float(solar_series[r][i] or 0.0)
            for r in range(n_rooms)
        }
        u = [float((c[i] or 0.0)) / 100.0 for c in control_series]
        records.append(
            {
                "y": [float(t) for t in temps],
                "u": u,
                "d_outdoor": float(outdoor),
                "d_solar": d_solar,
                "timestamp": float(ts),
            }
        )
    return records


async def async_rebuild_history_from_recorder(
    hass: Any,
    coordinator: Any,
    max_records: int,
) -> List[Dict[str, Any]]:
    """Fetch recorder history for the model inputs and return buffer records.

    Returns an empty list if the recorder is unavailable or holds no usable
    data, so callers can fall back to the persisted buffer.
    """
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
    except Exception:  # recorder not installed/loaded
        _LOGGER.debug("Recorder unavailable; skipping history rebuild")
        return []

    ids = _entity_ids(coordinator)
    if not ids["room_names"]:
        return []

    dt = float(coordinator.dt)
    if dt <= 0:
        return []

    end = datetime.now(tz=timezone.utc)
    # Cover the buffer's nominal span; a little head-room so the ZOH has a
    # starting value at the first grid point.
    start = end - timedelta(seconds=max_records * dt + dt)

    entity_ids = ids["temp"] + ids["solar"] + ids["control"] + [ids["outdoor"]]

    def _fetch() -> Dict[str, List[Any]]:
        return get_significant_states(
            hass,
            start,
            end,
            entity_ids,
            None,            # filters
            True,            # include_start_time_state
            False,           # significant_changes_only — we want every change
            False,           # minimal_response — need full State objects
            True,            # no_attributes
        )

    try:
        history = await get_instance(hass).async_add_executor_job(_fetch)
    except Exception:
        _LOGGER.warning("History rebuild from recorder failed", exc_info=True)
        return []

    if not history:
        return []

    # Grid spans from the earliest available temperature/outdoor sample to now,
    # so we don't emit records before any data existed.
    earliest = _earliest_usable_ts(history, ids)
    if earliest is None:
        return []
    grid_start = max(earliest, start.timestamp())
    grid = build_uniform_grid(grid_start, end.timestamp(), dt)
    if not grid:
        return []
    if len(grid) > max_records:
        grid = grid[-max_records:]

    records = build_records_from_history(history, ids, grid)
    if records:
        _LOGGER.debug(
            "Rebuilt %d history records from recorder (%d entities)",
            len(records),
            len(entity_ids),
        )
    return records


def _earliest_usable_ts(
    history: Dict[str, List[Any]], ids: Dict[str, Any]
) -> Optional[float]:
    """Earliest instant at which every room temperature and the outdoor
    temperature have at least one sample (so a full record can be formed)."""
    firsts: List[float] = []
    for e in ids["temp"] + [ids["outdoor"]]:
        samples = _samples_from_states(history.get(e))
        if not samples:
            return None  # an essential channel has no data at all
        firsts.append(min(ts for ts, _ in samples))
    return max(firsts) if firsts else None
