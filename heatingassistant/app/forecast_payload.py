"""Build Ingress forecast payloads from the last App control cycle."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from heatingassistant.engine.naming import room_slug


def build_app_forecast_payload(
    *,
    rooms: list[Mapping[str, Any]],
    room_temperatures: Mapping[str, float | None],
    outdoor_temp: float | None,
    energy_price: float | None,
    snapshot: Mapping[str, Any],
    plot_forecast_hours: float | None = None,
    now: datetime | None = None,
    room_power_meta: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Assemble dashboard forecast arrays from cached MPC trajectories.

    Timestamps use ``now + k·dt`` with ``dt`` from the control snapshot
    (``update_interval``). A bridge sample at ``now`` mirrors the classic
    coordinator payload shape expected by Ingress charts.

    Room keys use :func:`room_slug` so they match synthetic entity / UI slugs.
    """

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    dt = float(snapshot.get("dt") or 900.0)
    predictions = list(snapshot.get("predictions") or [])
    linearised = list(snapshot.get("linearised_predictions") or [])
    heating_schedule = list(snapshot.get("heating_schedule") or [])
    outdoor_forecast = list(snapshot.get("outdoor_forecast") or [])
    solar_forecast = list(snapshot.get("solar_forecast") or [])
    filtered_temps = snapshot.get("filtered_temperatures") or {}
    if not isinstance(filtered_temps, Mapping):
        filtered_temps = {}
    power_meta = room_power_meta or {}

    n_pred = len(predictions)
    if plot_forecast_hours is not None and dt > 0:
        target_steps = max(0, int(round(float(plot_forecast_hours) * 3600.0 / dt)))
    else:
        target_steps = n_pred
    main_n = min(target_steps, n_pred) if n_pred else 0
    extra = max(0, target_steps - n_pred) if n_pred > 0 else 0

    rooms_payload: dict[str, Any] = {}
    for room in rooms:
        name = room.get("name")
        if not isinstance(name, str) or not name:
            continue
        slug = room_slug(name)
        setpoint = _as_float(room.get("setpoint"), 21.0)
        comfort = _as_float(room.get("comfort_offset"), 2.0)
        enabled = bool(room.get("enabled", True))
        current_temp = room_temperatures.get(name)
        if current_temp is None:
            current_temp = _as_float(room.get("temperature"), setpoint)
        # Bridge from EKF estimated output when available (classic forecast
        # sensor behaviour); fall back to measured room temperature.
        estimated = filtered_temps.get(name)
        if estimated is None:
            bridge_temp = float(current_temp)
        else:
            try:
                bridge_temp = float(estimated)
            except (TypeError, ValueError):
                bridge_temp = float(current_temp)
        meta = dict(power_meta.get(name) or {})

        bridge_capacity = _capacity_at(
            power_meta, name, outdoor_temp if outdoor_temp is not None else None
        )
        forecast: list[dict[str, Any]] = [
            {
                "time": now.isoformat(),
                "temperature": round(bridge_temp, 2),
                "linearised_temperature": round(bridge_temp, 2),
                "heating_power": _step_heating(heating_schedule, 0, name),
                "solar_gain": _step_solar(solar_forecast, 0, name),
                "outdoor_temp": None if outdoor_temp is None else round(float(outdoor_temp), 2),
                "setpoint": round(setpoint, 2) if enabled else None,
                "constraint_upper": round(setpoint + comfort, 2) if enabled else None,
                "constraint_lower": round(setpoint - comfort, 2) if enabled else None,
                "enabled": enabled,
            }
        ]
        if bridge_capacity is not None:
            forecast[0]["heating_capacity"] = bridge_capacity

        trajectory: list[float] = []
        for i in range(main_n):
            step_time = now + timedelta(seconds=dt * (i + 1))
            entry: dict[str, Any] = {
                "time": step_time.isoformat(),
                "setpoint": round(setpoint, 2) if enabled else None,
                "constraint_upper": round(setpoint + comfort, 2) if enabled else None,
                "constraint_lower": round(setpoint - comfort, 2) if enabled else None,
                "enabled": enabled,
            }
            pred = predictions[i] if isinstance(predictions[i], Mapping) else {}
            temp = pred.get(name)
            if temp is not None:
                temp_r = round(float(temp), 2)
                trajectory.append(temp_r)
                entry["temperature"] = temp_r
            power = _step_heating(heating_schedule, i, name)
            if power is not None:
                entry["heating_power"] = power
            # solar_forecast is N+1 (index 0 = now); future step i uses i+1.
            solar = _step_solar(solar_forecast, i + 1, name)
            if solar is None:
                solar = _step_solar(solar_forecast, i, name)
            if solar is not None:
                entry["solar_gain"] = solar
            step_outdoor: float | None = None
            if i < len(outdoor_forecast):
                step_outdoor = float(outdoor_forecast[i])
                entry["outdoor_temp"] = round(step_outdoor, 2)
            capacity = _capacity_at(power_meta, name, step_outdoor)
            if capacity is not None:
                entry["heating_capacity"] = capacity
            if i < len(linearised) and isinstance(linearised[i], Mapping):
                lin = linearised[i].get(name)
                if lin is not None:
                    entry["linearised_temperature"] = round(float(lin), 2)
            forecast.append(entry)

        # Hold final actuation flat when the plot horizon exceeds the MPC horizon.
        if extra > 0 and main_n > 0:
            last_power = _step_heating(heating_schedule, main_n - 1, name)
            last_solar = _step_solar(solar_forecast, main_n, name)
            if last_solar is None:
                last_solar = _step_solar(solar_forecast, main_n - 1, name)
            last_outdoor = (
                float(outdoor_forecast[min(main_n - 1, len(outdoor_forecast) - 1)])
                if outdoor_forecast
                else outdoor_temp
            )
            last_temp = trajectory[-1] if trajectory else bridge_temp
            last_lin = None
            if main_n - 1 < len(linearised) and isinstance(linearised[main_n - 1], Mapping):
                raw_lin = linearised[main_n - 1].get(name)
                if raw_lin is not None:
                    last_lin = round(float(raw_lin), 2)
            last_capacity = _capacity_at(power_meta, name, last_outdoor)
            for k in range(extra):
                step_time = now + timedelta(seconds=dt * (main_n + k + 1))
                entry = {
                    "time": step_time.isoformat(),
                    "temperature": last_temp,
                    "setpoint": round(setpoint, 2) if enabled else None,
                    "constraint_upper": round(setpoint + comfort, 2) if enabled else None,
                    "constraint_lower": round(setpoint - comfort, 2) if enabled else None,
                    "enabled": enabled,
                    "extended": True,
                }
                if last_lin is not None:
                    entry["linearised_temperature"] = last_lin
                if last_power is not None:
                    entry["heating_power"] = last_power
                if last_solar is not None:
                    entry["solar_gain"] = last_solar
                if last_outdoor is not None:
                    entry["outdoor_temp"] = round(float(last_outdoor), 2)
                if last_capacity is not None:
                    entry["heating_capacity"] = last_capacity
                forecast.append(entry)
                trajectory.append(last_temp)

        room_block: dict[str, Any] = {
            "trajectory": trajectory,
            "forecast": forecast,
            "setpoint": round(setpoint, 2),
            "comfort_offset": comfort,
            "horizon_steps": n_pred,
            "plot_horizon_steps": target_steps,
            "step_seconds": dt,
            "horizon_minutes": round(n_pred * dt / 60.0, 1) if n_pred else 0.0,
        }
        for key in (
            "max_power",
            "current_rated_max_power",
            "current_max_power",
            "max_cooling_power",
        ):
            if key in meta:
                room_block[key] = meta[key]
        rooms_payload[slug] = room_block

    outdoor_data: list[dict[str, Any]] = [
        {
            "time": now.isoformat(),
            "outdoor_temp": None if outdoor_temp is None else round(float(outdoor_temp), 2),
        }
    ]
    for i in range(max(main_n + extra, 0)):
        step_time = now + timedelta(seconds=dt * (i + 1))
        if i < len(outdoor_forecast):
            value = outdoor_forecast[i]
        elif outdoor_forecast:
            value = outdoor_forecast[-1]
        else:
            value = outdoor_temp
        outdoor_data.append(
            {
                "time": step_time.isoformat(),
                "outdoor_temp": None if value is None else round(float(value), 2),
            }
        )

    price_data: list[dict[str, Any]] = []
    price_series = list(snapshot.get("price_forecast") or [])
    if price_series:
        for i, price in enumerate(price_series):
            price_data.append(
                {
                    "time": (now + timedelta(seconds=dt * i)).isoformat(),
                    "price": round(float(price), 5),
                }
            )
    elif energy_price is not None:
        # Persist the latest scalar price across the horizon when no day-ahead
        # attribute series was available (SWD-277/278).
        steps = max(main_n + extra, n_pred, 1)
        for i in range(steps):
            price_data.append(
                {
                    "time": (now + timedelta(seconds=dt * i)).isoformat(),
                    "price": round(float(energy_price), 5),
                }
            )

    return {
        "rooms": rooms_payload,
        "outdoor_forecast": outdoor_data,
        "price_forecast": price_data,
        "step_seconds": dt,
        "plot_forecast_hours": plot_forecast_hours,
        "mode": snapshot.get("mode"),
    }


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _step_heating(
    schedule: list[Any], index: int, room_name: str
) -> float | None:
    if index >= len(schedule) or not isinstance(schedule[index], Mapping):
        return None
    value = schedule[index].get(room_name)
    if value is None:
        return None
    return round(float(value), 1)


def _step_solar(schedule: list[Any], index: int, room_name: str) -> float | None:
    if index >= len(schedule) or not isinstance(schedule[index], Mapping):
        return None
    value = schedule[index].get(room_name)
    if value is None:
        return None
    return round(float(value), 1)


def _capacity_at(
    power_meta: Mapping[str, Mapping[str, float]],
    room_name: str,
    outdoor_temp: float | None,
) -> float | None:
    """Best-effort heating capacity for a step.

    When outdoor temp is unknown, fall back to the room's current rated max.
    Per-step COP recalculation needs heat-source objects; the runtime passes
    snapshot-time rated capacity which is already outdoor-aware.
    """

    meta = power_meta.get(room_name)
    if not meta:
        return None
    value = meta.get("current_rated_max_power")
    if value is None:
        value = meta.get("max_power")
    if value is None:
        return None
    return round(float(value), 1)
