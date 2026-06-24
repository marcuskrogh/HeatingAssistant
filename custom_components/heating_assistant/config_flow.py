"""Config flow for the Heating Assistant integration.

Initial setup and reconfigure are limited to the site location
(latitude/longitude). All other configuration — rooms, sensors, heat sources,
schedules — is managed from the Heating Assistant panel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional

import voluptuous as vol

from homeassistant import config_entries
if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .schedule import parse_time as _parse_time
from .const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
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
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


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


def _number_box(
    *,
    min_value: float,
    max_value: float,
    step: "float | str" = 1.0,
    unit: Optional[str] = None,
) -> NumberSelector:
    # ``step`` may be the literal "any" to allow arbitrary precision. HA's
    # NumberSelector rejects numeric steps below 1e-3, so latitude/longitude
    # (which need ~1e-6 precision) must use "any" rather than a tiny float —
    # otherwise building the schema raises and the config flow fails to load
    # with a "400: Bad Request".
    kwargs: Dict[str, Any] = {
        "min": min_value,
        "max": max_value,
        "step": step,
        "mode": NumberSelectorMode.BOX,
    }
    if unit is not None:
        kwargs["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**kwargs))


# ---------------------------------------------------------------------------
# Location schema (initial setup and reconfigure)
# ---------------------------------------------------------------------------
#
# The integration config is intentionally limited to the site location. Every
# other setting (sensors, timing, rooms, heat sources, schedules) is managed
# from the Heating Assistant panel / options flow, so both the add and the
# reconfigure entry-points show this single location-only form.


def _location_schema(
    *,
    latitude_default: float,
    longitude_default: float,
) -> vol.Schema:
    """Schema for the location-only config and reconfigure steps."""
    return vol.Schema(
        {
            vol.Required(CONF_LATITUDE, default=float(latitude_default)): _number_box(
                min_value=-90.0, max_value=90.0, step="any", unit="°",
            ),
            vol.Required(CONF_LONGITUDE, default=float(longitude_default)): _number_box(
                min_value=-180.0, max_value=180.0, step="any", unit="°",
            ),
        }
    )


def _initial_entry_data(user_input: Dict[str, Any]) -> Dict[str, Any]:
    """Build persisted config-entry data from the location-only user step."""
    return {
        CONF_LATITUDE: float(user_input[CONF_LATITUDE]),
        CONF_LONGITUDE: float(user_input[CONF_LONGITUDE]),
        CONF_UPDATE_INTERVAL: int(DEFAULT_UPDATE_INTERVAL),
    }


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
            self._data.update(_initial_entry_data(user_input))
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
        """Allow the site location to be revisited after the initial setup."""
        entry = self._get_reconfigure_entry()
        existing = dict(entry.data) if entry is not None else {}

        if user_input is not None:
            new_data = {
                **existing,
                CONF_LATITUDE: float(user_input[CONF_LATITUDE]),
                CONF_LONGITUDE: float(user_input[CONF_LONGITUDE]),
            }
            return self.async_update_reload_and_abort(
                entry,
                data=new_data,
                reason="reconfigure_successful",
            )

        ha_lat = self.hass.config.latitude if self.hass is not None else 0.0
        ha_lon = self.hass.config.longitude if self.hass is not None else 0.0

        schema = _location_schema(
            latitude_default=float(existing.get(CONF_LATITUDE, ha_lat)),
            longitude_default=float(existing.get(CONF_LONGITUDE, ha_lon)),
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
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
