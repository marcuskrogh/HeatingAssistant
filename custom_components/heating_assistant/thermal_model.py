"""
Thermal model for the Heating Assistant integration.

Each room is modelled as a lumped-parameter RC thermal circuit:

    C_i * dT_i/dt = Q_heater_i
                  + sum_{j adj i} (T_j - T_i) / R_ij   # inter-room conduction
                  + (T_outdoor - T_i) / R_i_ext          # fabric heat loss
                  + Q_solar_i                            # solar gain

where
    C_i   – thermal mass of room i  [J/K]
    T_i   – temperature of room i   [°C]
    R_ij  – thermal resistance between rooms i and j  [K/W]
    R_i_ext – thermal resistance to the outdoor environment  [K/W]
    Q_heater_i – power from all heat sources assigned to room i  [W]
    Q_solar_i  – solar heat gain through windows of room i  [W]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class RoomConnection:
    """Describes a thermal connection between two rooms."""

    connected_room: str   # name of the adjacent room
    r_value: float        # thermal resistance K/W


@dataclass
class Window:
    """Describes a window contributing to solar heat gain."""

    area: float           # m²
    orientation: float    # degrees clockwise from North (0=N, 90=E, 180=S, 270=W)
    tilt: float = 90.0    # degrees from horizontal (90 = vertical wall)


@dataclass
class Room:
    """Lumped-parameter thermal model of a single room."""

    name: str
    thermal_mass: float                         # J/K
    r_external: float                           # K/W (to outdoor)
    connections: List[RoomConnection] = field(default_factory=list)
    windows: List[Window] = field(default_factory=list)
    temperature: float = 20.0                   # °C, current state
    setpoint: float = 21.0                      # °C, desired temperature


class HouseModel:
    """
    Aggregated thermal model of an entire house.

    Usage::

        model = HouseModel(rooms)
        new_temps = model.step(
            dt=900,
            heat_inputs={"living_room": 1000, "bedroom": 0},
            outdoor_temp=-5.0,
            solar_gains={"living_room": 200, "bedroom": 50},
        )
    """

    def __init__(self, rooms: List[Room]) -> None:
        self._rooms: Dict[str, Room] = {r.name: r for r in rooms}
        self._room_list: List[str] = [r.name for r in rooms]
        self._n = len(rooms)

        # Build state-space matrices once (they are time-invariant except for
        # the outdoor-temperature input column which is precomputed per step)
        self._C, self._A, self._B_ext = self._build_matrices()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def rooms(self) -> Dict[str, Room]:
        return self._rooms

    @property
    def room_names(self) -> List[str]:
        return self._room_list

    @property
    def temperatures(self) -> Dict[str, float]:
        return {name: self._rooms[name].temperature for name in self._room_list}

    def set_temperatures(self, temps: Dict[str, float]) -> None:
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].temperature = temp

    # ------------------------------------------------------------------
    # Matrix construction
    # ------------------------------------------------------------------

    def _build_matrices(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Build the continuous-time state matrices for the lumped RC model.

        State equation:
            C * dT/dt = A * T + B_ext * T_outdoor + Q   (Q = heater + solar)

        Returns
        -------
        C : (n,) diagonal capacitance vector
        A : (n, n) conductance matrix  (off-diagonal: R_ij, diagonal: -sum_j R_ij - R_i_ext)
        B_ext : (n,) vector of outdoor conductances  (1/R_i_ext for each room)
        """
        n = self._n
        C = np.zeros(n)
        A = np.zeros((n, n))
        B_ext = np.zeros(n)

        idx = {name: i for i, name in enumerate(self._room_list)}

        for name, room in self._rooms.items():
            i = idx[name]
            C[i] = room.thermal_mass
            g_ext = 1.0 / room.r_external
            B_ext[i] = g_ext
            A[i, i] -= g_ext

            for conn in room.connections:
                j = idx[conn.connected_room]
                g = 1.0 / conn.r_value
                A[i, j] += g
                A[i, i] -= g

        return C, A, B_ext

    # ------------------------------------------------------------------
    # Integration step
    # ------------------------------------------------------------------

    def step(
        self,
        dt: float,
        heat_inputs: Dict[str, float],
        outdoor_temp: float,
        solar_gains: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Advance the thermal model by one time step using the explicit
        (forward) Euler method.  For small dt (≤ 15 min) this is accurate
        enough while keeping the implementation dependency-free.

        Parameters
        ----------
        dt : float
            Time step in seconds.
        heat_inputs : dict
            Mapping room name → heater power [W].
        outdoor_temp : float
            Outdoor air temperature [°C].
        solar_gains : dict
            Mapping room name → solar heat gain [W].

        Returns
        -------
        dict
            New room temperatures {name: temp °C}.
        """
        T = np.array([self._rooms[name].temperature for name in self._room_list])
        Q = np.zeros(self._n)

        for name, power in heat_inputs.items():
            if name in self._rooms:
                Q[self._room_list.index(name)] += power

        for name, gain in solar_gains.items():
            if name in self._rooms:
                Q[self._room_list.index(name)] += gain

        # dT/dt = C^{-1} * (A*T + B_ext*T_outdoor + Q)
        dT_dt = (self._A @ T + self._B_ext * outdoor_temp + Q) / self._C
        T_new = T + dT_dt * dt

        new_temps = {}
        for i, name in enumerate(self._room_list):
            self._rooms[name].temperature = float(T_new[i])
            new_temps[name] = float(T_new[i])

        return new_temps

    # ------------------------------------------------------------------
    # Prediction helper used by the MPC controller
    # ------------------------------------------------------------------

    def predict(
        self,
        horizon: int,
        dt: float,
        heat_schedule: List[Dict[str, float]],
        outdoor_temps: List[float],
        solar_gain_schedule: List[Dict[str, float]],
        initial_temps: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, float]]:
        """
        Simulate the model over a prediction horizon without mutating state.

        Parameters
        ----------
        horizon : int
            Number of future time steps.
        dt : float
            Time step in seconds.
        heat_schedule : list of dict
            Heat input [W] per room for each future time step.
        outdoor_temps : list of float
            Outdoor temperature [°C] for each future time step.
        solar_gain_schedule : list of dict
            Solar heat gain [W] per room for each future time step.
        initial_temps : dict, optional
            Starting room temperatures; defaults to current model state.

        Returns
        -------
        list of dict
            Predicted temperatures {name: °C} for each step 1…horizon.
        """
        # Save state, run prediction on a copy, restore state
        saved = {name: room.temperature for name, room in self._rooms.items()}
        if initial_temps is not None:
            for name, temp in initial_temps.items():
                if name in self._rooms:
                    self._rooms[name].temperature = temp

        predictions: List[Dict[str, float]] = []
        for k in range(horizon):
            temps = self.step(
                dt=dt,
                heat_inputs=heat_schedule[k] if k < len(heat_schedule) else {},
                outdoor_temp=outdoor_temps[k] if k < len(outdoor_temps) else outdoor_temps[-1],
                solar_gains=solar_gain_schedule[k] if k < len(solar_gain_schedule) else {},
            )
            predictions.append(dict(temps))

        # Restore original state
        for name, temp in saved.items():
            self._rooms[name].temperature = temp

        return predictions
