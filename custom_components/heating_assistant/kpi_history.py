"""
Recorder-backed history fetch for per-room KPI calculations.

The in-memory identification buffer does not retain comfort-corridor bounds, so
the 24 h time-in-range KPI is rebuilt from the integration's own recorder
entities (temperature filtered + constraint sensors), mirroring the room-detail
charts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .dashboard import slugify
from .history_seed import _samples_from_states
from .kpi import TIME_IN_RANGE_WINDOW_S, room_time_in_range_pct

_LOGGER = logging.getLogger(__name__)

# Refresh at most once per coordinator update interval (typically 15 min).
TIME_IN_RANGE_REFRESH_S = 900.0


def room_comfort_entity_ids(room_name: str) -> Dict[str, str]:
    """Recorder entity ids for a room's comfort KPI inputs."""
    slug = slugify(room_name)
    prefix = f"sensor.heating_assistant_{slug}"
    return {
        "temperature_filtered": f"{prefix}_temperature_filtered",
        "temperature_measured": f"{prefix}_temperature_measured",
        "constraint_lower": f"{prefix}_constraint_lower",
        "constraint_upper": f"{prefix}_constraint_upper",
    }


async def async_fetch_entity_samples(
    hass: Any,
    entity_ids: Sequence[str],
    start_ts: float,
    end_ts: float,
) -> Dict[str, List[Tuple[float, float]]]:
    """Fetch ``(timestamp, value)`` samples for ``entity_ids`` from the recorder."""
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
    except Exception:
        _LOGGER.debug("Recorder unavailable; skipping KPI history fetch")
        return {}

    if not entity_ids:
        return {}

    start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ts, tz=timezone.utc)

    def _fetch() -> Dict[str, List[Any]]:
        return get_significant_states(
            hass,
            start,
            end,
            list(entity_ids),
            None,
            True,   # include_start_time_state
            False,  # significant_changes_only
            False,  # minimal_response
            True,   # no_attributes
        )

    try:
        history = await get_instance(hass).async_add_executor_job(_fetch)
    except Exception:
        _LOGGER.warning("KPI history fetch from recorder failed", exc_info=True)
        return {}

    return {
        entity_id: _samples_from_states(states)
        for entity_id, states in (history or {}).items()
    }


def _prefer_filtered_temperature(
    samples_by_entity: Dict[str, List[Tuple[float, float]]],
    ids: Dict[str, str],
) -> List[Tuple[float, float]]:
    filtered = samples_by_entity.get(ids["temperature_filtered"]) or []
    if filtered:
        return filtered
    return samples_by_entity.get(ids["temperature_measured"]) or []


async def async_compute_room_time_in_range_pct(
    hass: Any,
    coordinator: Any,
    room_name: str,
    room_snapshot: Any,
    window_end_ts: float,
    window_seconds: float = TIME_IN_RANGE_WINDOW_S,
) -> Optional[float]:
    """Compute rolling time-in-range for one room from recorder history."""
    ids = room_comfort_entity_ids(room_name)
    window_start = float(window_end_ts) - float(window_seconds)
    samples_by_entity = await async_fetch_entity_samples(
        hass,
        list(ids.values()),
        window_start,
        float(window_end_ts),
    )
    if not samples_by_entity:
        return None

    temp_samples = _prefer_filtered_temperature(samples_by_entity, ids)
    lower_samples = samples_by_entity.get(ids["constraint_lower"]) or []
    upper_samples = samples_by_entity.get(ids["constraint_upper"]) or []

    return room_time_in_range_pct(
        room_snapshot,
        temp_samples,
        lower_samples,
        upper_samples,
        float(window_end_ts),
        window_seconds=window_seconds,
    )


async def async_refresh_time_in_range_kpis(
    hass: Any,
    coordinator: Any,
    window_end_ts: float,
) -> Dict[str, Optional[float]]:
    """Refresh the 24 h time-in-range cache for every model room."""
    from .sensor import _kpi_room_snapshot  # noqa: PLC0415 — HA import boundary

    results: Dict[str, Optional[float]] = {}
    for room_name in coordinator.model.room_names:
        snapshot = _kpi_room_snapshot(coordinator, room_name)
        try:
            pct = await async_compute_room_time_in_range_pct(
                hass, coordinator, room_name, snapshot, window_end_ts,
            )
        except Exception:
            _LOGGER.warning(
                "Time-in-range KPI refresh failed for room %s",
                room_name,
                exc_info=True,
            )
            pct = coordinator._time_in_range_pct_24h.get(room_name)
        results[room_name] = pct
    return results
