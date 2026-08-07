"""Room-rename migration helpers for Heating Assistant configuration updates."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_ESTIMATED_PARAMS,
    CONF_HEAT_SOURCES,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_SETPOINTS,
    CONF_SOURCE_ROOM,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

#: ``entry.data`` keys whose value is a ``{room_name: ...}`` dict.
_ROOM_KEYED_STATE_KEYS = (
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SCHEDULES,
)


def _remap_keys(d: Any, renames: Dict[str, str]) -> Any:
    """Return ``d`` with any top-level keys present in ``renames`` remapped."""
    if not isinstance(d, dict):
        return d
    return {renames.get(k, k): v for k, v in d.items()}


def _migrate_room_name_data(data: Dict[str, Any], renames: Dict[str, str]) -> Dict[str, Any]:
    """Migrate every room-name-keyed structure in a ``data``/``options`` dict.

    Covers persisted per-room state, the nested ``rooms`` map inside the
    estimated-parameters snapshot, and the ``room`` link on each heat source.
    Returns a new dict; the input is not mutated.
    """
    if not renames:
        return dict(data)
    new = dict(data)

    for key in _ROOM_KEYED_STATE_KEYS:
        if isinstance(new.get(key), dict):
            new[key] = _remap_keys(new[key], renames)

    estimated = new.get(CONF_ESTIMATED_PARAMS)
    if isinstance(estimated, dict) and isinstance(estimated.get("rooms"), dict):
        estimated = dict(estimated)
        estimated["rooms"] = _remap_keys(estimated["rooms"], renames)
        new[CONF_ESTIMATED_PARAMS] = estimated

    sources = new.get(CONF_HEAT_SOURCES)
    if isinstance(sources, list):
        new[CONF_HEAT_SOURCES] = [
            {**s, CONF_SOURCE_ROOM: renames[s[CONF_SOURCE_ROOM]]}
            if isinstance(s, dict) and s.get(CONF_SOURCE_ROOM) in renames
            else s
            for s in sources
        ]
    return new


def _apply_renames_to_connections(
    rooms: List[Dict[str, Any]], renames: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Re-point inter-room connection targets to renamed rooms."""
    if not renames:
        return rooms
    out: List[Dict[str, Any]] = []
    for room in rooms:
        if isinstance(room, dict) and isinstance(room.get(CONF_CONNECTIONS), list):
            room = {
                **room,
                CONF_CONNECTIONS: [
                    {**c, CONF_CONNECTED_ROOM: renames[c[CONF_CONNECTED_ROOM]]}
                    if isinstance(c, dict) and c.get(CONF_CONNECTED_ROOM) in renames
                    else c
                    for c in room[CONF_CONNECTIONS]
                ],
            }
        out.append(room)
    return out


def _migrate_room_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    renames: Dict[str, str],
    all_room_names: List[str],
) -> None:
    """Best-effort: move this integration's per-room entities to the new name.

    Updates each affected registry entry's ``unique_id`` (and ``entity_id`` slug)
    in place so the room keeps its recorder history, dashboards and automations
    instead of orphaning the old entities and creating fresh ones.  Any failure
    is logged and swallowed — a rename must never be blocked by registry quirks.
    """
    from homeassistant.helpers import entity_registry as er

    from .naming import slugify as _slugify

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    existing_uids = {e.unique_id for e in entries}
    other_names = [n for n in all_room_names if n]

    for ent in entries:
        for old, new in renames.items():
            prefix = f"{DOMAIN}_{old}_"
            if not ent.unique_id.startswith(prefix):
                continue
            # Skip when a longer room name also prefixes this id (e.g. renaming
            # "living" must not capture "living_room"'s entities).
            if any(
                other != old
                and len(other) > len(old)
                and ent.unique_id.startswith(f"{DOMAIN}_{other}_")
                for other in other_names
            ):
                break
            new_uid = f"{DOMAIN}_{new}_" + ent.unique_id[len(prefix):]
            if new_uid in existing_uids:
                break
            updates: Dict[str, Any] = {"new_unique_id": new_uid}
            old_slug = _slugify(old)
            new_slug = _slugify(new)
            old_eid_part = f"{DOMAIN}_{old_slug}_"
            if old_slug != new_slug and old_eid_part in ent.entity_id:
                updates["new_entity_id"] = ent.entity_id.replace(
                    old_eid_part, f"{DOMAIN}_{new_slug}_", 1
                )
            try:
                registry.async_update_entity(ent.entity_id, **updates)
                existing_uids.add(new_uid)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.warning(
                    "Heating Assistant: failed migrating entity %s on room rename",
                    ent.entity_id,
                    exc_info=True,
                )
            break
