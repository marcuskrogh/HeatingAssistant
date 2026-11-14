"""Display-series helpers.

These read the estimator and the thermal model for Ingress sensors and the
control log. They do not write commands or change the controller.
"""

from __future__ import annotations

from typing import Any


def filtered_room_temperature(runtime: Any, room_name: str) -> float | None:
    """EKF air-temperature estimate x̂ for ``room_name``.

    Returns None when the controller has no estimate for that room.
    """

    engine = getattr(runtime, "control_engine", None)
    controller = getattr(engine, "_controller", None)
    if controller is None:
        return None
    try:
        temps = controller.filtered_temperatures
    except Exception:
        return None
    if not isinstance(temps, dict) or room_name not in temps:
        return None
    try:
        return float(temps[room_name])
    except (TypeError, ValueError):
        return None


def outdoor_ua_w_per_k(room: Any, *, window_open: bool) -> float | None:
    """Outdoor conductance [W/K], including the open-window term when active."""

    if room is None:
        return None
    try:
        r_external = float(getattr(room, "r_external"))
    except (TypeError, ValueError):
        return None
    if r_external <= 0.0:
        return None
    try:
        g_inf, g_out, _g_dup = room.conductances()
        ua = float(g_inf) + float(g_out)
    except Exception:
        ua = 1.0 / r_external
    if window_open:
        try:
            ua += float(getattr(room, "ua_open", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    return ua


def room_heat_loss_w(
    runtime: Any,
    room_name: str,
    t_air: float | None,
    t_out: float | None,
    *,
    window_open: bool = False,
) -> float | None:
    """UA(T_a − T_out) plus the open-window conductance term, in watts."""

    if t_air is None or t_out is None:
        return None
    rooms = getattr(getattr(getattr(runtime, "control_engine", None), "model", None), "rooms", {}) or {}
    room = rooms.get(room_name)
    ua = outdoor_ua_w_per_k(room, window_open=window_open)
    if ua is None:
        return None
    return ua * (float(t_air) - float(t_out))
