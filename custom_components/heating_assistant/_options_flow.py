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
    CONF_COMFORT_OFFSET,
    CONF_ENVELOPE_TIGHTNESS,
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_INFILTRATION_FRACTION,
    CONF_ROOM_NAME,
    CONF_SETPOINT,
    CONF_SKY_RADIATIVE_UA,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    CONF_R_EXTERNAL,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_THERMAL_MASS,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_SENSORS,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    CONF_HEAT_SOURCES,
    CONF_SOURCE_NAME,
    CONF_SOURCE_TYPE,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_HVAC_MODE,
    DEFAULT_SOURCE_HVAC_MODE,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SCHEDULE,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_ENVELOPE_TIGHTNESS,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    DEFAULT_WINDOW_TILT,
    DEFAULT_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_MIN_POWER,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_TURN_OFF_DEADBAND,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    FACADE_COLOUR_CUSTOM,
    FACADE_COLOUR_DARK,
    FACADE_COLOUR_LIGHT,
    FACADE_COLOUR_MEDIUM,
    FLOOR_TYPE_CONCRETE,
    FLOOR_TYPE_NONE,
    FLOOR_TYPE_SLAB_ON_GRADE,
    FLOOR_TYPE_UFH,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
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


#: Floor-type tokens accepted from the room form (mirrors const.FLOOR_TYPE_*).
FLOOR_TYPE_OPTIONS: List[str] = [
    FLOOR_TYPE_NONE,
    FLOOR_TYPE_CONCRETE,
    FLOOR_TYPE_SLAB_ON_GRADE,
    FLOOR_TYPE_UFH,
]

#: Facade colour tokens accepted from the room form (mirrors const.FACADE_COLOUR_*).
FACADE_COLOUR_OPTIONS: List[str] = [
    FACADE_COLOUR_LIGHT,
    FACADE_COLOUR_MEDIUM,
    FACADE_COLOUR_DARK,
    FACADE_COLOUR_CUSTOM,
]


