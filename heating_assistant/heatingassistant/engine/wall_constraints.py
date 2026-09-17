"""Physical envelope constraints for the hidden 2R2C wall node.

The wall/mass temperature is unmeasured.  Without a bound it can absorb
air-node innovations and q_int / R degeneracy, producing trajectories that
cannot occur for a node thermally between indoor air and outdoor air.

Envelope (per room, per sample):

    min(T_a, T_out) − δ_lo  ≤  T_w  ≤  max(T_a, T_out) + δ_hi

δ_hi leaves room for solar-heated surfaces; δ_lo is a small lag below the
colder of air and outdoor (overnight walls should not undercut outdoor by
many kelvin).  Missing outdoor temperature collapses the envelope onto air.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

#: Allowed under-shoot below min(T_a, T_out) [K].
WALL_SLACK_BELOW_K = 1.5

#: Allowed over-shoot above max(T_a, T_out) [K] (solar / stored heat).
WALL_SLACK_ABOVE_K = 8.0

#: Wall diffusion as a fraction of the air-node σ_w (same per-room q-scale).
WALL_PROCESS_NOISE_FRACTION = 0.1

#: Weight on envelope violation² in the N-step PE objective (same K² units
#: as air SSE before the 1/(n σ_R²) scale).
WALL_ENVELOPE_PENALTY = 1.0

#: Absolute safety box [°C] matching estimation.constants._T_WALL_*.
_WALL_ABS_LO = -30.0
_WALL_ABS_HI = 60.0


def envelope_limits(
    t_air: float,
    t_out: Optional[float],
    slack_below: float = WALL_SLACK_BELOW_K,
    slack_above: float = WALL_SLACK_ABOVE_K,
) -> Tuple[float, float]:
    """Return ``(lo, hi)`` for a physically plausible wall temperature."""
    ta = float(t_air)
    if t_out is None or not np.isfinite(t_out):
        to = ta
    else:
        to = float(t_out)
    lo = min(ta, to) - float(slack_below)
    hi = max(ta, to) + float(slack_above)
    if lo > hi:
        lo, hi = hi, lo
    lo = max(_WALL_ABS_LO, lo)
    hi = min(_WALL_ABS_HI, hi)
    if lo > hi:
        return (_WALL_ABS_LO, _WALL_ABS_HI)
    return (lo, hi)


def clip_wall_temperature(
    t_wall: float,
    t_air: float,
    t_out: Optional[float],
) -> float:
    """Project one wall temperature onto the air–outdoor envelope."""
    lo, hi = envelope_limits(t_air, t_out)
    return float(np.clip(float(t_wall), lo, hi))


def envelope_signed_violation(
    t_wall: float,
    t_air: float,
    t_out: Optional[float],
) -> float:
    """Signed distance outside the envelope (0 inside)."""
    lo, hi = envelope_limits(t_air, t_out)
    tw = float(t_wall)
    if tw < lo:
        return tw - lo
    if tw > hi:
        return tw - hi
    return 0.0


def project_wall_block(
    x: np.ndarray,
    t_air: Sequence[float],
    t_out: Optional[float],
    n_rooms: int,
) -> np.ndarray:
    """Clip wall nodes of ``x = [T_a (n), T_w (n), …]`` in place and return x."""
    x = np.asarray(x, dtype=float)
    n = int(n_rooms)
    if n <= 0 or x.size < 2 * n:
        return x
    air = np.asarray(t_air, dtype=float)
    to: Optional[float]
    if t_out is None or not np.isfinite(t_out):
        to = None
    else:
        to = float(t_out)
    for i in range(n):
        if i < air.size and np.isfinite(air[i]):
            ta = float(air[i])
        else:
            ta = float(x[i])
        x[n + i] = clip_wall_temperature(float(x[n + i]), ta, to)
    return x


def accumulate_wall_envelope_penalty(
    total_sse: float,
    total_grad: np.ndarray,
    x: np.ndarray,
    sx: np.ndarray,
    t_air: Sequence[float],
    t_out: Optional[float],
    n_rooms: int,
    weight: float = WALL_ENVELOPE_PENALTY,
) -> Tuple[float, np.ndarray]:
    """Add ``w · viol²`` and ``2 w viol · ∂T_w/∂θ`` for each room."""
    n = int(n_rooms)
    air = np.asarray(t_air, dtype=float)
    sse = float(total_sse)
    grad = total_grad
    w = float(weight)
    if w <= 0.0 or n <= 0:
        return sse, grad
    for i in range(n):
        wi = n + i
        if wi >= x.size:
            break
        ta = float(air[i]) if i < air.size and np.isfinite(air[i]) else float(x[i])
        viol = envelope_signed_violation(float(x[wi]), ta, t_out)
        if viol == 0.0:
            continue
        sse += w * viol * viol
        grad = grad + (2.0 * w * viol) * sx[:, wi]
    return sse, grad


def _record_timestamp(record: Dict[str, Any]) -> Optional[float]:
    t_val = record.get("timestamp", record.get("t"))
    if t_val is None:
        return None
    try:
        return float(t_val)
    except (TypeError, ValueError):
        return None


def _record_t_out(record: Dict[str, Any]) -> Optional[float]:
    if "d_outdoor" in record:
        try:
            val = float(record["d_outdoor"])
            return val if np.isfinite(val) else None
        except (TypeError, ValueError):
            pass
    d_val = record.get("d")
    if d_val is not None and len(d_val) > 0:
        try:
            val = float(d_val[0])
            return val if np.isfinite(val) else None
        except (TypeError, ValueError):
            return None
    return None


def _record_t_air(record: Dict[str, Any], room_index: int) -> Optional[float]:
    y = record.get("y", record.get("ym"))
    if y is None or room_index >= len(y):
        return None
    try:
        val = float(y[room_index])
        return val if np.isfinite(val) else None
    except (TypeError, ValueError):
        return None


def segment_anchor_records(
    history: Sequence[Dict[str, Any]],
    n_wall_segs: int,
    dataset_start_timestamps: Optional[Iterable[float]],
) -> List[Dict[str, Any]]:
    """One history record per wall-init segment (first sample / nearest start)."""
    if not history:
        return []
    n_segs = max(1, int(n_wall_segs))
    starts = list(dataset_start_timestamps) if dataset_start_timestamps else []
    if n_segs <= 1 or not starts:
        return [history[0]] * n_segs
    times = [_record_timestamp(rec) for rec in history]
    anchors: List[Dict[str, Any]] = []
    for ts in starts[:n_segs]:
        try:
            target = float(ts)
        except (TypeError, ValueError):
            anchors.append(history[0])
            continue
        best = history[0]
        best_d = float("inf")
        for rec, t_val in zip(history, times):
            if t_val is None:
                continue
            dist = abs(t_val - target)
            if dist < best_d:
                best_d = dist
                best = rec
        anchors.append(best)
    while len(anchors) < n_segs:
        anchors.append(anchors[-1] if anchors else history[0])
    return anchors


def t_wall_init_envelope_pairs(
    history: Sequence[Dict[str, Any]],
    n_rooms: int,
    n_wall_segs: int,
    dataset_start_timestamps: Optional[Iterable[float]] = None,
) -> List[Tuple[float, float]]:
    """Envelope ``(lo, hi)`` per (segment, room) in layout ``t_wall_init`` order."""
    n = int(n_rooms)
    anchors = segment_anchor_records(history, n_wall_segs, dataset_start_timestamps)
    if not anchors:
        return [( _WALL_ABS_LO, _WALL_ABS_HI )] * (n * max(1, int(n_wall_segs)))
    pairs: List[Tuple[float, float]] = []
    for rec in anchors:
        t_out = _record_t_out(rec)
        for i in range(n):
            t_air = _record_t_air(rec, i)
            if t_air is None:
                t_air = 20.0
            pairs.append(envelope_limits(t_air, t_out))
    return pairs


def apply_t_wall_init_envelope_bounds(
    bounds: List[Tuple[float, float]],
    layout: Any,
    history: Sequence[Dict[str, Any]],
    n_rooms: int,
    dataset_start_timestamps: Optional[Iterable[float]] = None,
    theta_prior: Optional[np.ndarray] = None,
) -> None:
    """Tighten the ``t_wall_init`` box in *bounds* (and clip *theta_prior*)."""
    a, b = layout.idx_t_wall_init
    n_segs = int(getattr(layout, "n_wall_segs", 1) or 1)
    pairs = t_wall_init_envelope_pairs(
        history, n_rooms, n_segs, dataset_start_timestamps,
    )
    for k, (lo, hi) in enumerate(pairs):
        idx = a + k
        if idx >= b or idx >= len(bounds):
            break
        bounds[idx] = (lo, hi)
        if theta_prior is not None and idx < len(theta_prior):
            theta_prior[idx] = float(np.clip(theta_prior[idx], lo, hi))
