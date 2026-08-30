"""Build outdoor / solar / price disturbance inputs for App MPC compute."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from heatingassistant.engine.electricity_price import build_price_forecast
from heatingassistant.engine.solar_forecast import (
    compute_ghi_series,
    parse_irradiance_forecast,
)
from heatingassistant.engine.weather import (
    parse_cloud_forecast,
    parse_temperature_forecast,
    resolve_cloud_cover_now,
)


def build_mpc_disturbance_inputs(
    *,
    outdoor_temp: float | None,
    weather_attrs: Mapping[str, Any] | None,
    price_value: float | None,
    price_attrs: Mapping[str, Any] | None,
    solar_value: float | None,
    solar_attrs: Mapping[str, Any] | None,
    horizon: int,
    dt_s: float,
    now: datetime | None = None,
    price_adder: float = 0.0,
) -> dict[str, Any]:
    """Assemble optional kwargs for ``controller.compute`` from tag attrs.

    Missing inputs are omitted so the controller falls back to persistence /
    geometric solar. Returns keys understood by ``ControlEngine.compute_actions``.
    """

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    result: dict[str, Any] = {}
    weather_attrs = dict(weather_attrs or {})
    price_attrs = dict(price_attrs or {})
    solar_attrs = dict(solar_attrs or {})
    horizon_n = max(1, int(horizon))

    cloud_now = resolve_cloud_cover_now(weather_attrs)

    forecast_data = weather_attrs.get("forecast")
    if isinstance(forecast_data, list) and forecast_data:
        outdoor_seq = parse_temperature_forecast(
            forecast_data, horizon=horizon, dt=dt_s, now=now
        )
        if outdoor_seq:
            result["outdoor_forecast"] = outdoor_seq
        cloud_seq = parse_cloud_forecast(
            forecast_data,
            horizon=horizon,
            dt=dt_s,
            now=now,
            current_cloud_cover=cloud_now,
        )
        if cloud_seq:
            result["cloud_forecast"] = cloud_seq
            if cloud_now is None:
                cloud_now = cloud_seq[0]

    if outdoor_temp is not None and "outdoor_forecast" not in result:
        # Persistence so Ingress always has an outdoor series for plots.
        result["outdoor_forecast"] = [float(outdoor_temp)] * horizon_n

    if cloud_now is not None:
        result["cloud_cover_now"] = cloud_now
        if "cloud_forecast" not in result:
            result["cloud_forecast"] = [cloud_now] * horizon_n

    ghi_now = None
    if solar_value is not None:
        try:
            ghi_now = float(solar_value)
        except (TypeError, ValueError):
            ghi_now = None
    if ghi_now is not None:
        result["ghi_now"] = ghi_now

    if solar_attrs or ghi_now is not None:
        series = (
            parse_irradiance_forecast(SimpleNamespace(attributes=solar_attrs))
            if solar_attrs
            else None
        )
        ghi_forecast, ghi_from_series = compute_ghi_series(
            series, ghi_now, horizon=horizon, dt=dt_s, now=now
        )
        if ghi_from_series is not None:
            result["ghi_now"] = ghi_from_series
        if ghi_forecast:
            result["ghi_forecast"] = ghi_forecast

    if price_value is not None or price_attrs:
        state = SimpleNamespace(
            state="" if price_value is None else str(price_value),
            attributes=price_attrs,
        )
        price_seq = build_price_forecast(
            state,
            now=now,
            horizon=horizon,
            dt_s=dt_s,
            adder=price_adder,
        )
        if price_seq:
            result["price_forecast"] = price_seq

    return result
