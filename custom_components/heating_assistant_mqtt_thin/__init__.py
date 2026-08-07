"""Staged thin Home Assistant bridge for the Heating Assistant App."""

from __future__ import annotations

import inspect
import json
import logging
import time
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_INSTANCE_ID, DATA_MANAGERS, DEFAULT_INSTANCE_ID, DOMAIN, QOS
from .mqtt_topics import MqttTagPayload, bindings as bindings_topic, tag_in, tag_out

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = _BridgeManager(hass, entry)
    await manager.async_start()
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_MANAGERS, {})[entry.entry_id] = manager
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = hass.data.get(DOMAIN, {}).get(DATA_MANAGERS, {}).pop(entry.entry_id, None)
    if manager is not None:
        await manager.async_stop()
    return True


class _BridgeManager:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.instance_id = entry.data.get(CONF_INSTANCE_ID, DEFAULT_INSTANCE_ID)
        self._binding_unsub: Callable[[], None] | None = None
        self._binding_subs: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        self._binding_unsub = await _maybe_await(
            mqtt.async_subscribe(
                self.hass,
                bindings_topic(self.instance_id),
                self._async_bindings_message,
                qos=QOS,
            )
        )

    async def async_stop(self) -> None:
        if self._binding_unsub is not None:
            self._binding_unsub()
            self._binding_unsub = None
        self._clear_binding_subscriptions()

    async def _async_bindings_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.payload)
            raw_bindings = payload.get("bindings", payload)
            if not isinstance(raw_bindings, list):
                raise ValueError("bindings payload must be a list or {'bindings': list}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Ignoring invalid Heating Assistant bindings payload: %s", exc)
            return

        self._clear_binding_subscriptions()
        for binding in raw_bindings:
            if not isinstance(binding, dict):
                continue
            tag = binding.get("tag")
            entity_id = binding.get("entity_id")
            direction = binding.get("direction")
            if not isinstance(tag, str) or not isinstance(entity_id, str):
                continue
            if direction == "in":
                self._bind_entity_input(tag, entity_id)
            elif direction == "out":
                await self._bind_entity_output(tag, entity_id)

    def _bind_entity_input(self, tag: str, entity_id: str) -> None:
        async def _state_changed(event: Event) -> None:
            await self._publish_entity_state(tag, event.data.get("new_state"))

        self._binding_subs.append(
            async_track_state_change_event(self.hass, [entity_id], _state_changed)
        )
        self.hass.async_create_task(self._publish_entity_state(tag, self.hass.states.get(entity_id)))

    async def _bind_entity_output(self, tag: str, entity_id: str) -> None:
        async def _message_received(message: Any) -> None:
            try:
                payload = MqttTagPayload.decode(message.payload)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                _LOGGER.warning("Ignoring invalid tag output payload for %s: %s", tag, exc)
                return
            await self._write_entity(entity_id, payload.value)

        unsubscribe = await _maybe_await(
            mqtt.async_subscribe(self.hass, tag_out(self.instance_id, tag), _message_received, qos=QOS)
        )
        self._binding_subs.append(unsubscribe)

    async def _publish_entity_state(self, tag: str, state: State | None) -> None:
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            payload = MqttTagPayload(None, status="BAD", reason="entity_unavailable", ts=time.time())
        else:
            payload = MqttTagPayload(_coerce_state_value(state.state), status="GOOD", ts=time.time())
        await _maybe_await(
            mqtt.async_publish(
                self.hass,
                tag_in(self.instance_id, tag),
                payload.encode(),
                qos=QOS,
                retain=False,
            )
        )

    async def _write_entity(self, entity_id: str, value: Any) -> None:
        domain = entity_id.split(".", 1)[0]
        if domain == "switch":
            await self.hass.services.async_call(
                "switch",
                SERVICE_TURN_ON if _truthy(value) else SERVICE_TURN_OFF,
                {"entity_id": entity_id},
                blocking=False,
            )
        elif domain == "number":
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": float(value)},
                blocking=False,
            )

    def _clear_binding_subscriptions(self) -> None:
        while self._binding_subs:
            self._binding_subs.pop()()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_state_value(value: str) -> Any:
    lower = value.lower()
    if lower == "on":
        return True
    if lower == "off":
        return False
    try:
        return float(value)
    except ValueError:
        return value


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "on", "open", "heat"}
    return bool(value)
