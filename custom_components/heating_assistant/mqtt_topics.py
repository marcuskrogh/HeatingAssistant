"""MQTT topic and payload helpers mirrored from the App contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any

TOPIC_ROOT = "heatingassistant"
VALID_STATUSES = frozenset({"GOOD", "BAD", "UNCERTAIN"})
VALID_DIRECTIONS = frozenset({"in", "out"})


def _json_default(value: Any) -> Any:
    """Serialize HA attribute types that plain json.dumps rejects."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, set):
        return list(value)
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return value.as_dict()
    return str(value)


def _validate_part(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if "/" in value or "+" in value or "#" in value:
        raise ValueError(f"{name} must not contain MQTT topic separators or wildcards")
    return value


def tag_topic(instance_id: str, tag: str, direction: str) -> str:
    _validate_part(instance_id, "instance_id")
    _validate_part(tag, "tag")
    if direction not in VALID_DIRECTIONS:
        raise ValueError("direction must be 'in' or 'out'")
    return f"{TOPIC_ROOT}/{instance_id}/tag/{tag}/{direction}"


def tag_in(instance_id: str, tag: str) -> str:
    return tag_topic(instance_id, tag, "in")


def tag_out(instance_id: str, tag: str) -> str:
    return tag_topic(instance_id, tag, "out")


def bindings(instance_id: str) -> str:
    _validate_part(instance_id, "instance_id")
    return f"{TOPIC_ROOT}/{instance_id}/bindings"


def entities(instance_id: str) -> str:
    _validate_part(instance_id, "instance_id")
    return f"{TOPIC_ROOT}/{instance_id}/entities"


def status(instance_id: str) -> str:
    _validate_part(instance_id, "instance_id")
    return f"{TOPIC_ROOT}/{instance_id}/status"


# Domains the Ingress entity pickers can select from.
PICKER_DOMAINS = frozenset(
    {
        "sensor",
        "weather",
        "binary_sensor",
        "switch",
        "climate",
        "number",
        "input_boolean",
    }
)


@dataclass(frozen=True)
class MqttTagPayload:
    value: Any
    status: str = "GOOD"
    reason: str | None = None
    ts: float | None = None
    attributes: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError("status must be GOOD, BAD, or UNCERTAIN")
        if self.attributes is not None and not isinstance(self.attributes, dict):
            raise TypeError("attributes must be a dict or None")

    def encode(self) -> str:
        data = {
            "value": self.value,
            "status": self.status,
            "reason": self.reason,
            "ts": float(self.ts) if self.ts is not None else None,
        }
        if self.attributes is not None:
            data["attributes"] = self.attributes
        return json.dumps(
            data,
            separators=(",", ":"),
            sort_keys=True,
            default=_json_default,
        )

    @classmethod
    def decode(cls, payload: str | bytes) -> "MqttTagPayload":
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
        attributes = data.get("attributes")
        if attributes is not None and not isinstance(attributes, dict):
            raise ValueError("attributes must be an object when present")
        return cls(
            value=data["value"],
            status=data["status"],
            reason=data["reason"],
            ts=data["ts"],
            attributes=attributes,
        )
