"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.config_flow import (
    HeatingAssistantConfigFlow,
    _build_period_dict,
    _flatten_sections,
    _is_valid_time_string,
    _location_schema,
    _number_box,
)
from heatingassistant.engine.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_END,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    NAME,
)


def test_is_valid_time_string_rejects_non_string():
    assert _is_valid_time_string(630) is False
    assert _is_valid_time_string(None) is False


def test_build_period_dict_includes_non_empty_days():
    period = _build_period_dict(
        {
            CONF_SCHEDULE_NAME: "weekdays",
            CONF_SCHEDULE_MODE: "comfort",
            CONF_SCHEDULE_START: "07:00",
            CONF_SCHEDULE_END: "09:00",
            CONF_SCHEDULE_DAYS: ["mon", "tue", "wed"],
        },
        room_setpoint=22.0,
        room_comfort_offset=2.0,
    )
    assert period[CONF_SCHEDULE_DAYS] == ["mon", "tue", "wed"]


def test_number_box_omits_unit_when_not_provided():
    selector = _number_box(min_value=0.0, max_value=10.0, step=0.5)
    config = selector._args[0]
    assert "unit_of_measurement" not in config._kwargs
    selector = _number_box(min_value=-90.0, max_value=90.0, step="any", unit="°")
    config = selector._args[0]
    assert config._kwargs["unit_of_measurement"] == "°"
    assert config._kwargs["step"] == "any"


def test_location_schema_contains_latitude_and_longitude():
    schema = _location_schema(latitude_default=55.5, longitude_default=12.3)
    assert callable(schema)
    data = schema(
        {
            CONF_LATITUDE: 55.6761,
            CONF_LONGITUDE: 12.5683,
        }
    )
    assert data[CONF_LATITUDE] == pytest.approx(55.6761)
    assert data[CONF_LONGITUDE] == pytest.approx(12.5683)


def _make_flow(**overrides):
    flow = HeatingAssistantConfigFlow()
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(latitude=59.0, longitude=18.0),
        config_entries=SimpleNamespace(async_get_entry=MagicMock(return_value=None)),
    )
    flow.context = overrides.get("context", {})
    flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": overrides.get("step_id", "user")}
    )
    flow.async_create_entry = MagicMock(
        return_value={"type": "create_entry", "title": NAME, "data": {}}
    )
    flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reconfigure_successful"}
    )
    flow._get_entry = overrides.get("_get_entry")
    return flow


@pytest.mark.asyncio
async def test_async_step_user_creates_entry_from_location():
    flow = _make_flow()
    result = await flow.async_step_user(
        {CONF_LATITUDE: 55.6761, CONF_LONGITUDE: 12.5683}
    )

    flow.async_create_entry.assert_called_once()
    assert result["type"] == "create_entry"
    assert flow._data[CONF_LATITUDE] == pytest.approx(55.6761)
    assert flow._data[CONF_LONGITUDE] == pytest.approx(12.5683)
    assert flow._data[CONF_UPDATE_INTERVAL] == DEFAULT_UPDATE_INTERVAL


@pytest.mark.asyncio
async def test_async_step_user_shows_location_form_when_no_input():
    flow = _make_flow()
    result = await flow.async_step_user(None)

    flow.async_show_form.assert_called_once()
    kwargs = flow.async_show_form.call_args.kwargs
    assert kwargs["step_id"] == "user"
    assert callable(kwargs["data_schema"])
    assert result["type"] == "form"


@pytest.mark.asyncio
async def test_async_step_reconfigure_updates_existing_entry():
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_LATITUDE: 55.0, CONF_LONGITUDE: 12.0, CONF_UPDATE_INTERVAL: 900},
    )
    flow = _make_flow(_get_entry=lambda: entry)
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    result = await flow.async_step_reconfigure(
        {CONF_LATITUDE: 56.0, CONF_LONGITUDE: 13.0}
    )

    flow.async_update_reload_and_abort.assert_called_once()
    new_data = flow.async_update_reload_and_abort.call_args.kwargs["data"]
    assert new_data[CONF_LATITUDE] == pytest.approx(56.0)
    assert new_data[CONF_LONGITUDE] == pytest.approx(13.0)
    assert new_data[CONF_UPDATE_INTERVAL] == 900
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_async_step_reconfigure_shows_form_with_existing_defaults():
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_LATITUDE: 55.1, CONF_LONGITUDE: 12.2},
    )
    flow = _make_flow(_get_entry=lambda: entry, step_id="reconfigure")

    await flow.async_step_reconfigure(None)

    flow.async_show_form.assert_called_once()
    kwargs = flow.async_show_form.call_args.kwargs
    assert kwargs["step_id"] == "reconfigure"
    assert callable(kwargs["data_schema"])


def test_get_reconfigure_entry_uses_context_entry_id_fallback():
    entry = SimpleNamespace(entry_id="entry-42", data={})
    flow = _make_flow(context={"entry_id": "entry-42"})
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    resolved = flow._get_reconfigure_entry()

    assert resolved is entry
    flow.hass.config_entries.async_get_entry.assert_called_once_with("entry-42")


def test_get_reconfigure_entry_prefers_get_entry_helper():
    entry = SimpleNamespace(entry_id="entry-99", data={})
    flow = _make_flow(_get_entry=lambda: entry)

    assert flow._get_reconfigure_entry() is entry


def test_get_reconfigure_entry_returns_none_without_entry_id():
    flow = _make_flow(context={})
    assert flow._get_reconfigure_entry() is None
