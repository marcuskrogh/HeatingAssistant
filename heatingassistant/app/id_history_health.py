"""ID history health snapshot for System Status (card-only; SWD-317)."""

from __future__ import annotations

from typing import Any


ID_HISTORY_AGE_WARNING_MULT = 2.0
ID_HISTORY_APPEND_ERROR_STREAK = 3


def evaluate_id_history_health(
    *,
    now_ts: float,
    update_interval_s: float,
    buffer_last_ts: float,
    disk_last_ts: float,
    append_failure_streak: int,
    last_append_ok: bool | None,
) -> dict[str, Any]:
    """Return card fields + local qualities (does not affect overall health)."""

    interval = max(1.0, float(update_interval_s))
    warn_after = ID_HISTORY_AGE_WARNING_MULT * interval

    if buffer_last_ts > 0.0:
        age_s = max(0.0, float(now_ts) - float(buffer_last_ts))
    else:
        age_s = None

    if age_s is None:
        age_quality = "warning"
    elif age_s > warn_after:
        age_quality = "warning"
    else:
        age_quality = "healthy"

    streak = max(0, int(append_failure_streak))
    if streak >= ID_HISTORY_APPEND_ERROR_STREAK:
        append_quality = "error"
        append_detail = f"failed ×{streak}"
    elif last_append_ok is False:
        # Failures colour as error only at the streak threshold; show count plainly.
        append_quality = "healthy"
        append_detail = f"failed ×{streak}" if streak else "failed"
    elif last_append_ok is True:
        append_quality = "healthy"
        append_detail = "ok"
    else:
        append_quality = "healthy"
        append_detail = "none yet"

    if buffer_last_ts > 0.0 and disk_last_ts > 0.0:
        lag_s = abs(float(buffer_last_ts) - float(disk_last_ts))
    elif buffer_last_ts > 0.0 or disk_last_ts > 0.0:
        # One side missing — treat as full lag vs the known stamp age.
        known = max(float(buffer_last_ts), float(disk_last_ts))
        lag_s = max(0.0, float(now_ts) - known) if known > 0.0 else None
    else:
        lag_s = None

    if lag_s is None:
        lag_quality = "warning"
    elif lag_s > warn_after:
        lag_quality = "warning"
    else:
        lag_quality = "healthy"

    return {
        "last_sample_age_s": age_s,
        "last_sample_quality": age_quality,
        "last_append_ok": last_append_ok,
        "append_failure_streak": streak,
        "append_quality": append_quality,
        "append_detail": append_detail,
        "buffer_disk_lag_s": lag_s,
        "lag_quality": lag_quality,
        "update_interval_s": interval,
        "warn_after_s": warn_after,
    }
