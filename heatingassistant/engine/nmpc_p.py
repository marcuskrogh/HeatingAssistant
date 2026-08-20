"""Proportional tracking with feedforward on the heater fraction."""

from __future__ import annotations


def p_command(
    u_ref: float,
    t_ref: float,
    t_hat: float,
    kp: float,
    u_min: float,
    u_max: float,
) -> float:
    """Return ``clip(u_ref + K_p (T_ref − T_hat), u_min, u_max)``."""

    raw = float(u_ref) + float(kp) * (float(t_ref) - float(t_hat))
    lo = float(u_min)
    hi = float(u_max)
    if raw < lo:
        return lo
    if raw > hi:
        return hi
    return raw
