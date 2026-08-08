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
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill blank MQTT settings from Supervisor discovery.

    Rules:
    - Explicit non-blank option values always win.
    - Username/password are treated as a pair: discover credentials only when
      **both** are blank. If either is set, leave the other alone.
    - When ``fallback`` has durable non-blank credentials and discovery fails
      (or is skipped), restore those instead of leaving blank auth.
    - ``mqtt_source`` is ``supervisor`` only when discovery actually filled a
      field.
    """

    merged = dict(options)
    prior = dict(fallback or {})
    broker = normalize_mqtt_broker(merged.get("mqtt_broker"))
    if broker is not None:
        merged["mqtt_broker"] = broker

    both_creds_blank = _blank(merged.get("mqtt_username")) and _blank(
        merged.get("mqtt_password")
    )
    needs_broker = _blank(merged.get("mqtt_broker"))
    needs_port = "mqtt_port" not in merged or merged.get("mqtt_port") in (None, "")
    needs_discovery = both_creds_blank or needs_broker or needs_port

    filled_from_supervisor = False
    service: dict[str, Any] | None = None
    if needs_discovery:
        service = (
            discovered if discovered is not None else fetch_supervisor_mqtt_service(token=token)
        )

    if service:
        if needs_broker and service.get("mqtt_broker"):
            merged["mqtt_broker"] = service["mqtt_broker"]
            filled_from_supervisor = True
        if needs_port:
            merged["mqtt_port"] = service.get("mqtt_port", 1883)
            filled_from_supervisor = True
        if both_creds_blank:
            if not _blank(service.get("mqtt_username")):
                merged["mqtt_username"] = service["mqtt_username"]
                filled_from_supervisor = True
            if not _blank(service.get("mqtt_password")):
                merged["mqtt_password"] = service["mqtt_password"]
                filled_from_supervisor = True

    # Preserve durable secrets when options left credentials blank and discovery
    # did not supply a replacement (Supervisor down / mqtt:need not yet active).
    if both_creds_blank and _blank(merged.get("mqtt_username")) and not _blank(
        prior.get("mqtt_username")
    ):
        merged["mqtt_username"] = prior["mqtt_username"]
    if both_creds_blank and _blank(merged.get("mqtt_password")) and not _blank(
        prior.get("mqtt_password")
    ):
        merged["mqtt_password"] = prior["mqtt_password"]
    if _blank(merged.get("mqtt_broker")) and not _blank(prior.get("mqtt_broker")):
        prior_broker = normalize_mqtt_broker(prior.get("mqtt_broker"))
        if prior_broker:
            merged["mqtt_broker"] = prior_broker

    merged["mqtt_source"] = "supervisor" if filled_from_supervisor else "options"
    if filled_from_supervisor:
        _logger.info(
            "Using Supervisor MQTT service at %s:%s (user=%s)",
            merged.get("mqtt_broker"),
            merged.get("mqtt_port"),
            merged.get("mqtt_username") or "(none)",
        )
    return merged


def redact_mqtt_secrets(payload: Any) -> Any:
    """Return a deep copy of ``payload`` with MQTT passwords removed for HTTP."""

    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "mqtt_password":
                redacted[key] = "" if value in (None, "") else "***"
                continue
            redacted[key] = redact_mqtt_secrets(value)
        return redacted
    if isinstance(payload, list):
        return [redact_mqtt_secrets(item) for item in payload]
    return payload
