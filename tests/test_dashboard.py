"""Unit tests for the Lovelace dashboard generator."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.const import DOMAIN
from custom_components.heating_assistant.dashboard import (
    DashboardSpec,
    HeatSourceSpec,
    RoomSpec,
    build_dashboard,
    build_dashboard_from_coordinator,
    dashboard_to_yaml,
    slugify,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_cards(node):
    """Walk the dashboard tree yielding every dict that looks like a card."""
    if isinstance(node, dict):
        if "type" in node:
            yield node
        for v in node.values():
            yield from _iter_cards(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_cards(item)


def _iter_entity_refs(node):
    """Yield every entity_id referenced anywhere in the dashboard."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                yield value
            if key == "entities" and isinstance(value, list):
                for ent in value:
                    if isinstance(ent, str):
                        yield ent
                    elif isinstance(ent, dict) and isinstance(ent.get("entity"), str):
                        yield ent["entity"]
            yield from _iter_entity_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_entity_refs(item)


# ---------------------------------------------------------------------------
# slugify – matches HA's behaviour on names the integration produces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,slug",
    [
        ("Living Room", "living_room"),
        ("Living  Room", "living_room"),
        ("living_room", "living_room"),
        ("Bedroom 1", "bedroom_1"),
        ("kitchen", "kitchen"),
        ("Heating Assistant – Living Room – Temperature Measured",
         "heating_assistant_living_room_temperature_measured"),
        ("--__weird--", "weird"),
    ],
)
def test_slugify_matches_ha_conventions(raw, slug):
    assert slugify(raw) == slug


def test_slugify_handles_empty_string():
    # Slugify must always return a non-empty string so the entity_id is valid.
    assert slugify("") == "_"
    assert slugify("---") == "_"


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


@pytest.fixture
def two_room_spec() -> DashboardSpec:
    return DashboardSpec(
        rooms=(
            RoomSpec(name="Living Room", icon="mdi:sofa"),
            RoomSpec(name="Bedroom", icon="mdi:bed", has_schedule=True),
        ),
        sources=(
            HeatSourceSpec(name="lr_heater", room="Living Room", kind="heat_pump"),
            HeatSourceSpec(name="br_heater", room="Bedroom", kind="electric_heater"),
        ),
    )


