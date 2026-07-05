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

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .const import DOMAIN

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

DASHBOARD_VARIANT_CLASSIC = "classic"
DASHBOARD_VARIANT_INDUSTRIAL = "industrial"


@dataclass(frozen=True)
class HeatSourceSpec:
    """Minimal description of a heat source the dashboard needs to render."""

    name: str
    room: str
    kind: str = "electric_heater"  # "electric_heater" | "heat_pump"
    entity_id: Optional[str] = None  # HA entity_id of the physical device


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

    ``has_price`` controls whether the *Power & electricity price* dual-axis
    chart is inserted into each room's per-room view.  Set to ``True`` when a
    price entity is configured so users can see the controller reacting to
    spot-price variations.
    """

    rooms: Sequence[RoomSpec]
    sources: Sequence[HeatSourceSpec]
    history_hours: float = 12.0
    forecast_hours: float = 3.0
    title: str = "Heating Assistant"
    url_path: str = "heating-assistant"
    has_price: bool = False

    @property
    def graph_span_hours(self) -> float:
        return float(self.history_hours) + float(self.forecast_hours)


# ---------------------------------------------------------------------------
# Slug / entity-id helpers
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    """Backward-compatible re-export; prefer :func:`naming.slugify`."""
    from .naming import slugify as _slugify

    return _slugify(value)


def _climate_eid(room_name: str) -> str:
    """Entity ID of a room's climate entity.

    The climate entity's name is ``"Heating Assistant – {room}"`` (no
    metric suffix), so HA slugifies it to
    ``climate.heating_assistant_{slug(room)}``. Earlier revisions of the
    generator wrongly appended ``_climate`` to match the ``unique_id``;
    that broke the dashboard because the registry-assigned entity_id is
    derived from ``_attr_name``, not ``_attr_unique_id``.
    """
    return f"climate.{DOMAIN}_{slugify(room_name)}"


def _button_eid(metric: str) -> str:
    """Entity ID of an integration button.

    The button names are e.g. ``"Heating Assistant – Estimate Parameters"``,
    so HA assigns ``button.heating_assistant_estimate_parameters``. The
    ``_button`` suffix lives only on the ``unique_id``.
    """
    return f"button.{DOMAIN}_{metric}"


def _eid(platform: str, room_or_source: str, metric: str) -> str:
    """Compose an entity_id matching the integration's naming convention."""
    return f"{platform}.{DOMAIN}_{slugify(room_or_source)}_{metric}"


def _system_eid(platform: str, metric: str) -> str:
    return f"{platform}.{DOMAIN}_{metric}"


# ---------------------------------------------------------------------------
# Shared apexcharts fragments
# ---------------------------------------------------------------------------


def _forecast_generator(field: str) -> str:
    """Return the README-style ``data_generator`` for a forecast field.

    The integration's forecast attributes are arrays of dicts containing
    ``time`` (ISO 8601) plus one or more field names (``temperature``,
    ``setpoint``, ``heating_power``, ``outdoor_temp``, ``solar_gain``,
    ``constraint_upper``/``constraint_lower``). This helper produces a
    matching JS snippet so each chart series consumes the right field.
    """
    return (
        "const fc = entity.attributes.forecast;\n"
        "if (!fc) return [];\n"
        "return fc.map(f => [new Date(f.time).getTime(), "
        f"f.{field} ?? null]);"
    )


def _comfort_top_cap_generator() -> str:
    """Return a top-cap series used to shade above the comfort corridor."""
    return (
        "const fc = entity.attributes.forecast;\n"
        "if (!fc) return [];\n"
        "const upper = fc.map(f => Number(f.constraint_upper)).filter(v => Number.isFinite(v));\n"
        "const lower = fc.map(f => Number(f.constraint_lower)).filter(v => Number.isFinite(v));\n"
        "if (!upper.length) return [];\n"
        "const span = lower.length ? Math.max(...upper) - Math.min(...lower) : 0;\n"
        "const cap = Math.max(...upper) + Math.max(2.0, span * 0.5);\n"
        "return fc.map(f => [new Date(f.time).getTime(), cap]);"
    )


def _history_series_kwargs() -> Dict[str, Any]:
    """README-style options for a series that draws from recorder history."""
    return {
        "extend_to": "now",
        "group_by": {"func": "raw", "fill": "last"},
    }


# ---------------------------------------------------------------------------
# Card builders – per-room subview
# ---------------------------------------------------------------------------


def _thermostat_card(room: RoomSpec) -> Dict[str, Any]:
    return {
        "type": "thermostat",
        "entity": _climate_eid(room.name),
        "name": room.name,
    }


