"""Paho MQTT client bus for the HAOS App runtime."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import paho.mqtt.client as mqtt

from heatingassistant.mqtt.bridge import _topic_matches

_logger = logging.getLogger(__name__)

_CONNACK_MESSAGES = {
    1: "incorrect protocol version",
    2: "invalid client identifier",
    3: "server unavailable",
    4: "bad username or password",
    5: "not authorised",
}


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
        ssl: bool = False,
        client_id: str | None = None,
        connect_timeout_s: float = 0.0,
        publish_timeout_s: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._ssl = bool(ssl)
        self._client_id = client_id or f"heatingassistant-{threading.get_ident()}"
        self._publish_timeout_s = publish_timeout_s
        self._subscriptions: list[tuple[str, Any]] = []
        self._on_connect_handlers: list[Any] = []
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._last_error: str | None = None
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            name="heatingassistant-mqtt-async",
            daemon=True,
        )
        self._loop_thread.start()

        self._client = self._build_client()
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()
        if connect_timeout_s > 0 and not self._connected.wait(timeout=connect_timeout_s):
            _logger.warning(
                "MQTT broker %s:%s not connected within %.1fs; continuing App start",
                host,
                port,
                connect_timeout_s,
            )
            if self._last_error is None:
                self._last_error = (
                    f"not connected within {connect_timeout_s:.1f}s to {host}:{port}"
                )

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=self._client_id,
            protocol=mqtt.MQTTv311,
        )
        if self._username:
            client.username_pw_set(self._username, self._password or "")
        if self._ssl:
            # Local Mosquitto addon certs are typically self-signed; accept them
            # for the provisioned mqtt:need endpoint on the Supervisor network.
            client.tls_set()
            client.tls_insecure_set(True)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: dict[str, Any],
        rc: int,
        _properties: Any = None,
    ) -> None:
        if rc != 0:
            reason = _CONNACK_MESSAGES.get(rc, f"connect failed rc={rc}")
            _logger.error(
                "MQTT connect to %s:%s failed with rc=%s (%s)",
                self._host,
                self._port,
                rc,
                reason,
            )
            self._last_error = f"{reason} (rc={rc}) @ {self._host}:{self._port}"
            self._connected.clear()
            return
        _logger.info(
            "MQTT connected to %s:%s ssl=%s user=%s",
            self._host,
            self._port,
            self._ssl,
            self._username or "(none)",
        )
        self._last_error = None
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
            if self._last_error is None:
                self._last_error = f"unexpected disconnect rc={rc} @ {self._host}:{self._port}"

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

    @property
    def last_error(self) -> str | None:
        """Most recent connect/disconnect failure reason, if any."""

        return self._last_error

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def ssl(self) -> bool:
        return self._ssl

    def reconfigure(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl: bool | None = None,
    ) -> None:
        """Update broker endpoint/credentials and restart the MQTT client.

        Used when Supervisor discovery succeeds after a blank/anonymous start
        (SWD-273). Safe to call from a background thread.
        """

        with self._lock:
            if host is not None and host.strip():
                self._host = host.strip()
            if port is not None:
                self._port = int(port)
            if username is not None:
                cleaned = username.strip()
                self._username = cleaned or None
            if password is not None:
                self._password = password
            if ssl is not None:
                self._ssl = bool(ssl)
            self._restart_client_locked()

    def _restart_client_locked(self) -> None:
        try:
            self._client.loop_stop()
        except Exception:  # noqa: BLE001 - best-effort stop
            pass
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort disconnect
            pass
        self._connected.clear()
        self._last_error = f"reconfiguring MQTT client for {self._host}:{self._port}"
        self._client = self._build_client()
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()
        _logger.info(
            "MQTT client reconfigured for %s:%s ssl=%s user=%s",
            self._host,
            self._port,
            self._ssl,
            self._username or "(none)",
        )

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
        wait_connected: bool = False,
    ) -> None:
        """Publish a message to the broker.

        By default this fails fast when disconnected so Ingress HTTP handlers
        (config / tuning Apply) never block on a 10s MQTT wait. Callers that
        want a brief connect race window can pass ``wait_connected=True``.
        """

        if not self._connected.is_set():
            if wait_connected and self._publish_timeout_s > 0:
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