def test_dashboard_has_overview_room_diagnostics_settings_views(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    assert dashboard["title"] == "Heating Assistant"
    titles = [v["title"] for v in dashboard["views"]]
    assert titles == ["Overview", "Living Room", "Bedroom", "Diagnostics", "Settings & Services"]


def test_room_views_are_marked_as_subviews(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    views = {v["title"]: v for v in dashboard["views"]}
    assert views["Living Room"]["subview"] is True
    assert views["Bedroom"]["subview"] is True
    assert views["Overview"].get("subview") in (None, False)
    assert views["Diagnostics"].get("subview") in (None, False)


def test_all_views_use_sections_layout(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    for view in dashboard["views"]:
        assert view["type"] == "sections"
        assert isinstance(view["sections"], list) and view["sections"]


def test_room_view_path_uses_slug(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    views = {v["title"]: v for v in dashboard["views"]}
    assert views["Living Room"]["path"] == "living_room"
    assert views["Bedroom"]["path"] == "bedroom"


# ---------------------------------------------------------------------------
# Per-room view contents
# ---------------------------------------------------------------------------


def _room_view(dashboard, title):
    return next(v for v in dashboard["views"] if v["title"] == title)


def test_room_view_contains_thermostat_and_mpc_triplet(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    cards = list(_iter_cards(view))
    types = [c["type"] for c in cards]
    assert "thermostat" in types
    # MPC triplet → three apexcharts on the room view.
    assert sum(1 for t in types if t == "custom:apexcharts-card") >= 3


def test_room_view_temperature_chart_references_canonical_entities(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    refs = set(_iter_entity_refs(view))
    expected = {
        f"climate.{DOMAIN}_living_room_climate",
        f"sensor.{DOMAIN}_living_room_temperature_measured",
        f"sensor.{DOMAIN}_living_room_temperature_filtered",
        f"sensor.{DOMAIN}_living_room_temperature_forecast",
        f"sensor.{DOMAIN}_living_room_setpoint",
        f"sensor.{DOMAIN}_living_room_constraint_upper",
        f"sensor.{DOMAIN}_living_room_constraint_lower",
        f"sensor.{DOMAIN}_living_room_heating_power_measured",
        f"sensor.{DOMAIN}_living_room_heating_power_forecast",
        f"sensor.{DOMAIN}_living_room_solar_gain_measured",
        f"sensor.{DOMAIN}_living_room_solar_gain_forecast",
        f"sensor.{DOMAIN}_outdoor_temperature_measured",
        f"sensor.{DOMAIN}_outdoor_temperature_forecast",
        f"sensor.{DOMAIN}_living_room_model_fit_quality",
        f"sensor.{DOMAIN}_living_room_residual_acf",
        f"sensor.{DOMAIN}_living_room_open_loop_rmse",
        f"sensor.{DOMAIN}_living_room_prediction_error",
        f"sensor.{DOMAIN}_living_room_parameter_confidence",
    }
    missing = expected - refs
    assert not missing, f"Missing entity references on room view: {missing}"


def test_control_card_includes_per_source_action_for_room(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    refs = set(_iter_entity_refs(view))
    # The heat pump in Living Room contributes a control_action series.
    assert f"sensor.{DOMAIN}_lr_heater_control_action" in refs
    # The bedroom heater's series must NOT appear on the living-room view.
    assert f"sensor.{DOMAIN}_br_heater_control_action" not in refs


def test_room_without_schedule_has_no_schedule_section():
    spec = DashboardSpec(
        rooms=(RoomSpec(name="Den", has_schedule=False),),
        sources=(),
    )
    view = _room_view(build_dashboard(spec), "Den")
    headings = [
        c.get("heading")
        for c in _iter_cards(view)
        if c.get("type") == "heading"
    ]
    assert "Schedule" not in headings


def test_room_with_schedule_has_schedule_section(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Bedroom")
    headings = [
        c.get("heading")
        for c in _iter_cards(view)
        if c.get("type") == "heading"
    ]
    assert "Schedule" in headings


# ---------------------------------------------------------------------------
# Overview / diagnostics / settings
# ---------------------------------------------------------------------------


def test_overview_has_one_comfort_tile_per_room(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    overview = next(v for v in dashboard["views"] if v["title"] == "Overview")
    tiles = [c for c in _iter_cards(overview) if c.get("type") == "tile"]
    refs = {t["entity"] for t in tiles}
    assert f"climate.{DOMAIN}_living_room_climate" in refs
    assert f"climate.{DOMAIN}_bedroom_climate" in refs


def test_overview_links_to_room_subviews(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    overview = next(v for v in dashboard["views"] if v["title"] == "Overview")
    nav_paths = []
    for card in _iter_cards(overview):
        tap = card.get("tap_action")
        if isinstance(tap, dict) and tap.get("action") == "navigate":
            nav_paths.append(tap["navigation_path"])
    assert "/heating-assistant/living_room" in nav_paths
    assert "/heating-assistant/bedroom" in nav_paths


def test_diagnostics_view_includes_estimation_buttons_and_per_room_rows(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    diag = next(v for v in dashboard["views"] if v["title"] == "Diagnostics")
    refs = set(_iter_entity_refs(diag))
    assert f"button.{DOMAIN}_estimate_parameters_button" in refs
    assert f"button.{DOMAIN}_reset_parameters_button" in refs
    assert f"sensor.{DOMAIN}_living_room_model_fit_quality" in refs
    assert f"sensor.{DOMAIN}_bedroom_model_fit_quality" in refs
    assert f"sensor.{DOMAIN}_living_room_parameter_confidence" in refs
    assert f"sensor.{DOMAIN}_bedroom_parameter_confidence" in refs


def test_diagnostics_dry_run_button_calls_estimate_ml_with_apply_false(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    diag = next(v for v in dashboard["views"] if v["title"] == "Diagnostics")
    dry_runs = [
        c for c in _iter_cards(diag)
        if c.get("type") == "entity-button"
        and c.get("tap_action", {}).get("service") == f"{DOMAIN}.estimate_parameters_ml"
    ]
    assert dry_runs, "Expected a dry-run estimation button on the diagnostics view"
    assert dry_runs[0]["tap_action"]["service_data"] == {"apply_parameters": False}


def test_diagnostics_open_loop_card_wires_to_service(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    diag = next(v for v in dashboard["views"] if v["title"] == "Diagnostics")
    services = []
    for card in _iter_cards(diag):
        footer = card.get("footer")
        if isinstance(footer, dict):
            for ent in footer.get("entities", []):
                if isinstance(ent, dict):
                    tap = ent.get("tap_action") or {}
                    if tap.get("action") == "call-service":
                        services.append(tap["service"])
    assert f"{DOMAIN}.run_open_loop_simulation" in services
    assert f"{DOMAIN}.analyze_model_fit" in services


def test_settings_view_has_regenerate_dashboard_call(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    settings = next(v for v in dashboard["views"] if v["title"] == "Settings & Services")
    services = [
        ent.get("service")
        for card in _iter_cards(settings)
        for ent in card.get("entities", [])
        if isinstance(ent, dict) and ent.get("type") == "call-service"
    ]
    assert f"{DOMAIN}.regenerate_dashboard" in services


# ---------------------------------------------------------------------------
# Apex-charts contract
# ---------------------------------------------------------------------------


def test_every_forecast_series_uses_data_generator_on_forecast_attribute(two_room_spec):
    """Forecast entities expose a `forecast` attribute; every dashed series
    in the temperature / control / disturbance cards must consume it via
    apexcharts-card's ``data_generator``."""
    dashboard = build_dashboard(two_room_spec)
    forecast_metrics = {
        "temperature_forecast",
        "heating_power_forecast",
        "solar_gain_forecast",
        "outdoor_temperature_forecast",
        "setpoint",
        "constraint_upper",
        "constraint_lower",
    }
    for card in _iter_cards(dashboard):
        if card.get("type") != "custom:apexcharts-card":
            continue
        for series in card.get("series", []):
            entity = series.get("entity", "")
            metric = entity.rsplit("_", 1)[-1]
            metric_long = entity[len(f"sensor.{DOMAIN}_"):] if entity.startswith(f"sensor.{DOMAIN}_") else ""
            if any(m in metric_long for m in forecast_metrics):
                # Either the historical chart relies on extend_to/group_by,
                # or the series renders the forecast array via data_generator.
                has_generator = bool(series.get("data_generator"))
                has_history_fallback = (
                    series.get("extend_to") == "now"
                    or "group_by" in series
                )
                assert has_generator or has_history_fallback, (
                    f"Forecast series for {entity} has neither data_generator "
                    f"nor a history fallback"
                )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_dashboard_with_no_rooms_still_builds():
    spec = DashboardSpec(rooms=(), sources=())
    dashboard = build_dashboard(spec)
    titles = [v["title"] for v in dashboard["views"]]
    assert titles == ["Overview", "Diagnostics", "Settings & Services"]


def test_build_from_coordinator_extracts_rooms_and_sources():
    class FakeSchedule:
        is_empty = False

    class FakeModel:
        room_names = ["Office", "Hallway"]

    class FakeSource:
        def __init__(self, name, room):
            self.name = name
            self.room = room

    class FakeHeatPump(FakeSource):
        pass

    FakeHeatPump.__name__ = "HeatPump"  # type hint for kind extraction

    class FakeCoordinator:
        model = FakeModel()
        heat_sources = [FakeHeatPump("office_hp", "Office")]
        _room_schedule = {"Office": FakeSchedule()}

    dashboard = build_dashboard_from_coordinator(FakeCoordinator())
    titles = [v["title"] for v in dashboard["views"]]
    assert "Office" in titles
    assert "Hallway" in titles
    office = next(v for v in dashboard["views"] if v["title"] == "Office")
    headings = [c.get("heading") for c in _iter_cards(office) if c.get("type") == "heading"]
    assert "Schedule" in headings  # Office had a non-empty schedule
    hallway = next(v for v in dashboard["views"] if v["title"] == "Hallway")
    hallway_headings = [c.get("heading") for c in _iter_cards(hallway) if c.get("type") == "heading"]
    assert "Schedule" not in hallway_headings


def test_dashboard_to_yaml_round_trips(two_room_spec):
    yaml_module = pytest.importorskip("yaml")
    dashboard = build_dashboard(two_room_spec)
    yaml_text = dashboard_to_yaml(dashboard)
    assert isinstance(yaml_text, str) and yaml_text.startswith("title:")
    loaded = yaml_module.safe_load(yaml_text)
    assert loaded == dashboard


def test_dashboard_to_yaml_preserves_view_order(two_room_spec):
    yaml_module = pytest.importorskip("yaml")
    yaml_text = dashboard_to_yaml(build_dashboard(two_room_spec))
    loaded = yaml_module.safe_load(yaml_text)
    titles = [v["title"] for v in loaded["views"]]
    assert titles == ["Overview", "Living Room", "Bedroom", "Diagnostics", "Settings & Services"]


def test_build_from_coordinator_handles_missing_schedule_map():
    class FakeModel:
        room_names = ["A"]

    class FakeCoordinator:
        model = FakeModel()
        heat_sources = []
        # _room_schedule attribute deliberately omitted

    dashboard = build_dashboard_from_coordinator(FakeCoordinator())
    assert dashboard["title"] == "Heating Assistant"
