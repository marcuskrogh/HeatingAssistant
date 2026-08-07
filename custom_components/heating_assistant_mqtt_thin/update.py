"""Update entity showing when a restart is required after bundle sync."""

from __future__ import annotations

from homeassistant.components.update import UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NAME, VERSION
from .version_sync import disk_manifest_version, restart_required


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the restart-required update entity."""

    async_add_entities([HeatingAssistantBridgeUpdateEntity(entry)])


class HeatingAssistantBridgeUpdateEntity(UpdateEntity):
    """Expose loaded-vs-disk version drift in Home Assistant."""

    _attr_has_entity_name = True
    _attr_name = "Bridge restart"
    _attr_installed_version = VERSION

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_bridge_restart"
        self._entry = entry

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": NAME,
            "manufacturer": "Heating Assistant",
        }

    @property
    def latest_version(self) -> str | None:
        return disk_manifest_version()

    @property
    def release_summary(self) -> str | None:
        if restart_required():
            return "Restart Home Assistant to load the thin bridge version on disk."
        return "Loaded bridge version matches the manifest on disk."
