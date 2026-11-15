"""Living Room parameter set fitted to 8–15 Nov 2026.

The heat-loss coefficient is the stable part of the fit. The split between
the pump's minimum output and the extra heat per degree of offset is not.
``HeatPump.min_power`` is still stored, but it no longer changes thermal
output, so the minimum output is ``internal_gain`` for now.

The 26 Sep set stays in the snapshot history. ``revert_parameters`` with
``history_index=0`` promotes that entry back to active.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Mapping, MutableMapping

from .const import CONF_ESTIMATED_PARAMS
from .parameter_lifecycle import _MAX_PARAMETER_HISTORY, _active_from_snapshot

_SNAPSHOT_NAME = "living_room_estimated_params.json"


def living_room_snapshot() -> dict[str, Any]:
    text = (
        resources.files("heatingassistant.engine.data")
        .joinpath(_SNAPSHOT_NAME)
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _room(active: Mapping[str, Any], name: str = "Living Room") -> Mapping[str, Any] | None:
    rooms = active.get("rooms")
    if not isinstance(rooms, Mapping):
        return None
    room = rooms.get(name)
    return room if isinstance(room, Mapping) else None


def _is_september_set(room: Mapping[str, Any]) -> bool:
    try:
        capacity = float(room.get("thermal_mass"))
        resistance = float(room.get("r_external"))
        solar = float(room.get("solar_scale", 1.0))
    except (TypeError, ValueError):
        return False
    if resistance <= 0.0:
        return False
    heat_loss = 1.0 / resistance
    return (
        2.4e8 <= capacity <= 3.2e8
        and 250.0 <= heat_loss <= 320.0
        and 0.2 <= solar <= 0.35
    )


def apply_living_room_fit(options: MutableMapping[str, Any]) -> bool:
    """Replace an active 26 Sep Living Room set with the November fit.

    The replaced set is inserted at the front of parameter history so it can
    be restored. A snapshot that is already the November fit is left alone.
    Returns True when ``options`` was changed.
    """

    raw = options.get(CONF_ESTIMATED_PARAMS)
    if not isinstance(raw, Mapping):
        return False
    active = _active_from_snapshot(raw)
    room = _room(active)
    if room is None or not _is_september_set(room):
        return False

    packaged = living_room_snapshot()
    november = packaged["estimated_params"]["active"]
    history = list(raw.get("history") or [])
    history.insert(0, dict(active))
    snapshot = {
        "active": november,
        "history": history[:_MAX_PARAMETER_HISTORY],
        "rooms": november.get("rooms", {}),
        "sources": november.get("sources", {}),
        "connections": november.get("connections", {}),
        "estimated_at": november.get("estimated_at"),
        "source": november.get("source"),
    }
    options[CONF_ESTIMATED_PARAMS] = snapshot
    return True
