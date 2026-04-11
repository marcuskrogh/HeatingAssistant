"""
Diagnostics support for the Heating Assistant integration.

Provides a full system state dump accessible via the HA diagnostics
panel (Settings → Devices & Services → Heating Assistant → Diagnostics).
This includes configuration, model state, heat flow breakdown, prediction
trajectory, and controller parameters — useful for troubleshooting and
verifying setup.
"""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator
from .heat_sources import HeatPump


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> Dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    model = coordinator.model

    # --- Room details ---
    rooms_diag = {}
    for name, room in model.rooms.items():
        rooms_diag[name] = {
            "temperature": round(room.temperature, 2),
            "setpoint": room.setpoint,
            "thermal_mass": room.thermal_mass,
            "r_external": room.r_external,
            "time_constant_hours": round(
                model.time_constant(name) / 3600, 2
            ),
            "connections": [
                {"room": c.connected_room, "r_value": c.r_value}
                for c in room.connections
            ],
            "windows": [
                {
                    "area": w.area,
                    "orientation": w.orientation,
                    "tilt": w.tilt,
                }
                for w in room.windows
            ],
        }

    # --- Heat source details ---
    sources_diag = {}
    for src in coordinator.heat_sources:
        info: Dict[str, Any] = {
            "type": type(src).__name__,
            "room": src.room,
            "max_power": src.max_power,
            "current_power": round(src.current_power, 1),
            "control_action": coordinator.actions.get(src.name, 0.0),
        }
        if isinstance(src, HeatPump):
            info.update({
                "cop_rated": src.cop_rated,
                "cop_temp_ref": src.cop_temp_ref,
                "cop_current": round(
                    src.cop(coordinator.outdoor_temp), 2
                ),
                "min_power": src.min_power,
            })
        sources_diag[src.name] = info

    # --- Heat flows ---
    heat_flows = coordinator.heat_flows

    # --- Predictions ---
    predictions_diag = []
    for i, pred in enumerate(coordinator.predictions):
        predictions_diag.append({
            "step": i + 1,
            "temperatures": {
                k: round(v, 2) for k, v in pred.items()
            },
        })

    # --- Solar gains ---
    solar_gains = {
        k: round(v, 1) for k, v in coordinator.solar_gains.items()
    }

    # --- Steady-state analysis ---
    steady_state = {}
    for name in model.room_names:
        sources_in_room = [
            s for s in coordinator.heat_sources if s.room == name
        ]
        total_max_power = sum(s.max_power for s in sources_in_room)
        if total_max_power > 0:
            steady_state[name] = {
                "max_heating_power": total_max_power,
                "steady_state_at_minus_10": round(
                    model.steady_state_temperature(name, total_max_power, -10.0), 1
                ),
                "steady_state_at_0": round(
                    model.steady_state_temperature(name, total_max_power, 0.0), 1
                ),
                "steady_state_at_5": round(
                    model.steady_state_temperature(name, total_max_power, 5.0), 1
                ),
            }

    return {
        "outdoor_temperature": coordinator.outdoor_temp,
        "rooms": rooms_diag,
        "heat_sources": sources_diag,
        "heat_flows": heat_flows,
        "solar_gains": solar_gains,
        "predictions": predictions_diag,
        "steady_state_analysis": steady_state,
        "controller": {
            "horizon": coordinator._horizon,
            "dt": coordinator._dt,
            "latitude": coordinator._latitude,
            "longitude": coordinator._longitude,
        },
    }
