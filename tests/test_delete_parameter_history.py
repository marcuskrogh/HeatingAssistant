"""Tests for the delete_parameter_history coordinator method."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator


class _FakeEntry:
    def __init__(self, data):
        self.entry_id = "e1"
        self.data = data


class _FakeConfigEntries:
    def __init__(self, entry):
        self._entry = entry

    def async_get_entry(self, entry_id):
        return self._entry if entry_id == self._entry.entry_id else None

    def async_update_entry(self, entry, data=None, **kwargs):
        if data is not None:
            entry.data = data


def _make_coord(history):
    snap = {"active": {"rooms": {}}, "history": list(history)}
    entry = _FakeEntry({"estimated_params": snap})
    hass = SimpleNamespace(config_entries=_FakeConfigEntries(entry))
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = hass
    coord._entry = SimpleNamespace(entry_id="e1")
    return coord, entry


def test_delete_removes_the_indexed_entry():
    coord, entry = _make_coord([{"k": 0}, {"k": 1}, {"k": 2}])
    coord.delete_parameter_history(1)
    remaining = [h["k"] for h in entry.data["estimated_params"]["history"]]
    assert remaining == [0, 2]


def test_delete_first_and_last():
    coord, entry = _make_coord([{"k": 0}, {"k": 1}, {"k": 2}])
    coord.delete_parameter_history(0)
    assert [h["k"] for h in entry.data["estimated_params"]["history"]] == [1, 2]
    coord.delete_parameter_history(1)
    assert [h["k"] for h in entry.data["estimated_params"]["history"]] == [1]


def test_delete_out_of_range_raises():
    coord, _ = _make_coord([{"k": 0}])
    with pytest.raises(ValueError):
        coord.delete_parameter_history(5)
    with pytest.raises(ValueError):
        coord.delete_parameter_history(-1)


def test_delete_preserves_active_and_other_keys():
    coord, entry = _make_coord([{"k": 0}, {"k": 1}])
    entry.data["estimated_params"]["rooms"] = {"living_room": {"thermal_mass": 5e6}}
    coord.delete_parameter_history(0)
    snap = entry.data["estimated_params"]
    assert snap["active"] == {"rooms": {}}
    assert snap["rooms"] == {"living_room": {"thermal_mass": 5e6}}
    assert [h["k"] for h in snap["history"]] == [1]
