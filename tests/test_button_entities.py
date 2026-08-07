"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.button import (
    EstimateParametersButton,
    ResetParametersButton,
    RunSysIdWithWindowButton,
    async_setup_entry,
)
from heatingassistant.engine.const import DOMAIN
from heatingassistant.engine.parameter_estimator import MIN_HISTORY_STEPS
from tests.helpers.coordinator_stubs import make_hass_stub


def _make_coordinator(*, history_steps=0, estimated_snapshot=None):
    coord = SimpleNamespace(
        history_buffer=[{"y": [20.0]}] * history_steps,
        estimated_params_snapshot=estimated_snapshot,
    )
    return coord


def _make_button(cls, coord):
    ent = object.__new__(cls)
    ent._coordinator = coord
    ent._attr_name = f"test-{cls.__name__}"
    ent._attr_unique_id = f"test_{cls.__name__}"
    ent.hass = make_hass_stub()
    return ent


class TestEstimateParametersButton:
    def test_extra_state_attributes_ready_when_buffer_full(self):
        coord = _make_coordinator(history_steps=MIN_HISTORY_STEPS)
        ent = _make_button(EstimateParametersButton, coord)
        attrs = ent.extra_state_attributes
        assert attrs["history_steps"] == MIN_HISTORY_STEPS
        assert attrs["min_steps_required"] == MIN_HISTORY_STEPS
        assert attrs["ready"] is True

    def test_extra_state_attributes_not_ready(self):
        coord = _make_coordinator(history_steps=10)
        ent = _make_button(EstimateParametersButton, coord)
        assert ent.extra_state_attributes["ready"] is False

    @pytest.mark.asyncio
    async def test_async_press_calls_estimate_service(self):
        coord = _make_coordinator()
        ent = _make_button(EstimateParametersButton, coord)
        await ent.async_press()
        ent.hass.services.async_call.assert_awaited_once_with(
            DOMAIN,
            "estimate_parameters_ml",
            {"apply_parameters": True},
            blocking=False,
        )


class TestResetParametersButton:
    def test_extra_state_attributes_with_snapshot(self):
        coord = _make_coordinator(
            estimated_snapshot={"estimated_at": "2024-06-01T12:00:00+00:00"},
        )
        ent = _make_button(ResetParametersButton, coord)
        attrs = ent.extra_state_attributes
        assert attrs["has_estimated_params"] is True
        assert attrs["estimated_at"] == "2024-06-01T12:00:00+00:00"

    def test_extra_state_attributes_without_snapshot(self):
        coord = _make_coordinator(estimated_snapshot=None)
        ent = _make_button(ResetParametersButton, coord)
        attrs = ent.extra_state_attributes
        assert attrs["has_estimated_params"] is False
        assert attrs["estimated_at"] is None

    @pytest.mark.asyncio
    async def test_async_press_calls_reset_service(self):
        coord = _make_coordinator()
        ent = _make_button(ResetParametersButton, coord)
        await ent.async_press()
        ent.hass.services.async_call.assert_awaited_once_with(
            DOMAIN,
            "reset_estimated_parameters",
            {},
            blocking=False,
        )


class TestRunSysIdWithWindowButton:
    @pytest.mark.asyncio
    async def test_async_press_with_window_datetimes(self):
        coord = _make_coordinator()
        ent = _make_button(
            RunSysIdWithWindowButton,
            coord,
        )
        ent.hass = make_hass_stub(
            temp_states={
                f"datetime.{DOMAIN}_sysid_window_start": "2024-06-01T08:00:00+00:00",
                f"datetime.{DOMAIN}_sysid_window_end": "2024-06-01T14:00:00+00:00",
            },
        )
        await ent.async_press()
        ent.hass.services.async_call.assert_awaited_once()
        args, kwargs = ent.hass.services.async_call.await_args
        assert args[0] == DOMAIN
        assert args[1] == "run_sysid_simulation"
        payload = args[2]
        assert payload["window_end"] > payload["window_start"]
        assert kwargs["blocking"] is False

    @pytest.mark.asyncio
    async def test_async_press_falls_back_without_datetime_entities(self):
        coord = _make_coordinator()
        ent = _make_button(RunSysIdWithWindowButton, coord)
        await ent.async_press()
        ent.hass.services.async_call.assert_awaited_once_with(
            DOMAIN,
            "run_sysid_simulation",
            {},
            blocking=False,
        )


@pytest.mark.asyncio
async def test_async_setup_entry_registers_three_buttons():
    coord = _make_coordinator()
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coord}})
    entry = SimpleNamespace(entry_id="entry-1")
    added: list = []
    async_add_entities = MagicMock(side_effect=lambda ents: added.extend(ents))

    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    assert len(added) == 3
    assert {type(e).__name__ for e in added} == {
        "EstimateParametersButton",
        "ResetParametersButton",
        "RunSysIdWithWindowButton",
    }
