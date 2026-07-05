"""Setup-assist thermal simulation and parameter estimation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator


def simulate_thermal_response(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    initial_temp: float,
    outdoor_temp: float,
    heating_power: float,
    duration_hours: float,
) -> Dict[str, Any]:
    """Run a standalone thermal simulation for setup verification."""
    if room_name not in coordinator.model.rooms:
        return {"error": f"Room {room_name!r} not found"}

    dt = 60.0
    steps = int(duration_hours * 3600 / dt)

    initial_temps = {
        name: initial_temp if name == room_name else outdoor_temp
        for name in coordinator.model.room_names
    }
    heat_schedule = [
        {
            name: heating_power if name == room_name else 0.0
            for name in coordinator.model.room_names
        }
        for _ in range(steps)
    ]
    outdoor_temps = [outdoor_temp] * steps
    solar_schedule = [{name: 0.0 for name in coordinator.model.room_names}] * steps

    preds = coordinator.model.predict(
        horizon=steps,
        dt=dt,
        heat_schedule=heat_schedule,
        outdoor_temps=outdoor_temps,
        solar_gain_schedule=solar_schedule,
        initial_temps=initial_temps,
    )

    trajectory = []
    for i, pred in enumerate(preds):
        minutes = (i + 1) * dt / 60.0
        if i % 5 == 0 or i == len(preds) - 1:
            trajectory.append({
                "time_minutes": round(minutes, 1),
                "temperature": round(pred[room_name], 2),
            })

    tau = coordinator.model.time_constant(room_name)
    t_ss = coordinator.model.steady_state_temperature(
        room_name, heating_power, outdoor_temp,
    )

    return {
        "trajectory": trajectory,
        "final_temperature": round(preds[-1][room_name], 2),
        "time_constant_hours": round(tau / 3600, 2),
        "steady_state_temperature": round(t_ss, 2),
    }


def estimate_parameters(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    heating_power: float,
    outdoor_temp: float,
    initial_temp: float,
    final_temp: float,
    duration_seconds: float,
) -> Dict[str, Any]:
    """Estimate thermal_mass and r_external from a heating experiment."""
    if room_name not in coordinator.model.rooms:
        return {"error": f"Room {room_name!r} not found"}

    room = coordinator.model.rooms[room_name]
    delta_t = final_temp - initial_temp
    avg_temp = (initial_temp + final_temp) / 2.0

    temp_diff = avg_temp - outdoor_temp
    if heating_power > 0 and temp_diff > 0:
        estimated_r = temp_diff / heating_power
    else:
        estimated_r = room.r_external

    avg_loss = temp_diff / estimated_r if estimated_r > 0 else 0
    q_net = heating_power - avg_loss
    if duration_seconds > 0 and abs(delta_t) > 0.01:
        estimated_mass = abs(q_net * duration_seconds / delta_t)
    else:
        estimated_mass = room.thermal_mass

    return {
        "estimated_thermal_mass": round(estimated_mass, 0),
        "estimated_r_external": round(estimated_r, 4),
        "current_thermal_mass": room.thermal_mass,
        "current_r_external": room.r_external,
        "notes": (
            "These are rough estimates. For thermal_mass, a typical room "
            "is 2–15 × 10⁶ J/K. For r_external, typical values are "
            "0.02–0.15 K/W. Run multiple experiments and average results."
        ),
    }