def _mpc_temperature_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Predicted-temperature chart – mirrors README §13.17.3 / §13.17.8."""
    measured = _eid("sensor", room.name, "temperature_measured")
    filtered = _eid("sensor", room.name, "temperature_filtered")
    forecast = _eid("sensor", room.name, "temperature_forecast")
    setpoint = _eid("sensor", room.name, "setpoint")
    upper = _eid("sensor", room.name, "constraint_upper")
    lower = _eid("sensor", room.name, "constraint_lower")
    return {
        "type": "custom:apexcharts-card",
        "header": {
            "show": True,
            "title": f"{room.name} – Temperature",
            "show_states": True,
        },
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        "apex_config": {
            "legend": {"show": False},
        },
        "yaxis": [
            {
                "id": "temp",
                "apex_config": {
                    "title": {"text": "Temperature (°C)"},
                    "tickAmount": 5,
                },
            }
        ],
        "series": [
            # History: filtered estimate y(k|k)
            {
                "entity": filtered,
                "name": "Filtered",
                "yaxis_id": "temp",
                "color": "#0D47A1",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 2,
                **_history_series_kwargs(),
                "show": {"in_header": True, "in_legend": False},
            },
            # History: measured y(k) — rendered as a thin line with no
            # stroke + raw markers (avoids `type: scatter`, which the
            # installed apexcharts-card version does not support).
            {
                "entity": measured,
                "name": "Measured",
                "yaxis_id": "temp",
                "color": "#E53935",
                "stroke_width": 0,
                "float_precision": 2,
                "extend_to": "now",
                "group_by": {"func": "raw", "fill": "null"},
                "show": {"in_header": False, "in_legend": False},
            },
            # History: setpoint
            {
                "entity": setpoint,
                "name": "Setpoint",
                "yaxis_id": "temp",
                "color": "#F44336",
                "stroke_width": 2,
                "stroke_dash": 5,
                "curve": "stepline",
                "float_precision": 1,
                **_history_series_kwargs(),
                "show": {"in_header": False, "in_legend": False},
            },
            # History: upper constraint cap for outside-corridor shading.
            {
                "entity": upper,
                "name": "Constraints",
                "yaxis_id": "temp",
                "type": "area",
                "color": "#E53935",
                "opacity": 0.16,
                "stroke_width": 0,
                "curve": "stepline",
                "transform": (
                    "const v = Number(x);"
                    "if (!Number.isFinite(v)) return null;"
                    "return v + 3.0;"
                ),
                **_history_series_kwargs(),
                "show": {"in_legend": False, "in_header": False},
            },
            # History: upper comfort bound – redraws feasible corridor with
            # card background to leave only outside regions shaded.
            {
                "entity": upper,
                "name": "Constraints",
                "yaxis_id": "temp",
                "type": "area",
                "color": "var(--card-background-color)",
                "opacity": 1.0,
                "stroke_width": 0,
                "curve": "stepline",
                **_history_series_kwargs(),
                "show": {"in_legend": False, "in_header": False},
            },
            # History: lower comfort bound – redraws lower out-of-corridor
            # region in red, still with no boundary line.
            {
                "entity": lower,
                "name": "Constraints",
                "yaxis_id": "temp",
                "type": "area",
                "color": "#E53935",
                "opacity": 0.16,
                "stroke_width": 0,
                "curve": "stepline",
                **_history_series_kwargs(),
                "show": {"in_legend": False, "in_header": False},
            },
            # Forecast: setpoint over the MPC horizon – hidden from legend; history entry covers it
            {
                "entity": setpoint,
                "name": "Setpoint",
                "data_generator": _forecast_generator("setpoint"),
                "yaxis_id": "temp",
                "color": "#F44336",
                "stroke_width": 2,
                "stroke_dash": 5,
                "curve": "stepline",
                "float_precision": 1,
                "show": {"in_legend": False, "in_header": False},
            },
            # Forecast: upper constraint
            {
                "entity": upper,
                "name": "Constraints",
                "data_generator": _comfort_top_cap_generator(),
                "yaxis_id": "temp",
                "type": "area",
                "color": "#E53935",
                "opacity": 0.16,
                "stroke_width": 0,
                "curve": "stepline",
                "show": {"in_header": False, "in_legend": False},
            },
            # Forecast: upper comfort bound – redraws feasible corridor with
            # card background to leave only outside regions shaded.
            {
                "entity": upper,
                "name": "Constraints",
                "data_generator": _forecast_generator("constraint_upper"),
                "yaxis_id": "temp",
                "type": "area",
                "color": "var(--card-background-color)",
                "opacity": 1.0,
                "stroke_width": 0,
                "curve": "stepline",
                "show": {"in_legend": False, "in_header": False},
            },
            # Forecast: lower comfort bound – redraws lower out-of-corridor
            # region in red, still with no boundary line.
            {
                "entity": lower,
                "name": "Constraints",
                "data_generator": _forecast_generator("constraint_lower"),
                "yaxis_id": "temp",
                "type": "area",
                "color": "#E53935",
                "opacity": 0.16,
                "stroke_width": 0,
                "curve": "stepline",
                "show": {"in_legend": False, "in_header": False},
            },
            # Forecast: predicted temperature trajectory (nonlinear model)
            {
                "entity": forecast,
                "name": "Predicted (nonlinear)",
                "data_generator": _forecast_generator("temperature"),
                "yaxis_id": "temp",
                "color": "#1E88E5",
                "stroke_width": 3,
                "curve": "smooth",
                "float_precision": 2,
                "show": {"in_header": True, "in_legend": False},
            },
            # Forecast: linearised model temperature trajectory (what the MPC sees)
            {
                "entity": forecast,
                "name": "Predicted (linearised)",
                "data_generator": _forecast_generator("linearised_temperature"),
                "yaxis_id": "temp",
                "color": "#FF8F00",
                "stroke_width": 2,
                "stroke_dash": 4,
                "curve": "smooth",
                "float_precision": 2,
                "show": {"in_header": False, "in_legend": False},
            },
        ],
    }


def _mpc_control_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Planned-heating-power chart – mirrors README §13.17.4."""
    measured = _eid("sensor", room.name, "heating_power_measured")
    forecast = _eid("sensor", room.name, "heating_power_forecast")
    return {
        "type": "custom:apexcharts-card",
        "header": {
            "show": True,
            "title": f"{room.name} – Power",
            "show_states": True,
        },
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        "yaxis": [
            {
                "id": "power",
                "apex_config": {
                    "title": {"text": "Power (W)"},
                    "tickAmount": 4,
                },
            }
        ],
        "series": [
            {
                "entity": measured,
                "name": "Actual",
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": "#BF360C",
                "opacity": 0.2,
                "stroke_width": 2,
                "float_precision": 0,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            },
            {
                "entity": forecast,
                "name": "Plan",
                "data_generator": _forecast_generator("heating_power"),
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": "#E65100",
                "opacity": 0.35,
                "stroke_width": 2,
                "float_precision": 0,
                "show": {"in_header": True},
            },
        ],
    }


