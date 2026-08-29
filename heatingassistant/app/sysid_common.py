"""Shared PE helpers used by HTTP handlers and Ingress sensors."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from heatingassistant.engine import const


def _dt(runtime: Any) -> float:
    return float(
        getattr(runtime, "options", {}).get(
            const.CONF_UPDATE_INTERVAL, const.DEFAULT_UPDATE_INTERVAL
        )
        or const.DEFAULT_UPDATE_INTERVAL
    )


def _iso_time(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None
