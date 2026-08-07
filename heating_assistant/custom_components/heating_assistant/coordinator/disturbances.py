"""Weather, solar, and electricity-price disturbance reading for the coordinator."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Tuple

from ..const import DEFAULT_GROUND_ALBEDO
from .. import electricity_price as _electricity_price
from .. import solar_forecast as _solar_fc
from .. import weather as _weather
from ..solar_model import room_solar_gains, room_solar_gains_from_exposure

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def read_outdoor_temp(coordinator: HeatingAssistantCoordinator) -> Optional[float]:
    return _weather.read_outdoor_temp(
        coordinator.hass, coordinator._outdoor_entity, coordinator._weather_entity
    )


async def async_read_weather_forecast(
    coordinator: HeatingAssistantCoordinator,
) -> Optional[List[float]]:
    """Read outdoor temperature forecast from a weather entity.

    Returns a list of N outdoor temperature values aligned to the MPC
    horizon steps, or None if no weather entity is configured or no
    forecast data is available.
    """
    forecast_data = await async_get_forecast_entries(coordinator)
    if not forecast_data:
        return None
    return _weather.parse_temperature_forecast(forecast_data, coordinator._horizon, coordinator.dt)


async def async_read_cloud_forecast(
    coordinator: HeatingAssistantCoordinator,
    cloud_cover_now: Optional[float] = None,
) -> Optional[List[float]]:
    """Read cloud-cover forecast (fraction in [0, 1]) from the weather entity.

    When ``cloud_cover_now`` is provided it is used as an anchor in the
    interpolation (smooth ramp from the current observation to the first
    forecast entry) and as a persistence fallback when no forecast data
    is available.
    """
    forecast_data = await async_get_forecast_entries(coordinator)
    return _weather.parse_cloud_forecast(
        forecast_data or [], coordinator._horizon, coordinator.dt,
        current_cloud_cover=cloud_cover_now,
    )


async def async_read_wind_forecast(
    coordinator: HeatingAssistantCoordinator,
    wind_speed_now: Optional[float] = None,
) -> Optional[List[float]]:
    """Read the wind-speed forecast [m/s] from the weather entity.

    ``wind_speed_now`` (already in m/s) anchors the interpolation at
    k = 0.  Returns ``None`` when the forecast carries no usable
    ``wind_speed`` field — the controller then holds the current wind
    over the horizon, exactly the previous behaviour.
    """
    forecast_data = await async_get_forecast_entries(coordinator)
    if not forecast_data:
        return None
    unit = None
    if coordinator._weather_entity:
        state = coordinator.hass.states.get(coordinator._weather_entity)
        if state is not None:
            unit = state.attributes.get("wind_speed_unit")
    return _weather.parse_wind_forecast(
        forecast_data, coordinator._horizon, coordinator.dt,
        now=coordinator.now_utc, current_wind=wind_speed_now, unit=unit,
    )


async def async_get_forecast_entries(
    coordinator: HeatingAssistantCoordinator,
) -> Optional[list]:
    """Fetch raw forecast entries from the configured weather entity and
    record success / failure on the coordinator for the diagnostic
    ``WeatherForecastStatusSensor``.
    """
    forecast_data, error = await _weather.fetch_forecast_entries(
        coordinator.hass, coordinator._weather_entity
    )
    if not coordinator._weather_entity:
        # Not configured — neither success nor failure.
        return None
    if forecast_data is not None:
        record_weather_success(coordinator)
        return forecast_data
    record_weather_failure(coordinator, error or "unknown error")
    return None


async def async_read_price_forecast(
    coordinator: HeatingAssistantCoordinator,
    now: datetime,
) -> Optional[List[float]]:
    """Read the electricity price forecast from the configured price entity.

    Delegates to :mod:`electricity_price`, which merges known day-ahead
    prices (``raw_today``/``raw_tomorrow``, ``today``/``tomorrow``) with
    the optional extended ``forecast`` attribute.  Supports Nord Pool,
    Energi Data Service (hourly and quarterly resolution), and generic
    price sensors.

    Returns a list of N prices [currency/kWh] aligned to horizon steps,
    or None when no price entity is configured or the sensor is unavailable.
    """
    if not coordinator._price_entity:
        return None
    state = coordinator.hass.states.get(coordinator._price_entity)
    adder = coordinator._price_net_tariff + coordinator._price_spot_surcharge
    return _electricity_price.build_price_forecast(
        state,
        now=now,
        horizon=coordinator._horizon,
        dt_s=float(coordinator.dt),
        adder=adder,
    )


def record_weather_success(coordinator: HeatingAssistantCoordinator) -> None:
    """Mark the most recent forecast fetch as successful."""
    if coordinator.weather_consecutive_failures > 0:
        _LOGGER.info(
            "Weather forecast recovered for %s after %d consecutive failures",
            coordinator._weather_entity,
            coordinator.weather_consecutive_failures,
        )
    coordinator.weather_last_error = None
    coordinator.weather_last_error_at = None
    coordinator.weather_last_success_at = coordinator.now_utc
    coordinator.weather_consecutive_failures = 0


def record_weather_failure(
    coordinator: HeatingAssistantCoordinator, reason: str
) -> None:
    """Record a forecast-fetch failure and log on threshold crossings."""
    coordinator.weather_consecutive_failures += 1
    coordinator.weather_last_error = reason
    coordinator.weather_last_error_at = coordinator.now_utc
    if coordinator.weather_consecutive_failures in coordinator._weather_warn_thresholds:
        _LOGGER.warning(
            "Weather forecast unavailable for %s (failure #%d): %s",
            coordinator._weather_entity,
            coordinator.weather_consecutive_failures,
            reason,
        )


def read_cloud_cover_now(coordinator: HeatingAssistantCoordinator) -> Optional[float]:
    return _weather.read_cloud_cover_now(coordinator.hass, coordinator._weather_entity)


def read_wind_speed_now(coordinator: HeatingAssistantCoordinator) -> Optional[float]:
    return _weather.read_wind_speed_now(coordinator.hass, coordinator._weather_entity)


def record_solar_fc_success(coordinator: HeatingAssistantCoordinator) -> None:
    """Mark the most recent solar-forecast read as successful."""
    if coordinator.solar_fc_consecutive_failures > 0:
        _LOGGER.info(
            "Solar forecast recovered for %s after %d consecutive failures",
            coordinator._solar_radiation_entity,
            coordinator.solar_fc_consecutive_failures,
        )
    coordinator.solar_fc_last_error = None
    coordinator.solar_fc_last_error_at = None
    coordinator.solar_fc_last_success_at = coordinator.now_utc
    coordinator.solar_fc_consecutive_failures = 0


def record_solar_fc_failure(
    coordinator: HeatingAssistantCoordinator, reason: str
) -> None:
    """Record a solar-forecast read failure and log on threshold crossings."""
    coordinator.solar_fc_consecutive_failures += 1
    coordinator.solar_fc_last_error = reason
    coordinator.solar_fc_last_error_at = coordinator.now_utc
    if coordinator.solar_fc_consecutive_failures in coordinator._weather_warn_thresholds:
        _LOGGER.warning(
            "Solar forecast unavailable for %s (failure #%d): %s",
            coordinator._solar_radiation_entity,
            coordinator.solar_fc_consecutive_failures,
            reason,
        )


def read_ghi(
    coordinator: HeatingAssistantCoordinator,
    now: datetime,
) -> Tuple[Optional[float], Optional[List[Optional[float]]]]:
    """Read the solar-radiation entity and derive a GHI series [W/m²].

    Returns ``(ghi_now, ghi_forecast)``.  Both are ``None`` when no entity
    is configured, it is unavailable / stale, or it carries no usable
    irradiance — in which case the solar model falls back to the analytical
    clear-sky model (today's behaviour).
    """
    coordinator._solar_provider = "none"
    if not coordinator._solar_radiation_entity:
        return None, None

    state = coordinator.hass.states.get(coordinator._solar_radiation_entity)
    if state is None or getattr(state, "state", None) in _solar_fc._UNAVAILABLE_STATES:
        record_solar_fc_failure(coordinator, "entity unavailable")
        return None, None
    if _solar_fc.is_stale(state, now):
        record_solar_fc_failure(coordinator, "forecast stale")
        return None, None

    series = _solar_fc.parse_irradiance_forecast(state)
    value_now = _solar_fc.read_irradiance_now(
        coordinator.hass, coordinator._solar_radiation_entity, now=now,
    )
    if not series and value_now is None:
        record_solar_fc_failure(coordinator, "no irradiance data")
        return None, None

    coordinator._solar_provider = "irradiance"
    forecast, ghi_now = _solar_fc.compute_ghi_series(
        series, value_now, coordinator._horizon, float(coordinator.dt), now,
    )
    if forecast is None and ghi_now is None:
        record_solar_fc_failure(coordinator, "no usable irradiance")
        return None, None

    record_solar_fc_success(coordinator)
    return ghi_now, forecast


def room_solar_gain(
    coordinator: HeatingAssistantCoordinator,
    name: str,
    now: datetime,
    cloud_cover: Optional[float],
    ghi: Optional[float],
) -> float:
    """Per-room solar gain [W]: windows when present, else exposure preset.

    Mirrors the controller's per-room selection so the coordinator's
    current-step ``solar_gains`` snapshot matches the forecast path.
    """
    room = coordinator.model.rooms[name]
    if room.windows:
        return room_solar_gains(
            room.windows, now, coordinator._latitude, coordinator._longitude,
            cloud_cover=cloud_cover, ghi=ghi, albedo=getattr(coordinator, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
        )
    return room_solar_gains_from_exposure(
        room.solar_exposure_aperture, room.solar_facing,
        now, coordinator._latitude, coordinator._longitude,
        cloud_cover=cloud_cover, ghi=ghi, albedo=getattr(coordinator, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
    )


def resolve_display_cloud_cover(
    coordinator: HeatingAssistantCoordinator,
) -> Optional[float]:
    """Best-effort cloud cover for an out-of-cycle solar-gain snapshot.

    Prefers the value the last full MPC cycle published, then the EMA
    (seeded from persistence on restart), then a fresh live read.  Returns
    ``None`` only when there is genuinely no cloud information available.
    """
    cloud_cover = coordinator.cloud_cover
    if cloud_cover is None:
        cloud_cover = coordinator._cloud_cover_filtered
    if cloud_cover is None:
        cloud_cover = read_cloud_cover_now(coordinator)
    return cloud_cover


def publish_current_solar_gains(coordinator: HeatingAssistantCoordinator) -> None:
    """Recompute and publish ``coordinator.solar_gains`` / ``coordinator.cloud_cover`` for now.

    Solar gain depends only on the time, the site location, the cloud cover
    and (optionally) the GHI — never on the outdoor temperature.  Keeping
    this out of the outdoor-temperature gate means the dashboard shows the
    real, sun-driven (and cloud-attenuated) value right after a restart
    instead of dropping to 0 while the outdoor entity is still unavailable.
    """
    cloud_cover = resolve_display_cloud_cover(coordinator)
    coordinator.cloud_cover = cloud_cover
    try:
        coordinator.solar_gains = {
            name: room_solar_gain(
                coordinator, name, coordinator.now_utc, cloud_cover, coordinator.ghi_now
            )
            for name in coordinator.model.room_names
        }
    except Exception:  # pragma: no cover - defensive; never block a cycle
        _LOGGER.debug("Solar-gain recompute failed", exc_info=True)
