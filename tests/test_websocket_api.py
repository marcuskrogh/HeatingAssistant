"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant import websocket_api
from heatingassistant.engine.const import (
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_ROOMS,
    CONF_TRACKING_WEIGHT,
    DEFAULT_PLOT_FORECAST_HOURS,
    DEFAULT_PLOT_HISTORY_HOURS,
    DOMAIN,
)


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
        },
        serialize_room_schedules=lambda: {"living_room": [{"start": "08:00"}]},
        build_forecast_payload=lambda plot_forecast_steps=None: {
            "predictions": [20.0, 20.5],
            "plot_forecast_steps": plot_forecast_steps,
        },
        preview_tuning_forecast=lambda tuning, plot_steps, weather: {
            "tuning": tuning,
            "plot_steps": plot_steps,
            "weather_keys": sorted(weather),
        },
        dt=900.0,
        _update_interval_s=900,
        _plot_history_hours=24.0,
        _plot_forecast_hours=12.0,
        _read_cloud_cover_now=lambda: 0.3,
        _smooth_cloud_cover=lambda x: x,
        _async_read_cloud_forecast=AsyncMock(return_value=[0.4, 0.5]),
        _async_read_wind_forecast=AsyncMock(return_value=[2.0, 1.5]),
        _read_wind_speed_now=lambda: 3.0,
        _read_ghi=lambda _now: (150.0, [120.0, 100.0]),
        _entry=SimpleNamespace(entry_id="entry-1"),
        dataset_store=SimpleNamespace(
            list_meta=lambda room_slug=None: [
                {"id": "ds-1", "room_slug": room_slug or "living_room"}
            ],
            get=lambda dataset_id: {"id": dataset_id, "records": []},
        ),
        experiment_manager=SimpleNamespace(
            to_list=lambda: [{"id": "exp-1", "status": "completed"}]
        ),
    )


@pytest.fixture
def hass(coordinator: SimpleNamespace) -> SimpleNamespace:
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_ROOMS: [{"name": "living_room"}],
            CONF_PLOT_HISTORY_HOURS: 48.0,
        },
        options={CONF_PLOT_FORECAST_HOURS: 6.0},
    )

    def _async_get_entry(entry_id: str):
        return entry if entry_id == "entry-1" else None

    return SimpleNamespace(
        data={DOMAIN: {"entry-1": coordinator}},
        config=SimpleNamespace(latitude=55.0, longitude=12.0),
        config_entries=SimpleNamespace(async_get_entry=_async_get_entry),
        async_add_executor_job=AsyncMock(
            side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)
        ),
    )


@pytest.fixture
def ws_handlers(hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict[str, object] = {}

    def _register(_hass, handler):
        captured[handler.__name__] = handler

    monkeypatch.setattr(
        websocket_api.websocket_api,
        "async_register_command",
        _register,
    )
    websocket_api.register_websocket_api(hass)
    return captured


def _connection() -> MagicMock:
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    return connection


@pytest.mark.asyncio
async def test_get_controller_config_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        websocket_api,
        "get_coordinator",
        lambda _hass: coordinator,
    )

    connection = _connection()
    handler = ws_handlers["ws_get_controller_config"]
    await handler(hass, connection, {"id": 42})

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
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_hass):
        raise RuntimeError("no coordinator")

    monkeypatch.setattr(websocket_api, "get_coordinator", _boom)

    connection = _connection()
    handler = ws_handlers["ws_get_controller_config"]
    await handler(hass, connection, {"id": 7})

    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    msg_id, code, message = connection.send_error.call_args.args
    assert msg_id == 7
    assert code == "config_fetch_failed"
    assert "no coordinator" in message


@pytest.mark.asyncio
async def test_get_schedules_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_schedules"](hass, connection, {"id": 1})

    connection.send_result.assert_called_once_with(
        1, {"room_schedules": coordinator.serialize_room_schedules()}
    )
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_get_forecasts_payload_shape_default_horizon(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_forecasts"](hass, connection, {"id": 2})

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 2
    assert payload["predictions"] == [20.0, 20.5]
    assert payload["plot_forecast_steps"] is None
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_get_forecasts_plot_hours_converts_to_steps(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_forecasts"](
        hass, connection, {"id": 3, "plot_forecast_hours": 2.0}
    )

    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 3
    assert payload["plot_forecast_steps"] == 8


@pytest.mark.asyncio
async def test_get_forecasts_send_error_on_failure(
    hass: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_hass):
        raise RuntimeError("forecast boom")

    monkeypatch.setattr(websocket_api, "get_coordinator", _boom)

    connection = _connection()
    await ws_handlers["ws_get_forecasts"](hass, connection, {"id": 4})

    connection.send_result.assert_not_called()
    msg_id, code, message = connection.send_error.call_args.args
    assert msg_id == 4
    assert code == "forecasts_fetch_failed"
    assert "forecast boom" in message


@pytest.mark.asyncio
async def test_preview_tuning_forecast_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_preview_tuning_forecast"](
        hass,
        connection,
        {
            "id": 5,
            CONF_TRACKING_WEIGHT: 2.5,
            "plot_forecast_hours": 1.5,
        },
    )

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 5
    assert payload["tuning"] == {CONF_TRACKING_WEIGHT: 2.5}
    assert payload["plot_steps"] == 6
    assert "cloud_forecast" in payload["weather_keys"]
    hass.async_add_executor_job.assert_awaited_once()
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_preview_tuning_forecast_send_error_on_failure(
    hass: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_hass):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(websocket_api, "get_coordinator", _boom)

    connection = _connection()
    await ws_handlers["ws_preview_tuning_forecast"](hass, connection, {"id": 6})

    connection.send_result.assert_not_called()
    msg_id, code, message = connection.send_error.call_args.args
    assert msg_id == 6
    assert code == "preview_tuning_failed"
    assert "preview failed" in message


