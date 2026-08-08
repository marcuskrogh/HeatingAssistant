"""MQTT bus interface and in-memory implementation for tests."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, Protocol

MessageHandler = Callable[[str, str | bytes, int, bool], Awaitable[None] | None]
Unsubscribe = Callable[[], None]


class MqttBus(Protocol):
    """Minimal publish/subscribe interface used by the App runtime."""

    async def publish(
        self,
        topic: str,
        payload: str | bytes,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        """Publish a message."""

    def subscribe(self, topic_filter: str, handler: MessageHandler) -> Unsubscribe:
        """Subscribe to a topic filter and return an unsubscribe callback."""


class InMemoryMqttBus:
    """Deterministic MQTT-like bus with retained messages and basic wildcards."""

    def __init__(self) -> None:
        self._subscriptions: list[tuple[str, MessageHandler]] = []
        self._retained: dict[str, tuple[str | bytes, int]] = {}
        self.published: list[tuple[str, str | bytes, int, bool]] = []

    async def publish(
        self,
        topic: str,
        payload: str | bytes,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        """Publish a message to current subscribers."""

        if retain:
            self._retained[topic] = (payload, qos)
        self.published.append((topic, payload, qos, retain))

        for topic_filter, handler in list(self._subscriptions):
            if _topic_matches(topic_filter, topic):
                result = handler(topic, payload, qos, retain)
                if inspect.isawaitable(result):
                    await result

    def subscribe(self, topic_filter: str, handler: MessageHandler) -> Unsubscribe:
        """Subscribe and immediately replay matching retained messages."""

        subscription = (topic_filter, handler)
        self._subscriptions.append(subscription)

        async def _replay(topic: str, payload: str | bytes, qos: int) -> None:
            result = handler(topic, payload, qos, True)
            if inspect.isawaitable(result):
                await result

        # Replay synchronously for sync handlers; async users can call
        # ``replay_retained`` explicitly when they need awaited delivery.
        for topic, (payload, qos) in list(self._retained.items()):
            if _topic_matches(topic_filter, topic):
                result = handler(topic, payload, qos, True)
                if inspect.isawaitable(result):
                    result.close()

        def _unsubscribe() -> None:
            try:
                self._subscriptions.remove(subscription)
            except ValueError:
                pass

        return _unsubscribe

    async def replay_retained(self, topic_filter: str, handler: MessageHandler) -> None:
        """Await retained-message replay for async handlers."""

        for topic, (payload, qos) in list(self._retained.items()):
            if _topic_matches(topic_filter, topic):
                result = handler(topic, payload, qos, True)
                if inspect.isawaitable(result):
                    await result


def create_mqtt_bus(options: Mapping[str, Any] | None = None) -> MqttBus:
    """Return a real MQTT bus when broker settings exist, else the in-memory bus."""

    config = dict(options or {})
    broker = config.get("mqtt_broker")
    if not isinstance(broker, str) or not broker.strip():
        return InMemoryMqttBus()

    from heatingassistant.mqtt.paho_bus import PahoMqttBus

    port_raw = config.get("mqtt_port", 1883)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 1883

    username = config.get("mqtt_username")
    password = config.get("mqtt_password")
    if isinstance(username, str) and not username.strip():
        username = None
    if isinstance(password, str) and not password.strip():
        password = None

    return PahoMqttBus(
        host=broker.strip(),
        port=port,
        username=username if isinstance(username, str) else None,
        password=password if isinstance(password, str) else None,
    )


def _topic_matches(topic_filter: str, topic: str) -> bool:
    """Return whether an MQTT topic filter matches a concrete topic."""

    filter_parts = topic_filter.split("/")
    topic_parts = topic.split("/")

    for index, part in enumerate(filter_parts):
        if part == "#":
            return index == len(filter_parts) - 1
        if index >= len(topic_parts):
            return False
        if part == "+":
            continue
        if part != topic_parts[index]:
            return False

    return len(topic_parts) == len(filter_parts)
