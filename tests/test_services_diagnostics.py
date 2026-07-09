"""Unit tests for diagnostic service handlers and get_coordinator helper."""

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.const import DOMAIN
from custom_components.heating_assistant.services import diagnostics as diag_mod
from custom_components.heating_assistant.services.context import get_coordinator
from custom_components.heating_assistant.services.diagnostics import (
    handle_analyze_model_fit,
    handle_compute_loglik_slice,
    handle_controller_performance,
    handle_validate_parameters,
)


def _stub_coordinator(*, room_names, rooms=None, history_buffer=None):
    """Minimal coordinator stand-in for service handler tests."""
    coordinator = MagicMock(spec=HeatingAssistantCoordinator)
    if rooms is None:
        rooms = {
            name: SimpleNamespace(
                thermal_mass=5_000_000.0,
                r_external=0.05,
                setpoint=22.0,
            )
            for name in room_names
        }
    coordinator.model = SimpleNamespace(room_names=list(room_names), rooms=rooms)
    coordinator._history_buffer = deque(history_buffer or [])
    coordinator.history_buffer = coordinator._history_buffer
    coordinator.async_update_listeners = MagicMock()
    coordinator.async_compute_loglik_slice = AsyncMock(
        return_value={"room": room_names[0], "log_likelihood": [[0.0]]}
    )
    return coordinator


def test_get_coordinator_returns_first_instance():
    """get_coordinator returns the HeatingAssistantCoordinator in hass.data."""
    coordinator = MagicMock(spec=HeatingAssistantCoordinator)
    hass = SimpleNamespace()
    hass.data = {DOMAIN: {"entry-1": coordinator, "other": "ignored"}}

    assert get_coordinator(hass) is coordinator


def test_get_coordinator_raises_when_missing():
    """get_coordinator raises ValueError when no coordinator is registered."""
    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}

    with pytest.raises(ValueError, match="No Heating Assistant coordinator found"):
        get_coordinator(hass)


@pytest.mark.asyncio
async def test_validate_parameters_reports_all_rooms(monkeypatch):
    """validate_parameters service returns per-room validation for every room."""
    coordinator = _stub_coordinator(room_names=["Living Room", "Kitchen"])
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)

    result = await handle_validate_parameters(hass, SimpleNamespace(data={}))

    assert set(result["rooms"]) == {"Living Room", "Kitchen"}
    assert result["rooms"]["Living Room"]["valid"] is True
    assert result["rooms"]["Living Room"]["thermal_mass_valid"] is True


@pytest.mark.asyncio
async def test_validate_parameters_filters_by_room_name(monkeypatch):
    """room_name filter limits validation to a single known room."""
    coordinator = _stub_coordinator(room_names=["Living Room", "Kitchen"])
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)

    result = await handle_validate_parameters(
        hass, SimpleNamespace(data={"room_name": "Kitchen"})
    )

    assert set(result["rooms"]) == {"Kitchen"}


@pytest.mark.asyncio
async def test_controller_performance_with_history(monkeypatch):
    """controller_performance_report computes metrics when history is sufficient."""
    coordinator = _stub_coordinator(
        room_names=["Living Room"],
        history_buffer=[
            {"y": [21.0]},
            {"y": [21.5]},
            {"y": [22.0]},
        ],
    )
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)

    result = await handle_controller_performance(hass, SimpleNamespace(data={}))

    room_result = result["rooms"]["Living Room"]
    assert room_result["setpoint"] == 22.0
    assert room_result["n_samples"] == 3
    assert "mean_tracking_error" in room_result


@pytest.mark.asyncio
async def test_controller_performance_insufficient_data(monkeypatch):
    """A single history sample yields an insufficient_data error."""
    coordinator = _stub_coordinator(
        room_names=["Living Room"],
        history_buffer=[{"y": [21.0]}],
    )
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)

    result = await handle_controller_performance(hass, SimpleNamespace(data={}))

    assert result["rooms"]["Living Room"]["error"] == "insufficient_data"


@pytest.mark.asyncio
async def test_analyze_model_fit_returns_report(monkeypatch):
    """analyze_model_fit delegates to generate_model_fit_report and refreshes listeners."""
    coordinator = _stub_coordinator(room_names=["Living Room"])
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)
    monkeypatch.setattr(
        "custom_components.heating_assistant.model_diagnostics.generate_model_fit_report",
        lambda *_a, **_k: {"rooms": {"Living Room": {"r_squared": 0.95}}},
    )

    result = await handle_analyze_model_fit(hass, SimpleNamespace(data={}))

    assert result["rooms"]["Living Room"]["r_squared"] == 0.95
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_analyze_model_fit_filters_room(monkeypatch):
    """room_name filter narrows the returned report to one room."""
    coordinator = _stub_coordinator(room_names=["Living Room", "Kitchen"])
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)
    monkeypatch.setattr(
        "custom_components.heating_assistant.model_diagnostics.generate_model_fit_report",
        lambda *_a, **_k: {
            "rooms": {
                "Living Room": {"r_squared": 0.95},
                "Kitchen": {"r_squared": 0.80},
            }
        },
    )

    result = await handle_analyze_model_fit(
        hass, SimpleNamespace(data={"room_name": "Kitchen"})
    )

    assert set(result["rooms"]) == {"Kitchen"}


@pytest.mark.asyncio
async def test_compute_loglik_slice_delegates_to_coordinator(monkeypatch):
    """compute_loglik_slice calls the coordinator and returns the grid."""
    coordinator = _stub_coordinator(room_names=["Living Room"])
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)

    result = await handle_compute_loglik_slice(
        hass,
        SimpleNamespace(data={"room_name": "Living Room", "n_grid": 11, "span_log": 1.0}),
    )

    coordinator.async_compute_loglik_slice.assert_awaited_once_with(
        "Living Room", n_grid=11, span_log=1.0
    )
    assert result["log_likelihood"] == [[0.0]]


@pytest.mark.asyncio
async def test_compute_loglik_slice_none_result(monkeypatch):
    """When the coordinator returns None, the handler reports an error."""
    coordinator = _stub_coordinator(room_names=["Living Room"])
    coordinator.async_compute_loglik_slice = AsyncMock(return_value=None)
    hass = SimpleNamespace()
    monkeypatch.setattr(diag_mod, "get_coordinator", lambda _h: coordinator)

    result = await handle_compute_loglik_slice(
        hass, SimpleNamespace(data={"room_name": "Living Room"})
    )

    assert result["error"] == "history_too_short_or_unknown_room"
