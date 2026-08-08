"""Paho MQTT client bus for the HAOS App runtime."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import paho.mqtt.client as mqtt

from heatingassistant.mqtt.bridge import _topic_matches

_logger = logging.getLogger(__name__)


class PahoMqttBus:
    """Bridge the App runtime to a real Mosquitto broker.

    Connection is non-blocking: construction starts the client loop immediately
    and never raises on a slow/unreachable broker. Ingress HTTP can bind while
    MQTT reconnects in the background.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        connect_timeout_s: float = 0.0,
        publish_timeout_s: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._publish_timeout_s = publish_timeout_s
        self._subscriptions: list[tuple[str, Any]] = []
        self._on_connect_handlers: list[Any] = []
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            name="heatingassistant-mqtt-async",
            daemon=True,
        )
        self._loop_thread.start()

        client_id = client_id or f"heatingassistant-{threading.get_ident()}"
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        if username:
            self._client.username_pw_set(username, password or "")
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.connect_async(host, port, keepalive=60)
        self._client.loop_start()
        if connect_timeout_s > 0 and not self._connected.wait(timeout=connect_timeout_s):
            _logger.warning(
                "MQTT broker %s:%s not connected within %.1fs; continuing App start",
                host,
                port,
                connect_timeout_s,
            )

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: dict[str, Any],
        rc: int,
        _properties: Any = None,
    ) -> None:
        if rc != 0:
            _logger.error("MQTT connect to %s:%s failed with rc=%s", self._host, self._port, rc)
            self._connected.clear()
            return
        _logger.info("MQTT connected to %s:%s", self._host, self._port)
        self._connected.set()
        with self._lock:
            topic_filters = [topic_filter for topic_filter, _handler in self._subscriptions]
            connect_handlers = list(self._on_connect_handlers)
        for topic_filter in topic_filters:
            self._client.subscribe(topic_filter, qos=1)
        for handler in connect_handlers:
            self._dispatch_connect_handler(handler)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        rc: int,
        _properties: Any = None,
    ) -> None:
        self._connected.clear()
        if rc != 0:
            _logger.warning(
                "MQTT disconnected from %s:%s (rc=%s); reconnecting",
                self._host,
                self._port,
                rc,
            )

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        with self._lock:
            handlers = list(self._subscriptions)
        for topic_filter, handler in handlers:
            if _topic_matches(topic_filter, msg.topic):
                self._dispatch_handler(handler, msg.topic, msg.payload, msg.qos, bool(msg.retain))

    def _dispatch_handler(
        self,
        handler: Any,
        topic: str,
        payload: bytes,
        qos: int,
        retain: bool,
    ) -> None:
        # Fire-and-forget onto the bus loop. Waiting here on the MQTT network
        # thread deadlocks when handlers publish (PUBACK needs this thread).
        async def _run() -> None:
            try:
                result = handler(topic, payload, qos, retain)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _logger.exception("MQTT handler failed for topic %s", topic)

        try:
            asyncio.run_coroutine_threadsafe(_run(), self._loop)
        except Exception:
            _logger.exception("Failed to schedule MQTT handler for topic %s", topic)

    def wait_connected(self, timeout_s: float) -> bool:
        """Block until connected or timeout; return True when connected."""

        if timeout_s <= 0:
            return self._connected.is_set()
        return self._connected.wait(timeout=timeout_s)

    @property
    def connected(self) -> bool:
        """Whether the client currently has an MQTT session."""

        return self._connected.is_set()

    def add_connect_handler(self, handler: Any) -> None:
        """Register a sync/async callback invoked after each successful connect."""

        with self._lock:
            self._on_connect_handlers.append(handler)
        if self._connected.is_set():
            self._dispatch_connect_handler(handler)

    def _dispatch_connect_handler(self, handler: Any) -> None:
        # Never block the MQTT network thread waiting for handler completion —
        # publish() may need the loop to process PUBACKs.
        async def _run() -> None:
            try:
                result = handler()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _logger.exception(
                    "MQTT connect handler failed for %s:%s", self._host, self._port
                )

        try:
            asyncio.run_coroutine_threadsafe(_run(), self._loop)
        except Exception:
            _logger.exception("Failed to schedule MQTT connect handler")

    async def publish(
        self,
        topic: str,
        payload: str | bytes,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        """Publish a message to the broker, waiting briefly for connect if needed."""

        if not self._connected.is_set():
            await asyncio.to_thread(self._connected.wait, self._publish_timeout_s)
        if not self._connected.is_set():
            raise RuntimeError(
                f"MQTT client is not connected to {self._host}:{self._port}"
            )
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=10)

    def subscribe(self, topic_filter: str, handler: Any) -> Any:
        """Subscribe to a topic filter and return an unsubscribe callback."""

        with self._lock:
            subscription = (topic_filter, handler)
            self._subscriptions.append(subscription)
        if self._connected.is_set():
            self._client.subscribe(topic_filter, qos=1)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscriptions.remove(subscription)
                except ValueError:
                    pass

        return _unsubscribe

    def close(self) -> None:
        """Stop the MQTT client and background asyncio loop."""

        self._client.loop_stop()
        self._client.disconnect()
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=2)
