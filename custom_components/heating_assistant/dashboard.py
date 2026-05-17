"""Lovelace dashboard generator for Heating Assistant.

This module produces a complete Lovelace dashboard configuration as a plain
Python dict for the Heating Assistant integration. It is intentionally pure
and has no Home Assistant runtime dependency so it can be unit tested and
invoked from a CLI.

The dashboard contains:

* an **Overview** view with comfort tiles per room, a system power chart,
  a weather strip and a controller-health card;
* one **per-room subview** with a thermostat card, the MPC triplet
  (predicted temperature, control input, disturbances), model-fit gauges,
  residual time series and an estimated-parameter table;
* a **Diagnostics** view with the estimation workflow, a per-room fit
  matrix, the residual whiteness panel and an open-loop validation card;
* a **Settings & Services** view with the regenerate-dashboard button and
  service shortcuts.

``apexcharts-card`` is a hard prerequisite – all forecast / history-overlay
charts use it. Optional community cards are not generated in this revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .const import DOMAIN

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeatSourceSpec:
    """Minimal description of a heat source the dashboard needs to render."""

    name: str
    room: str
    kind: str = "electric_heater"  # "electric_heater" | "heat_pump"


@dataclass(frozen=True)
class RoomSpec:
    """Minimal description of a room the dashboard needs to render."""

    name: str
    icon: Optional[str] = None
    has_schedule: bool = False


@dataclass(frozen=True)
class DashboardSpec:
    """Inputs to :func:`build_dashboard`.

    ``history_hours`` / ``forecast_hours`` set the chart window used by the
    MPC triplet on the per-room views; the total ``graph_span`` is their
    sum. The defaults mirror the recipe in ``README §13.17``.
    """

    rooms: Sequence[RoomSpec]
    sources: Sequence[HeatSourceSpec]
    history_hours: float = 6.0
    forecast_hours: float = 3.0
    title: str = "Heating Assistant"
    url_path: str = "heating-assistant"

    @property
    def graph_span_hours(self) -> float:
        return float(self.history_hours) + float(self.forecast_hours)


# ---------------------------------------------------------------------------
# Slug / entity-id helpers
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    """Slugify a string the same way Home Assistant does for entity IDs.

    Lowercases, replaces any run of non-alphanumeric characters with a
    single underscore, and strips leading/trailing underscores. This matches
    the slug HA derives from ``_attr_name`` for the integration's entities.
    """
    out: List[str] = []
    prev_us = True
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        else:
            if not prev_us:
                out.append("_")
                prev_us = True
    slug = "".join(out).strip("_")
    return slug or "_"


def _eid(platform: str, room_or_source: str, metric: str) -> str:
    """Compose an entity_id matching the integration's naming convention."""
    return f"{platform}.{DOMAIN}_{slugify(room_or_source)}_{metric}"


def _system_eid(platform: str, metric: str) -> str:
    return f"{platform}.{DOMAIN}_{metric}"


# ---------------------------------------------------------------------------
# Card builders – per-room subview
# ---------------------------------------------------------------------------


def _thermostat_card(room: RoomSpec) -> Dict[str, Any]:
    return {
        "type": "thermostat",
        "entity": _eid("climate", room.name, "climate"),
        "name": room.name,
    }


