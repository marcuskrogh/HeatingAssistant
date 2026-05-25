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
        # The climate entity drops the ``_climate`` suffix on the
        # registry-assigned entity_id (HA slugifies ``_attr_name``, which
        # is "Heating Assistant – Living Room").
        f"climate.{DOMAIN}_living_room",
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


def test_room_temperature_chart_plots_schedule_driven_setpoint_and_constraints(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    temp_card = next(
        c for c in _iter_cards(view)
        if c.get("type") == "custom:apexcharts-card"
        and c.get("header", {}).get("title") == "Living Room – Temperature"
    )
    series = temp_card["series"]
    setpoint_forecast = next(
        s for s in series
        if s.get("name") == "Setpoint" and s.get("data_generator")
    )
    constraint_upper = next(
        s for s in series
        if s.get("name") == "Constraints"
        and "constraint_upper" in (s.get("data_generator") or "")
    )
    constraint_lower = next(
        s for s in series
        if s.get("name") == "Constraints"
        and "constraint_lower" in (s.get("data_generator") or "")
    )
    assert "setpoint" in setpoint_forecast["data_generator"]
    assert "constraint_upper" in constraint_upper["data_generator"]
    assert "constraint_lower" in constraint_lower["data_generator"]


def test_room_temperature_chart_shades_outside_comfort_region_without_constraint_lines(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    temp_card = next(
        c for c in _iter_cards(view)
        if c.get("type") == "custom:apexcharts-card"
        and c.get("header", {}).get("title") == "Living Room – Temperature"
    )
    constraints = [
        s for s in temp_card["series"]
        if s.get("name") == "Constraints"
    ]
    assert len(constraints) == 3

    upper_shade = next(
        s for s in constraints
        if s.get("color") == "#E53935"
        and "constraint_upper" in (s.get("data_generator") or "")
        and "constraint_lower" in (s.get("data_generator") or "")
    )
    corridor_clear = next(
        s for s in constraints
        if s.get("color") == "var(--card-background-color)"
    )
    lower_shade = next(
        s for s in constraints
        if s.get("color") == "#E53935"
        and "constraint_lower" in (s.get("data_generator") or "")
        and "constraint_upper" not in (s.get("data_generator") or "")
    )

    assert upper_shade.get("type") == "area"
    assert corridor_clear.get("type") == "area"
    assert lower_shade.get("type") == "area"
    assert upper_shade.get("stroke_width") == 0
    assert corridor_clear.get("stroke_width") == 0
    assert lower_shade.get("stroke_width") == 0


def test_room_temperature_chart_hides_labels_below_plot(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    temp_card = next(
        c for c in _iter_cards(view)
        if c.get("type") == "custom:apexcharts-card"
        and c.get("header", {}).get("title") == "Living Room – Temperature"
    )
    apex_config = temp_card.get("apex_config", {})
    assert apex_config.get("xaxis", {}).get("labels", {}).get("show") is False
    assert "legend" not in apex_config


def test_room_view_does_not_reference_legacy_climate_suffix(two_room_spec):
    """Regression guard for the entity_id bug: the climate card must use
    ``climate.heating_assistant_living_room`` (HA's slug of the name), not
    ``climate.heating_assistant_living_room_climate`` (the unique_id)."""
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    refs = set(_iter_entity_refs(view))
    assert f"climate.{DOMAIN}_living_room_climate" not in refs
    assert f"climate.{DOMAIN}_living_room" in refs


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
    assert f"climate.{DOMAIN}_living_room" in refs
    assert f"climate.{DOMAIN}_bedroom" in refs


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


def _iter_call_service_rows(node):
    """Yield every ``call-service`` row dict inside ``entities`` lists."""
    if isinstance(node, dict):
        if node.get("type") == "call-service" and node.get("service"):
            yield node
        for v in node.values():
            yield from _iter_call_service_rows(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_call_service_rows(item)


def _iter_button_action_cards(node):
    """Yield every standalone button card with a call-service tap_action."""
    if isinstance(node, dict):
        if (
            node.get("type") == "button"
            and isinstance(node.get("tap_action"), dict)
            and node["tap_action"].get("action") == "call-service"
            and node["tap_action"].get("service")
        ):
            yield node
        for v in node.values():
            yield from _iter_button_action_cards(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_button_action_cards(item)


def test_diagnostics_view_includes_estimation_buttons_and_per_room_rows(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    diag = next(v for v in dashboard["views"] if v["title"] == "Diagnostics")
    refs = set(_iter_entity_refs(diag))
    # Buttons drop the ``_button`` unique-id suffix — the entity_id is the
    # slug of ``_attr_name`` (no ``_button``).
    assert f"button.{DOMAIN}_estimate_parameters" in refs
    assert f"button.{DOMAIN}_reset_parameters" in refs
    assert f"sensor.{DOMAIN}_living_room_model_fit_quality" in refs
    assert f"sensor.{DOMAIN}_bedroom_model_fit_quality" in refs
    assert f"sensor.{DOMAIN}_living_room_parameter_confidence" in refs
    assert f"sensor.{DOMAIN}_bedroom_parameter_confidence" in refs


def test_diagnostics_view_does_not_reference_legacy_button_suffix(two_room_spec):
    """Regression guard for the button entity_id bug."""
    diag = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Diagnostics"
    )
    refs = set(_iter_entity_refs(diag))
    assert f"button.{DOMAIN}_estimate_parameters_button" not in refs
    assert f"button.{DOMAIN}_reset_parameters_button" not in refs


def test_diagnostics_dry_run_row_calls_estimate_ml_with_apply_false(two_room_spec):
    diag = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Diagnostics"
    )
    buttons = [
        card for card in _iter_button_action_cards(diag)
        if card["tap_action"]["service"] == f"{DOMAIN}.estimate_parameters_ml"
    ]
    assert buttons, "Expected a dry-run estimation button card on the diagnostics view"
    assert buttons[0]["tap_action"].get("service_data") == {"apply_parameters": False}


def test_diagnostics_open_loop_card_wires_services_via_entity_rows(two_room_spec):
    """Open-loop actions are exposed as standalone button cards (not as
    ``call-service`` rows inside an entities card, which newer HA rejects)."""
    diag = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Diagnostics"
    )
    services = {card["tap_action"]["service"] for card in _iter_button_action_cards(diag)}
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


def test_overview_lists_energy_total_per_source(two_room_spec):
    overview = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Overview"
    )
    refs = set(_iter_entity_refs(overview))
    assert f"sensor.{DOMAIN}_lr_heater_energy_total" in refs
    assert f"sensor.{DOMAIN}_br_heater_energy_total" in refs


def test_room_view_includes_open_loop_chart(two_room_spec):
    view = _room_view(build_dashboard(two_room_spec), "Living Room")
    # The open-loop chart uses a data_generator over the simulation attribute.
    generators = [
        s.get("data_generator", "")
        for card in _iter_cards(view)
        if card.get("type") == "custom:apexcharts-card"
        for s in card.get("series", [])
    ]
    assert any("attributes.simulation" in g for g in generators)


def test_diagnostics_view_offers_loglik_slice_buttons_per_room(two_room_spec):
    """Each room gets its own standalone button card for compute_loglik_slice."""
    diag = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Diagnostics"
    )
    rooms_covered = {
        card["tap_action"]["service_data"]["room_name"]
        for card in _iter_button_action_cards(diag)
        if card["tap_action"]["service"] == f"{DOMAIN}.compute_loglik_slice"
        and isinstance(card["tap_action"].get("service_data"), dict)
    }
    assert rooms_covered == {"Living Room", "Bedroom"}


def test_diagnostics_view_renders_history_markdown(two_room_spec):
    diag = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Diagnostics"
    )
    markdowns = [c for c in _iter_cards(diag) if c.get("type") == "markdown"]
    assert any(
        "estimation_history" in m.get("content", "") for m in markdowns
    )


