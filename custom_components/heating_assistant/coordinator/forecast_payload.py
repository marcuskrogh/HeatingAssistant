"""Build the forecast payload for WebSocket delivery and dashboard plots."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator
    from .types import ControlTrajectory

_LOGGER = logging.getLogger(__name__)


def build_forecast_payload(
    coordinator: HeatingAssistantCoordinator,
    room_names: Optional[List[str]] = None,
    plot_forecast_steps: Optional[int] = None,
    *,
    predictions: Optional[list] = None,
    linearised_predictions: Optional[list] = None,
    heating_schedule: Optional[list] = None,
    outdoor_forecast: Optional[List[float]] = None,
    solar_forecast: Optional[list] = None,
    price_forecast: Optional[List[float]] = None,
    control_trajectory: Optional["ControlTrajectory"] = None,
    step_dt: Optional[float] = None,
) -> dict:
    """Build the full forecast payload for WebSocket delivery.

    Returns per-room trajectory/forecast arrays plus the outdoor and price
    forecasts.  This is the authoritative source used by the custom
    ``heating_assistant/get_forecasts`` WS command; sensor attributes only
    keep lightweight metadata to stay under HA Recorder's 16 KB limit.

    ``plot_forecast_steps`` lets the dashboard request a plot horizon that
    differs from the controller (MPC) horizon.  This is purely a *display*
    horizon and never changes the control problem:

    * ``None`` (default) → use the full controller horizon (legacy
      behaviour).
    * ``<= len(predictions)`` → truncate to that many steps.
    * ``> len(predictions)`` → extend past the controller horizon by
      **holding the final actuation flat** and simulating the thermal
      model forward, so the plotted trajectory continues smoothly.  The
      extended steps are flagged with ``"extended": True``.

    Optional keyword overrides let :meth:`preview_tuning_forecast` build a
    payload from a one-off MPC solve without mutating the live coordinator
    state.
    """
    from ..dashboard import slugify as _slugify

    if room_names is None:
        room_names = coordinator.model.room_names

    now = getattr(coordinator, "now_utc", None) or datetime.now(tz=timezone.utc)
    dt = float(step_dt if step_dt is not None else coordinator.dt)
    predictions = coordinator.predictions if predictions is None else predictions
    outdoor_forecast = (
        coordinator.outdoor_forecast if outdoor_forecast is None else outdoor_forecast
    )
    solar_forecast = (
        coordinator.solar_forecast if solar_forecast is None else solar_forecast
    )
    heating_schedule = (
        coordinator.heating_schedule if heating_schedule is None else heating_schedule
    )
    linearised_predictions = (
        coordinator.linearised_predictions
        if linearised_predictions is None
        else linearised_predictions
    )
    if price_forecast is None:
        price_forecast = coordinator.price_forecast or []
    traj = (
        getattr(coordinator, "_control_trajectory", None)
        if control_trajectory is None
        else control_trajectory
    )

    # Resolve the requested display horizon and precompute the (house-wide)
    # forward simulation used to extend the plot beyond the MPC horizon.
    n_pred = len(predictions)
    n_outdoor_all = len(outdoor_forecast)
    if plot_forecast_steps is None:
        target_steps = n_pred
    else:
        target_steps = max(0, int(plot_forecast_steps))
    main_n = min(target_steps, n_pred)
    # Extension needs a base trajectory to simulate forward from; with no
    # controller predictions yet there is nothing to hold flat.
    extra = max(0, target_steps - n_pred) if n_pred > 0 else 0

    extended_predictions: List[Dict[str, float]] = []
    outdoor_ext: List[float] = []
    last_heat_step: Dict[str, float] = {}
    last_solar_step: Dict[str, float] = {}
    if extra > 0 and n_pred > 0:
        last_heat_step = dict(heating_schedule[-1]) if heating_schedule else {}
        last_solar_step = dict(solar_forecast[-1]) if solar_forecast else {}
        last_outdoor = (
            outdoor_forecast[-1] if outdoor_forecast
            else (coordinator.outdoor_temp if coordinator.outdoor_temp is not None else 0.0)
        )
        for k in range(extra):
            idx = n_pred + k
            outdoor_ext.append(
                outdoor_forecast[idx] if idx < n_outdoor_all else last_outdoor
            )
        try:
            extended_predictions = coordinator.model.predict(
                horizon=extra,
                dt=dt,
                heat_schedule=[last_heat_step] * extra,
                outdoor_temps=outdoor_ext,
                solar_gain_schedule=[last_solar_step] * extra,
                initial_temps=predictions[-1],
            )
        except Exception:  # pragma: no cover - defensive: never break the plot
            _LOGGER.debug(
                "Forecast plot extension failed; showing controller horizon only",
                exc_info=True,
            )
            extended_predictions = []
            extra = 0

    rooms_payload: dict = {}
    for room_name in room_names:
        room = coordinator.model.rooms[room_name]
        step_sp = traj.setpoints.get(room_name) if traj is not None else None
        step_off = traj.comfort_offsets.get(room_name) if traj is not None else None
        step_enabled = traj.enabled_steps.get(room_name) if traj is not None else None
        comfort_offset = float(getattr(room, "comfort_offset", 2.0))
        # Horizon steps an experiment governs this cycle (control at now+i·dt),
        # so the frontend can shade the experiment region exactly on the same
        # grid the actuator signal is drawn on.
        exp_steps = getattr(coordinator, "_experiment_horizon_steps", {}).get(room_name)

        room_sources = coordinator.sources_for_room(room_name)
        outdoor_now = coordinator.outdoor_temp if coordinator.outdoor_temp is not None else 0.0

        def _rated_heat_cap(src: Any, t_out: float) -> float:
            fn = getattr(src, "rated_heating_capacity", None)
            if callable(fn):
                return float(fn(t_out))
            if hasattr(src, "thermal_power"):
                return float(src.thermal_power(1.0, t_out))
            return float(getattr(src, "max_power", 0.0) or 0.0)

        def _delivered_heat_cap(src: Any, t_out: float) -> float:
            return _rated_heat_cap(src, t_out)

        def _heat_cap(t_out: float, _src=room_sources) -> float:
            return round(sum(_rated_heat_cap(s, t_out) for s in _src), 1)

        def _cool_cap(t_out: float, _src=room_sources) -> float:
            return round(sum(
                s.rated_cooling_power
                for s in _src
                if getattr(s, "can_cool", False)
                and hasattr(s, "rated_cooling_power")
            ), 1)

        current_heating = sum(
            getattr(s, "current_power", 0.0)
            for s in room_sources
        )
        current_solar = coordinator.solar_gains.get(room_name, 0.0)
        filtered_now = coordinator.filtered_temperatures.get(room_name)
        now_temp = (
            round(float(filtered_now), 2)
            if filtered_now is not None
            else round(room.temperature, 2)
        )
        now_sp = round(float(room.setpoint), 2)
        now_enabled = coordinator.is_room_enabled(room_name)

        _now_heat_cap = _heat_cap(outdoor_now)
        _now_cool_cap = _cool_cap(outdoor_now)
        forecast: List[Dict[str, Any]] = [{
            "time": now.isoformat(),
            "temperature": now_temp,
            "heating_power": round(current_heating, 1),
            "solar_gain": round(current_solar, 1),
            "outdoor_temp": (
                None if coordinator.outdoor_temp is None
                else round(coordinator.outdoor_temp, 2)
            ),
            "heating_capacity": _now_heat_cap,
            **( {"cooling_capacity": _now_cool_cap} if _now_cool_cap > 0 else {} ),
            "setpoint": now_sp if now_enabled else None,
            "constraint_upper": round(now_sp + comfort_offset, 2) if now_enabled else None,
            "constraint_lower": round(now_sp - comfort_offset, 2) if now_enabled else None,
            "enabled": now_enabled,
        }]

        trajectory: List[float] = []
        n_sched = len(heating_schedule)
        n_solar = len(solar_forecast)
        n_outdoor = len(outdoor_forecast)
        n_lin = len(linearised_predictions)
        for i in range(main_n):
            pred = predictions[i]
            temp = pred.get(room_name)
            step_time = now + timedelta(seconds=dt * (i + 1))
            have_traj = (
                step_sp is not None and step_off is not None
                and i < len(step_sp) and i < len(step_off)
            )
            have_enabled = (step_enabled is not None and i < len(step_enabled))
            step_on = bool(step_enabled[i]) if have_enabled else now_enabled
            s = (
                round(float(step_sp[i]), 2) if have_traj
                else round(float(room.setpoint), 2)
            )
            o = float(step_off[i]) if have_traj else comfort_offset
            # While an experiment governs this step the MPC's comfort corridor
            # is relaxed wide open (so it doesn't react to the forced input);
            # don't surface that widened band on the plot — it would zoom the
            # temperature axis right out — so omit the corridor on those steps.
            governed = (
                exp_steps is not None and i < len(exp_steps) and exp_steps[i]
            )
            show_corridor = step_on and not governed
            entry: Dict[str, Any] = {
                "time": step_time.isoformat(),
                "setpoint": s if step_on else None,
                "constraint_upper": round(s + o, 2) if show_corridor else None,
                "constraint_lower": round(s - o, 2) if show_corridor else None,
                "enabled": step_on,
            }
            if temp is not None:
                temp_r = round(temp, 2)
                trajectory.append(temp_r)
                entry["temperature"] = temp_r
            if i < n_sched:
                entry["heating_power"] = round(
                    heating_schedule[i].get(room_name, 0.0), 1
                )
            # ``heating_schedule[i]`` is the control at now+i·dt; flag it when
            # an experiment governs that step so the shaded plot region lines
            # up exactly with the experiment signal.
            if governed:
                entry["experiment"] = True
            if i < n_solar:
                entry["solar_gain"] = round(
                    solar_forecast[i].get(room_name, 0.0), 1
                )
            if i < n_outdoor:
                _t_out_i = outdoor_forecast[i]
                entry["outdoor_temp"] = round(_t_out_i, 2)
                entry["heating_capacity"] = _heat_cap(_t_out_i)
                _cc_i = _cool_cap(_t_out_i)
                if _cc_i > 0:
                    entry["cooling_capacity"] = _cc_i
            if i < n_lin:
                lin_temp = linearised_predictions[i].get(room_name)
                if lin_temp is not None:
                    entry["linearised_temperature"] = round(lin_temp, 2)
            forecast.append(entry)

        # Plot horizon extends past the controller horizon: hold the final
        # actuation (heating power, setpoint, comfort band) flat and use the
        # forward-simulated temperatures from ``extended_predictions``.
        if extra > 0:
            if step_sp is not None and len(step_sp) > 0:
                last_s = round(float(step_sp[-1]), 2)
                last_o = (
                    float(step_off[-1])
                    if step_off is not None and len(step_off) > 0
                    else comfort_offset
                )
            else:
                last_s = round(float(room.setpoint), 2)
                last_o = comfort_offset
            last_on = (
                bool(step_enabled[-1])
                if step_enabled is not None and len(step_enabled) > 0
                else now_enabled
            )
            last_power = (
                round(last_heat_step.get(room_name, 0.0), 1)
                if heating_schedule else None
            )
            last_solar_room = round(last_solar_step.get(room_name, 0.0), 1)
            for k in range(extra):
                step_time = now + timedelta(seconds=dt * (n_pred + k + 1))
                entry = {
                    "time": step_time.isoformat(),
                    "setpoint": last_s if last_on else None,
                    "constraint_upper": round(last_s + last_o, 2) if last_on else None,
                    "constraint_lower": round(last_s - last_o, 2) if last_on else None,
                    "enabled": last_on,
                    "extended": True,
                }
                if k < len(extended_predictions):
                    t = extended_predictions[k].get(room_name)
                    if t is not None:
                        t_r = round(float(t), 2)
                        trajectory.append(t_r)
                        entry["temperature"] = t_r
                if last_power is not None:
                    entry["heating_power"] = last_power
                entry["solar_gain"] = last_solar_room
                if k < len(outdoor_ext):
                    _t_out_k = outdoor_ext[k]
                    entry["outdoor_temp"] = round(_t_out_k, 2)
                    entry["heating_capacity"] = _heat_cap(_t_out_k)
                    _cc_k = _cool_cap(_t_out_k)
                    if _cc_k > 0:
                        entry["cooling_capacity"] = _cc_k
                forecast.append(entry)

        # Scalar capacity summary exposed as room-level fields.
        max_power = sum(s.max_power for s in room_sources)
        current_max_power = sum(
            _delivered_heat_cap(s, outdoor_now) for s in room_sources
        )
        current_rated_max_power = sum(
            _rated_heat_cap(s, outdoor_now) for s in room_sources
        )
        max_cooling_power = sum(
            s.rated_cooling_power
            for s in room_sources
            if getattr(s, "can_cool", False) and hasattr(s, "rated_cooling_power")
        )
        current_max_cooling_power = sum(
            s.rated_cooling_power
            for s in room_sources
            if getattr(s, "can_cool", False)
            and hasattr(s, "rated_cooling_power")
        )

        rooms_payload[_slugify(room_name)] = {
            "trajectory": trajectory,
            "forecast": forecast,
            "setpoint": now_sp,
            "comfort_offset": comfort_offset,
            "horizon_steps": len(predictions),
            "plot_horizon_steps": target_steps,
            "step_seconds": dt,
            "horizon_minutes": round(len(predictions) * dt / 60, 1),
            "max_power": max_power if max_power > 0 else None,
            "current_max_power": current_max_power if current_max_power > 0 else None,
            "current_rated_max_power": (
                current_rated_max_power if current_rated_max_power > 0 else None
            ),
            "max_cooling_power": max_cooling_power if max_cooling_power > 0 else None,
            "current_max_cooling_power": current_max_cooling_power if current_max_cooling_power > 0 else None,
        }

    # Outdoor forecast — limited to the display horizon, then held flat for
    # any extension steps so the disturbance plot spans the full window.
    outdoor_data: List[Dict[str, Any]] = [{
        "time": now.isoformat(),
        "outdoor_temp": (
            None if coordinator.outdoor_temp is None
            else round(coordinator.outdoor_temp, 2)
        ),
    }]
    for i in range(min(main_n, n_outdoor_all)):
        step_time = now + timedelta(seconds=dt * (i + 1))
        outdoor_data.append({
            "time": step_time.isoformat(),
            "outdoor_temp": round(outdoor_forecast[i], 2),
        })
    for k in range(len(outdoor_ext)):
        step_time = now + timedelta(seconds=dt * (n_pred + k + 1))
        outdoor_data.append({
            "time": step_time.isoformat(),
            "outdoor_temp": round(outdoor_ext[k], 2),
        })

    # Price forecast — bounded to the display horizon so the power/price
    # plot's price line spans the same window as the heating-power line
    # (truncated when shorter, held flat when the plot extends past it).
    price_data: List[Dict[str, Any]] = []
    price_len = len(price_forecast)
    if plot_forecast_steps is None:
        n_price_points = price_len
    else:
        n_price_points = target_steps + 1
    last_price = price_forecast[-1] if price_len else None
    for i in range(n_price_points):
        if i < price_len:
            price = price_forecast[i]
        elif last_price is not None:
            price = last_price
        else:
            break
        step_time = now + timedelta(seconds=dt * i)
        try:
            price_data.append({
                "time": step_time.isoformat(),
                "price": round(float(price), 5),
            })
        except (TypeError, ValueError):
            pass

    return {
        "rooms": rooms_payload,
        "outdoor_forecast": outdoor_data,
        "price_forecast": price_data,
        "step_seconds": dt,
        "horizon_steps": len(predictions),
    }