def _price_and_power_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Heating power (actual + plan) vs electricity price – dual-axis chart."""
    measured = _eid("sensor", room.name, "heating_power_measured")
    forecast = _eid("sensor", room.name, "heating_power_forecast")
    price_current = _system_eid("sensor", "electricity_price")
    price_fc = _system_eid("sensor", "electricity_price_forecast")
    return {
        "type": "custom:apexcharts-card",
        "header": {
            "show": True,
            "title": f"{room.name} – Power & Price",
            "show_states": True,
        },
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        # ApexCharts requires stroke.curve as a per-series array; the per-series
        # ``curve`` key in apexcharts-card is unreliable when area and line series
        # are mixed in the same chart (the line/price series reverts to smooth).
        # Setting it here at the chart level is the authoritative fix.
        "apex_config": {
            "stroke": {
                "curve": ["stepline", "stepline", "stepline", "stepline"],
            },
        },
        "yaxis": [
            {
                "id": "power",
                "apex_config": {
                    "title": {"text": "Power (W)"},
                    "tickAmount": 4,
                },
            },
            {
                "id": "price",
                "opposite": True,
                "min": 0,
                "apex_config": {
                    "title": {"text": "Price"},
                    "decimalsInFloat": 4,
                    "tickAmount": 4,
                },
            },
        ],
        "series": [
            {
                "entity": measured,
                "name": "Actual",
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": "#BF360C",
                "opacity": 0.2,
                "stroke_width": 2,
                "float_precision": 0,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            },
            {
                "entity": forecast,
                "name": "Plan",
                "data_generator": _forecast_generator("heating_power"),
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": "#E65100",
                "opacity": 0.35,
                "stroke_width": 2,
                "float_precision": 0,
                "show": {"in_header": True},
            },
            {
                "entity": price_current,
                "name": "Price",
                "yaxis_id": "price",
                "color": "#1565C0",
                "stroke_width": 2,
                "curve": "stepline",
                "float_precision": 4,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            },
            {
                "entity": price_fc,
                "name": "Price fc.",
                "data_generator": _forecast_generator("price"),
                "yaxis_id": "price",
                "color": "#42A5F5",
                "stroke_width": 2,
                "stroke_dash": 4,
                "curve": "stepline",
                "float_precision": 4,
                "show": {"in_legend": False, "in_header": False},
            },
        ],
    }


def _disturbance_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Outdoor temperature + solar gain chart – mirrors README §13.17.5."""
    outdoor_meas = _system_eid("sensor", "outdoor_temperature_measured")
    outdoor_fc = _system_eid("sensor", "outdoor_temperature_forecast")
    solar_meas = _eid("sensor", room.name, "solar_gain_measured")
    solar_fc = _eid("sensor", room.name, "solar_gain_forecast")
    return {
        "type": "custom:apexcharts-card",
        "header": {
            "show": True,
            "title": f"{room.name} – Disturbances",
            "show_states": True,
        },
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        "yaxis": [
            {"id": "temp", "apex_config": {"title": {"text": "Outdoor Temp (°C)"}}},
            {
                "id": "power",
                "opposite": True,
                "min": 0,
                "apex_config": {"title": {"text": "Solar Gain (W)"}},
            },
        ],
        "series": [
            {
                "entity": outdoor_meas,
                "name": "Outdoor",
                "yaxis_id": "temp",
                "color": "#37474F",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 1,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            },
            {
                "entity": solar_meas,
                "name": "Solar",
                "yaxis_id": "power",
                "type": "area",
                "color": "#FF8F00",
                "opacity": 0.25,
                "stroke_width": 2,
                "float_precision": 0,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            },
            {
                "entity": outdoor_fc,
                "name": "Outdoor",
                "data_generator": _forecast_generator("outdoor_temp"),
                "yaxis_id": "temp",
                "color": "#78909C",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 1,
                "show": {"in_legend": False, "in_header": False},
            },
            {
                "entity": solar_fc,
                "name": "Solar",
                "data_generator": _forecast_generator("solar_gain"),
                "yaxis_id": "power",
                "type": "area",
                "color": "#FFC107",
                "opacity": 0.4,
                "stroke_width": 2,
                "float_precision": 0,
                "show": {"in_legend": False, "in_header": False},
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
        "apex_config": {
            "noData": {
                "text": (
                    "Collecting history – the open-loop trace needs ~30 min "
                    "of operation before it can be computed."
                ),
                "align": "center",
                "verticalAlign": "middle",
                "style": {"fontSize": "13px", "color": "#9E9E9E"},
            },
        },
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
                "name": "Simulated",
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
    """Show schedule state and quick-action service rows when the room has a schedule.

    Uses ``call-service`` rows in the main ``entities`` list rather than the
    ``footer: type: buttons`` form, because the footer-buttons schema
    requires each item to reference an existing entity – which our
    suspend/resume actions don't.
    """
    if not room.has_schedule:
        return None
    return {
        "type": "entities",
        "title": "Comfort schedule",
        "state_color": True,
        "entities": [
            {"entity": _climate_eid(room.name), "name": "Active setpoint"},
            {
                "type": "call-service",
                "name": f"Disable schedule – {room.name}",
                "icon": "mdi:calendar-remove",
                "service": f"{DOMAIN}.set_schedule_enabled",
                "service_data": {"room_name": room.name, "enabled": False},
            },
            {
                "type": "call-service",
                "name": f"Enable schedule – {room.name}",
                "icon": "mdi:calendar-check",
                "service": f"{DOMAIN}.set_schedule_enabled",
                "service_data": {"room_name": room.name, "enabled": True},
            },
        ],
    }


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------


def _overview_view(spec: DashboardSpec) -> Dict[str, Any]:
    """Top-level overview with general status, room KPIs, and key actions."""
    comfort_tiles = [
        {
            "type": "tile",
            "entity": _climate_eid(room.name),
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

    room_snapshot_entities: List[Dict[str, Any]] = []
    for room in spec.rooms:
        room_snapshot_entities.extend(
            [
                {"type": "section", "label": room.name},
                {
                    "entity": _eid("sensor", room.name, "temperature_filtered"),
                    "name": "Temperature",
                    "icon": "mdi:thermometer",
                    "icon_color": "#1E88E5",
                },
                {
                    "entity": _eid("sensor", room.name, "setpoint"),
                    "name": "Setpoint",
                    "icon": "mdi:target",
                    "icon_color": "#FB8C00",
                },
                {
                    "entity": _eid("sensor", room.name, "heating_power_measured"),
                    "name": "Power",
                    "icon": "mdi:fire",
                    "icon_color": "#E53935",
                },
                {
                    "entity": _eid("sensor", room.name, "model_fit_quality"),
                    "name": "Fit R²",
                    "icon": "mdi:chart-line",
                    "icon_color": "#43A047",
                },
            ]
        )
    if not room_snapshot_entities:
        room_snapshot_entities = [{"type": "section", "label": "No rooms configured"}]

    room_snapshot_card: Dict[str, Any] = {
        "type": "entities",
        "title": "Room snapshot",
        "state_color": True,
        "entities": room_snapshot_entities,
    }

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
                {"type": "heading", "heading": "Room snapshot", "heading_style": "title"},
                room_snapshot_card,
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "System status", "heading_style": "title"},
                {
                    "type": "gauge",
                    "name": "MPC Compute Time",
                    "entity": mpc_perf,
                    "needle": True,
                    "min": 0,
                    "max": 60,
                    "severity": {"green": 0, "yellow": 20, "red": 40},
                },
                {
                    "type": "entities",
                    "state_color": True,
                    "entities": [
                        {
                            "entity": system_summary,
                            "name": "Total power",
                            "icon": "mdi:fire",
                            "icon_color": "#E53935",
                        },
                        {
                            "entity": _system_eid("sensor", "outdoor_temperature_measured"),
                            "name": "Outdoor temperature",
                            "icon": "mdi:thermometer",
                            "icon_color": "#1E88E5",
                        },
                        {
                            "entity": weather_status,
                            "name": "Weather forecast",
                            "icon": "mdi:weather-partly-cloudy",
                            "icon_color": "#00ACC1",
                        },
                        {
                            "entity": est_status,
                            "name": "Model parameters",
                            "icon": "mdi:function-variant",
                            "icon_color": "#43A047",
                        },
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
                {"type": "heading", "heading": "Quick actions", "heading_style": "title"},
                {
                    "type": "button",
                    "name": "Estimate parameters",
                    "icon": "mdi:flask-outline",
                    "tap_action": {
                        "action": "call-service",
                        "service": f"{DOMAIN}.estimate_parameters_ml",
                        "service_data": {"apply_parameters": True},
                    },
                },
                {
                    "type": "button",
                    "name": "Regenerate dashboard",
                    "icon": "mdi:refresh",
                    "tap_action": {
                        "action": "call-service",
                        "service": f"{DOMAIN}.regenerate_dashboard",
                    },
                },
                {
                    "type": "button",
                    "name": "Open diagnostics",
                    "icon": "mdi:stethoscope",
                    "tap_action": {
                        "action": "navigate",
                        "navigation_path": f"/{spec.url_path}/diagnostics",
                    },
                },
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


def _overview_temperature_chart(spec: DashboardSpec) -> Dict[str, Any]:
    """All-rooms temperature chart for the overview view.

    Each room contributes four series: its filtered-estimate history (solid
    line), its MPC forecast (dashed line), and constraint bounds (upper/lower).
    Setpoints are not plotted here so the chart stays readable when there are
    many rooms; users get the full triplet on the per-room subview.
    """
    palette = _ROOM_PALETTE
    series: List[Dict[str, Any]] = []
    for i, room in enumerate(spec.rooms):
        color = palette[i % len(palette)]
        series.append(
            {
                "entity": _eid("sensor", room.name, "temperature_filtered"),
                "name": room.name,
                "yaxis_id": "temp",
                "color": color,
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 2,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            }
        )
        series.append(
            {
                "entity": _eid("sensor", room.name, "temperature_forecast"),
                "name": f"{room.name} Forecast",
                "data_generator": _forecast_generator("temperature"),
                "yaxis_id": "temp",
                "color": color,
                "stroke_width": 2,
                "stroke_dash": 4,
                "opacity": 0.85,
                "curve": "smooth",
                "float_precision": 2,
                "show": {"in_header": i == 0},
            }
        )
        series.append(
            {
                "entity": _eid("sensor", room.name, "constraint_upper"),
                "name": "Comfort bounds",
                "data_generator": _forecast_generator("constraint_upper"),
                "yaxis_id": "temp",
                "color": color,
                "stroke_width": 1,
                "opacity": 0.4,
                "curve": "stepline",
                "float_precision": 1,
                "show": {"in_legend": i == 0, "in_header": False},
            }
        )
        series.append(
            {
                "entity": _eid("sensor", room.name, "constraint_lower"),
                "name": "Comfort bounds",
                "data_generator": _forecast_generator("constraint_lower"),
                "yaxis_id": "temp",
                "color": color,
                "stroke_width": 1,
                "opacity": 0.4,
                "curve": "stepline",
                "float_precision": 1,
                "show": {"in_legend": False, "in_header": False},
            }
        )
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": "Room Temperatures", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        "yaxis": [
            {
                "id": "temp",
                "apex_config": {
                    "title": {"text": "Temperature (°C)"},
                    "tickAmount": 5,
                },
            }
        ],
        "series": series,
    }


def _overview_power_chart(spec: DashboardSpec) -> Dict[str, Any]:
    """All-rooms heating-power chart (stacked area) for the overview view.

    Each room contributes two series: measured power (history) and forecasted
    power (MPC plan over the control horizon).
    """
    palette = _ROOM_PALETTE
    series: List[Dict[str, Any]] = []
    for i, room in enumerate(spec.rooms):
        color = palette[i % len(palette)]
        series.append(
            {
                "entity": _eid("sensor", room.name, "heating_power_measured"),
                "name": room.name,
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": color,
                "stroke_width": 1,
                "opacity": 0.5,
                "float_precision": 0,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            }
        )
        series.append(
            {
                "entity": _eid("sensor", room.name, "heating_power_forecast"),
                "name": f"{room.name} Plan",
                "data_generator": _forecast_generator("heating_power"),
                "yaxis_id": "power",
                "curve": "stepline",
                "color": color,
                "stroke_width": 2,
                "opacity": 0.85,
                "float_precision": 0,
                "show": {"in_header": i == 0},
            }
        )
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": "Power – All Rooms", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        "yaxis": [
            {
                "id": "power",
                "apex_config": {
                    "title": {"text": "Power (W)"},
                    "tickAmount": 4,
                },
            }
        ],
        "series": series,
    }


# Repeating colour palette used across the multi-room overview charts so
# the same room keeps the same colour on the temperature and power plots.
_ROOM_PALETTE: List[str] = [
    "#1E88E5",  # blue
    "#43A047",  # green
    "#E53935",  # red
    "#FB8C00",  # orange
    "#8E24AA",  # purple
    "#00ACC1",  # cyan
    "#FFB300",  # amber
    "#6D4C41",  # brown
    "#3949AB",  # indigo
    "#7CB342",  # lime
]


def _system_weather_strip(spec: DashboardSpec) -> Dict[str, Any]:
    """Outdoor temperature + per-room solar gain, history + forecast."""
    outdoor_meas = _system_eid("sensor", "outdoor_temperature_measured")
    outdoor_fc = _system_eid("sensor", "outdoor_temperature_forecast")
    palette = _ROOM_PALETTE

    solar_series: List[Dict[str, Any]] = []
    for i, room in enumerate(spec.rooms):
        color = palette[i % len(palette)]
        solar_series.append(
            {
                "entity": _eid("sensor", room.name, "solar_gain_measured"),
                "name": f"{room.name} solar",
                "yaxis_id": "power",
                "type": "area",
                "color": color,
                "opacity": 0.2,
                "stroke_width": 1,
                "float_precision": 0,
                **_history_series_kwargs(),
                "show": {"in_header": False},
            }
        )
        solar_series.append(
            {
                "entity": _eid("sensor", room.name, "solar_gain_forecast"),
                "name": f"{room.name} solar",
                "data_generator": _forecast_generator("solar_gain"),
                "yaxis_id": "power",
                "type": "area",
                "color": color,
                "opacity": 0.35,
                "stroke_width": 1,
                "float_precision": 0,
                "show": {"in_legend": False, "in_header": False},
            }
        )

    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": "Outdoor & Solar", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#424242"},
        "yaxis": [
            {"id": "temp", "apex_config": {"title": {"text": "Outdoor Temp (°C)"}}},
            {
                "id": "power",
                "opposite": True,
                "min": 0,
                "apex_config": {"title": {"text": "Solar Gain (W)"}},
            },
        ],
        "series": [
            {
                "entity": outdoor_meas,
                "name": "Outdoor",
                "yaxis_id": "temp",
                "color": "#37474F",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 1,
                **_history_series_kwargs(),
                "show": {"in_header": True},
            },
            {
                "entity": outdoor_fc,
                "name": "Outdoor",
                "data_generator": _forecast_generator("outdoor_temp"),
                "yaxis_id": "temp",
                "color": "#78909C",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 1,
                "show": {"in_legend": False, "in_header": False},
            },
            *solar_series,
        ],
    }


