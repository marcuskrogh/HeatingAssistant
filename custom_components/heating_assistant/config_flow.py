"""Config flow for the Heating Assistant integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig
import homeassistant.helpers.config_validation as cv

from ._options_flow import (
    BUILDING_AGE_TO_R_EXTERNAL,
    COMPASS_TO_DEGREES,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    ROOM_SIZE_TO_THERMAL_MASS,
    RoomFlowHelper,
    WindowFlowHelper,
    HeaterFlowHelper,
    degrees_to_compass as _degrees_to_compass,
    nearest_choice as _nearest_choice,
    window_display as _window_display,
    heater_display as _heater_display,
)
from .const import (
    CONF_COMFORT_OFFSET,
    CONF_ENERGY_WEIGHT,
    CONF_HORIZON,
    CONF_TRACKING_WEIGHT,
    CONF_LATITUDE,
    CONF_INFILTRATION_FRACTION,
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
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_WINDOW_SENSORS,
    CONF_THERMAL_MASS,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    CONF_SOURCE_NAME,
    CONF_SOURCE_TYPE,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_TURN_OFF_DEADBAND,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_ENVELOPE_TIGHTNESS,
    DEFAULT_TRACKING_WEIGHT,
    DEFAULT_HORIZON,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    DEFAULT_WINDOW_OPEN_DEBOUNCE,
    DEFAULT_WINDOW_OPEN_Q_INFLATION,
    DEFAULT_THERMAL_MASS,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DEFAULT_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_MIN_POWER,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_TURN_OFF_DEADBAND,
    DOMAIN,
    NAME,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_ROOM_SETPOINT = 22.0


def _coerce_to_list(value: Any) -> list:
    """Ensure a value is a list of entity ID strings.

    Handles three cases that arise from stored data:
    - Already a list  → returned as-is.
    - Comma-separated string → split into a list.
    - None / empty    → empty list.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [e.strip() for e in value.split(",") if e.strip()]
    return []


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
        """Step 1: Basic site settings (location, outdoor sensor, time step)."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=NAME, data=self._data)

        ha_lat = self.hass.config.latitude
        ha_lon = self.hass.config.longitude

        schema = vol.Schema(
            {
                vol.Required(CONF_LATITUDE, default=ha_lat): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=ha_lon): vol.Coerce(float),
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="weather")
                ),
                vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
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
# Schema builders
# ---------------------------------------------------------------------------


def _room_form_schema(
    *,
    name_default: str = "",
    sensors_default: Any = None,
    window_sensors_default: Any = None,
    room_size_default: str = "medium",
    building_age_default: str = "1980_1999",
    envelope_tightness_default: str = DEFAULT_ENVELOPE_TIGHTNESS,
    comfort_offset_default: float = DEFAULT_COMFORT_OFFSET,
) -> vol.Schema:
    """Schema shared by the add-room and edit-room forms."""
    # EntitySelector(multiple=True) requires a list as the default value.
    sensors_list = _coerce_to_list(sensors_default)
    window_sensors_list = _coerce_to_list(window_sensors_default)

    return vol.Schema(
        {
            vol.Required(CONF_ROOM_NAME, default=name_default): str,
            vol.Optional(CONF_TEMP_SENSORS, default=sensors_list): EntitySelector(
                EntitySelectorConfig(domain="sensor", multiple=True)
            ),
            vol.Optional(CONF_WINDOW_SENSORS, default=window_sensors_list): EntitySelector(
                EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required("room_size", default=room_size_default): vol.In(
                list(ROOM_SIZE_TO_THERMAL_MASS)
            ),
            vol.Required("building_age", default=building_age_default): vol.In(
                list(BUILDING_AGE_TO_R_EXTERNAL)
            ),
            vol.Required(
                "envelope_tightness", default=envelope_tightness_default,
            ): vol.In(list(ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION)),
            vol.Required(
                CONF_COMFORT_OFFSET, default=comfort_offset_default,
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10.0)),
        }
    )


def _window_form_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WINDOW_AREA): vol.All(
                vol.Coerce(float), vol.Range(min=0.01, max=50.0)
            ),
            vol.Required(CONF_WINDOW_ORIENTATION, default="S"): vol.In(
                list(COMPASS_TO_DEGREES)
            ),
            vol.Optional(CONF_WINDOW_TILT, default=DEFAULT_WINDOW_TILT): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=90.0)
            ),
        }
    )


def _heater_form_schema(
    *,
    name_default: str = "",
    type_default: str = SOURCE_TYPE_HEAT_PUMP,
    max_power_default: float = 5000.0,
    heater_entity_default: str = "",
    efficiency_default: float = DEFAULT_EFFICIENCY,
    cop_rated_default: float = DEFAULT_COP_RATED,
    cop_temp_ref_default: float = DEFAULT_COP_TEMP_REF,
    min_power_default: float = DEFAULT_MIN_POWER,
    max_temp_offset_default: float = DEFAULT_MAX_TEMP_OFFSET,
    turn_off_deadband_default: float = 0.0,
    emitter_time_constant_default: float = 60.0,
) -> vol.Schema:
    """Schema for adding/editing a heat source (heater)."""
    return vol.Schema(
        {
            vol.Required(CONF_SOURCE_NAME, default=name_default): str,
            vol.Required(CONF_SOURCE_TYPE, default=type_default): vol.In(
                [SOURCE_TYPE_ELECTRIC, SOURCE_TYPE_HEAT_PUMP]
            ),
            vol.Required(
                CONF_SOURCE_MAX_POWER, default=max_power_default
            ): vol.All(vol.Coerce(float), vol.Range(min=100.0, max=100000.0)),
            vol.Required(CONF_SOURCE_HEATER_ENTITY, default=heater_entity_default): EntitySelector(
                EntitySelectorConfig(domain="switch")
            ),
            vol.Optional(
                CONF_SOURCE_EFFICIENCY, default=efficiency_default
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1.0)),
            vol.Optional(
                CONF_SOURCE_COP_RATED, default=cop_rated_default
            ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=10.0)),
            vol.Optional(
                CONF_SOURCE_COP_TEMP_REF, default=cop_temp_ref_default
            ): vol.All(vol.Coerce(float), vol.Range(min=-20.0, max=20.0)),
            vol.Optional(
                CONF_SOURCE_MIN_POWER, default=min_power_default
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100000.0)),
            vol.Optional(
                CONF_SOURCE_MAX_TEMP_OFFSET, default=max_temp_offset_default
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=20.0)),
            vol.Optional(
                CONF_SOURCE_TURN_OFF_DEADBAND, default=turn_off_deadband_default
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=10.0)),
            vol.Optional(
                CONF_SOURCE_EMITTER_TIME_CONSTANT, default=emitter_time_constant_default
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=600.0)),
        }
    )


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class HeatingAssistantOptionsFlow(config_entries.OptionsFlow):
    """Multi-step options flow: global settings + room/window management."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._rooms: RoomFlowHelper = RoomFlowHelper()
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
            opts = self.config_entry.options
            data = self.config_entry.data
            rooms_source = opts.get(CONF_ROOMS) or data.get(CONF_ROOMS) or []
            self._rooms = RoomFlowHelper(rooms_source)
            self._initialized = True

        return self.async_show_menu(
            step_id="init",
            menu_options=["global_settings", "mpc_tuning", "manage_rooms", "save"],
        )

    # ------------------------------------------------------------------
    # Global settings
    # ------------------------------------------------------------------

    async def async_step_global_settings(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form for site / timing / sensor settings."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_init()

        current = self._data

        # For single-entity optional selectors we use description.suggested_value
        # to pre-fill the picker without setting an empty string as the default
        # (EntitySelector rejects "" as an invalid entity ID).
        outdoor_temp = current.get(CONF_OUTDOOR_TEMP_ENTITY) or None
        weather = current.get(CONF_WEATHER_ENTITY) or None
        outdoor_temp = current.get(CONF_OUTDOOR_TEMP_ENTITY) or None

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_OUTDOOR_TEMP_ENTITY,
                    description={"suggested_value": outdoor_temp},
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    description={"suggested_value": weather},
                ): EntitySelector(EntitySelectorConfig(domain="weather")),
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
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
                vol.Optional(
                    CONF_WINDOW_OPEN_DEBOUNCE,
                    default=current.get(
                        CONF_WINDOW_OPEN_DEBOUNCE, DEFAULT_WINDOW_OPEN_DEBOUNCE,
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_WINDOW_OPEN_CLOSE_SETTLE,
                    default=current.get(
                        CONF_WINDOW_OPEN_CLOSE_SETTLE,
                        DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_WINDOW_OPEN_Q_INFLATION,
                    default=current.get(
                        CONF_WINDOW_OPEN_Q_INFLATION,
                        DEFAULT_WINDOW_OPEN_Q_INFLATION,
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=1000.0)),
            }
        )

        return self.async_show_form(step_id="global_settings", data_schema=schema)

    # ------------------------------------------------------------------
    # MPC tuning
    # ------------------------------------------------------------------

    async def async_step_mpc_tuning(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form for MPC controller and model tuning parameters."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_init()

        current = self._data
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HORIZON,
                    default=current.get(CONF_HORIZON, DEFAULT_HORIZON),
                ): vol.All(vol.Coerce(int), vol.Range(min=1)),
                vol.Optional(
                    CONF_TRACKING_WEIGHT,
                    default=current.get(CONF_TRACKING_WEIGHT, DEFAULT_TRACKING_WEIGHT),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_ENERGY_WEIGHT,
                    default=current.get(CONF_ENERGY_WEIGHT, DEFAULT_ENERGY_WEIGHT),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_SMOOTHING_WEIGHT,
                    default=current.get(CONF_SMOOTHING_WEIGHT, DEFAULT_SMOOTHING_WEIGHT),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_SOFT_CONSTRAINT_WEIGHT,
                    default=current.get(
                        CONF_SOFT_CONSTRAINT_WEIGHT, DEFAULT_SOFT_CONSTRAINT_WEIGHT
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_TERMINAL_WEIGHT,
                    default=current.get(CONF_TERMINAL_WEIGHT, DEFAULT_TERMINAL_WEIGHT),
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=10000.0)),
            }
        )

        return self.async_show_form(step_id="mpc_tuning", data_schema=schema)

    # ------------------------------------------------------------------
    # Save & close
    # ------------------------------------------------------------------

    async def async_step_save(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Persist all accumulated changes and close the options flow."""
        self._data[CONF_ROOMS] = self._rooms.rooms
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
            menu_options += ["edit_room", "manage_room_windows", "manage_room_heaters", "remove_room"]
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
            # EntitySelector(multiple=True) already returns a list.
            sensors = _coerce_to_list(user_input.get(CONF_TEMP_SENSORS, []))
            window_sensors = _coerce_to_list(user_input.get(CONF_WINDOW_SENSORS, []))
            tightness = user_input.get("envelope_tightness", DEFAULT_ENVELOPE_TIGHTNESS)
            err = self._rooms.add(
                name=user_input[CONF_ROOM_NAME],
                sensors=sensors,
                thermal_mass=ROOM_SIZE_TO_THERMAL_MASS[user_input["room_size"]],
                r_external=BUILDING_AGE_TO_R_EXTERNAL[user_input["building_age"]],
                setpoint=DEFAULT_ROOM_SETPOINT,
                infiltration_fraction=(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[tightness]
                ),
                comfort_offset=user_input[CONF_COMFORT_OFFSET],
                window_sensors=window_sensors,
            )
            if err is None:
                return await self.async_step_manage_rooms()
            errors[CONF_ROOM_NAME] = err

        return self.async_show_form(
            step_id="add_room",
            data_schema=_room_form_schema(),
            errors=errors,
        )

    async def async_step_edit_room(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select which room to edit."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            if self._rooms.select(user_input["room_name"]):
                return await self.async_step_room_detail()
            return await self.async_step_manage_rooms()

        names = self._rooms.names()
        schema = vol.Schema(
            {vol.Required("room_name", default=names[0]): vol.In(names)}
        )
        return self.async_show_form(step_id="edit_room", data_schema=schema)

    async def async_step_room_detail(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Edit the fields of the currently-selected room."""
        room = self._rooms.current_room()
        if room is None:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            sensors = _coerce_to_list(user_input.get(CONF_TEMP_SENSORS, []))
            window_sensors = _coerce_to_list(user_input.get(CONF_WINDOW_SENSORS, []))
            tightness = user_input.get("envelope_tightness", DEFAULT_ENVELOPE_TIGHTNESS)
            err = self._rooms.update_current(
                name=user_input[CONF_ROOM_NAME],
                sensors=sensors,
                thermal_mass=ROOM_SIZE_TO_THERMAL_MASS[user_input["room_size"]],
                r_external=BUILDING_AGE_TO_R_EXTERNAL[user_input["building_age"]],
                setpoint=DEFAULT_ROOM_SETPOINT,
                infiltration_fraction=(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[tightness]
                ),
                comfort_offset=user_input[CONF_COMFORT_OFFSET],
                window_sensors=window_sensors,
            )
            if err is None:
                return await self.async_step_manage_rooms()
            # Re-show the form with the values the user just entered.
            return self.async_show_form(
                step_id="room_detail",
                data_schema=_room_form_schema(
                    name_default=user_input.get(CONF_ROOM_NAME, ""),
                    sensors_default=sensors,
                    window_sensors_default=window_sensors,
                    room_size_default=user_input.get("room_size", "medium"),
                    building_age_default=user_input.get("building_age", "1980_1999"),
                    envelope_tightness_default=tightness,
                    comfort_offset_default=user_input.get(
                        CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET,
                    ),
                ),
                errors={CONF_ROOM_NAME: err},
            )

        # Pre-fill with values already stored for this room.
        room_sensors = _coerce_to_list(
            room.get(CONF_TEMP_SENSORS) or room.get(CONF_TEMP_SENSOR, [])
        )
        room_window_sensors = _coerce_to_list(room.get(CONF_WINDOW_SENSORS, []))
        infiltration_fraction = float(room.get(
            CONF_INFILTRATION_FRACTION,
            ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[DEFAULT_ENVELOPE_TIGHTNESS],
        ))
        schema = _room_form_schema(
            name_default=room.get(CONF_ROOM_NAME, ""),
            sensors_default=room_sensors,
            window_sensors_default=room_window_sensors,
            room_size_default=_nearest_choice(
                float(room.get(CONF_THERMAL_MASS, DEFAULT_THERMAL_MASS)),
                ROOM_SIZE_TO_THERMAL_MASS,
                "medium",
            ),
            building_age_default=_nearest_choice(
                float(room.get(CONF_R_EXTERNAL, DEFAULT_R_EXTERNAL)),
                BUILDING_AGE_TO_R_EXTERNAL,
                "1980_1999",
            ),
            envelope_tightness_default=_nearest_choice(
                infiltration_fraction,
                ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
                DEFAULT_ENVELOPE_TIGHTNESS,
            ),
            comfort_offset_default=float(
                room.get(CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET)
            ),
        )
        return self.async_show_form(step_id="room_detail", data_schema=schema)

    async def async_step_remove_room(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select and remove a room."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            self._rooms.remove_by_name(user_input["room_name"])
            return await self.async_step_manage_rooms()

        names = self._rooms.names()
        schema = vol.Schema(
            {vol.Required("room_name", default=names[0]): vol.In(names)}
        )
        return self.async_show_form(step_id="remove_room", data_schema=schema)

    async def async_step_manage_room_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select room before entering the windows sub-flow."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            if self._rooms.select(user_input["room_name"]):
                return await self.async_step_manage_windows()
            return await self.async_step_manage_rooms()

        names = self._rooms.names()
        schema = vol.Schema(
            {vol.Required("room_name", default=names[0]): vol.In(names)}
        )
        return self.async_show_form(step_id="manage_room_windows", data_schema=schema)

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _current_windows(self) -> Optional[WindowFlowHelper]:
        room = self._rooms.current_room()
        return WindowFlowHelper(room) if room is not None else None

    async def async_step_manage_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Window management menu for the currently-selected room."""
        windows = self._current_windows()
        if windows is None:
            return await self.async_step_manage_room_windows()
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
        windows = self._current_windows()
        if windows is None:
            return await self.async_step_manage_room_windows()

        if user_input is not None:
            windows.add_from_compass(
                area=user_input[CONF_WINDOW_AREA],
                compass=user_input[CONF_WINDOW_ORIENTATION],
                tilt=user_input[CONF_WINDOW_TILT],
            )
            return await self.async_step_manage_windows()

        return self.async_show_form(
            step_id="add_window", data_schema=_window_form_schema()
        )

    async def async_step_remove_window(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select and remove a window from the current room."""
        windows = self._current_windows()
        if windows is None or not windows:
            return await self.async_step_manage_windows()

        if user_input is not None:
            windows.remove(int(user_input["window_idx"]))
            return await self.async_step_manage_windows()

        room = self._rooms.current_room() or {}
        window_options = windows.display_options(
            room.get(CONF_ROOM_NAME, "Room")
        )
        schema = vol.Schema(
            {vol.Required("window_idx", default="0"): vol.In(window_options)}
        )
        return self.async_show_form(step_id="remove_window", data_schema=schema)

    async def async_step_finish_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Return to the rooms menu from the windows sub-flow."""
        return await self.async_step_manage_rooms()

    # ------------------------------------------------------------------
    # Heater management
    # ------------------------------------------------------------------

    def _current_heaters(self) -> Optional[HeaterFlowHelper]:
        room = self._rooms.current_room()
        return HeaterFlowHelper(room) if room is not None else None

    async def async_step_manage_room_heaters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select room before entering the heaters sub-flow."""
        if not self._rooms:
            return await self.async_step_manage_rooms()

        if user_input is not None:
            if self._rooms.select(user_input["room_name"]):
                return await self.async_step_manage_heaters()
            return await self.async_step_manage_rooms()

        names = self._rooms.names()
        schema = vol.Schema(
            {vol.Required("room_name", default=names[0]): vol.In(names)}
        )
        return self.async_show_form(step_id="manage_room_heaters", data_schema=schema)

    async def async_step_manage_heaters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Heater management menu for the currently-selected room."""
        heaters = self._current_heaters()
        if heaters is None:
            return await self.async_step_manage_room_heaters()
        menu_options: List[str] = ["add_heater"]
        if heaters:
            menu_options += ["edit_heater", "remove_heater"]
        menu_options.append("finish_heaters")
        return self.async_show_menu(
            step_id="manage_heaters", menu_options=menu_options
        )

    async def async_step_add_heater(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form to add a heater to the current room."""
        heaters = self._current_heaters()
        if heaters is None:
            return await self.async_step_manage_room_heaters()

        if user_input is not None:
            heaters.add(
                name=user_input[CONF_SOURCE_NAME],
                source_type=user_input[CONF_SOURCE_TYPE],
                max_power=user_input[CONF_SOURCE_MAX_POWER],
                heater_entity=user_input[CONF_SOURCE_HEATER_ENTITY],
                efficiency=user_input.get(CONF_SOURCE_EFFICIENCY),
                cop_rated=user_input.get(CONF_SOURCE_COP_RATED),
                cop_temp_ref=user_input.get(CONF_SOURCE_COP_TEMP_REF),
                min_power=user_input.get(CONF_SOURCE_MIN_POWER),
                max_temp_offset=user_input.get(CONF_SOURCE_MAX_TEMP_OFFSET),
                turn_off_deadband=user_input.get(CONF_SOURCE_TURN_OFF_DEADBAND),
                emitter_time_constant=user_input.get(CONF_SOURCE_EMITTER_TIME_CONSTANT),
            )
            return await self.async_step_manage_heaters()

        return self.async_show_form(
            step_id="add_heater", data_schema=_heater_form_schema()
        )

    async def async_step_edit_heater(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select which heater to edit."""
        heaters = self._current_heaters()
        if heaters is None or not heaters:
            return await self.async_step_manage_heaters()

        if user_input is not None:
            heater_idx = int(user_input["heater_idx"])
            room = self._rooms.current_room()
            if room is not None:
                heater = heaters.heat_sources[heater_idx] if 0 <= heater_idx < len(heaters.heat_sources) else None
                if heater is None:
                    return await self.async_step_manage_heaters()
                return await self.async_step_heater_detail(heater_idx=heater_idx)
            return await self.async_step_manage_heaters()

        room = self._rooms.current_room() or {}
        heater_options = heaters.display_options(
            room.get(CONF_ROOM_NAME, "Room")
        )
        schema = vol.Schema(
            {vol.Required("heater_idx", default="0"): vol.In(heater_options)}
        )
        return self.async_show_form(step_id="edit_heater", data_schema=schema)

    async def async_step_heater_detail(
        self, user_input: Optional[Dict[str, Any]] = None, heater_idx: Optional[int] = None
    ) -> ConfigFlowResult:
        """Edit a heater's configuration."""
        heaters = self._current_heaters()
        if heaters is None:
            return await self.async_step_manage_room_heaters()

        if heater_idx is None:
            return await self.async_step_edit_heater()

        heater = heaters.heat_sources[heater_idx] if 0 <= heater_idx < len(heaters.heat_sources) else None
        if heater is None:
            return await self.async_step_manage_heaters()

        if user_input is not None:
            heaters.update(
                heater_idx,
                name=user_input[CONF_SOURCE_NAME],
                source_type=user_input[CONF_SOURCE_TYPE],
                max_power=user_input[CONF_SOURCE_MAX_POWER],
                heater_entity=user_input[CONF_SOURCE_HEATER_ENTITY],
                efficiency=user_input.get(CONF_SOURCE_EFFICIENCY),
                cop_rated=user_input.get(CONF_SOURCE_COP_RATED),
                cop_temp_ref=user_input.get(CONF_SOURCE_COP_TEMP_REF),
                min_power=user_input.get(CONF_SOURCE_MIN_POWER),
                max_temp_offset=user_input.get(CONF_SOURCE_MAX_TEMP_OFFSET),
                turn_off_deadband=user_input.get(CONF_SOURCE_TURN_OFF_DEADBAND),
                emitter_time_constant=user_input.get(CONF_SOURCE_EMITTER_TIME_CONSTANT),
            )
            return await self.async_step_manage_heaters()

        schema = _heater_form_schema(
            name_default=heater.get(CONF_SOURCE_NAME, ""),
            type_default=heater.get(CONF_SOURCE_TYPE, SOURCE_TYPE_HEAT_PUMP),
            max_power_default=float(heater.get(CONF_SOURCE_MAX_POWER, 5000.0)),
            heater_entity_default=heater.get(CONF_SOURCE_HEATER_ENTITY, ""),
            efficiency_default=float(heater.get(CONF_SOURCE_EFFICIENCY, DEFAULT_EFFICIENCY)),
            cop_rated_default=float(heater.get(CONF_SOURCE_COP_RATED, DEFAULT_COP_RATED)),
            cop_temp_ref_default=float(heater.get(CONF_SOURCE_COP_TEMP_REF, DEFAULT_COP_TEMP_REF)),
            min_power_default=float(heater.get(CONF_SOURCE_MIN_POWER, DEFAULT_MIN_POWER)),
            max_temp_offset_default=float(heater.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET)),
            turn_off_deadband_default=float(heater.get(CONF_SOURCE_TURN_OFF_DEADBAND, 0.0)),
            emitter_time_constant_default=float(heater.get(CONF_SOURCE_EMITTER_TIME_CONSTANT, 60.0)),
        )
        return self.async_show_form(step_id="heater_detail", data_schema=schema)

    async def async_step_remove_heater(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select and remove a heater from the current room."""
        heaters = self._current_heaters()
        if heaters is None or not heaters:
            return await self.async_step_manage_heaters()

        if user_input is not None:
            heaters.remove(int(user_input["heater_idx"]))
            return await self.async_step_manage_heaters()

        room = self._rooms.current_room() or {}
        heater_options = heaters.display_options(
            room.get(CONF_ROOM_NAME, "Room")
        )
        schema = vol.Schema(
            {vol.Required("heater_idx", default="0"): vol.In(heater_options)}
        )
        return self.async_show_form(step_id="remove_heater", data_schema=schema)

    async def async_step_finish_heaters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Return to the rooms menu from the heaters sub-flow."""
        return await self.async_step_manage_rooms()
