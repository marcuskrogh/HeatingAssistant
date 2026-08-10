"""Aggregate App/module health into a simple quality enum for Ingress UI."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class SystemQuality(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"


_QUALITY_RANK = {
    SystemQuality.HEALTHY: 0,
    SystemQuality.WARNING: 1,
    SystemQuality.ERROR: 2,
}


def _worse(a: SystemQuality, b: SystemQuality) -> SystemQuality:
    return a if _QUALITY_RANK[a] >= _QUALITY_RANK[b] else b


def evaluate_system_health(
    *,
    mqtt_connected: bool,
    mqtt_last_error: str | None,
    mqtt_discovery_error: str | None,
    api_reachable: bool = True,
    tag_statuses: Mapping[str, Any] | None = None,
    control_mode: str | None = None,
    fallback_reason: str | None = None,
    bindings_count: int = 0,
    entity_catalog_count: int = 0,
    started: bool = False,
    uptime_s: float | None = None,
    last_control_duration_s: float | None = None,
    last_control_ts: float | None = None,
    update_interval_s: float | None = None,
) -> dict[str, Any]:
    """Return overall quality, per-module rows, and a top issue summary."""

    modules: list[dict[str, Any]] = []

    # API — Ingress already reached us when this runs; keep as explicit module.
    if api_reachable:
        modules.append(
            {
                "id": "api",
                "label": "API",
                "quality": SystemQuality.HEALTHY.value,
                "detail": "reachable",
            }
        )
    else:
        modules.append(
            {
                "id": "api",
                "label": "API",
                "quality": SystemQuality.ERROR.value,
                "detail": "unreachable",
            }
        )

    if mqtt_connected:
        mqtt_detail = "connected"
        mqtt_quality = SystemQuality.HEALTHY
        if mqtt_discovery_error:
            mqtt_quality = SystemQuality.WARNING
            mqtt_detail = f"connected; discovery note: {mqtt_discovery_error}"
    else:
        mqtt_quality = SystemQuality.ERROR
        mqtt_detail = mqtt_last_error or mqtt_discovery_error or "disconnected"
    modules.append(
        {
            "id": "mqtt",
            "label": "MQTT",
            "quality": mqtt_quality.value,
            "detail": mqtt_detail,
        }
    )

    bad_tags = [
        str(tag)
        for tag, status in (tag_statuses or {}).items()
        if str(status).upper() == "BAD"
    ]
    if bad_tags:
        sample = ", ".join(bad_tags[:4])
        extra = f" (+{len(bad_tags) - 4} more)" if len(bad_tags) > 4 else ""
        modules.append(
            {
                "id": "sensors",
                "label": "Sensors / tags",
                "quality": SystemQuality.WARNING.value,
                "detail": f"BAD quality on {len(bad_tags)} tag(s): {sample}{extra}",
            }
        )
    else:
        modules.append(
            {
                "id": "sensors",
                "label": "Sensors / tags",
                "quality": SystemQuality.HEALTHY.value,
                "detail": "no BAD tag statuses",
            }
        )

    if fallback_reason:
        modules.append(
            {
                "id": "control",
                "label": "Control / MPC",
                "quality": SystemQuality.WARNING.value,
                "detail": f"fallback ({control_mode or 'unknown'}): {fallback_reason}",
            }
        )
    else:
        duration_note = (
            f"last solve {last_control_duration_s:.2f}s"
            if isinstance(last_control_duration_s, (int, float))
            else "no solve yet"
        )
        modules.append(
            {
                "id": "control",
                "label": "Control / MPC",
                "quality": SystemQuality.HEALTHY.value,
                "detail": f"mode={control_mode or 'unknown'}; {duration_note}",
            }
        )

    if bindings_count <= 0:
        modules.append(
            {
                "id": "bindings",
                "label": "Bindings",
                "quality": SystemQuality.WARNING.value,
                "detail": "no MQTT bindings configured",
            }
        )
    else:
        modules.append(
            {
                "id": "bindings",
                "label": "Bindings",
                "quality": SystemQuality.HEALTHY.value,
                "detail": f"{bindings_count} binding(s)",
            }
        )

    catalog_quality = SystemQuality.HEALTHY
    catalog_detail = f"{entity_catalog_count} entit{'y' if entity_catalog_count == 1 else 'ies'}"
    if entity_catalog_count <= 0:
        catalog_quality = SystemQuality.WARNING
        catalog_detail = "HA entity catalog not received yet"
    modules.append(
        {
            "id": "entity_catalog",
            "label": "Entity catalog",
            "quality": catalog_quality.value,
            "detail": catalog_detail,
        }
    )

    overall = SystemQuality.HEALTHY
    for mod in modules:
        overall = _worse(overall, SystemQuality(mod["quality"]))

    issue_summary = None
    if overall != SystemQuality.HEALTHY:
        ranked = sorted(
            modules,
            key=lambda m: _QUALITY_RANK[SystemQuality(m["quality"])],
            reverse=True,
        )
        top = ranked[0]
        issue_summary = f"{top['label']}: {top['detail']}"

    return {
        "quality": overall.value,
        "issue_summary": issue_summary,
        "modules": modules,
        "uptime_s": uptime_s,
        "started": bool(started),
        "entity_catalog_count": int(entity_catalog_count),
        "bindings_count": int(bindings_count),
        "mqtt_connected": bool(mqtt_connected),
        "control": {
            "mode": control_mode,
            "fallback_reason": fallback_reason,
            "last_run_ts": last_control_ts,
            "last_duration_s": last_control_duration_s,
            "update_interval_s": update_interval_s,
        },
    }