def _heat_sources_card(room: RoomSpec, spec: DashboardSpec) -> Optional[Dict[str, Any]]:
    """Entities card listing the physical HA devices for each heat source in the room."""
    room_sources = [s for s in spec.sources if s.room == room.name and s.entity_id]
    if not room_sources:
        return None
    icon = "mdi:heat-pump" if any(s.kind == "heat_pump" for s in room_sources) else "mdi:radiator"
    return {
        "type": "entities",
        "title": "Heating device",
        "state_color": True,
        "icon": icon,
        "entities": [{"entity": s.entity_id, "name": s.name} for s in room_sources],
    }


def _room_view(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    """Per-room subview with MPC triplet, fit gauges, residuals, parameters."""
    _control_cards: List[Dict[str, Any]] = [
        {"type": "heading", "heading": "Control & comfort", "heading_style": "title"},
        _thermostat_card(room),
    ]
    _hs_card = _heat_sources_card(room, spec)
    if _hs_card is not None:
        _control_cards.append(_hs_card)

    sections: List[Dict[str, Any]] = [
        {"type": "grid", "cards": _control_cards},
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
                {
                    "type": "heading",
                    "heading": "Power & electricity price" if spec.has_price else "MPC control input",
                    "heading_style": "title",
                },
                _price_and_power_card(room, spec) if spec.has_price else _mpc_control_card(room, spec),
            ],
        },
    ]

    sections += [
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


def _industrial_overview_view(spec: DashboardSpec) -> Dict[str, Any]:
    """Industrial-process style overview with compact KPI cards and room navigation."""
    # MPC countdown ring – kept as needle gauge for the ring/arc animation.
    # No secondary text about the update interval; the ring makes the cadence visible.
    mpc_gauge: Dict[str, Any] = {
        "type": "gauge",
        "name": "MPC Compute Time",
        "entity": _system_eid("sensor", "mpc_performance"),
        "needle": True,
        "min": 0,
        "max": 60,
        "severity": {"green": 0, "yellow": 20, "red": 40},
    }

    # Compact process-status card groups the remaining KPIs into a single entity
    # list instead of individual large gauge cards, saving significant vertical
    # space especially on phone.
    compact_status_entities: List[Dict[str, Any]] = [
        {
            "entity": _system_eid("sensor", "system_summary"),
            "name": "Total Heating Power",
            "icon": "mdi:fire",
            "icon_color": "#E53935",
        },
        {
            "entity": _system_eid("sensor", "outdoor_temperature_measured"),
            "name": "Outdoor Temperature",
            "icon": "mdi:thermometer",
            "icon_color": "#1E88E5",
        },
        {
            "entity": _system_eid("sensor", "weather_forecast_status"),
            "name": "Weather Forecast",
            "icon": "mdi:weather-partly-cloudy",
            "icon_color": "#00ACC1",
        },
    ]
    if spec.rooms:
        compact_status_entities.append(
            {
                "entity": _eid("sensor", spec.rooms[0].name, "model_fit_quality"),
                "name": f"{spec.rooms[0].name} Fit R²",
                "icon": "mdi:chart-line",
                "icon_color": "#43A047",
            }
        )

    compact_status_card: Dict[str, Any] = {
        "type": "entities",
        "state_color": True,
        "entities": compact_status_entities,
    }

    room_tiles: List[Dict[str, Any]] = [
        {
            "type": "tile",
            "entity": _climate_eid(room.name),
            "name": room.name,
            "icon": room.icon or "mdi:radiator",
            "state_content": ["state", "current_temperature"],
            "tap_action": {
                "action": "navigate",
                "navigation_path": f"/{spec.url_path}/{slugify(room.name)}",
            },
        }
        for room in spec.rooms
    ]

    return {
        "title": "Overview",
        "path": "overview",
        "icon": "mdi:factory",
        "type": "sections",
        "sections": [
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Process KPIs", "heading_style": "title"},
                    mpc_gauge,
                    compact_status_card,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Room Units", "heading_style": "title"},
                    *(
                        room_tiles
                        if room_tiles
                        else [{"type": "markdown", "content": "_No rooms configured._"}]
                    ),
                ],
            },
        ],
    }