def test_no_chart_series_uses_unsupported_scatter_type(two_room_spec):
    """The installed apexcharts-card doesn't accept ``type: scatter`` series –
    measurements are drawn as a zero-stroke line with raw markers instead."""
    dashboard = build_dashboard(two_room_spec)
    for card in _iter_cards(dashboard):
        if card.get("type") != "custom:apexcharts-card":
            continue
        for series in card.get("series", []):
            assert series.get("type") != "scatter", (
                f"Series {series.get('name')!r} on card "
                f"{card.get('header', {}).get('title')!r} uses unsupported "
                "type: scatter"
            )


def test_forecast_series_use_readme_field_names(two_room_spec):
    """Forecast attribute fields are ``time``/``temperature``/``setpoint``/
    ``heating_power``/``outdoor_temp``/``solar_gain``/``constraint_*`` –
    every data_generator must read those, not the legacy
    ``timestamp``/``value`` shape."""
    dashboard = build_dashboard(two_room_spec)
    for card in _iter_cards(dashboard):
        if card.get("type") != "custom:apexcharts-card":
            continue
        for series in card.get("series", []):
            gen = series.get("data_generator")
            if not gen:
                continue
            # New (correct) shape uses ``f.time`` from the iterator.
            assert "new Date(f.time)" in gen or "new Date(p.time)" in gen, (
                f"data_generator on series {series.get('name')!r} does not use "
                f"the README's f.time/p.time field: {gen[:80]!r}"
            )
            # Old (broken) shape used ``p.timestamp`` / ``p.value`` — must
            # not regress.
            assert ".timestamp" not in gen, gen
            assert "p.value" not in gen, gen


