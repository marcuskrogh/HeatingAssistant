"""Heating Assistant MQTT topic and payload contract."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

TOPIC_ROOT = "heatingassistant"
DEFAULT_QOS = 1
VALID_STATUSES = frozenset({"GOOD", "BAD", "UNCERTAIN"})
VALID_DIRECTIONS = frozenset({"in", "out"})

TagStatus = Literal["GOOD", "BAD", "UNCERTAIN"]
TagDirection = Literal["in", "out"]


def _validate_topic_part(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if "/" in value or "+" in value or "#" in value:
        raise ValueError(f"{name} must not contain MQTT topic separators or wildcards")
    return value


def tag_topic(instance_id: str, tag: str, direction: TagDirection) -> str:
    """Return ``heatingassistant/{instance_id}/tag/{tag}/{direction}``."""

    _validate_topic_part(instance_id, "instance_id")
    _validate_topic_part(tag, "tag")
    if direction not in VALID_DIRECTIONS:
        raise ValueError("direction must be 'in' or 'out'")
    return f"{TOPIC_ROOT}/{instance_id}/tag/{tag}/{direction}"


def tag_in(instance_id: str, tag: str) -> str:
    """Topic for HA -> App telemetry."""

    return tag_topic(instance_id, tag, "in")


def tag_out(instance_id: str, tag: str) -> str:
    """Topic for App -> HA actuation commands."""

    return tag_topic(instance_id, tag, "out")


def cmd(instance_id: str, name: str) -> str:
    """Command topic for the application instance."""

    _validate_topic_part(instance_id, "instance_id")
    _validate_topic_part(name, "name")
    return f"{TOPIC_ROOT}/{instance_id}/cmd/{name}"


def status(instance_id: str) -> str:
    """Retained status topic for the application instance."""

    _validate_topic_part(instance_id, "instance_id")
    return f"{TOPIC_ROOT}/{instance_id}/status"


def bindings(instance_id: str) -> str:
    """Retained binding-map topic for the application instance."""

    _validate_topic_part(instance_id, "instance_id")
    return f"{TOPIC_ROOT}/{instance_id}/bindings"


def entities(instance_id: str) -> str:
    """Retained HA entity catalog topic for Ingress entity pickers."""

    _validate_topic_part(instance_id, "instance_id")
    return f"{TOPIC_ROOT}/{instance_id}/entities"


@dataclass(frozen=True)
class ParsedTagTopic:
    """Structured representation of a tag topic."""

    instance_id: str
    tag: str
    direction: TagDirection


def parse_tag_topic(topic: str) -> ParsedTagTopic | None:
    """Parse a tag topic, returning ``None`` when it is not part of the contract."""

    if not isinstance(topic, str):
        return None
    parts = topic.split("/")
    if len(parts) != 5:
        return None
    root, instance_id, kind, tag, direction = parts
    if root != TOPIC_ROOT or kind != "tag" or direction not in VALID_DIRECTIONS:
        return None
    if not instance_id or not tag:
        return None
    return ParsedTagTopic(instance_id=instance_id, tag=tag, direction=direction)  # type: ignore[arg-type]


@dataclass(frozen=True)
class MqttTagPayload:
    """JSON payload carried on tag topics."""

    value: Any
    status: TagStatus = "GOOD"
    reason: str | None = None
    ts: float | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError("status must be GOOD, BAD, or UNCERTAIN")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("reason must be a string or None")
        if self.ts is not None and not isinstance(self.ts, (int, float)):
            raise TypeError("ts must be a number or None")

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical payload dictionary."""

        return {
            "value": self.value,
            "status": self.status,
            "reason": self.reason,
            "ts": float(self.ts) if self.ts is not None else None,
        }

    def encode(self) -> str:
        """Encode the payload as compact JSON for MQTT."""

        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def decode(cls, payload: str | bytes | bytearray | dict[str, Any]) -> "MqttTagPayload":
        """Decode and validate a tag payload from JSON or a mapping."""

        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError("payload must be valid JSON") from exc
        elif isinstance(payload, dict):
            data = dict(payload)
        else:
            raise TypeError("payload must be JSON text, bytes, or a dict")

        if not isinstance(data, dict):
            raise ValueError("payload JSON must be an object")
        missing = {"value", "status", "reason", "ts"} - data.keys()
        if missing:
            raise ValueError(f"payload missing keys: {', '.join(sorted(missing))}")
        return cls(
            value=data["value"],
            status=data["status"],
            reason=data["reason"],
            ts=data["ts"],
        )