def _industrial_temperature_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Temperature Envelope", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#6c7a89"},
        "apex_config": {
            "chart": {"toolbar": {"show": False}},
            "grid": {"borderColor": "#2a3441"},
            "legend": {"show": True, "position": "top"},
            "stroke": {"lineCap": "round"},
        },
        "yaxis": [{"id": "temp", "apex_config": {"title": {"text": "°C"}, "tickAmount": 5}}],
        "series": [
            {
                "entity": _eid("sensor", room.name, "temperature_filtered"),
                "name": "Filtered",
                "yaxis_id": "temp",
                "color": "#58a6ff",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 2,
                **_history_series_kwargs(),
            },
            {
                "entity": _eid("sensor", room.name, "temperature_forecast"),
                "name": "Forecast",
                "data_generator": _forecast_generator("temperature"),
                "yaxis_id": "temp",
                "color": "#2f81f7",
                "stroke_width": 2,
                "stroke_dash": 4,
                "curve": "smooth",
                "float_precision": 2,
            },
            {
                "entity": _eid("sensor", room.name, "setpoint"),
                "name": "Setpoint",
                "yaxis_id": "temp",
                "color": "#f0883e",
                "stroke_width": 1,
                "curve": "stepline",
                "float_precision": 1,
                **_history_series_kwargs(),
            },
            {
                "entity": _eid("sensor", room.name, "setpoint"),
                "name": "Setpoint",
                "data_generator": _forecast_generator("setpoint"),
                "yaxis_id": "temp",
                "color": "#f0883e",
                "stroke_width": 1,
                "stroke_dash": 4,
                "curve": "stepline",
                "float_precision": 1,
                "show": {"in_legend": False, "in_header": False},
            },
            {
                "entity": _eid("sensor", room.name, "constraint_upper"),
                "name": "Upper corridor",
                "data_generator": _forecast_generator("constraint_upper"),
                "yaxis_id": "temp",
                "color": "#8b949e",
                "stroke_width": 1,
                "stroke_dash": 2,
                "curve": "stepline",
                "show": {"in_header": False},
            },
            {
                "entity": _eid("sensor", room.name, "constraint_lower"),
                "name": "Lower corridor",
                "data_generator": _forecast_generator("constraint_lower"),
                "yaxis_id": "temp",
                "color": "#8b949e",
                "stroke_width": 1,
                "stroke_dash": 2,
                "curve": "stepline",
                "show": {"in_header": False},
            },
        ],
    }


def _industrial_power_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Heating Power", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#6c7a89"},
        "apex_config": {
            "chart": {"toolbar": {"show": False}},
            "grid": {"borderColor": "#2a3441"},
            "legend": {"show": True, "position": "top"},
        },
        "yaxis": [{"id": "power", "apex_config": {"title": {"text": "W"}, "tickAmount": 5}}],
        "series": [
            {
                "entity": _eid("sensor", room.name, "heating_power_measured"),
                "name": "Measured",
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": "#f85149",
                "opacity": 0.25,
                "stroke_width": 2,
                **_history_series_kwargs(),
            },
            {
                "entity": _eid("sensor", room.name, "heating_power_forecast"),
                "name": "Plan",
                "data_generator": _forecast_generator("heating_power"),
                "yaxis_id": "power",
                "type": "area",
                "curve": "stepline",
                "color": "#ff7b72",
                "opacity": 0.35,
                "stroke_width": 2,
            },
        ],
    }


def _industrial_disturbance_card(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    return {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": f"{room.name} – Disturbances", "show_states": True},
        "graph_span": f"{int(spec.graph_span_hours)}h",
        "span": {"start": "minute", "offset": f"-{int(spec.history_hours)}h"},
        "now": {"show": True, "label": "", "color": "#6c7a89"},
        "apex_config": {
            "chart": {"toolbar": {"show": False}},
            "grid": {"borderColor": "#2a3441"},
            "legend": {"show": True, "position": "top"},
        },
        "yaxis": [
            {"id": "temp", "apex_config": {"title": {"text": "Outdoor °C"}}},
            {"id": "power", "opposite": True, "min": 0, "apex_config": {"title": {"text": "Solar W"}}},
        ],
        "series": [
            {
                "entity": _system_eid("sensor", "outdoor_temperature_measured"),
                "name": "Outdoor",
                "yaxis_id": "temp",
                "color": "#79c0ff",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 1,
                **_history_series_kwargs(),
            },
            {
                "entity": _system_eid("sensor", "outdoor_temperature_forecast"),
                "name": "Outdoor",
                "data_generator": _forecast_generator("outdoor_temp"),
                "yaxis_id": "temp",
                "color": "#a5d6ff",
                "stroke_width": 2,
                "stroke_dash": 4,
                "curve": "smooth",
                "float_precision": 1,
                "show": {"in_legend": False, "in_header": False},
            },
            {
                "entity": _eid("sensor", room.name, "solar_gain_measured"),
                "name": "Solar",
                "yaxis_id": "power",
                "type": "area",
                "color": "#e3b341",
                "opacity": 0.2,
                "stroke_width": 1,
                **_history_series_kwargs(),
            },
            {
                "entity": _eid("sensor", room.name, "solar_gain_forecast"),
                "name": "Solar",
                "data_generator": _forecast_generator("solar_gain"),
                "yaxis_id": "power",
                "type": "area",
                "color": "#f2cc60",
                "opacity": 0.35,
                "stroke_width": 1,
                "show": {"in_legend": False, "in_header": False},
            },
        ],
    }


def _industrial_room_view(room: RoomSpec, spec: DashboardSpec) -> Dict[str, Any]:
    # Room-temperature gauge retains the ring/arc for at-a-glance comfort status.
    temperature_gauge: Dict[str, Any] = {
        "type": "gauge",
        "entity": _eid("sensor", room.name, "temperature_filtered"),
        "name": "Room Temperature",
        "needle": True,
        "min": 10,
        "max": 30,
        "severity": {"green": 18, "yellow": 16, "red": 14},
    }

    # Power and model-fit are surfaced in a compact entities card with design
    # palette icon colours instead of large individual gauge cards.
    compact_room_kpis: Dict[str, Any] = {
        "type": "entities",
        "state_color": True,
        "entities": [
            {
                "entity": _eid("sensor", room.name, "heating_power_measured"),
                "name": "Delivered Power",
                "icon": "mdi:fire",
                "icon_color": "#E53935",
            },
            {
                "entity": _eid("sensor", room.name, "model_fit_quality"),
                "name": "Model Fit R²",
                "icon": "mdi:chart-line",
                "icon_color": "#43A047",
            },
            {
                "entity": _eid("sensor", room.name, "setpoint"),
                "name": "Setpoint",
                "icon": "mdi:target",
                "icon_color": "#FB8C00",
            },
        ],
    }

    return {
        "title": room.name,
        "path": slugify(room.name),
        "icon": room.icon or "mdi:radiator",
        "subview": True,
        "type": "sections",
        "sections": [
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Unit KPIs", "heading_style": "title"},
                    {"type": "thermostat", "entity": _climate_eid(room.name), "name": room.name},
                    temperature_gauge,
                    compact_room_kpis,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Temperature", "heading_style": "title"},
                    _industrial_temperature_card(room, spec),
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Heating Power", "heading_style": "title"},
                    _industrial_power_card(room, spec),
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Disturbances", "heading_style": "title"},
                    _industrial_disturbance_card(room, spec),
                ],
            },
        ],
    }


