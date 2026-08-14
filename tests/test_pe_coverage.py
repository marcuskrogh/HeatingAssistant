"""SWD-337: next-fit PE data-coverage categoriser."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from heatingassistant.app.sysid_services import _room_has_contact, handle_get_pe_coverage
from heatingassistant.engine.estimation.coverage import (
    CLOSED_RECOMMEND_S,
    OPEN_RECOMMEND_S,
    STATUS_CHECKED,
    STATUS_NA,
    STATUS_UNCHECKED,
    categorise_pe_coverage,
)
from heatingassistant.engine.heat_sources import ElectricHeater


pytestmark = pytest.mark.unit


def _heater(room: str = "studio") -> ElectricHeater:
    return ElectricHeater("h", room, 3000.0)


def _history(
    n: int,
    *,
    dt: float = 900.0,
    room: str = "studio",
    open_range=None,
    u_on: bool = True,
    solar_amp: float = 0.0,
) -> list[dict]:
    t0 = 1_700_000_000.0
    recs = []
    for i in range(n):
        is_open = open_range is not None and open_range[0] <= i < open_range[1]
        recs.append(
            {
                "y": [20.0],
                "u": [0.6 if (i % 4 < 2 and u_on) else 0.0],
                "d_outdoor": 2.0,
                "d_solar": {room: max(0.0, solar_amp * (i % 8) / 7.0)},
                "timestamp": t0 + dt * i,
                "window_open": {room: bool(is_open)},
            }
        )
    return recs


def _by_id(result: dict) -> dict:
    return {cat["id"]: cat for cat in result["categories"]}


def test_closed_window_checked_after_twelve_hours():
    dt = 900.0
    n = int(CLOSED_RECOMMEND_S / dt) + 2
    result = categorise_pe_coverage(
        _history(n, dt=dt, u_on=False),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=dt,
        has_contact_entity=True,
        min_history_steps=10,
    )
    closed = _by_id(result)["closed_window_envelope"]
    assert closed["status"] == STATUS_CHECKED
    assert closed["have_s"] >= CLOSED_RECOMMEND_S
    assert closed["recommend_s"] == CLOSED_RECOMMEND_S


def test_closed_window_unchecked_when_short():
    result = categorise_pe_coverage(
        _history(20),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=True,
        min_history_steps=10,
    )
    closed = _by_id(result)["closed_window_envelope"]
    assert closed["status"] == STATUS_UNCHECKED
    assert closed["have_s"] < CLOSED_RECOMMEND_S


def test_heater_excitation_follows_duty_cycle_gate():
    excited = categorise_pe_coverage(
        _history(40, u_on=True),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=True,
        min_history_steps=10,
    )
    quiet = categorise_pe_coverage(
        _history(40, u_on=False),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=True,
        min_history_steps=10,
    )
    assert _by_id(excited)["heater_excitation"]["status"] == STATUS_CHECKED
    assert _by_id(quiet)["heater_excitation"]["status"] == STATUS_UNCHECKED


def test_solar_variation_follows_solar_gate():
    varied = categorise_pe_coverage(
        _history(40, solar_amp=400.0),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=True,
        min_history_steps=10,
    )
    flat = categorise_pe_coverage(
        _history(40, solar_amp=0.0),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=True,
        min_history_steps=10,
    )
    assert _by_id(varied)["solar_variation"]["status"] == STATUS_CHECKED
    assert _by_id(flat)["solar_variation"]["status"] == STATUS_UNCHECKED


def test_open_contact_checked_after_thirty_minutes():
    dt = 900.0
    n_open = int(OPEN_RECOMMEND_S / dt) + 1
    result = categorise_pe_coverage(
        _history(20, dt=dt, open_range=(0, n_open)),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=dt,
        has_contact_entity=True,
        min_history_steps=10,
    )
    open_cat = _by_id(result)["open_contact"]
    assert open_cat["status"] == STATUS_CHECKED
    assert open_cat["have_s"] >= OPEN_RECOMMEND_S
    assert open_cat["recommend_s"] == OPEN_RECOMMEND_S


def test_open_contact_na_without_entity():
    result = categorise_pe_coverage(
        _history(20, open_range=(0, 10)),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=False,
        min_history_steps=10,
    )
    open_cat = _by_id(result)["open_contact"]
    assert open_cat["status"] == STATUS_NA
    assert open_cat["have_s"] is None
    assert open_cat["recommend_s"] is None
    assert "No window or door contact" in open_cat["hint"]


def test_open_contact_unchecked_with_entity_but_little_open_time():
    result = categorise_pe_coverage(
        _history(20, open_range=(0, 1)),
        room_name="studio",
        room_names=["studio"],
        sources=[_heater()],
        dt=900.0,
        has_contact_entity=True,
        min_history_steps=10,
    )
    open_cat = _by_id(result)["open_contact"]
    assert open_cat["status"] == STATUS_UNCHECKED
    assert open_cat["have_s"] == pytest.approx(900.0)


def test_room_has_contact_reads_window_sensors():
    runtime = SimpleNamespace(
        options={
            "rooms": [
                {"name": "Living Room", "window_sensors": ["binary_sensor.door"]},
                {"name": "Studio", "window_sensors": []},
            ]
        }
    )
    assert _room_has_contact(runtime, "Living Room") is True
    assert _room_has_contact(runtime, "Studio") is False
    assert _room_has_contact(runtime, "Unknown") is False


@pytest.mark.asyncio
async def test_handle_get_pe_coverage_uses_buffer_history(monkeypatch):
    dt = 900.0
    t0 = 1_800_000_000.0
    history = _history(16, dt=dt)
    for rec in history:
        rec["timestamp"] = t0 + (rec["timestamp"] - history[0]["timestamp"])

    runtime = SimpleNamespace(
        options={
            "update_interval": dt,
            "rooms": [
                {
                    "name": "studio",
                    "window_sensors": ["binary_sensor.studio_window"],
                }
            ],
        },
        _history_buffer=list(history),
        id_history_store=None,
        control_engine=SimpleNamespace(
            model=SimpleNamespace(room_names=["studio"]),
            heat_sources=[_heater()],
        ),
    )

    async def _fake_resolve(runtime_arg, **kwargs):
        assert runtime_arg is runtime
        return list(history)

    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.resolve_history",
        _fake_resolve,
    )

    result = await handle_get_pe_coverage(runtime, {"room_name": "studio"})
    assert result["room"] == "studio"
    assert result["n_steps"] == 16
    ids = [cat["id"] for cat in result["categories"]]
    assert ids == [
        "closed_window_envelope",
        "heater_excitation",
        "solar_variation",
        "open_contact",
    ]


def test_pe_page_sources_include_recommended_data_checklist():
    root = Path(__file__).resolve().parents[1]
    static = root / "heatingassistant" / "app" / "static" / "js"
    datasets = (static / "identification" / "sysid-datasets.js").read_text(encoding="utf-8")
    detail = (static / "identification" / "sysid-detail.js").read_text(encoding="utf-8")
    conn = (static / "ha-connection.js").read_text(encoding="utf-8")
    css = (
        root / "heatingassistant" / "app" / "static" / "css" / "pages" / "climate-card.css"
    ).read_text(encoding="utf-8")
    assert "Recommended data" in datasets
    assert "getPeCoverage" in datasets
    assert "pe-coverage-list" in datasets
    assert "param-ua-open" in detail
    assert "refreshCoverage" in detail
    assert "heating_assistant/get_pe_coverage" in conn
    assert ".pe-coverage" in css
