"""Unit tests for Heating Assistant SysID datetime entities."""

from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub HA datetime / restore_state before importing the platform module.
class _EntityBase:
    pass


class _DateTimeEntityBase(_EntityBase):
    pass


class _RestoreEntityBase(_EntityBase):
    async def async_added_to_hass(self) -> None:
        return None

    async def async_get_last_state(self):
        return None

    def async_write_ha_state(self) -> None:
        return None


if "homeassistant.components.datetime" not in sys.modules:
    _dt_mod = types.ModuleType("homeassistant.components.datetime")
    _dt_mod.DateTimeEntity = _DateTimeEntityBase
    sys.modules["homeassistant.components.datetime"] = _dt_mod

if "homeassistant.helpers.restore_state" not in sys.modules:
    _rs_mod = types.ModuleType("homeassistant.helpers.restore_state")
    _rs_mod.RestoreEntity = _RestoreEntityBase
    sys.modules["homeassistant.helpers.restore_state"] = _rs_mod

from custom_components.heating_assistant.const import DOMAIN
from custom_components.heating_assistant.datetime import (
    SysIdWindowEndEntity,
    SysIdWindowStartEntity,
    async_setup_entry,
)


def _make_coordinator():
    return SimpleNamespace()


def _make_entity(cls):
    coord = _make_coordinator()
    ent = object.__new__(cls)
    ent._coordinator = coord
    if cls is SysIdWindowStartEntity:
        ent._attr_name = "Heating Assistant – SysID Window Start"
        ent._attr_unique_id = f"{DOMAIN}_sysid_window_start"
        ent._default_offset = 6 * 3600.0
    else:
        ent._attr_name = "Heating Assistant – SysID Window End"
        ent._attr_unique_id = f"{DOMAIN}_sysid_window_end"
        ent._default_offset = 0.0
    ent._value = None
    ent.async_write_ha_state = MagicMock()
    return ent


class TestSysIdWindowStartEntity:
    def test_native_value_returns_stored_datetime(self):
        ent = _make_entity(SysIdWindowStartEntity)
        expected = datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc)
        ent._value = expected
        assert ent.native_value == expected

    @pytest.mark.asyncio
    async def test_async_set_value_stores_utc_and_writes_state(self):
        ent = _make_entity(SysIdWindowStartEntity)
        naive = datetime(2024, 6, 1, 10, 30)
        await ent.async_set_value(naive)
        assert ent._value.tzinfo == timezone.utc
        assert ent._value.hour == 10
        ent.async_write_ha_state.assert_called_once()

    def test_extra_state_attributes_exposes_unix_timestamp(self):
        ent = _make_entity(SysIdWindowStartEntity)
        ent._value = datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc)
        attrs = ent.extra_state_attributes
        assert attrs["unix_timestamp"] == ent._value.timestamp()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_restores_last_state(self):
        ent = _make_entity(SysIdWindowStartEntity)
        restored = datetime(2024, 5, 20, 12, 0, tzinfo=timezone.utc)
        ent.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state=restored.isoformat()),
        )
        await ent.async_added_to_hass()
        assert ent.native_value == restored


class TestSysIdWindowEndEntity:
    def test_native_value_returns_stored_datetime(self):
        ent = _make_entity(SysIdWindowEndEntity)
        expected = datetime(2024, 6, 1, 18, 0, tzinfo=timezone.utc)
        ent._value = expected
        assert ent.native_value == expected

    @pytest.mark.asyncio
    async def test_async_set_value_preserves_timezone(self):
        ent = _make_entity(SysIdWindowEndEntity)
        aware = datetime(2024, 6, 1, 18, 0, tzinfo=timezone.utc)
        await ent.async_set_value(aware)
        assert ent.native_value == aware
        ent.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_uses_fallback_when_no_state(self):
        ent = _make_entity(SysIdWindowEndEntity)
        ent.async_get_last_state = AsyncMock(return_value=None)
        before = datetime.now(timezone.utc)
        await ent.async_added_to_hass()
        after = datetime.now(timezone.utc)
        assert ent.native_value is not None
        assert before - timedelta(seconds=5) <= ent.native_value <= after

    @pytest.mark.asyncio
    async def test_async_added_to_hass_ignores_invalid_restored_state(self):
        ent = _make_entity(SysIdWindowStartEntity)
        ent.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state="not-a-datetime"),
        )
        await ent.async_added_to_hass()
        assert ent.native_value is not None


@pytest.mark.asyncio
async def test_async_setup_entry_registers_two_datetime_entities():
    coord = _make_coordinator()
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coord}})
    entry = SimpleNamespace(entry_id="entry-1")
    added: list = []
    async_add_entities = MagicMock(side_effect=lambda ents: added.extend(ents))

    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    assert len(added) == 2
    assert {type(e).__name__ for e in added} == {
        "SysIdWindowStartEntity",
        "SysIdWindowEndEntity",
    }
