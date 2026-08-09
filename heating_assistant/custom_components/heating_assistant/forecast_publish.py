"""JSON-safe forecast attribute helpers for the thin MQTT bridge (SWD-279)."""

from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Attributes the App needs for MPC disturbance / price forecasts (SWD-278/279).
FORECAST_ATTR_KEYS = (
    "forecast",
    "temperature",
    "cloud_coverage",
    "wind_speed",
    "wind_speed_unit",
    "raw_today",
    "raw_tomorrow",
    "today",
    "tomorrow",
    "prices_today",
    "prices_tomorrow",
    "unit_of_measurement",
)

# Last successful weather.get_forecasts payload per entity_id. Survives a
# transient service failure so App tag attrs are not wiped to scalar-only
# temperature/cloud keys (review-fix SWD-279).
_LAST_WEATHER_FORECAST: dict[str, list[Any]] = {}


def clear_weather_forecast_cache() -> None:
    """Clear the in-memory weather forecast cache (tests)."""

    _LAST_WEATHER_FORECAST.clear()


async def forecast_attributes_for_publish(
    hass: Any, state: Any
) -> dict[str, Any] | None:
    """Return a JSON-safe attribute subset for forecast builders, or None."""

    attrs = getattr(state, "attributes", None) or {}
    selected: dict[str, Any] = {}
    for key in FORECAST_ATTR_KEYS:
        if key in attrs and attrs[key] is not None:
            selected[key] = attrs[key]
    # Solar irradiance integrations often use one of these list keys.
    for key in ("forecast", "forecasts", "data", "entries"):
        if key in attrs and attrs[key] is not None and key not in selected:
            selected[key] = attrs[key]

    domain = getattr(state, "domain", None)
    entity_id = getattr(state, "entity_id", None)
    # Modern HA weather entities no longer expose ``forecast`` on state attrs;
    # call weather.get_forecasts so outdoor/cloud series reach the App (SWD-279).
    if domain == "weather" and entity_id and not selected.get("forecast"):
        entity_key = str(entity_id)
        forecast = await fetch_weather_forecast_entries(hass, entity_key)
        if forecast:
            _LAST_WEATHER_FORECAST[entity_key] = list(forecast)
            selected["forecast"] = forecast
        else:
            cached = _LAST_WEATHER_FORECAST.get(entity_key)
            if cached:
                selected["forecast"] = list(cached)

    if not selected:
        return None
    return json_safe(selected)


async def fetch_weather_forecast_entries(
    hass: Any, entity_id: str
) -> list[Any] | None:
    """Fetch hourly forecast entries via weather.get_forecasts when available."""

    services = getattr(hass, "services", None)
    if services is None:
        return None
    try:
        has_service = services.has_service("weather", "get_forecasts")
    except Exception:  # noqa: BLE001 — defensive against stub hass in tests
        has_service = False
    if not has_service:
        return None
    try:
        response = await services.async_call(
            "weather",
            "get_forecasts",
            service_data={"entity_id": entity_id, "type": "hourly"},
            blocking=True,
            return_response=True,
        )
    except Exception as exc:  # noqa: BLE001 — fall back to attrs-only publish
        _LOGGER.debug(
            "weather.get_forecasts failed for %s: %s", entity_id, exc
        )
        return None
    if not isinstance(response, dict):
        return None
    block = response.get(entity_id)
    if not isinstance(block, dict):
        return None
    forecast = block.get("forecast")
    return forecast if isinstance(forecast, list) and forecast else None


def json_safe(value: Any) -> Any:
    """Recursively convert values so MQTT JSON encoding cannot fail."""

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, set):
        return [json_safe(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return json_safe(value.as_dict())
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)
