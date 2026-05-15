"""Config flow for the Heating Assistant integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_MPC_ANALYTIC_DERIVATIVES,
    CONF_MPC_SOLVER,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_R_EXTERNAL,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_HEAT_SOURCES,
    CONF_SETPOINT,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_THERMAL_MASS,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    DEFAULT_HORIZON,
    DEFAULT_MPC_ANALYTIC_DERIVATIVES,
    DEFAULT_MPC_SOLVER,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_THERMAL_MASS,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compass helpers (UI only — YAML accepts raw degrees)
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

DEFAULT_ROOM_SETPOINT = 22.0


def _degrees_to_compass(degrees: float) -> str:
    """Return the nearest compass label for an orientation in degrees."""
    # Find closest entry in the 8-point compass (step = 45°)
    normalized = degrees % 360.0
    best = min(_DEGREES_TO_COMPASS, key=lambda d: abs((d - normalized + 180) % 360 - 180))
    return _DEGREES_TO_COMPASS[best]


def _window_display(room_name: str, idx: int, window: Dict[str, Any]) -> str:
    """Human-readable window label for the remove-window selector."""
    area = window.get(CONF_WINDOW_AREA, "?")
    compass = _degrees_to_compass(float(window.get(CONF_WINDOW_ORIENTATION, 0)))
    tilt = window.get(CONF_WINDOW_TILT, DEFAULT_WINDOW_TILT)
    return f"{room_name} · Window {idx + 1}: {area} m² facing {compass} (tilt {tilt}°)"


def _parse_entity_ids(raw_value: str) -> List[str]:
    """Parse comma-separated entity IDs from a text field."""
    if not raw_value or not raw_value.strip():
        return []
    return [entity.strip() for entity in raw_value.split(",") if entity.strip()]


def _format_entity_ids(entity_ids: List[str]) -> str:
    """Format entity IDs as a comma-separated string for UI defaults."""
    return ", ".join(entity_ids)


def _nearest_choice(value: float, mapping: Dict[str, float], default_key: str) -> str:
    """Return the nearest mapping key for a numeric value."""
    if not mapping:
        return default_key
    return min(mapping, key=lambda key: abs(mapping[key] - value))


def _normalize_solver(value: Any) -> str:
    """Normalize solver value for persisted options."""
    if value is None:
        return DEFAULT_MPC_SOLVER
    solver = str(value).lower()
    if solver == "cyipopt":
        return "ipopt"
    if solver in {"slsqp", "ipopt"}:
        return solver
    return DEFAULT_MPC_SOLVER


# ---------------------------------------------------------------------------
# Initial config flow
# ---------------------------------------------------------------------------


class HeatingAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """
        Step 1: Basic site settings (location, outdoor sensor, time step).

        Room and heat-source configuration is provided via YAML in
        ``configuration.yaml`` (see README for the schema), or can be
        configured after setup via the integration's options flow.
        """
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=NAME, data=self._data)

        # Pre-fill with HA's configured location
        ha_lat = self.hass.config.latitude
        ha_lon = self.hass.config.longitude

        schema = vol.Schema(
            {
                vol.Required(CONF_LATITUDE, default=ha_lat): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=ha_lon): vol.Coerce(float),
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY, default=""): str,
                vol.Optional(CONF_WEATHER_ENTITY, default=""): str,
                vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_HORIZON, default=DEFAULT_HORIZON): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=100)
                ),
                vol.Optional(
                    CONF_MPC_ANALYTIC_DERIVATIVES,
                    default=DEFAULT_MPC_ANALYTIC_DERIVATIVES,
                ): bool,
                vol.Optional(CONF_SIGMA_W, default=DEFAULT_SIGMA_W): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6, max=10.0)
                ),
                vol.Optional(CONF_SIGMA_V, default=DEFAULT_SIGMA_V): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-6, max=10.0)
                ),
                vol.Optional(CONF_SIGMA_B, default=DEFAULT_SIGMA_B): vol.All(
                    vol.Coerce(float), vol.Range(min=1e-8, max=1.0)
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "HeatingAssistantOptionsFlow":
        return HeatingAssistantOptionsFlow()


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class HeatingAssistantOptionsFlow(config_entries.OptionsFlow):
    """Multi-step options flow: global settings + room/window management."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._rooms: List[Dict[str, Any]] = []
        self._current_room_idx: Optional[int] = None
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Main options menu — initialises state on first call only."""
        if not self._initialized:
            current = self.config_entry.options or self.config_entry.data
            self._data = dict(current)
            self._data[CONF_MPC_SOLVER] = _normalize_solver(
                self._data.get(CONF_MPC_SOLVER)
            )
            # Rooms may live in options (UI-configured) or data (YAML/initial).
            opts = self.config_entry.options
            data = self.config_entry.data
            rooms_source = opts.get(CONF_ROOMS) or data.get(CONF_ROOMS) or []
            self._rooms = [dict(r) for r in rooms_source]
            self._initialized = True

        return self.async_show_menu(
            step_id="init",
            menu_options=["global_settings", "manage_rooms", "save"],
        )

    # ------------------------------------------------------------------
    # Global settings
    # ------------------------------------------------------------------

    async def async_step_global_settings(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form for site / timing / solver / noise settings."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_init()

        current = self._data
        solver_default = _normalize_solver(current.get(CONF_MPC_SOLVER))
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_OUTDOOR_TEMP_ENTITY,
                    default=current.get(CONF_OUTDOOR_TEMP_ENTITY, ""),
                ): str,
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    default=current.get(CONF_WEATHER_ENTITY, ""),
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                vol.Optional(
                    CONF_HORIZON,
                    default=current.get(CONF_HORIZON, DEFAULT_HORIZON),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                vol.Optional(
                    CONF_MPC_SOLVER,
                    default=solver_default,
                ): vol.In(["slsqp", "ipopt"]),
                vol.Optional(
                    CONF_MPC_ANALYTIC_DERIVATIVES,
                    default=current.get(
                        CONF_MPC_ANALYTIC_DERIVATIVES,
                        DEFAULT_MPC_ANALYTIC_DERIVATIVES,
                    ),
                ): bool,
                vol.Optional(
                    CONF_SIGMA_W,
                    default=current.get(CONF_SIGMA_W, DEFAULT_SIGMA_W),
                ): vol.All(vol.Coerce(float), vol.Range(min=1e-6, max=10.0)),
                vol.Optional(
                    CONF_SIGMA_V,
                    default=current.get(CONF_SIGMA_V, DEFAULT_SIGMA_V),
                ): vol.All(vol.Coerce(float), vol.Range(min=1e-6, max=10.0)),
                vol.Optional(
                    CONF_SIGMA_B,
                    default=current.get(CONF_SIGMA_B, DEFAULT_SIGMA_B),
                ): vol.All(vol.Coerce(float), vol.Range(min=1e-8, max=1.0)),
            }
        )

        return self.async_show_form(step_id="global_settings", data_schema=schema)

    # ------------------------------------------------------------------
    # Save & close
    # ------------------------------------------------------------------

    async def async_step_save(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Persist all accumulated changes and close the options flow."""
        self._data[CONF_MPC_SOLVER] = _normalize_solver(
            self._data.get(CONF_MPC_SOLVER)
        )
        self._data[CONF_ROOMS] = self._rooms
        return self.async_create_entry(title="", data=self._data)

    # ------------------------------------------------------------------
    # Room management
    # ------------------------------------------------------------------

    async def async_step_manage_rooms(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Room management menu."""
        menu_options: List[str] = ["add_room"]
        if self._rooms:
            menu_options += ["edit_room", "manage_room_windows", "remove_room"]
        menu_options.append("finish_rooms")
        return self.async_show_menu(step_id="manage_rooms", menu_options=menu_options)

    async def async_step_finish_rooms(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Return to the main menu from the rooms sub-flow."""
        return await self.async_step_init()

    async def async_step_add_room(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form to add a new room."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            room_name = user_input[CONF_ROOM_NAME].strip()
            if any(r[CONF_ROOM_NAME].lower() == room_name.lower() for r in self._rooms):
                errors[CONF_ROOM_NAME] = "duplicate_room"
            else:
                selected_sensors = _parse_entity_ids(
                    user_input.get(CONF_TEMP_SENSORS, "")
                )
                new_room: Dict[str, Any] = {
                    CONF_ROOM_NAME: room_name,
                    CONF_TEMP_SENSORS: selected_sensors,
                    CONF_THERMAL_MASS: ROOM_SIZE_TO_THERMAL_MASS[user_input["room_size"]],
                    CONF_R_EXTERNAL: BUILDING_AGE_TO_R_EXTERNAL[user_input["building_age"]],
                    CONF_SETPOINT: DEFAULT_ROOM_SETPOINT,
                    CONF_WINDOWS: [],
                }
                if selected_sensors:
                    new_room[CONF_TEMP_SENSOR] = selected_sensors[0]
                self._rooms.append(new_room)
                return await self.async_step_manage_rooms()

        schema = vol.Schema(
            {
                vol.Required(CONF_ROOM_NAME, default=""): str,
                vol.Optional(CONF_TEMP_SENSORS, default=""): str,
                vol.Required("room_size", default="medium"): vol.In(
                    list(ROOM_SIZE_TO_THERMAL_MASS)
                ),
                vol.Required("building_age", default="1980_1999"): vol.In(
                    list(BUILDING_AGE_TO_R_EXTERNAL)
                ),
            }
        )

        return self.async_show_form(
            step_id="add_room", data_schema=schema, errors=errors
        )

    async def async_step_edit_room(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select which room to edit."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            room_name = user_input["room_name"]
            self._current_room_idx = next(
                (i for i, r in enumerate(self._rooms) if r[CONF_ROOM_NAME] == room_name),
                None,
            )
            if self._current_room_idx is not None:
                return await self.async_step_room_detail()
            return await self.async_step_manage_rooms()

        room_names = [r[CONF_ROOM_NAME] for r in self._rooms]
        schema = vol.Schema(
            {vol.Required("room_name", default=room_names[0]): vol.In(room_names)}
        )
        return self.async_show_form(step_id="edit_room", data_schema=schema)

    async def async_step_room_detail(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Edit the fields of the currently-selected room."""
        room = self._rooms[self._current_room_idx]  # type: ignore[index]

        if user_input is not None:
            new_name = user_input[CONF_ROOM_NAME].strip()
            # Duplicate check: exclude the current room from the comparison
            if any(
                i != self._current_room_idx
                and r[CONF_ROOM_NAME].lower() == new_name.lower()
                for i, r in enumerate(self._rooms)
            ):
                return self.async_show_form(
                    step_id="room_detail",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_ROOM_NAME, default=user_input.get(CONF_ROOM_NAME, "")
                            ): str,
                            vol.Optional(
                                CONF_TEMP_SENSORS,
                                default=user_input.get(CONF_TEMP_SENSORS, ""),
                            ): str,
                            vol.Required(
                                "room_size", default=user_input.get("room_size", "medium")
                            ): vol.In(list(ROOM_SIZE_TO_THERMAL_MASS)),
                            vol.Required(
                                "building_age",
                                default=user_input.get("building_age", "1980_1999"),
                            ): vol.In(list(BUILDING_AGE_TO_R_EXTERNAL)),
                        }
                    ),
                    errors={CONF_ROOM_NAME: "duplicate_room"},
                )
            selected_sensors = _parse_entity_ids(user_input.get(CONF_TEMP_SENSORS, ""))
            room[CONF_ROOM_NAME] = new_name
            room[CONF_TEMP_SENSORS] = selected_sensors
            if selected_sensors:
                room[CONF_TEMP_SENSOR] = selected_sensors[0]
            else:
                room.pop(CONF_TEMP_SENSOR, None)
            room[CONF_THERMAL_MASS] = ROOM_SIZE_TO_THERMAL_MASS[user_input["room_size"]]
            room[CONF_R_EXTERNAL] = BUILDING_AGE_TO_R_EXTERNAL[user_input["building_age"]]
            room[CONF_SETPOINT] = DEFAULT_ROOM_SETPOINT
            return await self.async_step_manage_rooms()

        room_sensors = room.get(CONF_TEMP_SENSORS, [])
        if not room_sensors and room.get(CONF_TEMP_SENSOR):
            room_sensors = [room[CONF_TEMP_SENSOR]]
        room_size_default = _nearest_choice(
            float(room.get(CONF_THERMAL_MASS, DEFAULT_THERMAL_MASS)),
            ROOM_SIZE_TO_THERMAL_MASS,
            "medium",
        )
        building_age_default = _nearest_choice(
            float(room.get(CONF_R_EXTERNAL, DEFAULT_R_EXTERNAL)),
            BUILDING_AGE_TO_R_EXTERNAL,
            "1980_1999",
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ROOM_NAME, default=room.get(CONF_ROOM_NAME, "")
                ): str,
                vol.Optional(
                    CONF_TEMP_SENSORS, default=_format_entity_ids(room_sensors)
                ): str,
                vol.Required("room_size", default=room_size_default): vol.In(
                    list(ROOM_SIZE_TO_THERMAL_MASS)
                ),
                vol.Required("building_age", default=building_age_default): vol.In(
                    list(BUILDING_AGE_TO_R_EXTERNAL)
                ),
            }
        )

        return self.async_show_form(step_id="room_detail", data_schema=schema)

    async def async_step_remove_room(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select and remove a room."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            room_name = user_input["room_name"]
            self._rooms = [r for r in self._rooms if r[CONF_ROOM_NAME] != room_name]
            return await self.async_step_manage_rooms()

        room_names = [r[CONF_ROOM_NAME] for r in self._rooms]
        schema = vol.Schema(
            {vol.Required("room_name", default=room_names[0]): vol.In(room_names)}
        )
        return self.async_show_form(step_id="remove_room", data_schema=schema)

    async def async_step_manage_room_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select room before entering the windows sub-flow."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            room_name = user_input["room_name"]
            self._current_room_idx = next(
                (i for i, r in enumerate(self._rooms) if r[CONF_ROOM_NAME] == room_name),
                None,
            )
            if self._current_room_idx is not None:
                return await self.async_step_manage_windows()
            return await self.async_step_manage_rooms()

        room_names = [r[CONF_ROOM_NAME] for r in self._rooms]
        schema = vol.Schema(
            {vol.Required("room_name", default=room_names[0]): vol.In(room_names)}
        )
        return self.async_show_form(step_id="manage_room_windows", data_schema=schema)

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    async def async_step_manage_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Window management menu for the currently-selected room."""
        if self._current_room_idx is None:
            return await self.async_step_manage_room_windows()
        room = self._rooms[self._current_room_idx]  # type: ignore[index]
        windows = room.get(CONF_WINDOWS, [])
        menu_options: List[str] = ["add_window"]
        if windows:
            menu_options.append("remove_window")
        menu_options.append("finish_windows")
        return self.async_show_menu(
            step_id="manage_windows", menu_options=menu_options
        )

    async def async_step_add_window(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form to add a window to the current room."""
        if user_input is not None:
            compass = user_input[CONF_WINDOW_ORIENTATION]
            window: Dict[str, Any] = {
                CONF_WINDOW_AREA: user_input[CONF_WINDOW_AREA],
                CONF_WINDOW_ORIENTATION: COMPASS_TO_DEGREES[compass],
                CONF_WINDOW_TILT: user_input[CONF_WINDOW_TILT],
            }
            self._rooms[self._current_room_idx].setdefault(  # type: ignore[index]
                CONF_WINDOWS, []
            ).append(window)
            return await self.async_step_manage_windows()

        schema = vol.Schema(
            {
                vol.Required(CONF_WINDOW_AREA): vol.All(
                    vol.Coerce(float), vol.Range(min=0.01, max=50.0)
                ),
                vol.Required(
                    CONF_WINDOW_ORIENTATION, default="S"
                ): vol.In(list(COMPASS_TO_DEGREES)),
                vol.Optional(
                    CONF_WINDOW_TILT, default=DEFAULT_WINDOW_TILT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=90.0)),
            }
        )

        return self.async_show_form(step_id="add_window", data_schema=schema)

    async def async_step_remove_window(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select and remove a window from the current room."""
        room = self._rooms[self._current_room_idx]  # type: ignore[index]
        windows = room.get(CONF_WINDOWS, [])

        if not windows:
            return await self.async_step_manage_windows()

        if user_input is not None:
            idx = int(user_input["window_idx"])
            windows.pop(idx)
            return await self.async_step_manage_windows()

        window_options = {
            str(i): _window_display(room.get(CONF_ROOM_NAME, "Room"), i, w)
            for i, w in enumerate(windows)
        }
        schema = vol.Schema(
            {vol.Required("window_idx", default="0"): vol.In(window_options)}
        )
        return self.async_show_form(step_id="remove_window", data_schema=schema)

    async def async_step_finish_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Return to the rooms menu from the windows sub-flow."""
        return await self.async_step_manage_rooms()
