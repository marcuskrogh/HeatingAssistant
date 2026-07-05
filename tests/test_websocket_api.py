"""WebSocket API payload shape tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant import websocket_api
from custom_components.heating_assistant.const import DOMAIN


@pytest.fixture
def coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        get_controller_config_snapshot=lambda: {
            "comfort_offset": 2.0,
            "tracking_weight": 1.0,
            "energy_weight": 0.1,
            "energy_price_weight": 0.0,
            "smoothing_weight": 0.05,
            "soft_constraint_weight": 10.0,
            "soft_constraint_linear_weight": 0.0,
            "terminal_weight": 1.0,
            "horizon": 12,
            "update_interval": 900,
            "window_open_debounce": 60,
            "window_open_close_settle": 300,
            "window_open_q_inflation": 100.0,
        }
    )


@pytest.fixture
def hass(coordinator: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})


@pytest.fixture
def ws_get_controller_config(hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def _register(_hass, handler):
        captured[handler.__name__] = handler

    monkeypatch.setattr(
        websocket_api.websocket_api,
        "async_register_command",
        _register,
    )
    websocket_api.register_websocket_api(hass)
    handler = captured.get("ws_get_controller_config")
    assert handler is not None
    return handler


@pytest.mark.asyncio
async def test_get_controller_config_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_get_controller_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        websocket_api,
        "get_coordinator",
        lambda _hass: coordinator,
    )

    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    await ws_get_controller_config(hass, connection, {"id": 42})

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 42
    assert set(payload) == {"config"}

    config = payload["config"]
    assert config == coordinator.get_controller_config_snapshot()
    assert isinstance(config["horizon"], int)
    assert isinstance(config["update_interval"], int)
    assert isinstance(config["comfort_offset"], float)
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_get_controller_config_send_error_on_failure(
    hass: SimpleNamespace,
    ws_get_controller_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_hass):
        raise RuntimeError("no coordinator")

    monkeypatch.setattr(websocket_api, "get_coordinator", _boom)

    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    await ws_get_controller_config(hass, connection, {"id": 7})

    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    msg_id, code, message = connection.send_error.call_args.args
    assert msg_id == 7
    assert code == "config_fetch_failed"
    assert "no coordinator" in message