def _mpc_temperature_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """ApexCharts card – measured/filtered temperature + MPC forecast + setpoint band."""
    measured = _eid("sensor", room.name, "temperature_measured")
    filtered = _eid("sensor", room.name, "temperature_filtered")
    forecast = _eid("sensor", room.name, "temperature_forecast")
    setpoint = _eid("sensor", room.name, "setpoint")
    upper = _eid("sensor", room.name, "constraint_upper")
    lower = _eid("sensor", room.name, "constraint_lower")
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Predicted Temperature", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "Now", "color": "#424242"},
        "yaxis": [
            {
                "id": "temp",
                "apex_config": {
                    "title": {"text": "Temperature (°C)"},
                    "tickAmount": 5,
                    "decimalsInFloat": 1,
                },
            }
        ],
        "series": [
            {
                "entity": filtered,
                "name": "Filtered estimate y(k|k)",
                "yaxis_id": "temp",
                "color": "#0D47A1",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 2,
                "extend_to": "now",
            },
            {
                "entity": measured,
                "name": "Measured y(k)",
                "yaxis_id": "temp",
                "color": "#D32F2F",
                "type": "scatter",
                "stroke_width": 0,
                "float_precision": 2,
            },
            {
                "entity": forecast,
                "name": "MPC forecast",
                "yaxis_id": "temp",
                "color": "#1976D2",
                "stroke_width": 2,
                "stroke_dash": 4,
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
            {
                "entity": setpoint,
                "name": "Setpoint",
                "yaxis_id": "temp",
                "color": "#9E9E9E",
                "stroke_width": 1,
                "stroke_dash": 2,
                "curve": "stepline",
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
            {
                "entity": upper,
                "name": "Upper constraint",
                "yaxis_id": "temp",
                "color": "#FFCDD2",
                "stroke_width": 1,
                "opacity": 0.4,
                "show": {"legend_value": False},
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
            {
                "entity": lower,
                "name": "Lower constraint",
                "yaxis_id": "temp",
                "color": "#FFCDD2",
                "stroke_width": 1,
                "opacity": 0.4,
                "show": {"legend_value": False},
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
        ],
    }


def _mpc_control_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """ApexCharts card – measured + forecast heating power for the room."""
    measured = _eid("sensor", room.name, "heating_power_measured")
    forecast = _eid("sensor", room.name, "heating_power_forecast")
    sources_here = [s for s in spec.sources if s.room == room.name]
    series: List[Dict[str, Any]] = [
        {
            "entity": measured,
            "name": "Measured power",
            "color": "#F57C00",
            "stroke_width": 2,
            "curve": "stepline",
            "float_precision": 0,
            "extend_to": "now",
        },
        {
            "entity": forecast,
            "name": "Planned power",
            "color": "#FFB74D",
            "stroke_width": 2,
            "stroke_dash": 4,
            "curve": "stepline",
            "data_generator": (
                "return entity.attributes.forecast.map(p => "
                "[new Date(p.timestamp).getTime(), p.value]);"
            ),
        },
    ]
    for src in sources_here:
        series.append(
            {
                "entity": _eid("sensor", src.name, "control_action"),
                "name": f"{src.name} action",
                "color": "#90A4AE",
                "stroke_width": 1,
                "curve": "stepline",
                "float_precision": 0,
                "show": {"in_chart": True, "legend_value": True},
                "opacity": 0.6,
            }
        )
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Control Input", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "Now", "color": "#424242"},
        "yaxis": [{"id": "power", "apex_config": {"title": {"text": "Power (W)"}, "decimalsInFloat": 0}}],
        "series": series,
    }


def _disturbance_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Outdoor temperature + solar gain for this room, history + forecast."""
    outdoor_meas = _system_eid("sensor", "outdoor_temperature_measured")
    outdoor_fc = _system_eid("sensor", "outdoor_temperature_forecast")
    solar_meas = _eid("sensor", room.name, "solar_gain_measured")
    solar_fc = _eid("sensor", room.name, "solar_gain_forecast")
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Disturbances", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "Now", "color": "#424242"},
        "yaxis": [
            {"id": "temp", "apex_config": {"title": {"text": "T_out (°C)"}, "decimalsInFloat": 1}},
            {"id": "solar", "opposite": True, "apex_config": {"title": {"text": "Solar (W)"}, "decimalsInFloat": 0}},
        ],
        "series": [
            {
                "entity": outdoor_meas,
                "name": "Outdoor T",
                "yaxis_id": "temp",
                "color": "#2E7D32",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 1,
                "extend_to": "now",
            },
            {
                "entity": outdoor_fc,
                "name": "Outdoor T forecast",
                "yaxis_id": "temp",
                "color": "#66BB6A",
                "stroke_width": 2,
                "stroke_dash": 4,
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
            {
                "entity": solar_meas,
                "name": "Solar gain",
                "yaxis_id": "solar",
                "color": "#FBC02D",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 0,
                "extend_to": "now",
            },
            {
                "entity": solar_fc,
                "name": "Solar forecast",
                "yaxis_id": "solar",
                "color": "#FFEE58",
                "stroke_width": 2,
                "stroke_dash": 4,
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
        ],
    }


def _fit_gauge_card(room: RoomSpec) -> Dict[str, Any]:
    """R² gauge plus RMSE / bias numeric chips for the room."""
    fit = _eid("sensor", room.name, "model_fit_quality")
    return {
        "type": "vertical-stack",
        "cards": [
            {
                "type": "gauge",
                "entity": fit,
                "name": "Model fit R²",
                "min": 0,
                "max": 1,
                "needle": True,
                "severity": {"green": 0.8, "yellow": 0.5, "red": 0},
            },
            {
                "type": "glance",
                "title": "Residual statistics",
                "show_state": True,
                "entities": [
                    {
                        "entity": fit,
                        "name": "RMSE",
                        "attribute": "rmse",
                    },
                    {
                        "entity": fit,
                        "name": "MAE",
                        "attribute": "mae",
                    },
                    {
                        "entity": fit,
                        "name": "Bias",
                        "attribute": "bias",
                    },
                    {
                        "entity": _eid("sensor", room.name, "residual_acf"),
                        "name": "Lag-1 ACF",
                    },
                    {
                        "entity": _eid("sensor", room.name, "open_loop_rmse"),
                        "name": "OL RMSE",
                    },
                ],
            },
        ],
    }


def _residual_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Time series of one-step prediction error with a zero-reference line."""
    err = _eid("sensor", room.name, "prediction_error")
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Residuals"},
        "graph_span": f"{int(spec.history_hours * 2)}h",
        "yaxis": [{"id": "err", "apex_config": {"title": {"text": "ε (°C)"}, "decimalsInFloat": 2}}],
        "series": [
            {
                "entity": err,
                "name": "y(k) − ŷ(k|k-1)",
                "yaxis_id": "err",
                "color": "#6A1B9A",
                "stroke_width": 1,
                "curve": "smooth",
                "float_precision": 3,
            }
        ],
    }


def _open_loop_card(room: RoomSpec) -> Dict[str, Any]:
    """Measured vs. free-run-simulated trace from OpenLoopRMSESensor."""
    ol = _eid("sensor", room.name, "open_loop_rmse")
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Open-loop trace", "show_states": True},
        "graph_span": "2h",
        "yaxis": [{"id": "temp", "apex_config": {"title": {"text": "Temperature (°C)"}, "decimalsInFloat": 2}}],
        "series": [
            {
                "entity": ol,
                "name": "Measured",
                "yaxis_id": "temp",
                "color": "#0D47A1",
                "stroke_width": 2,
                "curve": "smooth",
                "data_generator": (
                    "return (entity.attributes.simulation || []).map(p => "
                    "[new Date(p.time).getTime(), p.measured]);"
                ),
            },
            {
                "entity": ol,
                "name": "Simulated (free-run)",
                "yaxis_id": "temp",
                "color": "#EF6C00",
                "stroke_width": 2,
                "stroke_dash": 4,
                "data_generator": (
                    "return (entity.attributes.simulation || []).map(p => "
                    "[new Date(p.time).getTime(), p.predicted]);"
                ),
            },
        ],
    }


