"""Config flow for the Heating Assistant integration.

The flow is organised so the user-facing forms feel like other modern Home
Assistant integrations:

* Initial setup is a single location step (latitude/longitude). All other
  configuration happens in the Heating Assistant panel.
* Site settings can be revisited via the **Reconfigure** entry-point without
  diving into the options flow.
* All numeric fields use ``NumberSelector`` (with sliders where it helps),
  schedule times use ``TimeSelector``, and discrete choices use
  ``SelectSelector`` with dropdown / list modes.
* Room, heater and schedule editors group "advanced" fields into collapsed
  sections so the basics stay visible.

The persisted data shape is **unchanged** — sectioned input is flattened before
it is handed off to the storage helpers in ``_options_flow``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
    TimeSelectorConfig,
)
import homeassistant.helpers.config_validation as cv

try:
    from homeassistant.data_entry_flow import section as _ha_section
except ImportError:  # pragma: no cover — older HA fallback
    _ha_section = None  # type: ignore[assignment]

from .schedule import parse_time as _parse_time
from ._options_flow import (
    BUILDING_AGE_TO_R_EXTERNAL,
    COMPASS_TO_DEGREES,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    FACADE_COLOUR_OPTIONS,
    FLOOR_TYPE_OPTIONS,
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
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
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
    CONF_SKY_RADIATIVE_UA,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_PRICE_ENTITY,
    CONF_PRICE_NET_TARIFF,
    CONF_PRICE_SPOT_SURCHARGE,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_WINDOW_SENSORS,
    CONF_THERMAL_MASS,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    SOLAR_EXPOSURE_NONE,
    SOLAR_EXPOSURE_LOW,
    SOLAR_EXPOSURE_MEDIUM,
    SOLAR_EXPOSURE_HIGH,
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
    CONF_SOURCE_HVAC_MODE,
    DEFAULT_SOURCE_HVAC_MODE,
    SOURCE_HVAC_MODE_HEAT,
    SOURCE_HVAC_MODE_COOL,
    SOURCE_HVAC_MODE_HEAT_COOL,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_HEATING_EFFICIENCY,
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
    DEFAULT_ENERGY_PRICE_WEIGHT,
    DEFAULT_PRICE_NET_TARIFF,
    DEFAULT_PRICE_SPOT_SURCHARGE,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_ENVELOPE_TIGHTNESS,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_TRACKING_WEIGHT,
    DEFAULT_HORIZON,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
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

# Section identifiers used in both schemas and strings.json. Centralised so
# the flattening helper stays in sync with the schema builders.
SECTION_LOCATION = "location"
SECTION_SENSORS = "sensors"
SECTION_TIMING = "timing"
SECTION_ADV_ENVELOPE = "advanced_envelope"
SECTION_PERFORMANCE = "performance"

# Default heat-pump emitter time constant exposed in the heater form. Mirrors
# the existing UI behaviour (heat_pump → 60 s typical lag).
_DEFAULT_EMITTER_TIME_CONSTANT = 60.0


def _section(schema: vol.Schema, *, collapsed: bool = True) -> Any:
    """Wrap ``schema`` in a ``data_entry_flow.section`` when supported.

    On older Home Assistant versions where sections are unavailable, the
    inner schema is returned directly — fields will appear inline. Either
    way, ``_flatten_sections`` produces the same flat user_input shape
    downstream, so the storage layer never sees the difference.
    """
    if _ha_section is None:
        return schema
    return _ha_section(schema, {"collapsed": collapsed})


def _flatten_sections(
    user_input: Optional[Dict[str, Any]],
    section_keys: Iterable[str],
) -> Dict[str, Any]:
    """Lift section sub-dicts into the top-level dict.

    HA's ``data_entry_flow.section`` returns the section's data nested under
    the section key, e.g. ``{"sensors": {"outdoor_temp_entity": ...}}``.
    Every downstream consumer in this module wants a flat dict, so this
    helper merges them up. Top-level keys win on collision.
    """
    if not user_input:
        return {}
    result: Dict[str, Any] = {}
    for key, value in user_input.items():
        if key in section_keys and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                result.setdefault(sub_key, sub_val)
        else:
            result.setdefault(key, value)
    return result


def _section_defaults(current: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Return a dict suitable for ``section(..., {"default": ...})`` initial values."""
    return {k: current[k] for k in keys if k in current}


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


def _is_valid_time_string(value: Any) -> bool:
    """Return True when ``value`` parses as HH:MM(/:SS), False otherwise."""
    if not isinstance(value, str):
        return False
    try:
        _parse_time(value)
    except (TypeError, ValueError):
        return False
    return True


def _normalise_time_string(value: str) -> str:
    """Trim a HH:MM:SS string back to HH:MM for compact storage.

    ``TimeSelector`` returns ``HH:MM:SS``; the controller expects ``HH:MM``
    and the existing storage shape uses the shorter form. Validation
    happens before this is ever called, so the cast is safe.
    """
    if isinstance(value, str) and value.count(":") == 2:
        return ":".join(value.split(":")[:2])
    return value


