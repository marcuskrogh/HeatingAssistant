"""Pure-Python helpers for the Heating Assistant options flow.

This module owns the room and window CRUD logic that used to live inline in
``HeatingAssistantOptionsFlow``.  Pulling it out keeps ``config_flow.py``
focused on schema building and HA-framework wiring, and lets the validation /
mutation paths be unit-tested without standing up the full
:class:`~homeassistant.config_entries.OptionsFlow` machinery.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .const import (
    CONF_ENVELOPE_TIGHTNESS,
    CONF_INFILTRATION_FRACTION,
    CONF_ROOM_NAME,
    CONF_SETPOINT,
    CONF_R_EXTERNAL,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_THERMAL_MASS,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    DEFAULT_ENVELOPE_TIGHTNESS,
    DEFAULT_WINDOW_TILT,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
)

# ---------------------------------------------------------------------------
# UI-only mappings (kept here so the helpers stay self-contained for tests)
# ---------------------------------------------------------------------------

#: Mapping from 8-point compass label → orientation degrees (clockwise from N).
COMPASS_TO_DEGREES: Dict[str, float] = {
    "N": 0.0,
    "NE": 45.0,
    "E": 90.0,
    "SE": 135.0,
    "S": 180.0,
    "SW": 225.0,
    "W": 270.0,
    "NW": 315.0,
}

_DEGREES_TO_COMPASS: Dict[float, str] = {v: k for k, v in COMPASS_TO_DEGREES.items()}

ROOM_SIZE_TO_THERMAL_MASS: Dict[str, float] = {
    "small": 3_500_000.0,
    "medium": 5_000_000.0,
    "large": 8_000_000.0,
}

BUILDING_AGE_TO_R_EXTERNAL: Dict[str, float] = {
    "pre_1940": 0.03,
    "1940_1979": 0.045,
    "1980_1999": 0.06,
    "2000_plus": 0.08,
}


# ---------------------------------------------------------------------------
# Pure helpers (no HA dependency)
# ---------------------------------------------------------------------------


def degrees_to_compass(degrees: float) -> str:
    """Return the nearest compass label for an orientation in degrees."""
    normalized = degrees % 360.0
    best = min(
        _DEGREES_TO_COMPASS,
        key=lambda d: abs((d - normalized + 180) % 360 - 180),
    )
    return _DEGREES_TO_COMPASS[best]


def window_display(room_name: str, idx: int, window: Dict[str, Any]) -> str:
    """Human-readable window label for the remove-window selector."""
    area = window.get(CONF_WINDOW_AREA, "?")
    compass = degrees_to_compass(float(window.get(CONF_WINDOW_ORIENTATION, 0)))
    tilt = window.get(CONF_WINDOW_TILT, DEFAULT_WINDOW_TILT)
    return f"{room_name} · Window {idx + 1}: {area} m² facing {compass} (tilt {tilt}°)"


def parse_entity_ids(raw_value: str) -> List[str]:
    """Parse comma-separated entity IDs from a text field."""
    if not raw_value or not raw_value.strip():
        return []
    return [entity.strip() for entity in raw_value.split(",") if entity.strip()]


def format_entity_ids(entity_ids: List[str]) -> str:
    """Format entity IDs as a comma-separated string for UI defaults."""
    return ", ".join(entity_ids)


def nearest_choice(value: float, mapping: Dict[str, float], default_key: str) -> str:
    """Return the nearest mapping key for a numeric value."""
    if not mapping:
        return default_key
    return min(mapping, key=lambda key: abs(mapping[key] - value))


# ---------------------------------------------------------------------------
# Room CRUD helper
# ---------------------------------------------------------------------------


class RoomFlowHelper:
    """In-memory rooms list with the validation / mutation primitives the
    options flow needs.

    Owns:
        * ``rooms`` — the working list of room dicts (mutated in place)
        * ``current_idx`` — the room selected by the most recent ``select()``;
          consumed by ``update_current()`` and ``current_room()``.

    All methods return either a new state, an error key (string) the caller
    can translate via ``strings.json``, or ``None`` to mean "no error".
    """

    def __init__(self, rooms: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rooms: List[Dict[str, Any]] = [dict(r) for r in (rooms or [])]
        self.current_idx: Optional[int] = None

    # ── Read-only views ────────────────────────────────────────────────

    def __bool__(self) -> bool:
        return bool(self.rooms)

    def __len__(self) -> int:
        return len(self.rooms)

    def names(self) -> List[str]:
        return [r[CONF_ROOM_NAME] for r in self.rooms]

    def current_room(self) -> Optional[Dict[str, Any]]:
        if self.current_idx is None or self.current_idx >= len(self.rooms):
            return None
        return self.rooms[self.current_idx]

    def is_duplicate(self, name: str, exclude_idx: Optional[int] = None) -> bool:
        """True iff another room (case-insensitive) already has this name."""
        lname = name.lower()
        for i, r in enumerate(self.rooms):
            if i == exclude_idx:
                continue
            if r[CONF_ROOM_NAME].lower() == lname:
                return True
        return False

    # ── Selection ──────────────────────────────────────────────────────

    def select(self, name: str) -> bool:
        """Bind ``current_idx`` to the room with this name; return success."""
        for i, r in enumerate(self.rooms):
            if r[CONF_ROOM_NAME] == name:
                self.current_idx = i
                return True
        self.current_idx = None
        return False

    # ── Mutations ──────────────────────────────────────────────────────

    def add(
        self,
        *,
        name: str,
        sensors: List[str],
        thermal_mass: float,
        r_external: float,
        setpoint: float,
        infiltration_fraction: Optional[float] = None,
    ) -> Optional[str]:
        """Append a new room.  Returns an error key on duplicate, else ``None``.

        ``infiltration_fraction`` is the Sherman–Grimsrud share of
        ``1/r_external`` that the Phase 1 C1 overlay attributes to
        wind-driven air exchange.  When ``None`` the default for the
        configured envelope-tightness preset is used.
        """
        name = name.strip()
        if self.is_duplicate(name):
            return "duplicate_room"
        new_room: Dict[str, Any] = {
            CONF_ROOM_NAME: name,
            CONF_TEMP_SENSORS: list(sensors),
            CONF_THERMAL_MASS: thermal_mass,
            CONF_R_EXTERNAL: r_external,
            CONF_SETPOINT: setpoint,
            CONF_WINDOWS: [],
        }
        if infiltration_fraction is not None:
            new_room[CONF_INFILTRATION_FRACTION] = float(infiltration_fraction)
        if sensors:
            new_room[CONF_TEMP_SENSOR] = sensors[0]
        self.rooms.append(new_room)
        return None

    def update_current(
        self,
        *,
        name: str,
        sensors: List[str],
        thermal_mass: float,
        r_external: float,
        setpoint: float,
        infiltration_fraction: Optional[float] = None,
    ) -> Optional[str]:
        """Update the currently-selected room.  Returns an error key or ``None``."""
        if self.current_idx is None:
            return "no_room_selected"
        name = name.strip()
        if self.is_duplicate(name, exclude_idx=self.current_idx):
            return "duplicate_room"
        room = self.rooms[self.current_idx]
        room[CONF_ROOM_NAME] = name
        room[CONF_TEMP_SENSORS] = list(sensors)
        if sensors:
            room[CONF_TEMP_SENSOR] = sensors[0]
        else:
            room.pop(CONF_TEMP_SENSOR, None)
        room[CONF_THERMAL_MASS] = thermal_mass
        room[CONF_R_EXTERNAL] = r_external
        room[CONF_SETPOINT] = setpoint
        if infiltration_fraction is not None:
            room[CONF_INFILTRATION_FRACTION] = float(infiltration_fraction)
        elif CONF_INFILTRATION_FRACTION in room:
            # No explicit override on this edit → leave whatever was there
            # alone (keeps a previously customised value intact).
            pass
        return None

    def remove_by_name(self, name: str) -> bool:
        """Remove a room by name.  Resets ``current_idx`` afterwards."""
        before = len(self.rooms)
        self.rooms = [r for r in self.rooms if r[CONF_ROOM_NAME] != name]
        self.current_idx = None
        return len(self.rooms) != before


# ---------------------------------------------------------------------------
# Window CRUD helper
# ---------------------------------------------------------------------------


class WindowFlowHelper:
    """Mutate the ``CONF_WINDOWS`` list of a single, already-selected room.

    The room dict is held by reference so changes propagate to whoever owns it
    (typically a :class:`RoomFlowHelper`).
    """

    def __init__(self, room: Dict[str, Any]) -> None:
        self._room = room
        # ``setdefault`` keeps existing windows; only initialises an empty list.
        self._room.setdefault(CONF_WINDOWS, [])

    @property
    def windows(self) -> List[Dict[str, Any]]:
        return self._room[CONF_WINDOWS]

    def __bool__(self) -> bool:
        return bool(self.windows)

    def __len__(self) -> int:
        return len(self.windows)

    def add_from_compass(
        self,
        *,
        area: float,
        compass: str,
        tilt: float,
    ) -> None:
        """Append a window using a compass label (raises ``KeyError`` if invalid)."""
        self.windows.append(
            {
                CONF_WINDOW_AREA: area,
                CONF_WINDOW_ORIENTATION: COMPASS_TO_DEGREES[compass],
                CONF_WINDOW_TILT: tilt,
            }
        )

    def remove(self, idx: int) -> bool:
        """Remove the window at ``idx``.  Returns ``False`` if out of range."""
        if 0 <= idx < len(self.windows):
            self.windows.pop(idx)
            return True
        return False

    def display_options(
        self,
        room_name: str,
        labeller: Callable[[str, int, Dict[str, Any]], str] = window_display,
    ) -> Dict[str, str]:
        """Return ``{idx_str: human_label}`` for the remove-window selector."""
        return {str(i): labeller(room_name, i, w) for i, w in enumerate(self.windows)}