@pytest.mark.asyncio
async def test_get_ui_settings_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_ui_settings"](hass, connection, {"id": 7})

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 7
    assert set(payload) == {"ui_settings"}
    assert payload["ui_settings"] == {
        CONF_PLOT_HISTORY_HOURS: 24.0,
        CONF_PLOT_FORECAST_HOURS: 12.0,
    }
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_get_ui_settings_defaults_when_attributes_missing(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del coordinator._plot_history_hours
    del coordinator._plot_forecast_hours
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_ui_settings"](hass, connection, {"id": 8})

    _, payload = connection.send_result.call_args.args
    assert payload["ui_settings"] == {
        CONF_PLOT_HISTORY_HOURS: float(DEFAULT_PLOT_HISTORY_HOURS),
        CONF_PLOT_FORECAST_HOURS: float(DEFAULT_PLOT_FORECAST_HOURS),
    }


@pytest.mark.asyncio
async def test_get_model_config_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_model_config"](hass, connection, {"id": 9})

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 9
    assert set(payload) == {
        "rooms",
        "heat_sources",
        "system",
        "ui_settings",
        "system_params",
        "enums",
    }
    assert payload["rooms"] == [{"name": "living_room"}]
    assert payload["ui_settings"][CONF_PLOT_HISTORY_HOURS] == 48.0
    assert payload["ui_settings"][CONF_PLOT_FORECAST_HOURS] == 6.0
    assert "floor_types" in payload["enums"]
    assert "hvac_modes" in payload["enums"]
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_get_model_config_send_error_on_failure(
    hass: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_hass):
        raise RuntimeError("model config failed")

    monkeypatch.setattr(websocket_api, "get_coordinator", _boom)

    connection = _connection()
    await ws_handlers["ws_get_model_config"](hass, connection, {"id": 10})

    connection.send_result.assert_not_called()
    msg_id, code, message = connection.send_error.call_args.args
    assert msg_id == 10
    assert code == "model_config_fetch_failed"
    assert "model config failed" in message


@pytest.mark.asyncio
async def test_list_datasets_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_list_datasets"](
        hass, connection, {"id": 11, "room_slug": "studio"}
    )

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 11
    assert payload["datasets"] == [{"id": "ds-1", "room_slug": "studio"}]
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_list_datasets_empty_when_store_missing(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator.dataset_store = None
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_list_datasets"](hass, connection, {"id": 12})

    _, payload = connection.send_result.call_args.args
    assert payload["datasets"] == []


@pytest.mark.asyncio
async def test_get_dataset_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_dataset"](
        hass, connection, {"id": 13, "dataset_id": "ds-42"}
    )

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 13
    assert payload["dataset"] == {"id": "ds-42", "records": []}
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_get_dataset_none_when_store_missing(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator.dataset_store = None
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_get_dataset"](
        hass, connection, {"id": 14, "dataset_id": "missing"}
    )

    _, payload = connection.send_result.call_args.args
    assert payload["dataset"] is None


@pytest.mark.asyncio
async def test_list_experiments_payload_shape(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_list_experiments"](hass, connection, {"id": 15})

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 15
    assert payload["experiments"] == [{"id": "exp-1", "status": "completed"}]
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_list_experiments_empty_when_manager_missing(
    hass: SimpleNamespace,
    coordinator: SimpleNamespace,
    ws_handlers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator.experiment_manager = None
    monkeypatch.setattr(websocket_api, "get_coordinator", lambda _hass: coordinator)

    connection = _connection()
    await ws_handlers["ws_list_experiments"](hass, connection, {"id": 16})

    _, payload = connection.send_result.call_args.args
    assert payload["experiments"] == []
