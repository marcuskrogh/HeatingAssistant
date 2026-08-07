"""Application runtime for the MQTT-based Heating Assistant architecture."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Any

from heatingassistant.fusion.averaging import average_numeric_tags
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.mqtt.bridge import InMemoryMqttBus, MqttBus, Unsubscribe
from heatingassistant.mqtt.topics import (
    DEFAULT_QOS,
    MqttTagPayload,
    bindings as bindings_topic,
    parse_tag_topic,
    status as status_topic,
    tag_in,
    tag_out,
)
from heatingassistant.persistence import load_config, load_state, save_config, save_state


@dataclass(frozen=True)
class Binding:
    """A bridge binding between a HA entity and an App tag."""

    tag: str
    entity_id: str
    direction: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Binding":
        tag = data.get("tag")
        entity_id = data.get("entity_id")
        direction = data.get("direction")
        if not isinstance(tag, str) or not tag:
            raise ValueError("binding tag must be a non-empty string")
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("binding entity_id must be a non-empty string")
        if direction not in {"in", "out"}:
            raise ValueError("binding direction must be 'in' or 'out'")
        return cls(tag=tag, entity_id=entity_id, direction=direction)

    def to_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "entity_id": self.entity_id,
            "direction": self.direction,
        }


class HeatingRuntime:
    """Owns App state and MQTT-facing contract data."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        bus: MqttBus | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.options = dict(options) if options is not None else load_config(self.data_dir)
        save_config(self.data_dir, self.options)
        self.state = load_state(self.data_dir)
        self.instance_id = str(self.options.get("instance_id") or "default")
        self.mqtt_broker = self.options.get("mqtt_broker")
        self.bus = bus or InMemoryMqttBus()
        self.bindings = self._load_bindings()
        self.tag_values: dict[str, Any] = dict(self.state.get("tag_values") or {})
        self.tag_statuses: dict[str, str] = dict(self.state.get("tag_statuses") or {})
        self.room_temperatures: dict[str, float | None] = dict(
            self.state.get("room_temperatures") or {}
        )
        self.actuator_outputs: dict[str, float] = dict(self.state.get("actuator_outputs") or {})
        self.control_engine = ControlEngine(self.options)
        self._subscriptions: list[Unsubscribe] = []
        self._started = False
        self._recompute_room_temperatures()

    async def start(self) -> None:
        """Subscribe to inbound tags and publish retained App metadata."""

        if self._started:
            return
        self._subscriptions.append(
            self.bus.subscribe(
                f"heatingassistant/{self.instance_id}/tag/+/in",
                self._handle_tag_message,
            )
        )
        self._started = True
        await self.publish_bindings()
        await self.run_control_cycle()
        await self.publish_status()

    async def stop(self) -> None:
        """Unsubscribe from MQTT topics."""

        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self._started = False

    async def publish_bindings(self) -> None:
        """Publish the retained bridge binding map."""

        await self.bus.publish(
            bindings_topic(self.instance_id),
            json.dumps({"bindings": self.binding_dicts()}, sort_keys=True),
            qos=DEFAULT_QOS,
            retain=True,
        )

    async def publish_status(self) -> None:
        """Publish retained runtime status."""

        await self.bus.publish(
            status_topic(self.instance_id),
            json.dumps(self.status(), sort_keys=True),
            qos=DEFAULT_QOS,
            retain=True,
        )

    async def publish_actuator_outputs(self) -> None:
        """Publish the latest App -> HA actuator tag values."""

        for tag, value in sorted(self.actuator_outputs.items()):
            await self.bus.publish(
                tag_out(self.instance_id, tag),
                MqttTagPayload(value=value, status="GOOD").encode(),
                qos=DEFAULT_QOS,
                retain=True,
            )

    async def _handle_tag_message(
        self,
        topic: str,
        payload: str | bytes,
        qos: int,
        retain: bool,
    ) -> None:
        parsed = parse_tag_topic(topic)
        if parsed is None or parsed.instance_id != self.instance_id or parsed.direction != "in":
            return
        tag_payload = MqttTagPayload.decode(payload)
        self.update_tag(parsed.tag, tag_payload)
        await self.run_control_cycle()

    def update_tag(self, tag: str, payload: MqttTagPayload) -> None:
        """Store a tag payload and recompute any affected room averages."""

        self.tag_values[tag] = payload.value
        self.tag_statuses[tag] = payload.status
        self._recompute_room_temperatures()
        self._save_runtime_state()

    async def run_control_cycle(self) -> dict[str, float]:
        """Compute and publish actuator outputs for the current runtime state."""

        self._recompute_room_temperatures()
        outputs = self.control_engine.compute_actions(
            self.room_temperatures,
            self._outdoor_temperature(),
            self._setpoints(),
        )
        self.actuator_outputs = dict(outputs)
        self._save_runtime_state()
        await self.publish_actuator_outputs()
        await self.publish_status()
        return self.actuator_outputs

    async def update_config(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Persist config updates and rebuild runtime-derived state."""

        self.options = {**self.options, **dict(updates)}
        self.instance_id = str(self.options.get("instance_id") or "default")
        self.mqtt_broker = self.options.get("mqtt_broker")
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self.control_engine.update_config(self.options)
        self._recompute_room_temperatures()
        self._save_runtime_state()
        await self.publish_bindings()
        await self.run_control_cycle()
        return dict(self.options)

    async def update_bindings(self, bindings: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
        """Persist bridge bindings and publish the retained binding map."""

        self.options["bindings"] = [dict(item) for item in bindings]
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self._save_runtime_state()
        await self.publish_bindings()
        await self.publish_status()
        return self.binding_dicts()

    def room_temperature(self, room_name: str) -> float | None:
        """Return the current fused temperature for a room."""

        return self.room_temperatures.get(room_name)

    def binding_dicts(self) -> list[dict[str, str]]:
        return [binding.to_dict() for binding in self.bindings]

    def config(self) -> dict[str, Any]:
        """Return the persisted App configuration."""

        return dict(self.options)

    def state_snapshot(self) -> dict[str, Any]:
        """Return persisted runtime state plus current derived fields."""

        return {
            **dict(self.state),
            "tag_values": dict(self.tag_values),
            "tag_statuses": dict(self.tag_statuses),
            "room_temperatures": dict(self.room_temperatures),
            "actuator_outputs": dict(self.actuator_outputs),
            "control": self._control_status(),
        }

    def status(self) -> dict[str, Any]:
        """Expose a compact health/status snapshot for HTTP and MQTT."""

        return {
            "instance_id": self.instance_id,
            "mqtt_broker": self.mqtt_broker,
            "bindings_count": len(self.bindings),
            "control": self._control_status(),
            "actuator_outputs": dict(self.actuator_outputs),
            "rooms": [
                {
                    "name": room.get("name"),
                    "temp_tags": self._room_temp_tags(room),
                    "temperature": self.room_temperatures.get(str(room.get("name"))),
                }
                for room in self._rooms()
                if room.get("name") is not None
            ],
            "started": self._started,
            "status": "ok",
            "ts": time.time(),
        }

    def _control_status(self) -> dict[str, Any]:
        return {
            "mode": self.control_engine.mode,
            "fallback_reason": self.control_engine.fallback_reason,
        }

    def _load_bindings(self) -> list[Binding]:
        source = self.options.get("bindings", self.state.get("bindings", []))
        if isinstance(source, Mapping):
            source = source.get("bindings", [])
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes)):
            raise ValueError("bindings must be a list or {'bindings': list}")
        return [Binding.from_mapping(item) for item in source if isinstance(item, Mapping)]

    def _rooms(self) -> list[Mapping[str, Any]]:
        rooms = self.options.get("rooms", [])
        if not isinstance(rooms, list):
            return []
        return [room for room in rooms if isinstance(room, Mapping)]

    def _room_temp_tags(self, room: Mapping[str, Any]) -> list[str]:
        temp_tags = room.get("temp_tags")
        if isinstance(temp_tags, list):
            return [tag for tag in temp_tags if isinstance(tag, str) and tag]
        temp_tag = room.get("temp_tag")
        if isinstance(temp_tag, str) and temp_tag:
            return [temp_tag]
        return []

    def _outdoor_temperature(self) -> float | None:
        tag = self.options.get("outdoor_temp_tag") or self.options.get("outdoor_tag")
        if not isinstance(tag, str) or not tag:
            return None
        return self._coerce_number(self.tag_values.get(tag))

    def _setpoints(self) -> dict[str, float]:
        setpoints: dict[str, float] = {}
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            setpoint_tag = room.get("setpoint_tag")
            value = (
                self._coerce_number(self.tag_values.get(setpoint_tag))
                if isinstance(setpoint_tag, str)
                else None
            )
            if value is None:
                value = self._coerce_number(room.get("setpoint"))
            if value is not None:
                setpoints[name] = value
        return setpoints

    def _recompute_room_temperatures(self) -> None:
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            temp_tags = self._room_temp_tags(room)
            values = {tag: self._coerce_number(self.tag_values.get(tag)) for tag in temp_tags}
            statuses = {tag: self.tag_statuses.get(tag) for tag in temp_tags}
            self.room_temperatures[name] = average_numeric_tags(values, statuses)

    @staticmethod
    def _coerce_number(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _save_runtime_state(self) -> None:
        self.state["bindings"] = self.binding_dicts()
        self.state["tag_values"] = dict(self.tag_values)
        self.state["tag_statuses"] = dict(self.tag_statuses)
        self.state["room_temperatures"] = dict(self.room_temperatures)
        self.state["actuator_outputs"] = dict(self.actuator_outputs)
        self.state["config"] = dict(self.options)
        save_state(self.data_dir, self.state)


async def publish_tag_in(
    runtime: HeatingRuntime,
    tag: str,
    value: Any,
    *,
    status: str = "GOOD",
    reason: str | None = None,
    ts: float | None = None,
) -> None:
    """Test helper that publishes a tag/in payload through the runtime bus."""

    await runtime.bus.publish(
        tag_in(runtime.instance_id, tag),
        MqttTagPayload(value=value, status=status, reason=reason, ts=ts).encode(),
        qos=DEFAULT_QOS,
    )