def build_industrial_dashboard(spec: DashboardSpec) -> Dict[str, Any]:
    """Return an industrial-style dashboard with overview + room subviews."""
    views: List[Dict[str, Any]] = [_industrial_overview_view(spec)]
    for room in spec.rooms:
        views.append(_industrial_room_view(room, spec))
    return {
        "title": f"{spec.title} – Industrial",
        "views": views,
    }


def _diagnostics_view(spec: DashboardSpec) -> Dict[str, Any]:
    """Diagnostics view – estimation workflow + per-room fit matrix.

    Service actions are rendered as standalone ``type: button`` cards rather
    than ``type: call-service`` rows inside entities cards.  This avoids
    frontend compatibility issues in newer HA versions where call-service
    entity rows interact poorly with services that carry response data.
    """
    est_status = _system_eid("sensor", "estimated_parameters_status")

    # ------------------------------------------------------------------
    # Workflow section
    # ------------------------------------------------------------------
    estimation_card = {
        "type": "entities",
        "title": "Parameter estimation",
        "state_color": True,
        "entities": [
            {"entity": _button_eid("estimate_parameters"), "name": "Estimate now"},
            {"entity": _button_eid("reset_parameters"), "name": "Reset to defaults"},
            {"entity": est_status, "name": "Estimated parameters"},
        ],
    }

    # Dry-run as a standalone button card – keeps the entities card free of
    # call-service rows, which can trigger frontend configuration errors in
    # some HA releases when the target service returns response data.
    dry_run_button: Dict[str, Any] = {
        "type": "button",
        "name": "Dry-run estimation (no apply)",
        "icon": "mdi:flask-empty-outline",
        "tap_action": {
            "action": "call-service",
            "service": f"{DOMAIN}.estimate_parameters_ml",
            "service_data": {"apply_parameters": False},
        },
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

    # ------------------------------------------------------------------
    # Fit matrix / residuals
    # ------------------------------------------------------------------
    fit_rows: List[Dict[str, Any]] = [
        {"entity": _eid("sensor", room.name, "model_fit_quality"), "name": room.name}
        for room in spec.rooms
    ]
    fit_matrix = {
        "type": "entities",
        "title": "Per-room model fit",
        "state_color": True,
        "entities": fit_rows,
    }

    residual_panel = {
        "type": "entities",
        "title": "Residual whiteness (lag-1 ACF)",
        "state_color": True,
        "entities": [
            {"entity": _eid("sensor", room.name, "residual_acf"), "name": room.name}
            for room in spec.rooms
        ],
    }

    # ------------------------------------------------------------------
    # Open-loop validation
    # ------------------------------------------------------------------
    _btn = _button_eid("estimate_parameters")
    open_loop_status: Dict[str, Any] = {
        "type": "markdown",
        "content": (
            "{% set buf = state_attr('" + _btn + "', 'history_steps') | int(0) %}"
            "{% set need = state_attr('" + _btn + "', 'min_steps_required') | int(30) %}"
            "{% if buf < need %}"
            "⏳ **Collecting history:** {{ buf }}/{{ need }} steps"
            " — open-loop RMSE will be available in ≈ {{ [need - buf, 0] | max }} min."
            "{% else %}"
            "✅ Buffer ready ({{ buf }} steps). "
            "Press **Run open-loop simulation** to compute or refresh the RMSE."
            "{% endif %}"
        ),
    }

    open_loop_panel = {
        "type": "entities",
        "title": "Open-loop RMSE",
        "state_color": True,
        "entities": [
            {"entity": _eid("sensor", room.name, "open_loop_rmse"), "name": room.name}
            for room in spec.rooms
        ],
    }

    open_loop_run_button: Dict[str, Any] = {
        "type": "button",
        "name": "Run open-loop simulation",
        "icon": "mdi:play-circle",
        "tap_action": {
            "action": "call-service",
            "service": f"{DOMAIN}.run_open_loop_simulation",
        },
    }
    open_loop_analyse_button: Dict[str, Any] = {
        "type": "button",
        "name": "Analyse model fit",
        "icon": "mdi:chart-bell-curve",
        "tap_action": {
            "action": "call-service",
            "service": f"{DOMAIN}.analyze_model_fit",
        },
    }

    # ------------------------------------------------------------------
    # Parameter identifiability
    # ------------------------------------------------------------------
    parameter_panel = {
        "type": "entities",
        "title": "Identifiability",
        "state_color": True,
        "entities": [
            {"entity": _eid("sensor", room.name, "parameter_confidence"), "name": room.name}
            for room in spec.rooms
        ],
    }

    # ------------------------------------------------------------------
    # Log-likelihood landscape
    # ------------------------------------------------------------------
    loglik_entity_rows: List[Dict[str, Any]] = []
    if spec.rooms:
        loglik_entity_rows.append({"type": "section", "label": "Last computed"})
        for room in spec.rooms:
            loglik_entity_rows.append(
                {
                    "entity": _eid("sensor", room.name, "loglik_slice"),
                    "name": f"{room.name} – computed at",
                }
            )
    else:
        loglik_entity_rows.append({"type": "section", "label": "No rooms configured"})

    loglik_panel = {
        "type": "entities",
        "title": "Log-likelihood landscape",
        "entities": loglik_entity_rows,
    }

    # One button card per room – standalone so the frontend does not need to
    # handle a service response inline (compute_loglik_slice stores its result
    # on the LoglikSliceSensor attribute).
    loglik_button_cards: List[Dict[str, Any]] = [
        {
            "type": "button",
            "name": f"Compute slice – {room.name}",
            "icon": "mdi:chart-bell-curve",
            "tap_action": {
                "action": "call-service",
                "service": f"{DOMAIN}.compute_loglik_slice",
                "service_data": {"room_name": room.name, "n_grid": 11, "span_log": 1.0},
            },
        }
        for room in spec.rooms
    ]

    loglik_help = {
        "type": "markdown",
        "content": (
            "Each **Compute slice** button fires `heating_assistant."
            "compute_loglik_slice` on the matching room, which evaluates the"
            " CD-EKF log-likelihood on an 11×11 (log C, log R_ext) grid"
            " around the current MLE.\n\n"
            "The result is stored on the per-room `…_loglik_slice` sensor."
            " To read the full grid, use"
            " `state_attr('sensor.heating_assistant_<room>_loglik_slice',"
            " 'log_likelihood')` from a template or Plotly card."
        ),
    }

    return {
        "title": "Diagnostics",
        "path": "diagnostics",
        "icon": "mdi:stethoscope",
        "type": "sections",
        "sections": [
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Workflow", "heading_style": "title"},
                    estimation_card,
                    dry_run_button,
                    history_panel,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Fit matrix", "heading_style": "title"},
                    fit_matrix,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Residuals", "heading_style": "title"},
                    residual_panel,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Open-loop validation", "heading_style": "title"},
                    open_loop_status,
                    open_loop_panel,
                    open_loop_run_button,
                    open_loop_analyse_button,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Parameter identifiability", "heading_style": "title"},
                    parameter_panel,
                ],
            },
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": "Log-likelihood landscape", "heading_style": "title"},
                    loglik_panel,
                    *loglik_button_cards,
                    loglik_help,
                ],
            },
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