def _apply_advanced_envelope(
    room: Dict[str, Any],
    *,
    floor_type: Optional[str],
    facade_colour: Optional[str],
    facade_solar_share: Optional[float],
    thermal_bridge_psi_l: Optional[float],
    sky_radiative_ua: Optional[float],
    solar_exposure: Optional[str] = None,
    solar_facing: Optional[float] = None,
) -> None:
    """Write advanced envelope fields into ``room`` only when explicitly set.

    Values that match the model default are stripped from the room dict — this
    keeps the stored shape minimal so existing rooms aren't decorated with
    extra keys unless the user actually customised something.
    """
    def _set_or_strip(key: str, value: Any, default: Any) -> None:
        if value is None:
            return
        if value == default:
            room.pop(key, None)
        else:
            room[key] = value

    if floor_type is not None:
        if floor_type == DEFAULT_FLOOR_TYPE:
            room.pop(CONF_FLOOR_TYPE, None)
        else:
            room[CONF_FLOOR_TYPE] = floor_type

    if facade_colour is not None:
        if facade_colour == DEFAULT_FACADE_COLOUR:
            room.pop(CONF_FACADE_COLOUR, None)
        else:
            room[CONF_FACADE_COLOUR] = facade_colour

    _set_or_strip(
        CONF_FACADE_SOLAR_SHARE,
        None if facade_solar_share is None else float(facade_solar_share),
        DEFAULT_FACADE_SOLAR_SHARE,
    )
    _set_or_strip(
        CONF_THERMAL_BRIDGE_PSI_L,
        None if thermal_bridge_psi_l is None else float(thermal_bridge_psi_l),
        DEFAULT_THERMAL_BRIDGE_PSI_L,
    )
    _set_or_strip(
        CONF_SKY_RADIATIVE_UA,
        None if sky_radiative_ua is None else float(sky_radiative_ua),
        DEFAULT_SKY_RADIATIVE_UA,
    )
    _set_or_strip(
        CONF_SOLAR_EXPOSURE,
        solar_exposure,
        DEFAULT_SOLAR_EXPOSURE,
    )
    _set_or_strip(
        CONF_SOLAR_FACING,
        None if solar_facing is None else float(solar_facing),
        DEFAULT_SOLAR_FACING,
    )


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
        comfort_offset: Optional[float] = None,
        window_sensors: Optional[List[str]] = None,
        floor_type: Optional[str] = None,
        facade_colour: Optional[str] = None,
        facade_solar_share: Optional[float] = None,
        thermal_bridge_psi_l: Optional[float] = None,
        sky_radiative_ua: Optional[float] = None,
        solar_exposure: Optional[str] = None,
        solar_facing: Optional[float] = None,
    ) -> Optional[str]:
        """Append a new room.  Returns an error key on duplicate, else ``None``.

        ``infiltration_fraction`` is the Sherman–Grimsrud share of
        ``1/r_external`` that the Phase 1 C1 overlay attributes to
        wind-driven air exchange.  When ``None`` the default for the
        configured envelope-tightness preset is used.

        ``comfort_offset`` is the symmetric ±offset from the setpoint
        defining the comfort region [setpoint - offset, setpoint + offset].
        When ``None``, defaults to DEFAULT_COMFORT_OFFSET.

        ``floor_type``, ``facade_colour``, ``facade_solar_share``,
        ``thermal_bridge_psi_l`` and ``sky_radiative_ua`` are optional
        envelope refinements; they are written into the room dict only when
        the caller passes a non-default value, so existing rooms see no
        change in storage shape when these aren't touched.
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
        if comfort_offset is not None:
            new_room[CONF_COMFORT_OFFSET] = float(comfort_offset)
        else:
            new_room[CONF_COMFORT_OFFSET] = float(DEFAULT_COMFORT_OFFSET)
        if sensors:
            new_room[CONF_TEMP_SENSOR] = sensors[0]
        if window_sensors is not None:
            new_room[CONF_WINDOW_SENSORS] = list(window_sensors)
        _apply_advanced_envelope(
            new_room,
            floor_type=floor_type,
            facade_colour=facade_colour,
            facade_solar_share=facade_solar_share,
            thermal_bridge_psi_l=thermal_bridge_psi_l,
            sky_radiative_ua=sky_radiative_ua,
            solar_exposure=solar_exposure,
            solar_facing=solar_facing,
        )
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
        comfort_offset: Optional[float] = None,
        window_sensors: Optional[List[str]] = None,
        floor_type: Optional[str] = None,
        facade_colour: Optional[str] = None,
        facade_solar_share: Optional[float] = None,
        thermal_bridge_psi_l: Optional[float] = None,
        sky_radiative_ua: Optional[float] = None,
        solar_exposure: Optional[str] = None,
        solar_facing: Optional[float] = None,
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
        if window_sensors is not None:
            room[CONF_WINDOW_SENSORS] = list(window_sensors)
        if comfort_offset is not None:
            room[CONF_COMFORT_OFFSET] = float(comfort_offset)
        else:
            room[CONF_COMFORT_OFFSET] = room.get(CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET)
        if infiltration_fraction is not None:
            room[CONF_INFILTRATION_FRACTION] = float(infiltration_fraction)
        elif CONF_INFILTRATION_FRACTION in room:
            # No explicit override on this edit → leave whatever was there
            # alone (keeps a previously customised value intact).
            pass
        _apply_advanced_envelope(
            room,
            floor_type=floor_type,
            facade_colour=facade_colour,
            facade_solar_share=facade_solar_share,
            thermal_bridge_psi_l=thermal_bridge_psi_l,
            sky_radiative_ua=sky_radiative_ua,
            solar_exposure=solar_exposure,
            solar_facing=solar_facing,
        )
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

    def update(self, idx: int, *, area: float, compass: str, tilt: float) -> bool:
        """Update the window at ``idx``.  Returns ``False`` if out of range."""
        if not (0 <= idx < len(self.windows)):
            return False
        self.windows[idx] = {
            CONF_WINDOW_AREA: area,
            CONF_WINDOW_ORIENTATION: COMPASS_TO_DEGREES[compass],
            CONF_WINDOW_TILT: tilt,
        }
        return True

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


# ---------------------------------------------------------------------------
# Heater CRUD helper
# ---------------------------------------------------------------------------


def heater_display(room_name: str, idx: int, heater: Dict[str, Any]) -> str:
    """Human-readable heater label for the remove-heater selector."""
    name = heater.get(CONF_SOURCE_NAME, f"Heater {idx + 1}")
    source_type = heater.get(CONF_SOURCE_TYPE, "Unknown")
    source_type_label = "Heat Pump" if source_type == SOURCE_TYPE_HEAT_PUMP else "Electric Heater"
    return f"{room_name} · {name} ({source_type_label})"


class HeaterFlowHelper:
    """Mutate the heat sources list of a room or globally.

    Can operate on room-level heat sources (when initialized with a room dict)
    or global heat sources (when initialized with heat_sources list directly).
    """

    def __init__(self, room_or_sources: Dict[str, Any] | List[Dict[str, Any]] | None = None) -> None:
        if room_or_sources is None:
            self._room = None
            self._heat_sources_list = []
        elif isinstance(room_or_sources, dict) and CONF_HEAT_SOURCES not in room_or_sources:
            # It's a room dict
            self._room = room_or_sources
            self._heat_sources_list = self._room.setdefault(CONF_HEAT_SOURCES, [])
        elif isinstance(room_or_sources, list):
            # It's a heat sources list
            self._room = None
            self._heat_sources_list = room_or_sources
        else:
            # It's a dict with CONF_HEAT_SOURCES (unlikely but handle it)
            self._room = None
            self._heat_sources_list = room_or_sources.get(CONF_HEAT_SOURCES, [])

    @property
    def heat_sources(self) -> List[Dict[str, Any]]:
        return self._heat_sources_list

    def __bool__(self) -> bool:
        return bool(self._heat_sources_list)

    def __len__(self) -> int:
        return len(self._heat_sources_list)

    def add(
        self,
        *,
        name: str,
        source_type: str,
        max_power: float,
        heater_entity: str,
        efficiency: float | None = None,
        cop_rated: float | None = None,
        cop_temp_ref: float | None = None,
        min_power: float | None = None,
        max_temp_offset: float | None = None,
        hvac_mode: str | None = None,
        emitter_time_constant: float | None = None,
        cooling_cop: float | None = None,
        cooling_efficiency: float | None = None,
        heating_efficiency: float | None = None,
    ) -> None:
        """Append a new heater to the room."""
        new_heater: Dict[str, Any] = {
            CONF_SOURCE_NAME: name.strip(),
            CONF_SOURCE_TYPE: source_type,
            CONF_SOURCE_MAX_POWER: float(max_power),
            CONF_SOURCE_HEATER_ENTITY: heater_entity,
        }
        if efficiency is not None:
            new_heater[CONF_SOURCE_EFFICIENCY] = float(efficiency)
        if cop_rated is not None:
            new_heater[CONF_SOURCE_COP_RATED] = float(cop_rated)
        if cop_temp_ref is not None:
            new_heater[CONF_SOURCE_COP_TEMP_REF] = float(cop_temp_ref)
        if min_power is not None:
            new_heater[CONF_SOURCE_MIN_POWER] = float(min_power)
        if max_temp_offset is not None:
            new_heater[CONF_SOURCE_MAX_TEMP_OFFSET] = float(max_temp_offset)
        if hvac_mode is not None:
            new_heater[CONF_SOURCE_HVAC_MODE] = str(hvac_mode)
        if emitter_time_constant is not None:
            new_heater[CONF_SOURCE_EMITTER_TIME_CONSTANT] = float(emitter_time_constant)
        if cooling_cop is not None:
            new_heater[CONF_SOURCE_COOLING_COP] = float(cooling_cop)
        if cooling_efficiency is not None:
            new_heater[CONF_SOURCE_COOLING_EFFICIENCY] = float(cooling_efficiency)
        if heating_efficiency is not None:
            new_heater[CONF_SOURCE_HEATING_EFFICIENCY] = float(heating_efficiency)
        self._heat_sources_list.append(new_heater)

    def update(
        self,
        idx: int,
        *,
        name: str,
        source_type: str,
        max_power: float,
        heater_entity: str,
        efficiency: float | None = None,
        cop_rated: float | None = None,
        cop_temp_ref: float | None = None,
        min_power: float | None = None,
        max_temp_offset: float | None = None,
        hvac_mode: str | None = None,
        emitter_time_constant: float | None = None,
        cooling_cop: float | None = None,
        cooling_efficiency: float | None = None,
        heating_efficiency: float | None = None,
    ) -> bool:
        """Update heater at idx. Returns False if out of range."""
        if not (0 <= idx < len(self._heat_sources_list)):
            return False
        heater = self._heat_sources_list[idx]
        heater[CONF_SOURCE_NAME] = name.strip()
        heater[CONF_SOURCE_TYPE] = source_type
        heater[CONF_SOURCE_MAX_POWER] = float(max_power)
        heater[CONF_SOURCE_HEATER_ENTITY] = heater_entity
        if efficiency is not None:
            heater[CONF_SOURCE_EFFICIENCY] = float(efficiency)
        if cop_rated is not None:
            heater[CONF_SOURCE_COP_RATED] = float(cop_rated)
        if cop_temp_ref is not None:
            heater[CONF_SOURCE_COP_TEMP_REF] = float(cop_temp_ref)
        if min_power is not None:
            heater[CONF_SOURCE_MIN_POWER] = float(min_power)
        if max_temp_offset is not None:
            heater[CONF_SOURCE_MAX_TEMP_OFFSET] = float(max_temp_offset)
        if hvac_mode is not None:
            heater[CONF_SOURCE_HVAC_MODE] = str(hvac_mode)
        if emitter_time_constant is not None:
            heater[CONF_SOURCE_EMITTER_TIME_CONSTANT] = float(emitter_time_constant)
        if cooling_cop is not None:
            heater[CONF_SOURCE_COOLING_COP] = float(cooling_cop)
        if cooling_efficiency is not None:
            heater[CONF_SOURCE_COOLING_EFFICIENCY] = float(cooling_efficiency)
        if heating_efficiency is not None:
            heater[CONF_SOURCE_HEATING_EFFICIENCY] = float(heating_efficiency)
        return True

    def remove(self, idx: int) -> bool:
        """Remove the heater at idx. Returns False if out of range."""
        if 0 <= idx < len(self._heat_sources_list):
            self._heat_sources_list.pop(idx)
            return True
        return False

    def display_options(
        self,
        room_name: str,
        labeller: Callable[[str, int, Dict[str, Any]], str] = heater_display,
    ) -> Dict[str, str]:
        """Return ``{idx_str: human_label}`` for the remove-heater selector."""
        return {str(i): labeller(room_name, i, h) for i, h in enumerate(self._heat_sources_list)}


# ---------------------------------------------------------------------------
# Schedule CRUD helper
# ---------------------------------------------------------------------------

_DAY_ABBR_TO_LABEL: Dict[str, str] = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}


class ScheduleFlowHelper:
    """Mutate the ``CONF_SCHEDULE`` list of a single, already-selected room.

    The room dict is held by reference so changes propagate to whoever owns it
    (typically a :class:`RoomFlowHelper`).
    """

    def __init__(self, room: Dict[str, Any]) -> None:
        self._room = room
        self._room.setdefault(CONF_SCHEDULE, [])

    @property
    def periods(self) -> List[Dict[str, Any]]:
        return self._room[CONF_SCHEDULE]

    def __bool__(self) -> bool:
        return bool(self.periods)

    def __len__(self) -> int:
        return len(self.periods)

    def period_display(self, period: Dict[str, Any]) -> str:
        """Return a compact period summary with control-impacting overrides."""
        name = period.get(CONF_SCHEDULE_NAME, "?")
        start = period.get(CONF_SCHEDULE_START, "?")
        end = period.get(CONF_SCHEDULE_END, "?")
        mode = period.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_COMFORT)
        days = period.get(CONF_SCHEDULE_DAYS)
        if days:
            day_labels = ", ".join(
                _DAY_ABBR_TO_LABEL.get(d, d) for d in days
            )
            day_str = f", {day_labels}"
        else:
            day_str = ""

        details: List[str] = []
        if mode == SCHEDULE_MODE_OFF:
            frost = period.get(CONF_SCHEDULE_FROST_PROTECTION)
            if frost is not None:
                details.append(f"frost {float(frost):g}°C")
        else:
            setpoint = period.get(CONF_SCHEDULE_SETPOINT)
            if setpoint is not None:
                details.append(f"sp {float(setpoint):g}°C")
            comfort_offset = period.get(CONF_SCHEDULE_COMFORT_OFFSET)
            if comfort_offset is not None:
                details.append(f"±{float(comfort_offset):g}°C")
            tracking_weight = period.get(CONF_SCHEDULE_TRACKING_WEIGHT)
            if tracking_weight is not None:
                details.append(f"Q×{float(tracking_weight):g}")
            energy_weight = period.get(CONF_SCHEDULE_ENERGY_WEIGHT)
            if energy_weight is not None:
                details.append(f"R×{float(energy_weight):g}")

        detail_str = f"; {', '.join(details)}" if details else ""
        return f"{name}: {start}–{end} ({mode}{day_str}{detail_str})"

    def display_options(self) -> Dict[str, str]:
        """Return ``{idx_str: human_label}`` for period selection lists."""
        return {str(i): self.period_display(p) for i, p in enumerate(self.periods)}

    def add(self, period_data: Dict[str, Any]) -> None:
        """Append a new period to the schedule."""
        self.periods.append(dict(period_data))

    def update(self, idx: int, period_data: Dict[str, Any]) -> bool:
        """Replace the period at ``idx``.  Returns False if out of range."""
        if 0 <= idx < len(self.periods):
            self.periods[idx] = dict(period_data)
            return True
        return False

    def remove(self, idx: int) -> bool:
        """Remove the period at ``idx``.  Returns False if out of range."""
        if 0 <= idx < len(self.periods):
            self.periods.pop(idx)
            return True
        return False
