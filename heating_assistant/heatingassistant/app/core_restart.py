"""Core-restart stamp and MQTT Update discovery after a thin-bridge sync."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from heatingassistant.mqtt.topics import DEFAULT_QOS, cmd as cmd_topic

_logger = logging.getLogger(__name__)

STAMP_NAME = "integration_needs_core_restart"
DISCOVERY_OBJECT_ID = "heatingassistant_restart"
DISCOVERY_PREFIX = "homeassistant"
PAYLOAD_INSTALL = "INSTALL"
_SUPERVISOR_CORE_RESTART_URL = "http://supervisor/core/restart"


def stamp_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / STAMP_NAME


def read_stamp(data_dir: str | Path) -> dict[str, str] | None:
    """Return ``{from_version, to_version}`` when a restart is pending."""

    path = stamp_path(data_dir)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Legacy stamp was a UTC timestamp line.
        return {"from_version": "previous", "to_version": raw}
    if not isinstance(data, dict):
        return None
    from_version = data.get("from_version")
    to_version = data.get("to_version")
    if not isinstance(from_version, str) or not isinstance(to_version, str):
        return None
    if not from_version.strip() or not to_version.strip():
        return None
    return {"from_version": from_version.strip(), "to_version": to_version.strip()}


def write_stamp(
    data_dir: str | Path, *, from_version: str, to_version: str
) -> Path:
    path = stamp_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"from_version": from_version, "to_version": to_version}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def clear_stamp(data_dir: str | Path) -> None:
    try:
        stamp_path(data_dir).unlink()
    except FileNotFoundError:
        return


def discovery_topic() -> str:
    return f"{DISCOVERY_PREFIX}/update/{DISCOVERY_OBJECT_ID}/config"


def state_topic(instance_id: str) -> str:
    return f"heatingassistant/{instance_id}/restart/state"


def command_topic(instance_id: str) -> str:
    return cmd_topic(instance_id, "core_restart")


def discovery_payload(instance_id: str) -> dict[str, Any]:
    """MQTT discovery config for the Settings Restart required entity."""

    return {
        "name": "Restart required",
        "unique_id": "heatingassistant_core_restart",
        "object_id": "heatingassistant_restart_required",
        "title": "HeatingAssistant",
        "state_topic": state_topic(instance_id),
        "command_topic": command_topic(instance_id),
        "payload_install": PAYLOAD_INSTALL,
        "qos": DEFAULT_QOS,
        "device": {
            "identifiers": ["heatingassistant"],
            "name": "HeatingAssistant",
            "manufacturer": "Heating Assistant",
            "model": "App",
        },
    }


def state_payload(*, from_version: str, to_version: str) -> dict[str, Any]:
    return {
        "installed_version": from_version,
        "latest_version": to_version,
        "title": "HeatingAssistant",
        "release_summary": "Restart of Home Assistant required",
        "in_progress": False,
    }


def request_core_restart(
    *,
    token: str | None = None,
    url: str = _SUPERVISOR_CORE_RESTART_URL,
    timeout_s: float = 10.0,
) -> bool:
    """POST Supervisor Core restart. Return True on HTTP success."""

    bearer = token if token is not None else os.environ.get("SUPERVISOR_TOKEN")
    if not bearer:
        _logger.warning("SUPERVISOR_TOKEN missing; cannot request Core restart")
        return False
    request = Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except HTTPError as exc:
        _logger.warning("Core restart request failed: HTTP %s", exc.code)
        return False
    except URLError as exc:
        _logger.warning("Core restart request failed: %s", exc.reason)
        return False