def _parameter_table_card(room: RoomSpec) -> Dict[str, Any]:
    """Compact view of estimated parameter values + identifiability flags."""
    confidence = _eid("sensor", room.name, "parameter_confidence")
    filtered = _eid("sensor", room.name, "temperature_filtered")
    return {
        "type": "entities",
        "title": "Estimated parameters",
        "state_color": True,
        "entities": [
            {"entity": filtered, "name": "Thermal mass C", "icon": "mdi:weight-kilogram", "attribute": "thermal_mass"},
            {"entity": filtered, "name": "R_external", "icon": "mdi:home-thermometer-outline", "attribute": "r_external"},
            {"entity": confidence, "name": "C identified", "icon": "mdi:check-circle", "attribute": "is_thermal_mass_identified"},
            {"entity": confidence, "name": "R_ext identified", "icon": "mdi:check-circle", "attribute": "is_r_external_identified"},
        ],
    }


def _schedule_card(room: RoomSpec) -> Optional[Dict[str, Any]]:
    """Show schedule state if the room has one."""
    if not room.has_schedule:
        return None
    return {
        "type": "entities",
        "title": "Comfort schedule",
        "entities": [
            {"entity": _eid("climate", room.name, "climate"), "name": "Active setpoint"},
        ],
        "footer": {
            "type": "buttons",
            "entities": [
                {
                    "name": "Disable schedule",
                    "tap_action": {
                        "action": "call-service",
                        "service": f"{DOMAIN}.set_schedule_enabled",
                        "service_data": {"room_name": room.name, "enabled": False},
                    },
                },
                {
                    "name": "Enable schedule",
                    "tap_action": {
                        "action": "call-service",
                        "service": f"{DOMAIN}.set_schedule_enabled",
                        "service_data": {"room_name": room.name, "enabled": True},
                    },
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------


def _overview_view(spec: DashboardSpec) -> Dict[str, Any]:
    """Top-level overview with comfort tiles and system charts."""
    comfort_tiles = [
        {
            "type": "tile",
            "entity": _eid("climate", room.name, "climate"),
            "name": room.name,
            "icon": room.icon or "mdi:home-thermometer",
            "tap_action": {
                "action": "navigate",
                "navigation_path": f"/{spec.url_path}/{slugify(room.name)}",
            },
        }
        for room in spec.rooms
    ]

    system_summary = _system_eid("sensor", "system_summary")
    mpc_perf = _system_eid("sensor", "mpc_performance")
    weather_status = _system_eid("sensor", "weather_forecast_status")
    est_status = _system_eid("sensor", "estimated_parameters_status")

    power_series: List[Dict[str, Any]] = [
        {
            "entity": _eid("sensor", room.name, "heating_power_measured"),
            "name": f"{room.name}",
            "stroke_width": 1,
            "curve": "stepline",
            "group_by": {"func": "avg", "duration": "5min"},
        }
        for room in spec.rooms
    ]

    energy_card = {
        "type": "entities",
        "title": "Energy delivered (cumulative)",
        "state_color": True,
        "entities": [
            {
                "entity": _eid("sensor", src.name, "energy_total"),
                "name": src.name,
            }
            for src in spec.sources
        ]
        or [{"type": "section", "label": "No heat sources configured"}],
    }

    sections: List[Dict[str, Any]] = [
        {
            "type": "grid",
            "cards": [{"type": "heading", "heading": "Comfort", "heading_style": "title"}, *comfort_tiles],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "System totals", "heading_style": "title"},
                {
                    "type": "custom:apexcharts-card",
                    "header": {"show": True, "title": "Heating power (last 24 h)", "show_states": True},
                    "graph_span": "24h",
                    "stacked": True,
                    "series": power_series,
                },
                {
                    "type": "glance",
                    "title": "System status",
                    "entities": [
                        {"entity": system_summary, "name": "Total power"},
                        {"entity": _system_eid("sensor", "outdoor_temperature_measured"), "name": "Outdoor"},
                        {"entity": mpc_perf, "name": "MPC solve"},
                        {"entity": weather_status, "name": "Weather"},
                        {"entity": est_status, "name": "Parameters"},
                    ],
                },
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Energy", "heading_style": "title"},
                energy_card,
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Outdoor & solar", "heading_style": "title"},
                _system_weather_strip(spec),
            ],
        },
    ]

    return {
        "title": "Overview",
        "path": "overview",
        "icon": "mdi:view-dashboard",
        "type": "sections",
        "sections": sections,
    }


def _system_weather_strip(spec: DashboardSpec) -> Dict[str, Any]:
    """Outdoor temperature history + forecast, plus aggregate solar gain."""
    outdoor_meas = _system_eid("sensor", "outdoor_temperature_measured")
    outdoor_fc = _system_eid("sensor", "outdoor_temperature_forecast")
    solar_series: List[Dict[str, Any]] = [
        {
            "entity": _eid("sensor", room.name, "solar_gain_measured"),
            "name": room.name,
            "yaxis_id": "solar",
            "stroke_width": 1,
            "curve": "smooth",
        }
        for room in spec.rooms
    ]
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": "Outdoor & solar", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "Now", "color": "#424242"},
        "yaxis": [
            {"id": "temp", "apex_config": {"title": {"text": "T_out (°C)"}, "decimalsInFloat": 1}},
            {"id": "solar", "opposite": True, "apex_config": {"title": {"text": "Solar (W)"}, "decimalsInFloat": 0}},
        ],
        "series": [
            {
                "entity": outdoor_meas,
                "name": "Outdoor T",
                "yaxis_id": "temp",
                "color": "#2E7D32",
                "stroke_width": 2,
                "curve": "smooth",
                "extend_to": "now",
            },
            {
                "entity": outdoor_fc,
                "name": "Forecast",
                "yaxis_id": "temp",
                "color": "#66BB6A",
                "stroke_width": 2,
                "stroke_dash": 4,
                "data_generator": (
                    "return entity.attributes.forecast.map(p => "
                    "[new Date(p.timestamp).getTime(), p.value]);"
                ),
            },
            *solar_series,
        ],
    }


def _room_view(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Per-room subview with MPC triplet, fit gauges, residuals, parameters."""
    sections: List[Dict[str, Any]] = [
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Control & comfort", "heading_style": "title"},
                _thermostat_card(room),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "MPC predicted output", "heading_style": "title"},
                _mpc_temperature_card(room, spec),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "MPC control input", "heading_style": "title"},
                _mpc_control_card(room, spec),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Disturbances", "heading_style": "title"},
                _disturbance_card(room, spec),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Model fit", "heading_style": "title"},
                _fit_gauge_card(room),
                _residual_card(room, spec),
                _open_loop_card(room),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Estimated parameters", "heading_style": "title"},
                _parameter_table_card(room),
            ],
        },
    ]

    schedule = _schedule_card(room)
    if schedule is not None:
        sections.append(
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Schedule", "heading_style": "title"},
                    schedule,
                ],
            }
        )

    return {
        "title": room.name,
        "path": slugify(room.name),
        "icon": room.icon or "mdi:home-thermometer",
        "subview": True,
        "type": "sections",
        "sections": sections,
    }


def _diagnostics_view(spec: DashboardSpec) -> Dict[str, Any]:
    """Diagnostics view – estimation workflow + per-room fit matrix."""
    est_status = _system_eid("sensor", "estimated_parameters_status")

    estimation_card = {
        "type": "vertical-stack",
        "cards": [
            {
                "type": "entities",
                "title": "Parameter estimation",
                "entities": [
                    {"entity": _system_eid("button", "estimate_parameters_button"), "name": "Estimate now"},
                    {"entity": _system_eid("button", "reset_parameters_button"), "name": "Reset to defaults"},
                    est_status,
                ],
            },
            {
                "type": "entity-button",
                "name": "Dry-run estimation",
                "entity": _system_eid("button", "estimate_parameters_button"),
                "tap_action": {
                    "action": "call-service",
                    "service": f"{DOMAIN}.estimate_parameters_ml",
                    "service_data": {"apply_parameters": False},
                },
            },
        ],
    }

    fit_rows: List[Dict[str, Any]] = []
    for room in spec.rooms:
        fit = _eid("sensor", room.name, "model_fit_quality")
        fit_rows.append({"entity": fit, "name": room.name})

    fit_matrix = {
        "type": "entities",
        "title": "Per-room model fit",
        "state_color": True,
        "entities": fit_rows,
    }

    residual_rows: List[Dict[str, Any]] = []
    for room in spec.rooms:
        residual_rows.append({"entity": _eid("sensor", room.name, "residual_acf"), "name": room.name})
    residual_panel = {
        "type": "entities",
        "title": "Residual whiteness (lag-1 ACF)",
        "state_color": True,
        "entities": residual_rows,
    }

    open_loop_panel = {
        "type": "entities",
        "title": "Open-loop RMSE",
        "state_color": True,
        "entities": [
            {"entity": _eid("sensor", room.name, "open_loop_rmse"), "name": room.name}
            for room in spec.rooms
        ],
        "footer": {
            "type": "buttons",
            "entities": [
                {
                    "name": "Run open-loop simulation",
                    "tap_action": {
                        "action": "call-service",
                        "service": f"{DOMAIN}.run_open_loop_simulation",
                    },
                },
                {
                    "name": "Analyse model fit",
                    "tap_action": {
                        "action": "call-service",
                        "service": f"{DOMAIN}.analyze_model_fit",
                    },
                },
            ],
        },
    }

    parameter_panel = {
        "type": "entities",
        "title": "Identifiability",
        "state_color": True,
        "entities": [
            {"entity": _eid("sensor", room.name, "parameter_confidence"), "name": room.name}
            for room in spec.rooms
        ],
    }

    history_panel = {
        "type": "markdown",
        "title": "Estimation history",
        "content": (
            "{% set history = state_attr('"
            + est_status
            + "', 'estimation_history') %}"
            "{% if history %}"
            "| When | LL | Applied | Success |\n"
            "|---|---|---|---|\n"
            "{% for h in history[-10:] | reverse %}"
            "| {{ h.estimated_at }} | "
            "{{ '%.2f' % h.log_likelihood if h.log_likelihood is not none else '—' }} | "
            "{{ 'yes' if h.applied else 'no' }} | "
            "{{ 'yes' if h.success else 'no' }} |\n"
            "{% endfor %}"
            "{% else %}"
            "_No estimation runs yet. Press **Estimate now** to start._"
            "{% endif %}"
        ),
    }

    loglik_panel = {
        "type": "entities",
        "title": "Log-likelihood landscape",
        "entities": [
            {
                "type": "call-service",
                "name": f"Compute slice – {room.name}",
                "icon": "mdi:chart-bell-curve",
                "service": f"{DOMAIN}.compute_loglik_slice",
                "service_data": {"room_name": room.name, "n_grid": 11, "span_log": 1.0},
            }
            for room in spec.rooms
        ]
        or [{"type": "section", "label": "No rooms configured"}],
        "footer": {
            "type": "markdown",
            "content": (
                "Each button evaluates the CD-EKF log-likelihood on an 11×11"
                " (log C, log R_ext) grid around the current MLE. The grid"
                " comes back in the service response – use Developer Tools →"
                " Services to inspect it, or add a Plotly card."
            ),
        },
    }

    return {
        "title": "Diagnostics",
        "path": "diagnostics",
        "icon": "mdi:stethoscope",
        "type": "sections",
        "sections": [
            {"type": "grid", "cards": [{"type": "heading", "heading": "Workflow", "heading_style": "title"}, estimation_card]},
            {"type": "grid", "cards": [{"type": "heading", "heading": "Fit matrix", "heading_style": "title"}, fit_matrix]},
            {"type": "grid", "cards": [{"type": "heading", "heading": "Residuals", "heading_style": "title"}, residual_panel]},
            {"type": "grid", "cards": [{"type": "heading", "heading": "Open-loop validation", "heading_style": "title"}, open_loop_panel]},
            {"type": "grid", "cards": [{"type": "heading", "heading": "Parameter identifiability", "heading_style": "title"}, parameter_panel]},
            {"type": "grid", "cards": [{"type": "heading", "heading": "Log-likelihood landscape", "heading_style": "title"}, loglik_panel]},
            {"type": "grid", "cards": [{"type": "heading", "heading": "History", "heading_style": "title"}, history_panel]},
        ],
    }


def _settings_view(spec: DashboardSpec) -> Dict[str, Any]:
    return {
        "title": "Settings & Services",
        "path": "settings",
        "icon": "mdi:cog",
        "type": "sections",
        "sections": [
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Dashboard", "heading_style": "title"},
                    {
                        "type": "entities",
                        "title": "Dashboard maintenance",
                        "entities": [
                            {
                                "type": "call-service",
                                "name": "Regenerate dashboard",
                                "service": f"{DOMAIN}.regenerate_dashboard",
                            },
                            {
                                "type": "call-service",
                                "name": "Reload integration",
                                "service": f"{DOMAIN}.reload",
                            },
                        ],
                    },
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Schedules", "heading_style": "title"},
                    {
                        "type": "entities",
                        "title": "Schedule controls",
                        "entities": [
                            {
                                "type": "call-service",
                                "name": f"Disable schedule – {room.name}",
                                "service": f"{DOMAIN}.set_schedule_enabled",
                                "service_data": {"room_name": room.name, "enabled": False},
                            }
                            for room in spec.rooms
                            if room.has_schedule
                        ]
                        or [
                            {
                                "type": "call-service",
                                "name": "No schedules configured",
                                "service": f"{DOMAIN}.reload",
                                "icon": "mdi:information",
                            }
                        ],
                    },
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_dashboard(spec: DashboardSpec) -> Dict[str, Any]:
    """Return the full Lovelace dashboard configuration as a dict."""
    views: List[Dict[str, Any]] = [_overview_view(spec)]
    for room in spec.rooms:
        views.append(_room_view(room, spec))
    views.append(_diagnostics_view(spec))
    views.append(_settings_view(spec))

    return {
        "title": spec.title,
        "views": views,
    }


def dashboard_to_yaml(dashboard: Dict[str, Any]) -> str:
    """Serialise a dashboard dict to a Lovelace-ready YAML string.

    PyYAML ships with Home Assistant, so this is always importable at
    runtime. The dump options match HA's own conventions (preserve key
    order, allow unicode, no aliases).
    """
    import yaml

    class _NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:  # noqa: D401
            return True

    return yaml.dump(
        dashboard,
        Dumper=_NoAliasDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def build_dashboard_from_coordinator(coordinator: Any) -> Dict[str, Any]:
    """Build a dashboard dict from a live :class:`HeatingAssistantCoordinator`.

    The coordinator is duck-typed so this module stays import-light in tests:
    we require ``coordinator.model.room_names`` (an iterable of room names),
    ``coordinator.heat_sources`` (an iterable with ``.name``/``.room``), and
    ``coordinator._room_schedule`` (a mapping of room→schedule, optional).
    """
    rooms: List[RoomSpec] = []
    schedule_map = getattr(coordinator, "_room_schedule", {}) or {}
    for name in coordinator.model.room_names:
        schedule = schedule_map.get(name)
        has_schedule = bool(schedule) and not getattr(schedule, "is_empty", False)
        rooms.append(RoomSpec(name=name, has_schedule=has_schedule))

    sources: List[HeatSourceSpec] = []
    for src in getattr(coordinator, "heat_sources", []) or []:
        kind = type(src).__name__
        if kind == "HeatPump":
            kind_str = "heat_pump"
        elif kind == "ElectricHeater":
            kind_str = "electric_heater"
        else:
            kind_str = "electric_heater"
        sources.append(HeatSourceSpec(name=src.name, room=src.room, kind=kind_str))

    return build_dashboard(DashboardSpec(rooms=tuple(rooms), sources=tuple(sources)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:  # pragma: no cover - convenience CLI
    """Print a placeholder dashboard for ad-hoc inspection.

    ``python -m custom_components.heating_assistant.dashboard`` emits a
    two-room example dashboard so users can preview the YAML before
    installing the integration.
    """
    import json

    example = DashboardSpec(
        rooms=(
            RoomSpec(name="Living Room", icon="mdi:sofa"),
            RoomSpec(name="Bedroom", icon="mdi:bed", has_schedule=True),
        ),
        sources=(
            HeatSourceSpec(name="living_room_heater", room="Living Room", kind="heat_pump"),
            HeatSourceSpec(name="bedroom_heater", room="Bedroom", kind="electric_heater"),
        ),
    )
    print(json.dumps(build_dashboard(example), indent=2))


if __name__ == "__main__":  # pragma: no cover
    _main()
