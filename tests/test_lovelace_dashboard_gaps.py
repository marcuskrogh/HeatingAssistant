"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from types import SimpleNamespace

import pytest

from heatingassistant.engine.const import DOMAIN
from custom_components.heating_assistant.lovelace_dashboard import (
    DashboardSpec,
    HeatSourceSpec,
    RoomSpec,
    _heat_sources_card,
    _overview_power_chart,
    _overview_temperature_chart,
    _room_view,
    _system_weather_strip,
    build_dashboard_from_coordinator,
    build_dashboard_variant_from_coordinator,
)


def _two_room_spec(*, has_price: bool = False) -> DashboardSpec:
    return DashboardSpec(
        rooms=(
            RoomSpec(name="Living Room", icon="mdi:sofa"),
            RoomSpec(name="Bedroom", icon="mdi:bed"),
        ),
        sources=(
            HeatSourceSpec(
                name="lr_heater",
                room="Living Room",
                kind="heat_pump",
                entity_id="climate.lr_hp",
            ),
            HeatSourceSpec(name="br_heater", room="Bedroom", kind="electric_heater"),
        ),
        history_hours=12.0,
        forecast_hours=4.0,
        has_price=has_price,
    )


def test_overview_temperature_chart_builds_series_per_room():
    chart = _overview_temperature_chart(_two_room_spec())
    assert chart["type"] == "custom:apexcharts-card"
    assert chart["header"]["title"] == "Room Temperatures"
    assert chart["graph_span"] == "16h"
    assert len(chart["series"]) == 8  # 4 series × 2 rooms
    first_room_series = chart["series"][0]
    assert first_room_series["entity"].endswith("temperature_filtered")
    assert first_room_series["show"]["in_header"] is True
    forecast_series = chart["series"][1]
    assert "temperature_forecast" in forecast_series["entity"]
    assert "data_generator" in forecast_series
    assert forecast_series["stroke_dash"] == 4


def test_overview_power_chart_builds_stacked_area_series():
    chart = _overview_power_chart(_two_room_spec())
    assert chart["header"]["title"] == "Power – All Rooms"
    assert len(chart["series"]) == 4  # measured + forecast per room
    measured = chart["series"][0]
    assert measured["type"] == "area"
    assert measured["curve"] == "stepline"
    assert "heating_power_measured" in measured["entity"]
    plan = chart["series"][1]
    assert "data_generator" in plan
    assert plan["show"]["in_header"] is True


def test_system_weather_strip_includes_outdoor_and_solar_series():
    chart = _system_weather_strip(_two_room_spec())
    assert chart["header"]["title"] == "Outdoor & Solar"
    entities = [s.get("entity", "") for s in chart["series"]]
    assert any("outdoor_temperature_measured" in e for e in entities)
    assert any("outdoor_temperature_forecast" in e for e in entities)
    solar_entities = [e for e in entities if "solar_gain" in e]
    assert len(solar_entities) == 4  # measured + forecast per room


def test_heat_sources_card_returns_none_without_entity_ids():
    spec = DashboardSpec(
        rooms=(RoomSpec(name="Office"),),
        sources=(HeatSourceSpec(name="office_hp", room="Office", kind="heat_pump"),),
    )
    assert _heat_sources_card(RoomSpec(name="Office"), spec) is None


def test_heat_sources_card_lists_physical_devices_in_room_view():
    spec = _two_room_spec()
    view = _room_view(RoomSpec(name="Living Room"), spec)
    entities_cards = [
        card
        for section in view["sections"]
        for card in section.get("cards", [])
        if card.get("type") == "entities" and card.get("title") == "Heating device"
    ]
    assert len(entities_cards) == 1
    card = entities_cards[0]
    assert card["icon"] == "mdi:heat-pump"
    assert card["entities"] == [{"entity": "climate.lr_hp", "name": "lr_heater"}]


class _FakeSchedule:
    def __init__(self, *, is_empty: bool = False):
        self.is_empty = is_empty


class _FakeModel:
    room_names = ["Office", "Bedroom"]


