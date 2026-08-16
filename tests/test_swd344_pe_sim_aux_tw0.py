"""SWD-344: PE simulate aux series, window Tw0, category copy."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from heatingassistant.app.sysid_services import (
    _attach_aux_and_tw0,
    _iso_time,
    _resolve_simulation_t_wall,
    handle_get_pe_inputs,
    handle_run_open_loop_simulation,
)
from heatingassistant.engine.history.plot_series import identification_aux_series
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.simulation.sysid_helpers import (
    open_loop_t_wall_initial_dict,
    optimal_t_wall_for_window,
)
from heatingassistant.engine.thermal_model import HouseModel, Room


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = ROOT / "heatingassistant" / "app" / "static" / "js"


def _heater(room: str = "living_room", max_power: float = 2000.0) -> ElectricHeater:
    return ElectricHeater("h", room, max_power)


def _history(
    n: int = 16,
    *,
    room: str = "living_room",
    t0: float = 1_800_000_000.0,
    dt: float = 900.0,
    u_on: float = 0.4,
) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "y": [20.0 + 0.05 * i],
                "u": [u_on if i % 3 else 0.0],
                "d_outdoor": 8.0,
                "d_solar": {room: 50.0 + 10 * (i % 4)},
                "timestamp": t0 + dt * i,
            }
        )
    return rows


def _runtime(history: list[dict], room: str = "living_room"):
    heater = _heater(room)
    return SimpleNamespace(
        options={"update_interval": 900.0, "rooms": [{"name": room}]},
        _history_buffer=list(history),
        id_history_store=None,
        control_engine=SimpleNamespace(
            model=SimpleNamespace(room_names=[room], rooms={room: Room(room, 3.0e5, 0.05)}),
            heat_sources=[heater],
        ),
    )


def test_identification_aux_series_from_id_history():
    series = identification_aux_series(_history(), [_heater()], "living_room")
    assert len(series["heating_power"]) == 16
    assert len(series["outdoor_temp"]) == 16
    assert len(series["solar_gain"]) == 16
    assert series["outdoor_temp"][0]["value"] == pytest.approx(8.0)
    assert series["solar_gain"][3]["value"] == pytest.approx(80.0)
    assert series["heating_power"][1]["value"] == pytest.approx(800.0)
    assert _iso_time(_history()[0]["timestamp"]) == series["heating_power"][0]["time"]


def test_identification_aux_series_treats_percent_u_as_fraction():
    hist = [
        {
            "y": [20.0],
            "u": [50.0],
            "d_outdoor": 5.0,
            "d_solar": {"living_room": 0.0},
            "timestamp": 1_800_000_000.0,
        }
    ]
    series = identification_aux_series(hist, [_heater()], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(1000.0)


def test_identification_aux_series_accepts_scalar_u():
    hist = [
        {
            "y": [20.0],
            "u": 0.5,
            "d_outdoor": 5.0,
            "d_solar": {"living_room": 12.0},
            "timestamp": 1_800_000_000.0,
        }
    ]
    series = identification_aux_series(hist, [_heater()], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(1000.0)
    assert series["solar_gain"][0]["value"] == pytest.approx(12.0)


def test_optimal_t_wall_for_window_returns_finite_room_map():
    room = Room("living_room", 3.0e5, 0.05, temperature=20.0)
    model = HouseModel([room])
    hist = _history(n=24)
    estimated = optimal_t_wall_for_window(hist, model, [_heater()], 900.0)
    assert "living_room" in estimated
    assert 5.0 <= estimated["living_room"] <= 40.0


def test_resolve_simulation_t_wall_honors_lock():
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(control_engine=SimpleNamespace(model=HouseModel([room])))
    hist = _history()
    room_params = {"living_room": {"t_wall_initial": 19.5}}
    val = _resolve_simulation_t_wall(
        hist,
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        room_params,
        {"t_wall_locked": True},
    )
    assert val == {"living_room": pytest.approx(19.5)}
    assert room_params["living_room"]["t_wall_initial"] == pytest.approx(19.5)


def test_resolve_simulation_t_wall_optimizes_when_unlocked(monkeypatch):
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(control_engine=SimpleNamespace(model=HouseModel([room])))
    hist = _history(n=20)
    room_params: dict[str, dict[str, float]] = {}

    def _fake_opt(*_a, **_k):
        return {"living_room": 22.4}

    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        _fake_opt,
    )
    val = _resolve_simulation_t_wall(
        hist,
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        room_params,
        {"t_wall_locked": False},
    )
    assert val == {"living_room": pytest.approx(22.4)}
    assert room_params["living_room"]["t_wall_initial"] == pytest.approx(22.4)


def test_attach_aux_and_tw0_on_open_loop_payload():
    hist = _history()
    payload = {"living_room": {"rmse": 0.2}}
    _attach_aux_and_tw0(payload, hist, [_heater()], {"living_room": 22.4})
    assert payload["living_room"]["t_wall_initial"] == pytest.approx(22.4)
    assert payload["living_room"]["heating_power"]
    assert payload["living_room"]["outdoor_temp"]
    assert payload["living_room"]["solar_gain"]


def test_open_loop_t_wall_initial_dict_uses_room_name():
    room_params = {"living_room": {"t_wall_initial": 23.1}}
    d = open_loop_t_wall_initial_dict(room_params, ["living_room"])
    assert d == {"living_room": 23.1}


def test_handle_get_pe_inputs_returns_identification_series(monkeypatch):
    hist = _history()
    runtime = _runtime(hist)

    async def _fake_resolve(runtime_arg, **kwargs):
        assert runtime_arg is runtime
        assert kwargs.get("dataset_id") == "ds-1"
        return list(hist)

    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.resolve_history",
        _fake_resolve,
    )
    result = asyncio.run(
        handle_get_pe_inputs(
            runtime,
            {"room_name": "living_room", "dataset_id": "ds-1"},
        )
    )
    assert len(result["heating_power"]) == 16
    assert result["outdoor_temp"][0]["value"] == pytest.approx(8.0)
    assert result["heating_power"][1]["value"] == pytest.approx(800.0)


def test_handle_get_pe_inputs_applies_heater_scales():
    hist = _history()
    runtime = _runtime(hist)
    result = asyncio.run(
        handle_get_pe_inputs(
            runtime,
            {"room_name": "living_room", "heater_scales": {"h": 0.5}},
        )
    )
    assert result["heating_power"][1]["value"] == pytest.approx(400.0)


def test_handle_get_pe_inputs_unknown_room_raises():
    runtime = SimpleNamespace(
        options={"rooms": []},
        control_engine=SimpleNamespace(
            model=SimpleNamespace(room_names=["studio"]),
            heat_sources=[],
        ),
    )
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(handle_get_pe_inputs(runtime, {}))


def test_open_loop_dataset_path_applies_optimal_tw0(monkeypatch):
    hist = _history(n=20)
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(
        options={"update_interval": 900.0, "rooms": [{"name": "living_room"}]},
        _history_buffer=[],
        id_history_store=None,
        sysid_results={},
        open_loop_results={},
        _last_identified_heater_scales={},
        control_engine=SimpleNamespace(
            model=HouseModel([room]),
            heat_sources=[_heater()],
            _controller=SimpleNamespace(_system=object()),
        ),
    )

    async def _fake_resolve(_runtime, **kwargs):
        assert kwargs.get("dataset_id") == "ds-stored"
        return list(hist)

    def _fake_opt(history, _model, _sources, _dt, room_params=None):
        assert history == hist
        return {"living_room": 18.7}

    def _fake_ol(history, system, room_names, n_rooms, dt, segment_length, t_wall_initial):
        assert t_wall_initial["living_room"] == pytest.approx(18.7)
        return {
            "per_room": {
                "living_room": {"rmse": 0.1, "mae": 0.1, "simulation": []},
            }
        }

    async def _fake_rmse(*_a, **_k):
        return {"living_room": {"4h": 0.1}}

    monkeypatch.setattr("heatingassistant.app.sysid_services.resolve_history", _fake_resolve)
    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window", _fake_opt
    )
    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.compute_open_loop_predictions", _fake_ol
    )
    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.compute_open_loop_rmse_by_horizon",
        _fake_rmse,
    )
    result = asyncio.run(
        handle_run_open_loop_simulation(
            runtime, {"room_name": "living_room", "dataset_id": "ds-stored"}
        )
    )
    assert "error" not in result
    assert runtime.open_loop_results["living_room"]["t_wall_initial"] == pytest.approx(18.7)


def test_pe_guides_are_plain_dataset_requirements():
    datasets = (STATIC_JS / "identification" / "sysid-datasets.js").read_text(
        encoding="utf-8"
    )
    detail = (STATIC_JS / "identification" / "sysid-detail.js").read_text(
        encoding="utf-8"
    )
    conn = (STATIC_JS / "ha-connection.js").read_text(encoding="utf-8")
    guides_block = datasets.split("const PE_GUIDES = {", 1)[1].split("};", 1)[0]
    assert "<ol>" not in guides_block
    assert "Save Current Window" not in guides_block
    assert "Apply Parameters" not in guides_block
    assert "why:" not in guides_block
    assert "do:" not in guides_block
    assert "store:" not in guides_block
    assert "windows and doors kept shut" in datasets
    assert "heater both off and on" in datasets
    assert "changing sunlight" in datasets
    assert "window or door actually open" in datasets
    assert "no window or door contact configured" in datasets
    assert "getPeInputs" in detail
    assert "t_wall_locked" in detail
    assert "applySimulatedTw0" in detail
    assert "refreshAuxFromWindow" in detail
    assert "tWallInitialInput.value = '—'" not in detail
    assert "onAuxRefresh" not in detail
    assert "heaterScales" in detail
    assert "heating_assistant/get_pe_inputs" in conn
    assert "opts.datasetIds" in conn
    assert "heater_scales" in conn
