"""Tests for room_migration._migrate_room_entities (the entity-registry half).

The pure data-migration helpers are covered in test_room_rename_migration.py;
this covers the registry rewrite, whose prefix-collision rules decide whether a
renamed room keeps its recorder history or orphans its entities.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from custom_components.heating_assistant.const import DOMAIN
from custom_components.heating_assistant.room_migration import _migrate_room_entities

pytestmark = pytest.mark.integration


class _FakeRegistry:
    def __init__(self, entries):
        self.entries = entries
        self.updates: list[tuple[str, dict]] = []
        self.raise_on_update = False

    def async_update_entity(self, entity_id, **updates):
        if self.raise_on_update:
            raise RuntimeError("registry rejected update")
        self.updates.append((entity_id, updates))


def _entity(room, suffix):
    return SimpleNamespace(
        unique_id=f"{DOMAIN}_{room}_{suffix}",
        entity_id=f"sensor.{DOMAIN}_{room}_{suffix}",
    )


def _install_registry(monkeypatch, registry):
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda hass: registry
    er_mod.async_entries_for_config_entry = lambda reg, entry_id: list(registry.entries)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.entity_registry", er_mod)


def _migrate(monkeypatch, entries, renames, all_room_names):
    registry = _FakeRegistry(entries)
    _install_registry(monkeypatch, registry)
    _migrate_room_entities(
        SimpleNamespace(), SimpleNamespace(entry_id="entry-1"), renames, all_room_names
    )
    return registry


def test_rename_rewrites_unique_id_and_entity_id(monkeypatch):
    registry = _migrate(
        monkeypatch,
        [_entity("living", "temperature")],
        {"living": "lounge"},
        ["lounge"],
    )

    ((entity_id, updates),) = registry.updates
    assert entity_id == f"sensor.{DOMAIN}_living_temperature"
    assert updates["new_unique_id"] == f"{DOMAIN}_lounge_temperature"
    assert updates["new_entity_id"] == f"sensor.{DOMAIN}_lounge_temperature"


def test_longer_room_name_is_not_captured_by_prefix(monkeypatch):
    # Renaming "living" must not touch "living_room"'s entities even though
    # "heating_assistant_living_" prefixes their unique_ids.
    registry = _migrate(
        monkeypatch,
        [_entity("living_room", "temperature")],
        {"living": "lounge"},
        ["lounge", "living_room"],
    )

    assert registry.updates == []


def test_existing_target_unique_id_blocks_migration(monkeypatch):
    registry = _migrate(
        monkeypatch,
        [_entity("living", "temperature"), _entity("lounge", "temperature")],
        {"living": "lounge"},
        ["lounge"],
    )

    # The lounge uid already exists, so the living entity must be left alone
    # rather than colliding.
    assert registry.updates == []


def test_registry_failure_is_swallowed(monkeypatch):
    registry = _FakeRegistry([_entity("living", "temperature")])
    registry.raise_on_update = True
    _install_registry(monkeypatch, registry)

    _migrate_room_entities(
        SimpleNamespace(), SimpleNamespace(entry_id="entry-1"), {"living": "lounge"}, ["lounge"]
    )

    assert registry.updates == []
