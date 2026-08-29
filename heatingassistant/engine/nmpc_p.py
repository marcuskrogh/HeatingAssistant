"""Proportional tracking with feedforward on the heater fraction."""

from __future__ import annotations


def require_non_negative_p_gating(p_deadband: float, u_ref_gate: float) -> None:
    """Raise if NMPC-off P gating knobs are negative."""

    if float(p_deadband) < 0.0:
        raise ValueError(f"p_deadband must be >= 0; got {p_deadband}")
    if float(u_ref_gate) < 0.0:
        raise ValueError(f"u_ref_gate must be >= 0; got {u_ref_gate}")


def p_command(
    u_ref: float,
    t_ref: float,
    t_hat: float,
    kp: float,
    u_min: float,
    u_max: float,
    *,
    u_ref_gate: float = 0.0,
    p_deadband: float = 0.0,
) -> float:
    """Return ``clip(u_ref + K_p (T_ref − T_hat), u_min, u_max)``.

    When ``|u_ref|`` is below ``u_ref_gate`` and air is within ``p_deadband``
    of ``T_ref``, return 0 so P does not fight an NMPC-off interval.
    Defaults (0, 0) keep the ungated tracker. ``comfort_fallback_command``
    omits the kwargs. Negative knobs clamp to 0 here so the fast loop never
    raises; persist and the controller constructor reject them.
    """

    gate = max(0.0, float(u_ref_gate))
    band = max(0.0, float(p_deadband))
    u_ff = float(u_ref)
    if abs(u_ff) < gate and abs(float(t_ref) - float(t_hat)) <= band:
        return 0.0
    raw = u_ff + float(kp) * (float(t_ref) - float(t_hat))
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
