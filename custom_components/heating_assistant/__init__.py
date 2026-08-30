"""Thin Home Assistant MQTT bridge for the Heating Assistant App."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .bridge_manager import (
    _BridgeManager,
    _truthy,
    climate_attributes_for_publish,
)
from .const import DATA_MANAGERS, DOMAIN

__all__ = [
    "async_setup_entry",
    "async_unload_entry",
    "_BridgeManager",
    "_truthy",
    "climate_attributes_for_publish",
]


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