def _sysid_temperature_card(room: RoomSpec) -> Dict[str, Any]:
    """Temperature comparison chart: measured (red) vs EKF one-step prediction (blue) + ±2σ band."""
    sysid = _eid("sensor", room.name, "sysid_simulation")
    return {
        "type": "custom:apexcharts-card",
        "header": {
            "show": True,
            "title": f"{room.name} – SysID: Measured vs EKF Prediction",
            "show_states": True,
        },
        "graph_span": "1d",
        "apex_config": {
            "chart": {"animations": {"enabled": False}},
            "legend": {"show": True, "position": "top"},
            "noData": {
                "text": (
                    "No simulation data yet.  Press **Run SysID Simulation** "
                    "to generate the comparison."
                ),
                "align": "center",
                "verticalAlign": "middle",
                "style": {"fontSize": "13px", "color": "#9E9E9E"},
            },
        },
        "yaxis": [
            {
                "id": "temp",
                "apex_config": {
                    "title": {"text": "Temperature (°C)"},
                    "decimalsInFloat": 2,
                    "tickAmount": 5,
                },
            }
        ],
        "series": [
            # Covariance upper bound – rendered first so it forms the top of the band
            {
                "entity": sysid,
                "name": "95 % band",
                "yaxis_id": "temp",
                "type": "area",
                "color": "#1E88E5",
                "opacity": 0.15,
                "stroke_width": 0,
                "curve": "smooth",
                "data_generator": (
                    "return (entity.attributes.simulation || []).map(p => "
                    "[new Date(p.time).getTime(), p.cov_upper ?? null]);"
                ),
                "show": {"in_header": False, "in_legend": True},
            },
            # Covariance lower bound – same colour, card background fill creates the band
            {
                "entity": sysid,
                "name": "95 % band",
                "yaxis_id": "temp",
                "type": "area",
                "color": "var(--card-background-color)",
                "opacity": 1.0,
                "stroke_width": 0,
                "curve": "smooth",
                "data_generator": (
                    "return (entity.attributes.simulation || []).map(p => "
                    "[new Date(p.time).getTime(), p.cov_lower ?? null]);"
                ),
                "show": {"in_header": False, "in_legend": False},
            },
            # One-step EKF prediction – blue line
            {
                "entity": sysid,
                "name": "One-step prediction (EKF)",
                "yaxis_id": "temp",
                "color": "#1E88E5",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 2,
                "data_generator": (
                    "return (entity.attributes.simulation || []).map(p => "
                    "[new Date(p.time).getTime(), p.predicted ?? null]);"
                ),
                "show": {"in_header": True, "in_legend": True},
            },
            # Measured temperature – red line
            {
                "entity": sysid,
                "name": "Measured",
                "yaxis_id": "temp",
                "color": "#E53935",
                "stroke_width": 2,
                "curve": "smooth",
                "float_precision": 2,
                "data_generator": (
                    "return (entity.attributes.simulation || []).map(p => "
                    "[new Date(p.time).getTime(), p.measured ?? null]);"
                ),
                "show": {"in_header": True, "in_legend": True},
            },
        ],
    }


def _sysid_param_card(room: RoomSpec) -> Dict[str, Any]:
    """Compact table of the thermal and noise parameters used in the last simulation."""
    sysid = _eid("sensor", room.name, "sysid_simulation")
    return {
        "type": "entities",
        "title": f"{room.name} – Parameters used",
        "state_color": False,
        "entities": [
            {
                "entity": sysid,
                "name": "Open-loop RMSE",
                "icon": "mdi:chart-timeline-variant",
            },
            {
                "entity": sysid,
                "name": "MAE",
                "icon": "mdi:approximately-equal",
                "attribute": "mae",
            },
            {
                "entity": sysid,
                "name": "Thermal mass C [J/K]",
                "icon": "mdi:weight-kilogram",
                "attribute": "thermal_mass",
            },
            {
                "entity": sysid,
                "name": "R_external [K/W]",
                "icon": "mdi:home-thermometer-outline",
                "attribute": "r_external",
            },
            {
                "entity": sysid,
                "name": "σ_w (process noise)",
                "icon": "mdi:sigma",
                "attribute": "sigma_w",
            },
            {
                "entity": sysid,
                "name": "σ_v (measurement noise)",
                "icon": "mdi:sigma",
                "attribute": "sigma_v",
            },
            {
                "entity": sysid,
                "name": "Horizon [h]",
                "icon": "mdi:clock-outline",
                "attribute": "horizon_hours",
            },
            {
                "entity": sysid,
                "name": "Window start (UNIX)",
                "icon": "mdi:calendar-arrow-right",
                "attribute": "window_start",
            },
            {
                "entity": sysid,
                "name": "Window end (UNIX)",
                "icon": "mdi:calendar-arrow-left",
                "attribute": "window_end",
            },
        ],
    }