def _build_period_dict(
    user_input: Dict[str, Any],
    room_setpoint: float,
    room_comfort_offset: float,
) -> Dict[str, Any]:
    """Build a period dict from form input, omitting optional fields that match defaults."""
    period: Dict[str, Any] = {
        CONF_SCHEDULE_NAME: user_input[CONF_SCHEDULE_NAME],
        CONF_SCHEDULE_MODE: user_input[CONF_SCHEDULE_MODE],
        CONF_SCHEDULE_START: _normalise_time_string(user_input[CONF_SCHEDULE_START]),
        CONF_SCHEDULE_END: _normalise_time_string(user_input[CONF_SCHEDULE_END]),
        CONF_SCHEDULE_FROST_PROTECTION: float(
            user_input.get(CONF_SCHEDULE_FROST_PROTECTION, 12.0)
        ),
    }
    # Optional days — omit if empty (all days)
    days = user_input.get(CONF_SCHEDULE_DAYS) or []
    if days:
        period[CONF_SCHEDULE_DAYS] = list(days)

    # Optional setpoint — omit if equal to room setpoint
    setpoint = user_input.get(CONF_SCHEDULE_SETPOINT)
    if setpoint is not None and float(setpoint) != room_setpoint:
        period[CONF_SCHEDULE_SETPOINT] = float(setpoint)

    # Optional comfort_offset — omit if equal to room comfort_offset
    comfort_offset = user_input.get(CONF_SCHEDULE_COMFORT_OFFSET)
    if comfort_offset is not None and float(comfort_offset) != room_comfort_offset:
        period[CONF_SCHEDULE_COMFORT_OFFSET] = float(comfort_offset)

    # Optional tracking_weight — omit if equal to 1.0
    tracking_weight = user_input.get(CONF_SCHEDULE_TRACKING_WEIGHT)
    if tracking_weight is not None and float(tracking_weight) != 1.0:
        period[CONF_SCHEDULE_TRACKING_WEIGHT] = float(tracking_weight)

    # Optional energy_weight — omit if equal to 1.0
    energy_weight = user_input.get(CONF_SCHEDULE_ENERGY_WEIGHT)
    if energy_weight is not None and float(energy_weight) != 1.0:
        period[CONF_SCHEDULE_ENERGY_WEIGHT] = float(energy_weight)

    return period


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------


def _entity_selector_optional(domain: str) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=domain))


def _entity_selector_multi(domain: str) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=domain, multiple=True))


def _dropdown(options: List[str], *, translation_key: Optional[str] = None) -> SelectSelector:
    cfg_kwargs: Dict[str, Any] = {
        "options": options,
        "mode": SelectSelectorMode.DROPDOWN,
    }
    if translation_key is not None:
        cfg_kwargs["translation_key"] = translation_key
    return SelectSelector(SelectSelectorConfig(**cfg_kwargs))


def _degrees_to_compass(degrees: float) -> str:
    """Return the compass label whose degrees are nearest to ``degrees``.

    Inverse of ``COMPASS_TO_DEGREES`` (handling the 0/360 wrap) so a stored
    ``solar_facing`` in degrees pre-fills the compass dropdown correctly.
    """
    best_label = "S"
    best_diff = 360.0
    for label, deg in COMPASS_TO_DEGREES.items():
        diff = abs((float(degrees) - float(deg) + 180.0) % 360.0 - 180.0)
        if diff < best_diff:
            best_diff = diff
            best_label = label
    return best_label


def _radio(options: List[str], *, translation_key: Optional[str] = None) -> SelectSelector:
    cfg_kwargs: Dict[str, Any] = {
        "options": options,
        "mode": SelectSelectorMode.LIST,
    }
    if translation_key is not None:
        cfg_kwargs["translation_key"] = translation_key
    return SelectSelector(SelectSelectorConfig(**cfg_kwargs))


