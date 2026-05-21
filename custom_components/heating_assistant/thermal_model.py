"""
Thermal model for the Heating Assistant integration.

Each room is modelled as a lumped-parameter RC thermal circuit (1R1C):

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
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_DELTA_T_SKY,
    DEFAULT_FACADE_ABSORPTANCE,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    MAX_INFILTRATION_FRACTION,
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


class Room:
    """Lumped-parameter 1R1C thermal model of a single room.

    A single temperature ``T`` represents the room state.  All heat inputs
    (heater, solar, internal gains) land directly on this node; the
    outdoor coupling is via the bundled ``1/r_external``.

    The constructor accepts ``temperature=`` as the primary initialiser.
    For backward compatibility, ``air_temperature``, ``wall_temperature``,
    and ``slab_temperature`` keyword arguments are silently ignored (they
    were part of the 2R2C phase; removing them from the signature would
    break existing call sites).
    """

    def __init__(
        self,
        name: str,
        thermal_mass: float,
        r_external: float,
        connections: Optional[List["RoomConnection"]] = None,
        windows: Optional[List["Window"]] = None,
        setpoint: float = 21.0,
        comfort_offset: Optional[float] = None,
        internal_gain: float = 0.0,
        infiltration_fraction: float = DEFAULT_INFILTRATION_FRACTION,
        sky_radiative_ua: float = DEFAULT_SKY_RADIATIVE_UA,
        facade_absorptance: float = DEFAULT_FACADE_ABSORPTANCE,
        facade_solar_share: float = DEFAULT_FACADE_SOLAR_SHARE,
        thermal_bridge_psi_l: float = DEFAULT_THERMAL_BRIDGE_PSI_L,
        temperature: Optional[float] = None,
        # Legacy 2R2C parameters — accepted but ignored so existing call
        # sites (coordinator, parameter_estimator) continue to work
        # without modification until those are cleaned up.
        air_temperature: Optional[float] = None,
        wall_temperature: Optional[float] = None,
        slab_temperature: Optional[float] = None,
        c_air_fraction: float = 0.05,
        r_aw_fraction: float = 0.05,
        floor_type: str = "none",
        c_slab_fraction: Optional[float] = None,
        r_sa: Optional[float] = None,
        r_sg: Optional[float] = None,
    ) -> None:
        self.name = name
        self.thermal_mass = float(thermal_mass)
        self.r_external = float(r_external)
        self.connections = list(connections) if connections is not None else []
        self.windows = list(windows) if windows is not None else []
        self.setpoint = float(setpoint)
        self.comfort_offset = float(
            DEFAULT_COMFORT_OFFSET if comfort_offset is None else comfort_offset
        )
        self.internal_gain = float(internal_gain)
        self.infiltration_fraction = float(infiltration_fraction)

        # Phase 1 C3 — long-wave to sky.
        self.sky_radiative_ua: float = max(0.0, float(sky_radiative_ua))

        # Phase 1 C4 — sol-air on opaque surfaces.
        self.facade_absorptance: float = float(np.clip(facade_absorptance, 0.0, 1.0))
        self.facade_solar_share: float = max(0.0, float(facade_solar_share))

        # Phase 1 C5 — linear thermal-bridge correction.
        self.thermal_bridge_psi_l: float = max(0.0, float(thermal_bridge_psi_l))

        # Single temperature node — initialise from ``temperature`` kwarg,
        # or fall back to ``air_temperature`` (legacy), then default 20 °C.
        if temperature is not None:
            self.temperature: float = float(temperature)
        elif air_temperature is not None:
            self.temperature = float(air_temperature)
        else:
            self.temperature = 20.0

    def __repr__(self) -> str:
        return (
            f"Room(name={self.name!r}, thermal_mass={self.thermal_mass}, "
            f"r_external={self.r_external}, "
            f"temperature={self.temperature}, "
            f"setpoint={self.setpoint})"
        )


class HouseModel:
    """
    Aggregated 1R1C thermal model of an entire house.

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

        # Phase 1 C3 / C4 / C5 — per-room envelope-correction terms.
        self._sky_ua = np.array(
            [self._rooms[name].sky_radiative_ua for name in self._room_list],
            dtype=float,
        )
        self._thermal_bridge = np.array(
            [self._rooms[name].thermal_bridge_psi_l for name in self._room_list],
            dtype=float,
        )
        self._facade_absorptance = np.array(
            [self._rooms[name].facade_absorptance for name in self._room_list],
            dtype=float,
        )
        self._facade_solar_share = np.array(
            [self._rooms[name].facade_solar_share for name in self._room_list],
            dtype=float,
        )

        # Effective sky-temperature depression below outdoor air [K].
        self._delta_t_sky: float = DEFAULT_DELTA_T_SKY

        # Build state-space matrices once.
        self._C, self._A, self._B_ext = self._build_matrices()

        # Phase 1 C3 sky cooling-drift bias (per state row).
        # Magnitude: −sky_radiative_ua · ΔT_sky / thermal_mass.
        # Acts as a constant negative drift on the single temperature node.
        self._B_sky_offset = np.zeros(self._n)
        for i in range(self._n):
            if self._sky_ua[i] > 0.0 and self._C[i] > 0.0:
                self._B_sky_offset[i] = (
                    -self._sky_ua[i] * self._delta_t_sky / self._C[i]
                )

        # Per-room effective leakage area L_i (m²), derived so that the
        # wind-driven UA reduces to f_i / r_external_i at typical conditions.
        self._leakage_area = np.array(
            [
                max(0.0, room.infiltration_fraction)
                * (1.0 / room.r_external)
                / (AIR_RHO_CP * _SG_FACTOR_TYPICAL)
                for room in (self._rooms[name] for name in self._room_list)
            ],
            dtype=float,
        )

    def rebuild_derived_parameters(self) -> None:
        """Recompute all cached derived arrays from the current room attributes.

        Must be called whenever ``room.thermal_mass``, ``room.r_external``,
        or any connection's ``r_value`` is updated (e.g. after parameter
        estimation).  Follow this with ``_build_matrices()`` to refresh the
        state-space matrices and assign the results back to ``_C``, ``_A``,
        and ``_B_ext``.

        Updates in-place:
        * ``_leakage_area``  — Sherman–Grimsrud effective area
        * ``_B_sky_offset``  — sky cooling-drift bias vector
        """
        # Leakage area depends on r_external.
        for i, name in enumerate(self._room_list):
            room = self._rooms[name]
            self._leakage_area[i] = (
                max(0.0, room.infiltration_fraction)
                * (1.0 / room.r_external)
                / (AIR_RHO_CP * _SG_FACTOR_TYPICAL)
            )

        # Sky cooling-drift bias depends on thermal_mass (via C).
        # Recompute C first so the offset uses the latest values.
        C_new, _, _ = self._build_matrices()
        self._B_sky_offset = np.zeros(self._n)
        for i in range(self._n):
            if self._sky_ua[i] > 0.0 and C_new[i] > 0.0:
                self._B_sky_offset[i] = (
                    -self._sky_ua[i] * self._delta_t_sky / C_new[i]
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
    def n(self) -> int:
        """Number of rooms (state-vector size is n)."""
        return self._n

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room temperatures."""
        return {name: self._rooms[name].temperature for name in self._room_list}

    def set_temperatures(self, temps: Dict[str, float]) -> None:
        """Update the room temperatures from measurements."""
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].temperature = float(temp)

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
        vector — no wind data ⇒ stick to the typical-conditions UA.
        """
        if wind_speed is None or not np.all(np.isfinite([wind_speed])):
            return np.zeros(self._n)

        v = float(max(0.0, wind_speed))
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

    def _build_matrices(
        self,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Build the continuous-time state matrices for the 1R1C network.

        State ordering: ``x = [T_1, ..., T_n]`` of length ``n``.

        Equations of motion (without the wind-driven infiltration
        overlay, which is added at step time as a delta):

            C_i dT_i/dt = Q_i
                        + (T_out - T_i) * (1/R_ext_i + sky_ua_i + bridge_i)
                        + Σ_j (T_j - T_i) / R_ij

        Returns
        -------
        C : (n,) thermal capacitance vector.
        A : (n, n) drift matrix.
        B_ext : (n,) outdoor input vector.
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
            g_sky = float(self._sky_ua[i])
            g_bridge = float(self._thermal_bridge[i])
            g_outdoor_total = g_ext + g_sky + g_bridge

            # Diagonal: outdoor coupling (includes sky UA and thermal bridge).
            A[i, i] -= g_outdoor_total

            # Outdoor input vector.
            B_ext[i] = g_outdoor_total

            # Inter-room conduction.
            for conn in room.connections:
                k = idx[conn.connected_room]
                g = 1.0 / conn.r_value
                A[i, k] += g
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
        ground_temp: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Advance the thermal model by one time step using implicit (backward)
        Euler.

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
            Outdoor wind speed [m/s] (Phase 1 C1 Sherman–Grimsrud overlay).
            ``None`` ⇒ typical-conditions baseline.
        ground_temp : float or None, optional
            Ground temperature [°C] — accepted for API compatibility but
            has no effect in 1R1C (no slab node).

        Returns
        -------
        dict
            New room temperatures {name: temp °C}.
        """
        n = self._n
        # Pack current state: x = [T_1, ..., T_n].
        x = np.array([self._rooms[name].temperature for name in self._room_list])

        # Heat dispatch: all inputs land on the single temperature node.
        Q = np.zeros(n)
        for name, power in heat_inputs.items():
            if name in self._rooms:
                i = self._room_list.index(name)
                Q[i] += power
        for name, gain in solar_gains.items():
            if name in self._rooms:
                i = self._room_list.index(name)
                Q[i] += gain
                # Phase 1 C4 sol-air facade heat input on the single node.
                share = self._facade_solar_share[i]
                if share > 0.0:
                    alpha = self._facade_absorptance[i]
                    Q[i] += alpha * share * float(gain)
        for i, name in enumerate(self._room_list):
            Q[i] += self._rooms[name].internal_gain

        # Wind-driven external-conductance overlay (delta from typical).
        delta_ua = self.infiltration_delta_ua(outdoor_temp, wind_speed, x)

        # Build the effective drift matrix.
        A_eff = self._A.copy()
        B_eff_ext = self._B_ext.copy()
        for i in range(n):
            A_eff[i, i] -= delta_ua[i]
            B_eff_ext[i] += delta_ua[i]

        inv_C = 1.0 / self._C
        F = A_eff * inv_C[:, None]
        bias = (
            B_eff_ext * outdoor_temp
            + Q
        ) * inv_C
        # Phase 1 C3 sky cooling-drift bias — pre-divided by C in the
        # constructor, so add directly to the bias.
        bias = bias + self._B_sky_offset

        def rhs(state: np.ndarray) -> np.ndarray:
            return F @ state + bias

        def jacobian(_state: np.ndarray) -> np.ndarray:
            return F

        x_new = implicit_euler_step(rhs, jacobian, x, dt)

        new_temps: Dict[str, float] = {}
        for i, name in enumerate(self._room_list):
            self._rooms[name].temperature = float(x_new[i])
            new_temps[name] = float(x_new[i])

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
            Outdoor wind speed [m/s] per step.

        Returns
        -------
        list of dict
            Predicted temperatures {name: °C} for each step 1…horizon.
        """
        # Save temperatures, run prediction, restore state.
        saved = {n: r.temperature for n, r in self._rooms.items()}
        if initial_temps is not None:
            for name, temp in initial_temps.items():
                if name in self._rooms:
                    self._rooms[name].temperature = float(temp)

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

        # Restore original state.
        for name in self._rooms:
            self._rooms[name].temperature = saved[name]

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

            # Heat loss to outdoors via bundled 1/r_external.
            external_loss = (room.temperature - outdoor_temp) / room.r_external
            breakdown["external_loss"] = round(external_loss, 2)

            # Heat flow to each connected room.
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
