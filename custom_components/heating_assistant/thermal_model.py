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

from .const import (
    AIR_RHO_CP,
    DEFAULT_INFILTRATION_FRACTION,
    SHERMAN_GRIMSRUD_DT_TYPICAL,
    SHERMAN_GRIMSRUD_STACK_COEF,
    SHERMAN_GRIMSRUD_V_TYPICAL,
    SHERMAN_GRIMSRUD_WIND_COEF,
)
from .integrator import implicit_euler_step


def _sherman_grimsrud_factor(v: float, dT: float) -> float:
    """
    Square-root term of the Sherman–Grimsrud LBL infiltration model:

        √( C_s · |ΔT| + C_w · v² )

    Used both to derive the per-room leakage area ``L`` from
    typical-conditions calibration and to evaluate the wind-driven
    conductance at runtime.
    """
    return float(np.sqrt(
        SHERMAN_GRIMSRUD_STACK_COEF * abs(dT)
        + SHERMAN_GRIMSRUD_WIND_COEF * v * v
    ))


# Pre-computed Sherman–Grimsrud factor at the reference conditions used
# for typical-conditions calibration of ``L``.  Kept as a module constant
# so the cold-path leakage-area derivation is a single multiply.
_SG_FACTOR_TYPICAL = _sherman_grimsrud_factor(
    SHERMAN_GRIMSRUD_V_TYPICAL, SHERMAN_GRIMSRUD_DT_TYPICAL,
)


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
    r_external: float                           # K/W (at the typical reference
                                                # conditions:
                                                # v=SHERMAN_GRIMSRUD_V_TYPICAL m/s,
                                                # |ΔT|=SHERMAN_GRIMSRUD_DT_TYPICAL K).
                                                # When no wind information is
                                                # available the runtime UA equals
                                                # exactly 1 / r_external.
    connections: List[RoomConnection] = field(default_factory=list)
    windows: List[Window] = field(default_factory=list)
    temperature: float = 20.0                   # °C, current state
    setpoint: float = 21.0                      # °C, desired temperature
    internal_gain: float = 0.0                  # W, constant background heat
                                                # gain (occupants, electronics,
                                                # appliances).  Identified
                                                # jointly with C and R_ext.
    infiltration_fraction: float = DEFAULT_INFILTRATION_FRACTION
    # 0 ≤ infiltration_fraction ≤ 1.  Fraction of 1/r_external attributed
    # to wind-driven Sherman–Grimsrud infiltration at typical conditions.
    # When equal to 0 the room has no wind sensitivity; when equal to 1
    # the entire envelope loss is wind-driven (unusual but valid for a
    # leaky single-room outbuilding).  Defaulted by the building's
    # envelope-tightness preset; see const.ENVELOPE_TIGHTNESS_*.


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

        # Build state-space matrices once.  At the typical reference
        # conditions the assembled (A, B_ext) reproduces the bundled
        # 1/r_external behaviour exactly; the wind-driven overlay below is
        # zero at those conditions.
        self._C, self._A, self._B_ext = self._build_matrices()

        # Per-room effective leakage area L_i (m²), derived so that
        #   ρ c_p · L_i · √(C_s·ΔT_typ + C_w·v_typ²) = f_i / r_external_i .
        # This makes the wind-driven UA reduce to f_i / r_external_i at
        # typical conditions, which is the share of 1/r_external the
        # ``infiltration_fraction`` setting attributes to infiltration.
        self._leakage_area = np.array(
            [
                max(0.0, room.infiltration_fraction)
                * (1.0 / room.r_external)
                / (AIR_RHO_CP * _SG_FACTOR_TYPICAL)
                for room in (self._rooms[name] for name in self._room_list)
            ],
            dtype=float,
        )

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
    # Wind-driven infiltration overlay (Sherman–Grimsrud, Phase 1 C1)
    # ------------------------------------------------------------------

    def infiltration_delta_ua(
        self,
        outdoor_temp: float,
        wind_speed: Optional[float],
        room_temps: np.ndarray,
    ) -> np.ndarray:
        """
        Per-room *delta* on the external conductance relative to the
        typical-conditions baseline already baked into ``A`` and ``B_ext``.

        Returns ``Δ ∈ ℝⁿ`` such that the effective external conductance is

            UA_ext_i(v, ΔT) = (1 / r_external_i) + Δᵢ          [W/K],

        and the contribution to the heat balance is ``Δᵢ · (T_out − T_i)``.

        At the reference conditions ``v = SHERMAN_GRIMSRUD_V_TYPICAL`` and
        ``|ΔT| = SHERMAN_GRIMSRUD_DT_TYPICAL`` this returns the zero
        vector, so the baseline behaviour is preserved.

        When ``wind_speed`` is ``None`` the function also returns the zero
        vector — no wind data ⇒ stick to the typical-conditions UA, i.e.
        exactly the pre-C1 behaviour.
        """
        if wind_speed is None or not np.all(np.isfinite([wind_speed])):
            return np.zeros(self._n)

        v = float(max(0.0, wind_speed))
        # |ΔT| per room at the start of the substep (linearly-implicit
        # treatment — the coefficient is refreshed between sub-steps).
        dT_abs = np.abs(room_temps - outdoor_temp)
        sg = np.sqrt(
            SHERMAN_GRIMSRUD_STACK_COEF * dT_abs
            + SHERMAN_GRIMSRUD_WIND_COEF * v * v
        )
        ua_inf = AIR_RHO_CP * self._leakage_area * sg
        ua_inf_typ = AIR_RHO_CP * self._leakage_area * _SG_FACTOR_TYPICAL
        return ua_inf - ua_inf_typ

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
        wind_speed: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Advance the thermal model by one time step using implicit (backward)
        Euler.

        For the lumped-RC model the drift ``dT/dt = C⁻¹ (A T + B_ext T_out +
        Q)`` is affine in ``T`` (the Jacobian ``C⁻¹ A`` is independent of
        the state), so the implicit-Euler residual is linear in ``T_next``
        and Newton converges in a single iteration — i.e. one ``n × n``
        linear solve per call.  L-stability ensures the step stays
        accurate and well-conditioned even when later phases introduce
        stiffer envelope / slab dynamics.

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
        wind_speed : float or None, optional
            Outdoor wind speed [m/s].  When provided the Sherman–Grimsrud
            infiltration overlay (Phase 1 C1) adjusts the per-room
            external conductance for wind-driven air exchange.  When
            ``None`` the runtime UA equals exactly ``1 / r_external``
            (the typical-conditions baseline).

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

        # Constant per-room internal gains (occupants, electronics, ...)
        for i, name in enumerate(self._room_list):
            Q[i] += self._rooms[name].internal_gain

        # Wind-driven external-conductance overlay (delta from typical).
        # Linearly-implicit treatment: the coefficient is frozen at the
        # current state for this single ``dt`` step.
        delta_ua = self.infiltration_delta_ua(outdoor_temp, wind_speed, T)

        # dT/dt = C^{-1} * ((A - diag(δ)) T + (B_ext + δ) T_out + Q)
        inv_C = 1.0 / self._C
        F = (self._A - np.diag(delta_ua)) * inv_C[:, None]
        bias = ((self._B_ext + delta_ua) * outdoor_temp + Q) * inv_C

        def rhs(state: np.ndarray) -> np.ndarray:
            return F @ state + bias

        def jacobian(_state: np.ndarray) -> np.ndarray:
            return F

        T_new = implicit_euler_step(rhs, jacobian, T, dt)

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
        wind_speeds: Optional[List[float]] = None,
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
        wind_speeds : list of float, optional
            Outdoor wind speed [m/s] per step.  When omitted the model
            falls back to its typical-conditions external conductance
            (the pre-C1 behaviour).  Shorter than ``horizon`` is fine —
            the last entry is held constant.

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
            wind_k: Optional[float] = None
            if wind_speeds:
                wind_k = wind_speeds[k] if k < len(wind_speeds) else wind_speeds[-1]
            temps = self.step(
                dt=dt,
                heat_inputs=heat_schedule[k] if k < len(heat_schedule) else {},
                outdoor_temp=outdoor_temps[k] if k < len(outdoor_temps) else outdoor_temps[-1],
                solar_gains=solar_gain_schedule[k] if k < len(solar_gain_schedule) else {},
                wind_speed=wind_k,
            )
            predictions.append(dict(temps))

        # Restore original state
        for name, temp in saved.items():
            self._rooms[name].temperature = temp

        return predictions

    # ------------------------------------------------------------------
    # Heat-flow analysis
    # ------------------------------------------------------------------

    def compute_heat_flows(
        self,
        outdoor_temp: float,
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute the instantaneous heat-flow breakdown for every room.

        For each room the returned dict contains:

        * ``external_loss`` – heat flow to outdoor [W] (positive = losing heat)
        * ``<other_room>`` – heat flow to/from each connected room [W]
          (positive = losing heat to that room)
        * ``total_loss`` – algebraic sum of all loss terms [W]

        Parameters
        ----------
        outdoor_temp : float
            Outdoor air temperature [°C].

        Returns
        -------
        dict
            ``{room_name: {component: watts, ...}}``
        """
        idx = {name: i for i, name in enumerate(self._room_list)}
        flows: Dict[str, Dict[str, float]] = {}

        for name, room in self._rooms.items():
            breakdown: Dict[str, float] = {}

            # Heat loss to outdoors
            external_loss = (room.temperature - outdoor_temp) / room.r_external
            breakdown["external_loss"] = round(external_loss, 2)

            # Heat flow to each connected room
            total = external_loss
            for conn in room.connections:
                other_temp = self._rooms[conn.connected_room].temperature
                flow = (room.temperature - other_temp) / conn.r_value
                breakdown[conn.connected_room] = round(flow, 2)
                total += flow

            breakdown["total_loss"] = round(total, 2)
            flows[name] = breakdown

        return flows

    def time_constant(self, room_name: str) -> float:
        """
        Return the dominant thermal time constant τ = C × R_eff [seconds]
        for a room, where R_eff is the effective thermal resistance combining
        external and inter-room paths in parallel.

        This is useful for setup: it tells the user how many seconds the room
        takes to respond to a step change (63 % of final value in 1 τ).
        """
        room = self._rooms[room_name]
        g_total = 1.0 / room.r_external
        for conn in room.connections:
            g_total += 1.0 / conn.r_value
        r_eff = 1.0 / g_total
        return room.thermal_mass * r_eff

    def steady_state_temperature(
        self,
        room_name: str,
        heating_power: float,
        outdoor_temp: float,
    ) -> float:
        """
        Compute the steady-state temperature a room would reach with a
        constant heating power, assuming all connected rooms are at the
        outdoor temperature (worst case).

        Useful during setup to verify that a heater is powerful enough.

        Parameters
        ----------
        room_name : str
        heating_power : float
            Constant thermal power input [W].
        outdoor_temp : float
            Outdoor temperature [°C].

        Returns
        -------
        float
            Steady-state room temperature [°C].
        """
        room = self._rooms[room_name]
        g_total = 1.0 / room.r_external
        for conn in room.connections:
            g_total += 1.0 / conn.r_value
        return outdoor_temp + heating_power / g_total
