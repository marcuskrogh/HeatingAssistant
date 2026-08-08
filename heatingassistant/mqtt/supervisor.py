"""Discover Mosquitto credentials from the Home Assistant Supervisor."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_logger = logging.getLogger(__name__)

_SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"


def normalize_mqtt_broker(value: Any) -> str | None:
    """Return a bare MQTT hostname from options / discovery values."""

    if not isinstance(value, str):
        return None
    host = value.strip()
    if not host:
        return None
    for prefix in ("mqtts://", "mqtt://", "ssl://", "tcp://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
            break
    # Drop any path/query fragment left after scheme strip.
    host = host.split("/", 1)[0]
    # Drop explicit credentials / port from URL-style hosts.
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("[") and "]" in host:
        # IPv6 literal — keep bracketed form without port.
        end = host.index("]")
        host = host[: end + 1]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.strip()
    return host or None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def fetch_supervisor_mqtt_service(
    *,
    token: str | None = None,
    url: str = _SUPERVISOR_MQTT_URL,
    timeout_s: float = 5.0,
) -> dict[str, Any] | None:
    """GET Supervisor MQTT service details, or None when unavailable."""

    bearer = token if token is not None else os.environ.get("SUPERVISOR_TOKEN")
    if not bearer:
        return None
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 — Supervisor internal URL
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        _logger.warning("Supervisor MQTT discovery failed: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    host = normalize_mqtt_broker(data.get("host") or data.get("broker"))
    if not host:
        return None
    username = data.get("username")
    password = data.get("password")
    port_raw = data.get("port", 1883)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 1883
    return {
        "mqtt_broker": host,
        "mqtt_port": port,
        "mqtt_username": username if isinstance(username, str) else "",
        "mqtt_password": password if isinstance(password, str) else "",
        "mqtt_ssl": bool(data.get("ssl", False)),
        "mqtt_source": "supervisor",
    }


def apply_supervisor_mqtt_discovery(
    options: dict[str, Any],
    *,
    discovered: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Fill blank MQTT credentials from Supervisor discovery.

    Explicit non-blank option values always win. Discovery only supplies missing
    broker/auth fields so Mosquitto's no-anonymous policy can be satisfied on
    HAOS without forcing operators to paste add-on passwords.
    """

    merged = dict(options)
    broker = normalize_mqtt_broker(merged.get("mqtt_broker"))
    if broker is not None:
        merged["mqtt_broker"] = broker

    needs_discovery = _blank(merged.get("mqtt_username")) or _blank(
        merged.get("mqtt_password")
    ) or _blank(merged.get("mqtt_broker"))
    if not needs_discovery:
        merged.setdefault("mqtt_source", "options")
        return merged

    service = discovered if discovered is not None else fetch_supervisor_mqtt_service(token=token)
    if not service:
        merged.setdefault("mqtt_source", "options")
        return merged

    if _blank(merged.get("mqtt_broker")) and service.get("mqtt_broker"):
        merged["mqtt_broker"] = service["mqtt_broker"]
    if "mqtt_port" not in merged or merged.get("mqtt_port") in (None, ""):
        merged["mqtt_port"] = service.get("mqtt_port", 1883)
    if _blank(merged.get("mqtt_username")) and not _blank(service.get("mqtt_username")):
        merged["mqtt_username"] = service["mqtt_username"]
    if _blank(merged.get("mqtt_password")) and not _blank(service.get("mqtt_password")):
        merged["mqtt_password"] = service["mqtt_password"]
    merged["mqtt_source"] = "supervisor"
    _logger.info(
        "Using Supervisor MQTT service at %s:%s (user=%s)",
        merged.get("mqtt_broker"),
        merged.get("mqtt_port"),
        merged.get("mqtt_username") or "(none)",
    )
    return merged