def _number_box(
    *,
    min_value: float,
    max_value: float,
    step: float = 1.0,
    unit: Optional[str] = None,
) -> NumberSelector:
    kwargs: Dict[str, Any] = {
        "min": min_value,
        "max": max_value,
        "step": step,
        "mode": NumberSelectorMode.BOX,
    }
    if unit is not None:
        kwargs["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**kwargs))


def _number_slider(
    *,
    min_value: float,
    max_value: float,
    step: float = 0.1,
    unit: Optional[str] = None,
) -> NumberSelector:
    kwargs: Dict[str, Any] = {
        "min": min_value,
        "max": max_value,
        "step": step,
        "mode": NumberSelectorMode.SLIDER,
    }
    if unit is not None:
        kwargs["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**kwargs))


# ---------------------------------------------------------------------------
# Location schema (initial setup) and site-settings schema (reconfigure)
# ---------------------------------------------------------------------------


_SITE_SECTION_KEYS: Tuple[str, ...] = (
    SECTION_LOCATION,
    SECTION_SENSORS,
    SECTION_TIMING,
)


def _location_schema(
    *,
    latitude_default: float,
    longitude_default: float,
) -> vol.Schema:
    """Schema for the initial add-integration step — location only."""
    return vol.Schema(
        {
            vol.Required(CONF_LATITUDE, default=float(latitude_default)): _number_box(
                min_value=-90.0, max_value=90.0, step=0.000001, unit="°",
            ),
            vol.Required(CONF_LONGITUDE, default=float(longitude_default)): _number_box(
                min_value=-180.0, max_value=180.0, step=0.000001, unit="°",
            ),
        }
    )


def _initial_entry_data(flat: Dict[str, Any]) -> Dict[str, Any]:
    """Build persisted config-entry data from the location-only user step."""
    return {
        CONF_LATITUDE: float(flat[CONF_LATITUDE]),
        CONF_LONGITUDE: float(flat[CONF_LONGITUDE]),
        CONF_UPDATE_INTERVAL: int(DEFAULT_UPDATE_INTERVAL),
    }


def _site_settings_schema(
    *,
    latitude_default: float,
    longitude_default: float,
    outdoor_temp_default: Optional[str],
    weather_default: Optional[str],
    update_interval_default: int,
) -> vol.Schema:
    """Flat schema used by the initial setup and reconfigure flows.

    The visual grouping comes from field ordering and ``data_description``
    helper text rather than ``section()`` wrappers — the latter has
    proved fragile when mixed with top-level fields on some Home
    Assistant versions.
    """
    schema_dict: Dict[Any, Any] = {
        vol.Required(CONF_LATITUDE, default=float(latitude_default)): _number_box(
            min_value=-90.0, max_value=90.0, step=0.000001, unit="°",
        ),
        vol.Required(CONF_LONGITUDE, default=float(longitude_default)): _number_box(
            min_value=-180.0, max_value=180.0, step=0.000001, unit="°",
        ),
    }

    if outdoor_temp_default:
        schema_dict[
            vol.Optional(
                CONF_OUTDOOR_TEMP_ENTITY,
                description={"suggested_value": outdoor_temp_default},
            )
        ] = _entity_selector_optional("sensor")
    else:
        schema_dict[vol.Optional(CONF_OUTDOOR_TEMP_ENTITY)] = _entity_selector_optional("sensor")

    if weather_default:
        schema_dict[
            vol.Optional(
                CONF_WEATHER_ENTITY,
                description={"suggested_value": weather_default},
            )
        ] = _entity_selector_optional("weather")
    else:
        schema_dict[vol.Optional(CONF_WEATHER_ENTITY)] = _entity_selector_optional("weather")

    schema_dict[
        vol.Required(CONF_UPDATE_INTERVAL, default=int(update_interval_default))
    ] = _number_box(min_value=60, max_value=3600, step=30, unit="s")

    return vol.Schema(schema_dict)


def _site_settings_errors(flat: Dict[str, Any]) -> Dict[str, str]:
    """Validate that at least one outdoor source is configured."""
    outdoor = flat.get(CONF_OUTDOOR_TEMP_ENTITY)
    weather = flat.get(CONF_WEATHER_ENTITY)
    if not outdoor and not weather:
        return {"base": "outdoor_or_weather_required"}
    return {}


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
        """Step 1: Location only — rooms, sensors and heat sources are configured in the panel."""
        if user_input is not None:
            flat = _flatten_sections(user_input, _SITE_SECTION_KEYS)
            self._data.update(_initial_entry_data(flat))
            return self.async_create_entry(title=NAME, data=self._data)

        ha_lat = self.hass.config.latitude if self.hass is not None else 0.0
        ha_lon = self.hass.config.longitude if self.hass is not None else 0.0

        schema = _location_schema(
            latitude_default=ha_lat,
            longitude_default=ha_lon,
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )

    async def async_step_reconfigure(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Allow site basics to be revisited after the initial setup."""
        entry = self._get_reconfigure_entry()
        existing = dict(entry.data) if entry is not None else {}
        errors: Dict[str, str] = {}

        if user_input is not None:
            flat = _flatten_sections(user_input, _SITE_SECTION_KEYS)
            errors = _site_settings_errors(flat)
            if not errors:
                new_data = {**existing, **flat}
                return self.async_update_reload_and_abort(
                    entry,
                    data=new_data,
                    reason="reconfigure_successful",
                )
            existing = {**existing, **flat}

        ha_lat = self.hass.config.latitude if self.hass is not None else 0.0
        ha_lon = self.hass.config.longitude if self.hass is not None else 0.0

        schema = _site_settings_schema(
            latitude_default=float(existing.get(CONF_LATITUDE, ha_lat)),
            longitude_default=float(existing.get(CONF_LONGITUDE, ha_lon)),
            outdoor_temp_default=existing.get(CONF_OUTDOOR_TEMP_ENTITY) or None,
            weather_default=existing.get(CONF_WEATHER_ENTITY) or None,
            update_interval_default=int(
                existing.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            ),
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    def _get_reconfigure_entry(self) -> Optional[config_entries.ConfigEntry]:
        """Return the entry being reconfigured (HA 2024.x+ helper).

        Falls back to ``hass.config_entries.async_get_entry(self.context["entry_id"])``
        on older Home Assistant versions that don't expose the convenience
        method.
        """
        getter = getattr(self, "_get_entry", None)
        if callable(getter):
            try:
                return getter()
            except Exception:  # pragma: no cover — defensive
                pass
        entry_id = (self.context or {}).get("entry_id") if hasattr(self, "context") else None
        if entry_id and self.hass is not None:
            return self.hass.config_entries.async_get_entry(entry_id)
        return None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "HeatingAssistantOptionsFlow":
        return HeatingAssistantOptionsFlow()


# ---------------------------------------------------------------------------
# Room / window / heater / schedule schemas
# ---------------------------------------------------------------------------


_ROOM_SECTION_KEYS: Tuple[str, ...] = (SECTION_ADV_ENVELOPE,)


def _room_form_schema(
    *,
    name_default: str = "",
    sensors_default: Any = None,
    window_sensors_default: Any = None,
    room_size_default: str = "medium",
    building_age_default: str = "1980_1999",
    envelope_tightness_default: str = DEFAULT_ENVELOPE_TIGHTNESS,
    comfort_offset_default: float = DEFAULT_COMFORT_OFFSET,
    floor_type_default: str = DEFAULT_FLOOR_TYPE,
    facade_colour_default: str = DEFAULT_FACADE_COLOUR,
    facade_solar_share_default: float = DEFAULT_FACADE_SOLAR_SHARE,
    thermal_bridge_psi_l_default: float = DEFAULT_THERMAL_BRIDGE_PSI_L,
    sky_radiative_ua_default: float = DEFAULT_SKY_RADIATIVE_UA,
    solar_exposure_default: str = DEFAULT_SOLAR_EXPOSURE,
    solar_facing_default: str = "S",
) -> vol.Schema:
    """Schema shared by the add-room and edit-room forms.

    Basic envelope fields are shown at the top level; advanced refinements
    (floor type, facade colour, thermal bridges, sky coupling, solar preset)
    live inside a collapsed section.
    """
    sensors_list = _coerce_to_list(sensors_default)
    window_sensors_list = _coerce_to_list(window_sensors_default)

    advanced_schema = vol.Schema(
        {
            vol.Optional(CONF_FLOOR_TYPE, default=floor_type_default): _dropdown(
                FLOOR_TYPE_OPTIONS, translation_key="floor_type",
            ),
            vol.Optional(CONF_FACADE_COLOUR, default=facade_colour_default): _dropdown(
                FACADE_COLOUR_OPTIONS, translation_key="facade_colour",
            ),
            vol.Optional(
                CONF_FACADE_SOLAR_SHARE, default=float(facade_solar_share_default),
            ): _number_slider(min_value=0.0, max_value=1.0, step=0.05),
            vol.Optional(
                CONF_THERMAL_BRIDGE_PSI_L, default=float(thermal_bridge_psi_l_default),
            ): _number_box(min_value=0.0, max_value=50.0, step=0.1, unit="W/K"),
            vol.Optional(
                CONF_SKY_RADIATIVE_UA, default=float(sky_radiative_ua_default),
            ): _number_box(min_value=0.0, max_value=100.0, step=0.5, unit="W/K"),
            vol.Optional(
                CONF_SOLAR_EXPOSURE, default=solar_exposure_default,
            ): _dropdown(
                [
                    SOLAR_EXPOSURE_NONE,
                    SOLAR_EXPOSURE_LOW,
                    SOLAR_EXPOSURE_MEDIUM,
                    SOLAR_EXPOSURE_HIGH,
                ],
                translation_key="solar_exposure",
            ),
            vol.Optional(
                CONF_SOLAR_FACING, default=solar_facing_default,
            ): _dropdown(
                list(COMPASS_TO_DEGREES), translation_key="orientation",
            ),
        }
    )

    return vol.Schema(
        {
            vol.Required(CONF_ROOM_NAME, default=name_default): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Optional(CONF_TEMP_SENSORS, default=sensors_list): _entity_selector_multi("sensor"),
            vol.Optional(
                CONF_WINDOW_SENSORS, default=window_sensors_list,
            ): _entity_selector_multi("binary_sensor"),
            vol.Required("room_size", default=room_size_default): _dropdown(
                list(ROOM_SIZE_TO_THERMAL_MASS), translation_key="room_size",
            ),
            vol.Required("building_age", default=building_age_default): _dropdown(
                list(BUILDING_AGE_TO_R_EXTERNAL), translation_key="building_age",
            ),
            vol.Required(
                "envelope_tightness", default=envelope_tightness_default,
            ): _dropdown(
                list(ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION),
                translation_key="envelope_tightness",
            ),
            vol.Required(
                CONF_COMFORT_OFFSET, default=float(comfort_offset_default),
            ): _number_slider(min_value=0.1, max_value=5.0, step=0.1, unit="°C"),
            SECTION_ADV_ENVELOPE: _section(advanced_schema, collapsed=True),
        }
    )


def _window_form_schema(
    *,
    area_default: float = 1.0,
    orientation_default: str = "S",
    tilt_default: float = DEFAULT_WINDOW_TILT,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WINDOW_AREA, default=float(area_default)): _number_box(
                min_value=0.01, max_value=50.0, step=0.1, unit="m²",
            ),
            vol.Required(CONF_WINDOW_ORIENTATION, default=orientation_default): _dropdown(
                list(COMPASS_TO_DEGREES), translation_key="orientation",
            ),
            vol.Optional(CONF_WINDOW_TILT, default=float(tilt_default)): _number_slider(
                min_value=0.0, max_value=90.0, step=5.0, unit="°",
            ),
        }
    )


_HEATER_SECTION_KEYS: Tuple[str, ...] = (SECTION_PERFORMANCE,)


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
    hvac_mode_default: str = DEFAULT_SOURCE_HVAC_MODE,
    emitter_time_constant_default: float = _DEFAULT_EMITTER_TIME_CONSTANT,
    heating_efficiency_default: float = DEFAULT_HEATING_EFFICIENCY,
    cooling_cop_default: float = DEFAULT_COOLING_COP,
    cooling_efficiency_default: float = DEFAULT_COOLING_EFFICIENCY,
) -> vol.Schema:
    """Schema for adding/editing a heat source (heater).

    Essential identity and wiring fields are at the top level; performance
    details live inside a collapsed section.
    """
    schema_dict: Dict[Any, Any] = {
        vol.Required(CONF_SOURCE_NAME, default=name_default): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(CONF_SOURCE_TYPE, default=type_default): _radio(
            [SOURCE_TYPE_ELECTRIC, SOURCE_TYPE_HEAT_PUMP],
            translation_key="source_type",
        ),
        vol.Required(
            CONF_SOURCE_MAX_POWER, default=float(max_power_default),
        ): _number_box(min_value=100.0, max_value=100000.0, step=100.0, unit="W"),
    }
    if heater_entity_default:
        schema_dict[
            vol.Required(
                CONF_SOURCE_HEATER_ENTITY,
                description={"suggested_value": heater_entity_default},
            )
        ] = _entity_selector_optional("switch")
    else:
        schema_dict[vol.Required(CONF_SOURCE_HEATER_ENTITY)] = _entity_selector_optional("switch")

    # ── Performance details (collapsed advanced section) ──
    perf_schema = vol.Schema(
        {
            vol.Optional(
                CONF_SOURCE_EFFICIENCY, default=float(efficiency_default),
            ): _number_slider(min_value=0.1, max_value=1.0, step=0.05),
            vol.Optional(
                CONF_SOURCE_COP_RATED, default=float(cop_rated_default),
            ): _number_slider(min_value=1.0, max_value=8.0, step=0.1),
            vol.Optional(
                CONF_SOURCE_COP_TEMP_REF, default=float(cop_temp_ref_default),
            ): _number_box(min_value=-20.0, max_value=20.0, step=0.5, unit="°C"),
            vol.Optional(
                CONF_SOURCE_MIN_POWER, default=float(min_power_default),
            ): _number_box(min_value=0.0, max_value=100000.0, step=10.0, unit="W"),
            vol.Optional(
                CONF_SOURCE_MAX_TEMP_OFFSET, default=float(max_temp_offset_default),
            ): _number_box(min_value=0.1, max_value=20.0, step=0.1, unit="°C"),
            vol.Optional(CONF_SOURCE_HVAC_MODE, default=hvac_mode_default): _dropdown(
                [SOURCE_HVAC_MODE_HEAT, SOURCE_HVAC_MODE_COOL, SOURCE_HVAC_MODE_HEAT_COOL],
                translation_key="hvac_mode",
            ),
            vol.Optional(
                CONF_SOURCE_HEATING_EFFICIENCY, default=float(heating_efficiency_default),
            ): _number_slider(min_value=0.1, max_value=1.0, step=0.05),
            vol.Optional(
                CONF_SOURCE_COOLING_COP, default=float(cooling_cop_default),
            ): _number_slider(min_value=0.0, max_value=8.0, step=0.1),
            vol.Optional(
                CONF_SOURCE_COOLING_EFFICIENCY, default=float(cooling_efficiency_default),
            ): _number_slider(min_value=0.0, max_value=1.0, step=0.05),
            vol.Optional(
                CONF_SOURCE_EMITTER_TIME_CONSTANT,
                default=float(emitter_time_constant_default),
            ): _number_box(min_value=0.0, max_value=600.0, step=5.0, unit="s"),
        }
    )
    schema_dict[SECTION_PERFORMANCE] = _section(perf_schema, collapsed=True)

    return vol.Schema(schema_dict)


_DAY_OPTIONS = [
    {"value": "mon", "label": "Monday"},
    {"value": "tue", "label": "Tuesday"},
    {"value": "wed", "label": "Wednesday"},
    {"value": "thu", "label": "Thursday"},
    {"value": "fri", "label": "Friday"},
    {"value": "sat", "label": "Saturday"},
    {"value": "sun", "label": "Sunday"},
]



# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


_GLOBAL_SECTION_KEYS: Tuple[str, ...] = ()


class HeatingAssistantOptionsFlow(config_entries.OptionsFlow):
    """Multi-step options flow: global settings + room/window management."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._rooms: RoomFlowHelper = RoomFlowHelper()
        self._initialized: bool = False
        self._selected_window_idx: Optional[int] = None
        self._selected_heater_idx: Optional[int] = None

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
            menu_options=["global_settings", "manage_rooms", "manage_room_windows", "manage_room_heaters", "save"],
        )

    # ------------------------------------------------------------------
    # Global settings
    # ------------------------------------------------------------------

    async def async_step_global_settings(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form for site / timing / sensor settings.

        Uses plain voluptuous validators (``vol.All(vol.Coerce, vol.Range)``)
        for the numeric fields rather than ``NumberSelector``. This mirrors
        the original pre-modernisation schema that was known to render
        cleanly across the Home Assistant versions we target; the
        ``NumberSelector`` variant failed to render this specific page
        (likely because of the very small ``min=1e-8`` / ``step=0.0001``
        used for ``sigma_b``). The friendly labels and helper text from
        ``strings.json`` are unaffected by this change.
        """
        if user_input is not None:
            # Tolerate either a flat input dict (current path) or one with
            # legacy section keys (in case an in-flight form is submitted
            # from a partially-cached old version).
            flat = _flatten_sections(user_input, _GLOBAL_SECTION_KEYS)
            self._data.update(flat)
            return await self.async_step_init()

        current = self._data

        # Pre-fill optional single-entity pickers via description.suggested_value
        # (EntitySelector rejects "" as an invalid default).
        outdoor_temp = current.get(CONF_OUTDOOR_TEMP_ENTITY) or None
        weather = current.get(CONF_WEATHER_ENTITY) or None
        solar_radiation = current.get(CONF_SOLAR_RADIATION_ENTITY) or None
        price_entity = current.get(CONF_PRICE_ENTITY) or None

        schema_dict: Dict[Any, Any] = {
            vol.Optional(
                CONF_OUTDOOR_TEMP_ENTITY,
                description={"suggested_value": outdoor_temp},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_WEATHER_ENTITY,
                description={"suggested_value": weather},
            ): EntitySelector(EntitySelectorConfig(domain="weather")),
            # Optional solar-radiation (irradiance) sensor reporting the sun's
            # GHI in W/m² (e.g. Open-Meteo shortwave_radiation, a pyranometer),
            # ideally with an hourly forecast in its attributes.  When set, the
            # forecast supplies the solar model's intensity (decomposed +
            # transposed by geometry), replacing the modelled clear-sky
            # irradiance; the clear-sky model is the automatic fallback.
            vol.Optional(
                CONF_SOLAR_RADIATION_ENTITY,
                description={"suggested_value": solar_radiation},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_PRICE_ENTITY,
                description={"suggested_value": price_entity},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_PRICE_NET_TARIFF,
                default=float(
                    current.get(CONF_PRICE_NET_TARIFF)
                    if current.get(CONF_PRICE_NET_TARIFF) is not None
                    else DEFAULT_PRICE_NET_TARIFF
                ),
            ): _number_box(min_value=0.0, max_value=10.0, step=0.001),
            vol.Optional(
                CONF_PRICE_SPOT_SURCHARGE,
                default=float(
                    current.get(CONF_PRICE_SPOT_SURCHARGE)
                    if current.get(CONF_PRICE_SPOT_SURCHARGE) is not None
                    else DEFAULT_PRICE_SPOT_SURCHARGE
                ),
            ): _number_box(min_value=0.0, max_value=10.0, step=0.001),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=int(
                    current.get(CONF_UPDATE_INTERVAL) or DEFAULT_UPDATE_INTERVAL
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
        }

        return self.async_show_form(
            step_id="global_settings", data_schema=vol.Schema(schema_dict),
        )

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
            menu_options += ["edit_room", "remove_room"]
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
            flat = _flatten_sections(user_input, _ROOM_SECTION_KEYS)
            sensors = _coerce_to_list(flat.get(CONF_TEMP_SENSORS, []))
            window_sensors = _coerce_to_list(flat.get(CONF_WINDOW_SENSORS, []))
            tightness = flat.get("envelope_tightness", DEFAULT_ENVELOPE_TIGHTNESS)
            err = self._rooms.add(
                name=flat[CONF_ROOM_NAME],
                sensors=sensors,
                thermal_mass=ROOM_SIZE_TO_THERMAL_MASS[flat["room_size"]],
                r_external=BUILDING_AGE_TO_R_EXTERNAL[flat["building_age"]],
                setpoint=DEFAULT_ROOM_SETPOINT,
                infiltration_fraction=(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[tightness]
                ),
                comfort_offset=flat[CONF_COMFORT_OFFSET],
                window_sensors=window_sensors,
                floor_type=flat.get(CONF_FLOOR_TYPE),
                facade_colour=flat.get(CONF_FACADE_COLOUR),
                facade_solar_share=flat.get(CONF_FACADE_SOLAR_SHARE),
                thermal_bridge_psi_l=flat.get(CONF_THERMAL_BRIDGE_PSI_L),
                sky_radiative_ua=flat.get(CONF_SKY_RADIATIVE_UA),
                solar_exposure=flat.get(CONF_SOLAR_EXPOSURE),
                solar_facing=COMPASS_TO_DEGREES.get(
                    flat.get(CONF_SOLAR_FACING), DEFAULT_SOLAR_FACING,
                ),
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
            {vol.Required("room_name", default=names[0]): _dropdown(names)}
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
            flat = _flatten_sections(user_input, _ROOM_SECTION_KEYS)
            sensors = _coerce_to_list(flat.get(CONF_TEMP_SENSORS, []))
            window_sensors = _coerce_to_list(flat.get(CONF_WINDOW_SENSORS, []))
            tightness = flat.get("envelope_tightness", DEFAULT_ENVELOPE_TIGHTNESS)
            err = self._rooms.update_current(
                name=flat[CONF_ROOM_NAME],
                sensors=sensors,
                thermal_mass=ROOM_SIZE_TO_THERMAL_MASS[flat["room_size"]],
                r_external=BUILDING_AGE_TO_R_EXTERNAL[flat["building_age"]],
                setpoint=DEFAULT_ROOM_SETPOINT,
                infiltration_fraction=(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[tightness]
                ),
                comfort_offset=flat[CONF_COMFORT_OFFSET],
                window_sensors=window_sensors,
                floor_type=flat.get(CONF_FLOOR_TYPE),
                facade_colour=flat.get(CONF_FACADE_COLOUR),
                facade_solar_share=flat.get(CONF_FACADE_SOLAR_SHARE),
                thermal_bridge_psi_l=flat.get(CONF_THERMAL_BRIDGE_PSI_L),
                sky_radiative_ua=flat.get(CONF_SKY_RADIATIVE_UA),
                solar_exposure=flat.get(CONF_SOLAR_EXPOSURE),
                solar_facing=COMPASS_TO_DEGREES.get(
                    flat.get(CONF_SOLAR_FACING), DEFAULT_SOLAR_FACING,
                ),
            )
            if err is None:
                return await self.async_step_manage_rooms()
            return self.async_show_form(
                step_id="room_detail",
                data_schema=_room_form_schema(
                    name_default=flat.get(CONF_ROOM_NAME, ""),
                    sensors_default=sensors,
                    window_sensors_default=window_sensors,
                    room_size_default=flat.get("room_size", "medium"),
                    building_age_default=flat.get("building_age", "1980_1999"),
                    envelope_tightness_default=tightness,
                    comfort_offset_default=flat.get(
                        CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET,
                    ),
                    floor_type_default=flat.get(CONF_FLOOR_TYPE, DEFAULT_FLOOR_TYPE),
                    facade_colour_default=flat.get(CONF_FACADE_COLOUR, DEFAULT_FACADE_COLOUR),
                    facade_solar_share_default=flat.get(
                        CONF_FACADE_SOLAR_SHARE, DEFAULT_FACADE_SOLAR_SHARE,
                    ),
                    thermal_bridge_psi_l_default=flat.get(
                        CONF_THERMAL_BRIDGE_PSI_L, DEFAULT_THERMAL_BRIDGE_PSI_L,
                    ),
                    sky_radiative_ua_default=flat.get(
                        CONF_SKY_RADIATIVE_UA, DEFAULT_SKY_RADIATIVE_UA,
                    ),
                    solar_exposure_default=flat.get(
                        CONF_SOLAR_EXPOSURE, DEFAULT_SOLAR_EXPOSURE,
                    ),
                    solar_facing_default=flat.get(CONF_SOLAR_FACING, "S"),
                ),
                errors={CONF_ROOM_NAME: err},
                description_placeholders={"name": room.get(CONF_ROOM_NAME, "")},
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
            floor_type_default=room.get(CONF_FLOOR_TYPE, DEFAULT_FLOOR_TYPE),
            facade_colour_default=room.get(CONF_FACADE_COLOUR, DEFAULT_FACADE_COLOUR),
            facade_solar_share_default=float(
                room.get(CONF_FACADE_SOLAR_SHARE, DEFAULT_FACADE_SOLAR_SHARE)
            ),
            thermal_bridge_psi_l_default=float(
                room.get(CONF_THERMAL_BRIDGE_PSI_L, DEFAULT_THERMAL_BRIDGE_PSI_L)
            ),
            sky_radiative_ua_default=float(
                room.get(CONF_SKY_RADIATIVE_UA, DEFAULT_SKY_RADIATIVE_UA)
            ),
            solar_exposure_default=room.get(CONF_SOLAR_EXPOSURE, DEFAULT_SOLAR_EXPOSURE),
            solar_facing_default=_degrees_to_compass(
                room.get(CONF_SOLAR_FACING, DEFAULT_SOLAR_FACING)
            ),
        )
        return self.async_show_form(
            step_id="room_detail",
            data_schema=schema,
            description_placeholders={"name": room.get(CONF_ROOM_NAME, "")},
        )

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
            {vol.Required("room_name", default=names[0]): _dropdown(names)}
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
            {vol.Required("room_name", default=names[0]): _dropdown(names)}
        )
        return self.async_show_form(step_id="manage_room_windows", data_schema=schema)

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _current_windows(self) -> Optional[WindowFlowHelper]:
        room = self._rooms.current_room()
        return WindowFlowHelper(room) if room is not None else None

    def _current_room_name(self) -> str:
        room = self._rooms.current_room() or {}
        return room.get(CONF_ROOM_NAME, "")

    async def async_step_manage_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Window management menu for the currently-selected room."""
        windows = self._current_windows()
        if windows is None:
            return await self.async_step_manage_room_windows()
        menu_options = ["add_window", "edit_window", "remove_window", "finish_windows"]
        return self.async_show_menu(
            step_id="manage_windows",
            menu_options=menu_options,
            description_placeholders={"name": self._current_room_name()},
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
            step_id="add_window",
            data_schema=_window_form_schema(),
            description_placeholders={"name": self._current_room_name()},
        )

    async def async_step_edit_window(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select which window to edit."""
        windows = self._current_windows()
        if windows is None or not windows:
            return await self.async_step_manage_windows()

        if user_input is not None:
            self._selected_window_idx = int(user_input["window_idx"])
            return await self.async_step_window_detail()

        room = self._rooms.current_room() or {}
        window_options = windows.display_options(room.get(CONF_ROOM_NAME, "Room"))
        schema = vol.Schema(
            {vol.Required("window_idx", default="0"): vol.In(window_options)}
        )
        return self.async_show_form(
            step_id="edit_window",
            data_schema=schema,
            description_placeholders={"name": self._current_room_name()},
        )

    async def async_step_window_detail(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Edit a window's configuration."""
        windows = self._current_windows()
        if windows is None:
            return await self.async_step_manage_room_windows()

        idx = self._selected_window_idx
        if idx is None or not (0 <= idx < len(windows.windows)):
            self._selected_window_idx = None
            return await self.async_step_edit_window()

        window = windows.windows[idx]

        if user_input is not None:
            windows.update(
                idx,
                area=user_input[CONF_WINDOW_AREA],
                compass=user_input[CONF_WINDOW_ORIENTATION],
                tilt=user_input[CONF_WINDOW_TILT],
            )
            self._selected_window_idx = None
            return await self.async_step_manage_windows()

        orientation_default = _degrees_to_compass(
            window.get(CONF_WINDOW_ORIENTATION, COMPASS_TO_DEGREES["S"])
        )
        schema = _window_form_schema(
            area_default=float(window.get(CONF_WINDOW_AREA, 1.0)),
            orientation_default=orientation_default,
            tilt_default=float(window.get(CONF_WINDOW_TILT, DEFAULT_WINDOW_TILT)),
        )
        return self.async_show_form(
            step_id="window_detail",
            data_schema=schema,
            description_placeholders={
                "name": f"{orientation_default} · {window.get(CONF_WINDOW_AREA, 1.0)} m²"
            },
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
        return self.async_show_form(
            step_id="remove_window",
            data_schema=schema,
            description_placeholders={"name": self._current_room_name()},
        )

    async def async_step_finish_windows(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Return to the main menu from the windows sub-flow."""
        return await self.async_step_init()

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
            {vol.Required("room_name", default=names[0]): _dropdown(names)}
        )
        return self.async_show_form(step_id="manage_room_heaters", data_schema=schema)

    async def async_step_manage_heaters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Heater management menu for the currently-selected room."""
        heaters = self._current_heaters()
        if heaters is None:
            return await self.async_step_manage_room_heaters()
        menu_options = ["add_heater", "edit_heater", "remove_heater", "finish_heaters"]
        return self.async_show_menu(
            step_id="manage_heaters",
            menu_options=menu_options,
            description_placeholders={"name": self._current_room_name()},
        )

    async def async_step_add_heater(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Form to add a heater to the current room."""
        heaters = self._current_heaters()
        if heaters is None:
            return await self.async_step_manage_room_heaters()

        if user_input is not None:
            flat = _flatten_sections(user_input, _HEATER_SECTION_KEYS)
            heaters.add(
                name=flat[CONF_SOURCE_NAME],
                source_type=flat[CONF_SOURCE_TYPE],
                max_power=flat[CONF_SOURCE_MAX_POWER],
                heater_entity=flat[CONF_SOURCE_HEATER_ENTITY],
                efficiency=flat.get(CONF_SOURCE_EFFICIENCY),
                cop_rated=flat.get(CONF_SOURCE_COP_RATED),
                cop_temp_ref=flat.get(CONF_SOURCE_COP_TEMP_REF),
                min_power=flat.get(CONF_SOURCE_MIN_POWER),
                max_temp_offset=flat.get(CONF_SOURCE_MAX_TEMP_OFFSET),
                hvac_mode=flat.get(CONF_SOURCE_HVAC_MODE),
                emitter_time_constant=flat.get(CONF_SOURCE_EMITTER_TIME_CONSTANT),
                heating_efficiency=flat.get(CONF_SOURCE_HEATING_EFFICIENCY),
                cooling_cop=flat.get(CONF_SOURCE_COOLING_COP),
                cooling_efficiency=flat.get(CONF_SOURCE_COOLING_EFFICIENCY),
            )
            return await self.async_step_manage_heaters()

        return self.async_show_form(
            step_id="add_heater",
            data_schema=_heater_form_schema(),
            description_placeholders={"name": self._current_room_name()},
        )

    async def async_step_edit_heater(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Select which heater to edit."""
        heaters = self._current_heaters()
        if heaters is None or not heaters:
            return await self.async_step_manage_heaters()

        if user_input is not None:
            self._selected_heater_idx = int(user_input["heater_idx"])
            return await self.async_step_heater_detail()

        room = self._rooms.current_room() or {}
        heater_options = heaters.display_options(
            room.get(CONF_ROOM_NAME, "Room")
        )
        schema = vol.Schema(
            {vol.Required("heater_idx", default="0"): vol.In(heater_options)}
        )
        return self.async_show_form(step_id="edit_heater", data_schema=schema)

    async def async_step_heater_detail(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Edit a heater's configuration."""
        heaters = self._current_heaters()
        if heaters is None:
            return await self.async_step_manage_room_heaters()

        idx = self._selected_heater_idx
        if idx is None or not (0 <= idx < len(heaters.heat_sources)):
            self._selected_heater_idx = None
            return await self.async_step_edit_heater()

        heater = heaters.heat_sources[idx]

        if user_input is not None:
            flat = _flatten_sections(user_input, _HEATER_SECTION_KEYS)
            heaters.update(
                idx,
                name=flat[CONF_SOURCE_NAME],
                source_type=flat[CONF_SOURCE_TYPE],
                max_power=flat[CONF_SOURCE_MAX_POWER],
                heater_entity=flat[CONF_SOURCE_HEATER_ENTITY],
                efficiency=flat.get(CONF_SOURCE_EFFICIENCY),
                cop_rated=flat.get(CONF_SOURCE_COP_RATED),
                cop_temp_ref=flat.get(CONF_SOURCE_COP_TEMP_REF),
                min_power=flat.get(CONF_SOURCE_MIN_POWER),
                max_temp_offset=flat.get(CONF_SOURCE_MAX_TEMP_OFFSET),
                hvac_mode=flat.get(CONF_SOURCE_HVAC_MODE),
                emitter_time_constant=flat.get(CONF_SOURCE_EMITTER_TIME_CONSTANT),
                heating_efficiency=flat.get(CONF_SOURCE_HEATING_EFFICIENCY),
                cooling_cop=flat.get(CONF_SOURCE_COOLING_COP),
                cooling_efficiency=flat.get(CONF_SOURCE_COOLING_EFFICIENCY),
            )
            self._selected_heater_idx = None
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
            hvac_mode_default=heater.get(CONF_SOURCE_HVAC_MODE, DEFAULT_SOURCE_HVAC_MODE),
            emitter_time_constant_default=float(
                heater.get(CONF_SOURCE_EMITTER_TIME_CONSTANT, _DEFAULT_EMITTER_TIME_CONSTANT)
            ),
            heating_efficiency_default=float(
                heater.get(CONF_SOURCE_HEATING_EFFICIENCY, DEFAULT_HEATING_EFFICIENCY)
            ),
            cooling_cop_default=float(heater.get(CONF_SOURCE_COOLING_COP, DEFAULT_COOLING_COP)),
            cooling_efficiency_default=float(
                heater.get(CONF_SOURCE_COOLING_EFFICIENCY, DEFAULT_COOLING_EFFICIENCY)
            ),
        )
        return self.async_show_form(
            step_id="heater_detail",
            data_schema=schema,
            description_placeholders={"name": heater.get(CONF_SOURCE_NAME, "")},
        )

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
        """Return to the main menu from the heaters sub-flow."""
        return await self.async_step_init()

