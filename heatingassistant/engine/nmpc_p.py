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


def comfort_fallback_command(
    t_hat: float,
    setpoint: float,
    comfort_offset: float,
    kp: float,
    u_min: float,
    u_max: float,
) -> float:
    """P toward the setpoint only while air is outside the comfort band.

    Used when there is no accepted NMPC path so a live band violation still
    heats or cools instead of holding ``u = 0``.
    """

    off = abs(float(comfort_offset))
    sp = float(setpoint)
    t = float(t_hat)
    if (sp - off) <= t <= (sp + off):
        return 0.0
    return p_command(0.0, sp, t, kp, u_min, u_max)