class _FakeSource:
    def __init__(self, name: str, room: str, *, heater_entity: str | None = None):
        self.name = name
        self.room = room
        self.heater_entity = heater_entity


class _FakeHeatPump(_FakeSource):
    pass


class _FakeElectricHeater(_FakeSource):
    pass


FakeHeatPump = type("HeatPump", (_FakeHeatPump,), {})
FakeElectricHeater = type("ElectricHeater", (_FakeElectricHeater,), {})
FakeUnknownSource = type("WoodStove", (_FakeSource,), {})


def test_build_from_coordinator_maps_source_kinds_and_schedule_flags():
    class FakeCoordinator:
        model = _FakeModel()
        heat_sources = [
            FakeHeatPump("office_hp", "Office", heater_entity="climate.office"),
            FakeElectricHeater("bed_radiator", "Bedroom"),
            FakeUnknownSource("backup", "Office"),
        ]
        _room_schedule = {
            "Office": _FakeSchedule(),
            "Bedroom": _FakeSchedule(is_empty=True),
        }
        _horizon = 12
        dt = 1800.0
        _price_entity = "sensor.nordpool"

    dashboard = build_dashboard_from_coordinator(FakeCoordinator())
    assert dashboard["title"] == "Heating Assistant"
    titles = [v["title"] for v in dashboard["views"]]
    assert "Office" in titles
    assert "Bedroom" in titles

    office_view = next(v for v in dashboard["views"] if v["title"] == "Office")
    headings = [
        card.get("heading")
        for section in office_view["sections"]
        for card in section.get("cards", [])
        if card.get("type") == "heading"
    ]
    assert "Schedule" in headings
    bedroom_view = next(v for v in dashboard["views"] if v["title"] == "Bedroom")
    bedroom_headings = [
        card.get("heading")
        for section in bedroom_view["sections"]
        for card in section.get("cards", [])
        if card.get("type") == "heading"
    ]
    assert "Schedule" not in bedroom_headings

    price_spec = DashboardSpec(
        rooms=(RoomSpec(name="Office"),),
        sources=(
            HeatSourceSpec(
                name="office_hp",
                room="Office",
                kind="heat_pump",
                entity_id="climate.office",
            ),
        ),
        has_price=True,
    )
    price_view = _room_view(RoomSpec(name="Office"), price_spec)
    power_headings = [
        card.get("heading")
        for section in price_view["sections"]
        for card in section.get("cards", [])
        if card.get("type") == "heading"
    ]
    assert "Power & electricity price" in power_headings


def test_build_from_coordinator_defaults_forecast_hours_without_horizon():
    class FakeCoordinator:
        model = SimpleNamespace(room_names=["Office"])
        heat_sources = []
        _room_schedule = {}

    dashboard = build_dashboard_from_coordinator(FakeCoordinator())
    assert dashboard["title"] == "Heating Assistant"


def test_build_variant_from_coordinator_industrial_includes_first_room_fit():
    class FakeCoordinator:
        model = _FakeModel()
        heat_sources = [FakeHeatPump("office_hp", "Office")]
        _room_schedule = {}
        _horizon = None
        _update_interval_s = None

    dashboard = build_dashboard_variant_from_coordinator(
        FakeCoordinator(), variant="industrial"
    )
    overview = next(v for v in dashboard["views"] if v["title"] == "Overview")
    entities = [
        ent
        for section in overview["sections"]
        for card in section.get("cards", [])
        if card.get("type") == "entities"
        for ent in card.get("entities", [])
        if isinstance(ent, dict)
    ]
    fit_entities = [
        ent for ent in entities if ent.get("name", "").endswith("Fit R²")
    ]
    assert len(fit_entities) == 1
    assert f"sensor.{DOMAIN}_office_model_fit_quality" in fit_entities[0]["entity"]


def test_build_variant_from_coordinator_maps_electric_heater_kind():
    class FakeCoordinator:
        model = SimpleNamespace(room_names=["Office"])
        heat_sources = [FakeElectricHeater("office_rad", "Office")]
        _room_schedule = {}

    dashboard = build_dashboard_variant_from_coordinator(FakeCoordinator())
    assert dashboard["title"] == "Heating Assistant"
