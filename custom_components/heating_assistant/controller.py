"""
Model Predictive Controller for the Heating Assistant integration.

Algorithm
---------
1. Collect the current room temperatures and outdoor temperature.
2. Build a *N*-step prediction of disturbances (solar gains, outdoor temp).
3. For each room, run a receding-horizon search over discrete power levels
   (off / 33 % / 67 % / 100 %) to minimise a cost that balances:
   - Temperature tracking error  (setpoint − predicted temperature)²
   - Energy consumption (proportional to heater power)
4. Apply only the first step of the optimal sequence (receding horizon).

The search is performed independently for each room because the inter-room
coupling is weak over a single time step and independent optimisation avoids
a combinatorial explosion.  A full multi-room joint optimisation can be
added later without changing the public API.

Public API
----------
    controller = MPCController(model, heat_sources, config)
    actions = controller.compute(outdoor_temp, solar_gains, now)
    # actions: {source_name: setpoint_fraction (0‑1)}
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .thermal_model import HouseModel
from .heat_sources import HeatSource
from .solar_model import room_solar_gains


# Discrete control levels tried during the horizon search
_LEVELS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

# Asymmetric tracking-error penalty weights.
# Being *too cold* (positive error = setpoint − temperature > 0) is penalised
# with a weight of 1.0 so the controller strongly avoids under-heating.
# Being *too warm* (negative error) is penalised with a smaller weight of 0.5
# because mild overheating is less uncomfortable than under-heating and the
# heat pump / heater will naturally reduce output as the room warms up.
_PENALTY_TOO_COLD = 1.0
_PENALTY_TOO_WARM = 0.5


class MPCController:
    """
    Receding-horizon model-based controller.

    Parameters
    ----------
    model : HouseModel
        Thermal model of the house.
    heat_sources : list of HeatSource
        All controllable heat sources.
    horizon : int
        Prediction horizon (number of time steps).
    dt : float
        Time step in seconds.
    latitude : float
        Site latitude [degrees] (for solar prediction).
    longitude : float
        Site longitude [degrees].
    energy_weight : float
        Weight on the energy-consumption term in the cost function.
        Higher values favour lower energy use over tighter temperature tracking.
    """

    def __init__(
        self,
        model: HouseModel,
        heat_sources: List[HeatSource],
        horizon: int = 6,
        dt: float = 900.0,
        latitude: float = 55.0,
        longitude: float = 12.0,
        energy_weight: float = 0.01,
    ) -> None:
        self._model = model
        self._sources = heat_sources
        self._horizon = horizon
        self._dt = dt
        self._latitude = latitude
        self._longitude = longitude
        self._energy_weight = energy_weight

        # Group sources by room
        self._room_sources: Dict[str, List[HeatSource]] = {}
        for src in heat_sources:
            self._room_sources.setdefault(src.room, []).append(src)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def compute(
        self,
        outdoor_temp: float,
        solar_gains: Optional[Dict[str, float]] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """
        Compute optimal control actions for the current time step.

        Parameters
        ----------
        outdoor_temp : float
            Current outdoor temperature [°C].
        solar_gains : dict, optional
            Pre-computed solar gains {room: W}.  If None they are computed
            from the solar model using ``now`` and the stored lat/lon.
        now : datetime, optional
            Current time (UTC).  Required when solar_gains is None.

        Returns
        -------
        dict
            ``{source_name: setpoint_fraction}`` where setpoint_fraction ∈ [0, 1].
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)

        if solar_gains is None:
            solar_gains = self._compute_solar_gains(now)

        # Build prediction arrays for the horizon
        outdoor_temps = self._forecast_outdoor(outdoor_temp)
        solar_schedules = self._forecast_solar(now)

        actions: Dict[str, float] = {}

        # Optimise each room independently
        for room_name in self._model.room_names:
            sources_in_room = self._room_sources.get(room_name, [])
            if not sources_in_room:
                continue

            best_cost = float("inf")
            best_fractions = {src.name: 0.0 for src in sources_in_room}

            # Generate all combinations of discrete levels for sources in this room
            level_grid = list(itertools.product(_LEVELS, repeat=len(sources_in_room)))

            for fractions in level_grid:
                frac_map = {
                    src.name: fractions[k]
                    for k, src in enumerate(sources_in_room)
                }
                cost = self._rollout_cost(
                    room_name,
                    frac_map,
                    outdoor_temps,
                    solar_schedules,
                    sources_in_room,
                )
                if cost < best_cost:
                    best_cost = cost
                    best_fractions = dict(frac_map)

            actions.update(best_fractions)

        # Apply and return
        for src in self._sources:
            src.set_power(actions.get(src.name, 0.0), outdoor_temp)

        return actions

    # ------------------------------------------------------------------
    # Cost function (receding horizon rollout)
    # ------------------------------------------------------------------

    def _rollout_cost(
        self,
        room_name: str,
        frac_map: Dict[str, float],
        outdoor_temps: List[float],
        solar_schedules: List[Dict[str, float]],
        sources: List[HeatSource],
    ) -> float:
        """
        Simulate the house model over the prediction horizon with a constant
        control action and return the total discounted cost.
        """
        room = self._model.rooms[room_name]
        setpoint = room.setpoint

        # Build heat-input schedule: same action for all horizon steps
        heat_schedule = []
        for k in range(self._horizon):
            q_room = sum(
                src.thermal_power(frac_map[src.name], outdoor_temps[k])
                for src in sources
            )
            # Other rooms keep their current heater powers (constant)
            heat_k: Dict[str, float] = {}
            for r in self._model.room_names:
                if r == room_name:
                    heat_k[r] = q_room
                else:
                    heat_k[r] = sum(
                        s.current_power
                        for s in self._room_sources.get(r, [])
                    )
            heat_schedule.append(heat_k)

        predictions = self._model.predict(
            horizon=self._horizon,
            dt=self._dt,
            heat_schedule=heat_schedule,
            outdoor_temps=outdoor_temps,
            solar_gain_schedule=solar_schedules,
        )

        cost = 0.0
        discount = 1.0
        for k, pred in enumerate(predictions):
            temp = pred.get(room_name, setpoint)
            # Asymmetric tracking error: penalise under-heating more than over-heating.
            # See module-level _PENALTY_TOO_COLD / _PENALTY_TOO_WARM constants.
            error = setpoint - temp
            tracking = (_PENALTY_TOO_COLD if error >= 0 else _PENALTY_TOO_WARM) * error ** 2
            # Energy cost (proportional to total fraction)
            energy = self._energy_weight * sum(frac_map.values())
            cost += discount * (tracking + energy)
            discount *= 0.9  # discount factor per step

        return cost

    # ------------------------------------------------------------------
    # Disturbance forecasts
    # ------------------------------------------------------------------

    def _forecast_outdoor(self, current: float) -> List[float]:
        """
        Simple persistence forecast: outdoor temperature stays constant.
        A real implementation would pull a weather-API forecast.
        """
        return [current] * self._horizon

    def _forecast_solar(self, now: datetime) -> List[Dict[str, float]]:
        """
        Predict solar gains for each future time step using the solar model.
        """
        schedules = []
        for k in range(self._horizon):
            future = now + timedelta(seconds=self._dt * k)
            gains = {
                name: room_solar_gains(
                    self._model.rooms[name].windows,
                    future,
                    self._latitude,
                    self._longitude,
                )
                for name in self._model.room_names
            }
            schedules.append(gains)
        return schedules

    def _compute_solar_gains(self, now: datetime) -> Dict[str, float]:
        """Compute current-step solar gains for all rooms."""
        return {
            name: room_solar_gains(
                self._model.rooms[name].windows,
                now,
                self._latitude,
                self._longitude,
            )
            for name in self._model.room_names
        }
