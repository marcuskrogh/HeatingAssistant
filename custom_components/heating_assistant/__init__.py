"""Thin Home Assistant MQTT bridge for the Heating Assistant App."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_INSTANCE_ID, DATA_MANAGERS, DEFAULT_INSTANCE_ID, DOMAIN, QOS
from .mqtt_topics import (
    PICKER_DOMAINS,
    MqttTagPayload,
    bindings as bindings_topic,
    entities as entities_topic,
    tag_in,
    tag_out,
)

_LOGGER = logging.getLogger(__name__)

# Debounce catalog republish when many entities appear at once (startup / reload).
_CATALOG_DEBOUNCE_S = 2.0


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
        self._event_unsubs: list[Callable[[], None]] = []
        self._catalog_task: asyncio.Task[None] | None = None

    async def async_start(self) -> None:
        self._binding_unsub = await _maybe_await(
            mqtt.async_subscribe(
                self.hass,
                bindings_topic(self.instance_id),
                self._async_bindings_message,
                qos=QOS,
            )
        )
        self._event_unsubs.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._async_homeassistant_started,
            )
        )
        # ENTITY_REGISTRY_UPDATED fires when entities are added/removed/renamed.
        self._event_unsubs.append(
            self.hass.bus.async_listen(
                "entity_registry_updated",
                self._async_schedule_catalog_publish,
            )
        )
        if self.hass.is_running:
            await self._async_publish_entity_catalog()

    async def async_stop(self) -> None:
        if self._catalog_task is not None and not self._catalog_task.done():
            self._catalog_task.cancel()
            self._catalog_task = None
        while self._event_unsubs:
            self._event_unsubs.pop()()
        if self._binding_unsub is not None:
            self._binding_unsub()
            self._binding_unsub = None
        self._clear_binding_subscriptions()

    async def _async_homeassistant_started(self, _event: Event) -> None:
        await self._async_publish_entity_catalog()

    @callback
    def _async_schedule_catalog_publish(self, _event: Event) -> None:
        if self._catalog_task is not None and not self._catalog_task.done():
            self._catalog_task.cancel()

        async def _debounced() -> None:
            try:
                await asyncio.sleep(_CATALOG_DEBOUNCE_S)
                await self._async_publish_entity_catalog()
            except asyncio.CancelledError:
                return

        self._catalog_task = self.hass.async_create_task(_debounced())

    async def _async_publish_entity_catalog(self) -> None:
        """Publish a searchable HA entity list for Ingress configuration pickers."""

        catalog: list[dict[str, str]] = []
        for state in self.hass.states.async_all():
            entity_id = state.entity_id
            domain = entity_id.split(".", 1)[0]
            if domain not in PICKER_DOMAINS:
                continue
            name = state.name or entity_id
            display_state = str(state.state)
            if domain == "weather":
                temp = state.attributes.get("temperature")
                if temp is not None:
                    display_state = str(temp)
            entry: dict[str, str] = {
                "entity_id": entity_id,
                "name": name,
                "state": display_state,
            }
            unit = state.attributes.get("unit_of_measurement")
            if domain == "weather" and not isinstance(unit, str):
                unit = "°C" if state.attributes.get("temperature") is not None else None
            if isinstance(unit, str) and unit:
                entry["unit"] = unit
            catalog.append(entry)

        catalog.sort(key=lambda item: (item["name"].lower(), item["entity_id"]))
        payload = json.dumps(
            {"ts": time.time(), "entities": catalog},
            separators=(",", ":"),
            sort_keys=True,
        )
        await _maybe_await(
            mqtt.async_publish(
                self.hass,
                entities_topic(self.instance_id),
                payload,
                qos=QOS,
                retain=True,
            )
        )
        _LOGGER.debug(
            "Published Heating Assistant entity catalog (%d entities) on %s",
            len(catalog),
            entities_topic(self.instance_id),
        )

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
            # Weather entities report condition in ``state``; outdoor °C lives in
            # ``attributes.temperature`` (mirrors classic read_outdoor_temp).
            value: Any = _coerce_state_value(state.state)
            reason: str | None = None
            if state.domain == "weather":
                temp = state.attributes.get("temperature")
                if temp is not None:
                    try:
                        value = float(temp)
                        reason = str(state.state)
                    except (TypeError, ValueError):
                        pass
            payload = MqttTagPayload(value, status="GOOD", reason=reason, ts=time.time())
        # Retain last-known tag values so the App receives them on (re)subscribe
        # after a late MQTT connect (SWD-269). Without retain, the bind-time
        # snapshot is lost until the next HA state change.
        await _maybe_await(
            mqtt.async_publish(
                self.hass,
                tag_in(self.instance_id, tag),
                payload.encode(),
                qos=QOS,
                retain=True,
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