def _sysid_view(spec: DashboardSpec) -> Dict[str, Any]:
    """System Identification view.

    For each room:
      * A temperature comparison chart (measured red, EKF one-step prediction blue, ±2σ band)
      * A parameter summary card (C, R_ext, σ_w, σ_v, RMSE)

    A global action section lets the user trigger a new reconstruction and shows
    the service call parameters so they can be tweaked from the developer
    tools or a button card.
    """
    sections: List[Dict[str, Any]] = []

    # Entity IDs for the two window datetime pickers and the window-run button
    _win_start_eid = _system_eid("datetime", "sysid_window_start")
    _win_end_eid   = _system_eid("datetime", "sysid_window_end")
    _win_btn_eid   = _system_eid("button", "run_sysid_with_window_button")

    # ------------------------------------------------------------------
    # Intro / run-simulation section
    # ------------------------------------------------------------------
    intro_md: Dict[str, Any] = {
        "type": "markdown",
        "content": (
            "## System Identification\n\n"
            "This page compares **recorded measurements** (red) against the "
            "**EKF one-step-ahead predictions** x̂⁻(k) (blue) of the 1R1C "
            "thermal model.  At each step the filter predicts the next "
            "temperature from the previous *filtered* state, records that "
            "prediction, then corrects with the actual measurement.  "
            "Residuals stay bounded — unlike a free-run simulation — so "
            "systematic deviations pinpoint model-plant mismatch.\n\n"
            "The **shaded blue band** shows ±2σ of the one-step output "
            "uncertainty: √(P⁻[i,i] + σ_v²).  A wide band means the "
            "continuous process noise σ_w [K/√s] is large relative to the "
            "thermal time-constant.\n\n"
            "Use **Quick Horizon Presets** below to run over the most recent "
            "N hours, or pick an exact date/time range with "
            "**Custom Window** to target a specific experiment "
            "(e.g. a step-response test performed last night)."
        ),
    }

    sections.append(
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "System Identification", "heading_style": "title"},
                intro_md,
            ],
        }
    )

    # ------------------------------------------------------------------
    # Custom window selection section
    # ------------------------------------------------------------------
    window_md: Dict[str, Any] = {
        "type": "markdown",
        "content": (
            "### Custom Window\n\n"
            "Set a precise **start** and **end** time for the identification "
            "window — useful when the history buffer contains data from "
            "multiple sessions or when you ran a dedicated step-response "
            "experiment at a known time.  "
            "Only data within the selected range is used; everything outside "
            "is ignored.\n\n"
            "1. Adjust the **Window Start** and **Window End** pickers.\n"
            "2. Press **Run SysID with Window** to compute."
        ),
    }

    window_start_card: Dict[str, Any] = {
        "type": "entities",
        "title": "Window Start",
        "entities": [
            {
                "entity": _win_start_eid,
                "name": "Start",
            }
        ],
    }

    window_end_card: Dict[str, Any] = {
        "type": "entities",
        "title": "Window End",
        "entities": [
            {
                "entity": _win_end_eid,
                "name": "End",
            }
        ],
    }

    run_window_button: Dict[str, Any] = {
        "type": "button",
        "name": "Run SysID with Window",
        "icon": "mdi:calendar-search",
        "tap_action": {
            "action": "call-service",
            "service": "button.press",
            "service_data": {"entity_id": _win_btn_eid},
        },
    }

    sections.append(
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Custom Window", "heading_style": "title"},
                window_md,
                window_start_card,
                window_end_card,
                run_window_button,
            ],
        }
    )

    # ------------------------------------------------------------------
    # Quick horizon presets
    # ------------------------------------------------------------------
    horizon_buttons: List[Dict[str, Any]] = [
        {
            "type": "button",
            "name": f"{h}h horizon",
            "icon": "mdi:timer-outline",
            "tap_action": {
                "action": "call-service",
                "service": f"{DOMAIN}.run_sysid_simulation",
                "service_data": {"horizon_hours": float(h)},
            },
        }
        for h in (1, 3, 6, 12, 24)
    ]

    sections.append(
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Quick Horizon Presets", "heading_style": "title"},
                *horizon_buttons,
            ],
        }
    )

    # ------------------------------------------------------------------
    # Per-room chart + parameter table
    # ------------------------------------------------------------------
    for room in spec.rooms:
        sections.append(
            {
                "type": "grid",
                "cards": [
                    {
                        "type": "heading",
                        "heading": f"{room.name}",
                        "heading_style": "title",
                    },
                    _sysid_temperature_card(room),
                    _sysid_param_card(room),
                    {
                        "type": "button",
                        "name": f"Run SysID – {room.name} only",
                        "icon": "mdi:play-circle",
                        "tap_action": {
                            "action": "call-service",
                            "service": f"{DOMAIN}.run_sysid_simulation",
                            "service_data": {
                                "room_name": room.name,
                                "horizon_hours": 6.0,
                            },
                        },
                    },
                ],
            }
        )

    # ------------------------------------------------------------------
    # Help section: how to tune parameters from Developer Tools
    # ------------------------------------------------------------------
    help_md: Dict[str, Any] = {
        "type": "markdown",
        "title": "Tuning parameters",
        "content": (
            "To run a simulation with custom parameters, go to **Developer "
            "Tools → Services**, select `heating_assistant.run_sysid_simulation` "
            "and supply any combination of:\n\n"
            "| Field | Description | Default |\n"
            "|---|---|---|\n"
            "| `horizon_hours` | Window length from now [h] (ignored when `window_start`/`window_end` are set) | 6.0 |\n"
            "| `window_start` | UNIX timestamp – explicit window start (overrides horizon) | — |\n"
            "| `window_end` | UNIX timestamp – explicit window end | — |\n"
            "| `sigma_w` | Continuous process noise intensity [K/√s] | controller σ_w |\n"
            "| `sigma_v` | Measurement noise std [°C] | controller σ_v |\n"
            + "".join(
                f"| `thermal_mass_{slugify(r.name)}` | Thermal mass for {r.name} [J/K] | model value |\n"
                f"| `r_external_{slugify(r.name)}` | R_external for {r.name} [K/W] | model value |\n"
                for r in spec.rooms
            )
            + "\nRe-run estimation with **Estimate Parameters (ML)** on the "
            "Diagnostics page to update the model before comparing."
        ),
    }

    sections.append(
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "How to tune", "heading_style": "title"},
                help_md,
            ],
        }
    )

    return {
        "title": "System ID",
        "path": "system-id",
        "icon": "mdi:chart-scatter-plot",
        "type": "sections",
        "sections": sections,
    }


def build_dashboard(spec: DashboardSpec) -> Dict[str, Any]:
    """Return the full Lovelace dashboard configuration as a dict."""
    views: List[Dict[str, Any]] = [_overview_view(spec)]
    for room in spec.rooms:
        views.append(_room_view(room, spec))
    views.append(_diagnostics_view(spec))
    views.append(_sysid_view(spec))
    views.append(_settings_view(spec))

    return {
        "title": spec.title,
        "views": views,
    }


def build_dashboard_variant(
    spec: DashboardSpec,
    variant: str = DASHBOARD_VARIANT_CLASSIC,
) -> Dict[str, Any]:
    """Build a named dashboard variant."""
    if variant == DASHBOARD_VARIANT_INDUSTRIAL:
        return build_industrial_dashboard(spec)
    return build_dashboard(spec)


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
        sources.append(HeatSourceSpec(
            name=src.name,
            room=src.room,
            kind=kind_str,
            entity_id=getattr(src, "heater_entity", None),
        ))

    horizon = getattr(coordinator, "_horizon", None)
    dt = getattr(coordinator, "dt", None) or getattr(coordinator, "_update_interval_s", None)
    if horizon is not None and dt is not None:
        forecast_hours = horizon * float(dt) / 3600.0
    else:
        forecast_hours = 3.0

    has_price = bool(getattr(coordinator, "_price_entity", None))

    return build_dashboard_variant(
        DashboardSpec(
            rooms=tuple(rooms),
            sources=tuple(sources),
            forecast_hours=forecast_hours,
            has_price=has_price,
        )
    )


def build_dashboard_variant_from_coordinator(
    coordinator: Any,
    variant: str = DASHBOARD_VARIANT_CLASSIC,
) -> Dict[str, Any]:
    """Build a dashboard variant from a live coordinator."""
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
        sources.append(HeatSourceSpec(
            name=src.name,
            room=src.room,
            kind=kind_str,
            entity_id=getattr(src, "heater_entity", None),
        ))

    horizon = getattr(coordinator, "_horizon", None)
    dt = getattr(coordinator, "dt", None) or getattr(coordinator, "_update_interval_s", None)
    if horizon is not None and dt is not None:
        forecast_hours = horizon * float(dt) / 3600.0
    else:
        forecast_hours = 3.0

    has_price = bool(getattr(coordinator, "_price_entity", None))

    return build_dashboard_variant(
        DashboardSpec(
            rooms=tuple(rooms),
            sources=tuple(sources),
            forecast_hours=forecast_hours,
            has_price=has_price,
        ),
        variant=variant,
    )


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