def test_overview_has_two_all_rooms_charts(two_room_spec):
    """The overview must contain one temperature chart and one power chart
    that cover all rooms (per user feedback: keep rooms together, don't
    fragment the overview)."""
    overview = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Overview"
    )
    apex_titles = [
        c["header"]["title"]
        for c in _iter_cards(overview)
        if c.get("type") == "custom:apexcharts-card" and c.get("header")
    ]
    assert any("Room Temperatures" in t for t in apex_titles)
    assert any("Power" in t for t in apex_titles)


def test_overview_temperature_chart_covers_every_room(two_room_spec):
    overview = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Overview"
    )
    temp_card = next(
        c for c in _iter_cards(overview)
        if c.get("type") == "custom:apexcharts-card"
        and c.get("header", {}).get("title") == "Room Temperatures"
    )
    entities = {s["entity"] for s in temp_card["series"]}
    assert f"sensor.{DOMAIN}_living_room_temperature_filtered" in entities
    assert f"sensor.{DOMAIN}_bedroom_temperature_filtered" in entities


def test_overview_power_chart_forecast_series_are_room_specific(two_room_spec):
    overview = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Overview"
    )
    power_card = next(
        c for c in _iter_cards(overview)
        if c.get("type") == "custom:apexcharts-card"
        and c.get("header", {}).get("title") == "Power – All Rooms"
    )
    plan_series = [
        s for s in power_card["series"]
        if s.get("entity", "").endswith("_heating_power_forecast")
        and "heating_power" in s.get("data_generator", "")
    ]
    names = {s["name"] for s in plan_series}
    assert "Living Room Plan" in names
    assert "Bedroom Plan" in names


def test_diagnostics_loglik_panel_exposes_sensor_and_button_per_room(two_room_spec):
    diag = next(
        v for v in build_dashboard(two_room_spec)["views"] if v["title"] == "Diagnostics"
    )
    refs = set(_iter_entity_refs(diag))
    assert f"sensor.{DOMAIN}_living_room_loglik_slice" in refs
    assert f"sensor.{DOMAIN}_bedroom_loglik_slice" in refs


def test_dashboard_to_yaml_round_trips(two_room_spec):
    yaml_module = pytest.importorskip("yaml")
    dashboard = build_dashboard(two_room_spec)
    yaml_text = dashboard_to_yaml(dashboard)
    assert isinstance(yaml_text, str) and yaml_text.startswith("title:")
    loaded = yaml_module.safe_load(yaml_text)
    assert loaded == dashboard


def test_now_marker_line_is_enabled_without_now_text_label(two_room_spec):
    dashboard = build_dashboard(two_room_spec)
    for card in _iter_cards(dashboard):
        if card.get("type") != "custom:apexcharts-card":
            continue
        now_cfg = card.get("now")
        if not isinstance(now_cfg, dict) or not now_cfg.get("show"):
            continue
        assert now_cfg.get("stroke_width") == 2
        assert now_cfg.get("label") == ""


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


def test_build_from_coordinator_scales_forecast_to_mpc_horizon():
    """graph_span on all apex charts must reflect horizon * dt, not the 3 h default."""
    class FakeModel:
        room_names = ["Office"]

    class FakeCoordinator:
        model = FakeModel()
        heat_sources = []
        _room_schedule = {}
        _horizon = 24       # 24 steps
        dt = 600.0          # 600 s each → 4 h forecast window

    dashboard = build_dashboard_from_coordinator(FakeCoordinator())
    # Expected: history_hours=6 (default) + forecast_hours=4 → graph_span "10h"
    expected_span = "10h"
    for card in _iter_cards(dashboard):
        if card.get("type") != "custom:apexcharts-card" or "graph_span" not in card:
            continue
        # Only check MPC prediction charts that read entity.attributes.forecast.
        # Pure-history cards (residuals) and fixed-window cards (open-loop trace,
        # which reads entity.attributes.simulation) use different span formulae.
        has_forecast = any(
            "attributes.forecast" in (s.get("data_generator") or "")
            for s in card.get("series", [])
        )
        if not has_forecast:
            continue
        assert card["graph_span"] == expected_span, (
            f"Card {card.get('header', {}).get('title')!r} has "
            f"graph_span={card['graph_span']!r}, expected {expected_span!r}"
        )
