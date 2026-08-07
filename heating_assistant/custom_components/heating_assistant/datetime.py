"""
Heating Assistant – DateTime platform.

Provides two DateTimeEntity helpers that store the explicit start and end of
the user-selected SysID window.  Persisting as entities means:
  * The values survive HA restarts (restored via HA state machine).
  * They appear as native Lovelace datetime picker cards.
  * The ``RunSysIdWithWindowButton`` can read them without any extra glue.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

# Sentinel: when no window has been set yet, default to "now minus 6 hours"
# so the picker opens on something sensible rather than the epoch.
_FALLBACK_OFFSET_SECONDS = 6 * 3600.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant datetime entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SysIdWindowStartEntity(coordinator),
        SysIdWindowEndEntity(coordinator),
    ])


class _SysIdWindowEntity(DateTimeEntity, RestoreEntity):
    """Base for the two SysID window boundary entities."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        name: str,
        unique_suffix: str,
        default_offset: float,
    ) -> None:
        self._coordinator = coordinator
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{unique_suffix}"
        self._default_offset = default_offset
        self._value: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last known value from HA state machine."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable", "None"):
            try:
                restored = datetime.fromisoformat(last_state.state)
                if restored.tzinfo is None:
                    restored = restored.replace(tzinfo=timezone.utc)
                self._value = restored
                return
            except (ValueError, TypeError):
                pass
        # Fallback: derive a sensible default from wall clock
        now_utc = datetime.now(timezone.utc)
        from datetime import timedelta
        self._value = now_utc - timedelta(seconds=self._default_offset)

    @property
    def native_value(self) -> datetime | None:
        return self._value

    async def async_set_value(self, value: datetime) -> None:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        self._value = value
        self.async_write_ha_state()

    def as_unix_timestamp(self) -> float | None:
        if self._value is None:
            return None
        return self._value.timestamp()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "unix_timestamp": self.as_unix_timestamp(),
        }


class SysIdWindowStartEntity(_SysIdWindowEntity):
    """Datetime picker for the SysID window start."""

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(
            coordinator,
            name="Heating Assistant – SysID Window Start",
            unique_suffix="sysid_window_start",
            default_offset=6 * 3600.0,
        )


class SysIdWindowEndEntity(_SysIdWindowEntity):
    """Datetime picker for the SysID window end (defaults to now)."""

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(
            coordinator,
            name="Heating Assistant – SysID Window End",
            unique_suffix="sysid_window_end",
            default_offset=0.0,
        )
