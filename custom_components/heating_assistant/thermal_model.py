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
    DEFAULT_C_AIR_FRACTION,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_R_AW_FRACTION,
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
    """Lumped-parameter thermal model of a single room.

    Phase 1 A1 promotes each room from a single ``T`` node to a 2R2C
    network with:

    - a fast **air** node ``T_a`` (small ``C_a`` ≈ ``c_air_fraction × thermal_mass``),
    - a slow **envelope** node ``T_w`` (large ``C_w`` ≈ ``(1 − c_air_fraction) × thermal_mass``),
    - an internal film resistance ``R_aw`` coupling them, and
    - a wall-to-outdoor resistance ``R_we`` carrying the room's
      conductive heat loss to the outdoor.

    The bundled ``thermal_mass`` and ``r_external`` remain the user-
    facing typology inputs.  The split fractions
    ``c_air_fraction`` and ``r_aw_fraction`` are optional (defaulted by
    a sensible typology-neutral value) and can be overridden per room.

    The split derivation (performed inside ``HouseModel``):

    - ``c_air  = c_air_fraction × thermal_mass``
    - ``c_wall = (1 − c_air_fraction) × thermal_mass``
    - ``R_cond_total = r_external / (1 − infiltration_fraction)``
    - ``r_aw  = r_aw_fraction × R_cond_total``
    - ``r_we  = (1 − r_aw_fraction) × R_cond_total``

    The ``R_cond_total`` factor reflects that only the *conductive*
    share of the room's envelope path goes through the wall (the
    infiltration share bypasses the wall and lands on the air node
    directly, as configured by Phase 1 C1).

    The constructor accepts ``temperature=`` as a legacy alias that
    initialises *both* ``air_temperature`` and ``wall_temperature`` to
    the same value (the natural cold-start convention) — this keeps the
    pre-A1 construction call sites working unchanged.  The ``.temperature``
    attribute is a live ``@property`` that reads/writes the air node,
    matching what a room thermistor would measure.
    """

    def __init__(
        self,
        name: str,
        thermal_mass: float,
        r_external: float,
        connections: Optional[List["RoomConnection"]] = None,
        windows: Optional[List["Window"]] = None,
        air_temperature: Optional[float] = None,
        wall_temperature: Optional[float] = None,
        setpoint: float = 21.0,
        internal_gain: float = 0.0,
        infiltration_fraction: float = DEFAULT_INFILTRATION_FRACTION,
        c_air_fraction: float = DEFAULT_C_AIR_FRACTION,
        r_aw_fraction: float = DEFAULT_R_AW_FRACTION,
        temperature: Optional[float] = None,
    ) -> None:
        self.name = name
        self.thermal_mass = float(thermal_mass)
        self.r_external = float(r_external)
        self.connections = list(connections) if connections is not None else []
        self.windows = list(windows) if windows is not None else []
        self.setpoint = float(setpoint)
        self.internal_gain = float(internal_gain)
        self.infiltration_fraction = float(infiltration_fraction)
        self.c_air_fraction = float(c_air_fraction)
        self.r_aw_fraction = float(r_aw_fraction)

        # Cold-start convention: when ``temperature`` is explicitly
        # provided (legacy single-state API) it initialises BOTH nodes
        # to that value.  Otherwise air / wall fall back to their
        # explicit kwargs or the 20 °C default.
        if temperature is not None:
            self.air_temperature = float(temperature)
            self.wall_temperature = float(temperature)
        else:
            self.air_temperature = float(20.0 if air_temperature is None else air_temperature)
            self.wall_temperature = float(20.0 if wall_temperature is None else wall_temperature)

    # ------------------------------------------------------------------
    # Live-tracking accessor for the air-node temperature.  Reads and
    # writes go through the air state so the legacy
    # ``room.temperature`` callers stay correct under 2R2C.
    # ------------------------------------------------------------------

    @property
    def temperature(self) -> float:
        """Air-node temperature (what a room thermistor measures).

        Live alias for ``air_temperature`` — reads return the current
        air state; writes update only the air state.  The wall state
        ``wall_temperature`` is separate and is reconstructed by the
        EKF.
        """
        return self.air_temperature

    @temperature.setter
    def temperature(self, value: float) -> None:
        self.air_temperature = float(value)

    def __repr__(self) -> str:
        return (
            f"Room(name={self.name!r}, thermal_mass={self.thermal_mass}, "
            f"r_external={self.r_external}, "
            f"air_temperature={self.air_temperature}, "
            f"wall_temperature={self.wall_temperature}, "
            f"setpoint={self.setpoint})"
        )


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

        # Derived per-room 2R2C parameters.  ``c_air_fraction`` and
        # ``r_aw_fraction`` come from the Room dataclass (defaulted by
        # typology); ``infiltration_fraction`` is clamped to keep the
        # conductive path well-conditioned.
        self._c_air = np.zeros(self._n)
        self._c_wall = np.zeros(self._n)
        self._r_aw = np.zeros(self._n)
        self._r_we = np.zeros(self._n)
        for i, name in enumerate(self._room_list):
            room = self._rooms[name]
            f_inf = float(np.clip(
                room.infiltration_fraction, 0.0, MAX_INFILTRATION_FRACTION,
            ))
            cond_frac = max(1.0 - f_inf, 1e-3)
            c_air_frac = float(np.clip(room.c_air_fraction, 1e-3, 1.0 - 1e-3))
            r_aw_frac = float(np.clip(room.r_aw_fraction, 1e-3, 1.0 - 1e-3))
            self._c_air[i] = c_air_frac * room.thermal_mass
            self._c_wall[i] = (1.0 - c_air_frac) * room.thermal_mass
            r_cond_total = room.r_external / cond_frac
            self._r_aw[i] = r_aw_frac * r_cond_total
            self._r_we[i] = (1.0 - r_aw_frac) * r_cond_total

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
        # Infiltration is a parallel path that lands on the *air* node
        # (Phase 1 C1 + A1): cold infiltrating air mixes directly with
        # the room air, not the envelope mass.
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
    def n(self) -> int:
        """Number of rooms (state-vector size is ``2 * n``)."""
        return self._n

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room *air* temperatures (the user-visible value).

        Backward-compat accessor: callers that previously read the
        single ``temperature`` state now see the air-node value, which
        is what a thermistor would measure.  See ``wall_temperatures``
        for the slow envelope node.
        """
        return {name: self._rooms[name].air_temperature for name in self._room_list}

    @property
    def wall_temperatures(self) -> Dict[str, float]:
        """Per-room *wall* (envelope-node) temperatures.

        The wall node is unobserved by direct measurement and is
        reconstructed by the EKF from the dynamics.  Exposed here so
        diagnostics and the dashboard can surface it as a separate
        per-room sensor.
        """
        return {name: self._rooms[name].wall_temperature for name in self._room_list}

    def set_temperatures(self, temps: Dict[str, float]) -> None:
        """Update the air-node temperatures from measurements.

        Wall-node temperatures are not touched — the EKF reconstructs
        them.  On a cold start when no prior wall estimate exists, the
        caller should also call ``set_wall_temperatures`` to initialise
        the wall to the same value as the air.
        """
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].air_temperature = float(temp)

    def set_wall_temperatures(self, temps: Dict[str, float]) -> None:
        """Update the wall-node temperatures explicitly.

        Used by the EKF after a predict-update cycle to record the
        latest wall-temperature estimate, and during cold-start
        initialisation to set ``T_w = T_a`` per room.
        """
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].wall_temperature = float(temp)

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
        Build the continuous-time state matrices for the 2R2C network.

        State ordering: ``x = [T_a_0, …, T_a_{n−1}, T_w_0, …, T_w_{n−1}]``
        of length ``2n``.

        Equations of motion (without the wind-driven infiltration
        overlay, which is added at step time as a delta on the air
        block — see ``infiltration_delta_ua``):

            C_a_i Ṫ_a_i = Q_h_i + Q_s_i + Q_int_i + (T_w_i − T_a_i)/R_aw_i
            C_w_i Ṫ_w_i = (T_a_i − T_w_i)/R_aw_i + (T_out − T_w_i)/R_we_i
                                                 + Σ_j (T_w_j − T_w_i)/R_ij

        Returns
        -------
        C : (2n,) diagonal capacitance vector ``[C_a, C_w]``.
        A : (2n, 2n) drift matrix with block structure::

                A = [ -1/R_aw      +1/R_aw                  ]   ← air block
                    [ +1/R_aw   -(1/R_aw + 1/R_we + ΣR_ij)  ]   ← wall block

            Inter-room couplings appear in the wall–wall block:
            ``A[n+i, n+j] += 1/R_ij``.

        B_ext : (2n,) outdoor input vector with zero on the air block
            (conductive coupling to outdoor goes wall → outdoor) and
            ``1/R_we_i`` on the wall block.
        """
        n = self._n
        C = np.zeros(2 * n)
        A = np.zeros((2 * n, 2 * n))
        B_ext = np.zeros(2 * n)

        idx = {name: i for i, name in enumerate(self._room_list)}

        # Air block (rows 0..n−1) and wall block (rows n..2n−1).
        for name, room in self._rooms.items():
            i = idx[name]
            j_w = n + i  # wall index for room i

            C[i] = self._c_air[i]
            C[j_w] = self._c_wall[i]

            g_aw = 1.0 / self._r_aw[i]
            g_we = 1.0 / self._r_we[i]

            # Air node: gain/loss to wall via internal film R_aw.
            A[i, i] -= g_aw
            A[i, j_w] += g_aw

            # Wall node: gain/loss to air via R_aw + loss to outdoor via R_we.
            A[j_w, i] += g_aw
            A[j_w, j_w] -= g_aw
            A[j_w, j_w] -= g_we

            # Outdoor input on the wall block only.
            B_ext[j_w] = g_we

            # Inter-room conduction routes through the wall nodes
            # (mass-to-mass partition coupling — locked design call;
            # see roadmap §17.2).
            for conn in room.connections:
                k = idx[conn.connected_room]
                k_w = n + k
                g = 1.0 / conn.r_value
                A[j_w, k_w] += g
                A[j_w, j_w] -= g

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
            New room *air* temperatures {name: temp °C}.  Wall
            temperatures are also updated on each ``Room`` and can be
            read via :attr:`HouseModel.wall_temperatures`.
        """
        n = self._n
        # Pack current state: x = [T_a (n), T_w (n)].
        T_air = np.array([self._rooms[name].air_temperature for name in self._room_list])
        T_wall = np.array([self._rooms[name].wall_temperature for name in self._room_list])
        x = np.concatenate([T_air, T_wall])

        # Heat input lands on the air block only.  Q has shape (2n,) so
        # it slots straight into the air rows; the wall block stays zero.
        Q = np.zeros(2 * n)
        for name, power in heat_inputs.items():
            if name in self._rooms:
                Q[self._room_list.index(name)] += power
        for name, gain in solar_gains.items():
            if name in self._rooms:
                Q[self._room_list.index(name)] += gain
        # Constant per-room internal gains (occupants, electronics, ...).
        for i, name in enumerate(self._room_list):
            Q[i] += self._rooms[name].internal_gain

        # Wind-driven external-conductance overlay (delta from typical),
        # applied on the *air* block only (Phase 1 C1 + A1: infiltrating
        # air mixes directly with the room air, not the envelope mass).
        delta_ua = self.infiltration_delta_ua(outdoor_temp, wind_speed, T_air)

        # Build the effective 2n×2n drift matrix.  The overlay subtracts
        # δ from each air-block diagonal entry and adds δ to the air-block
        # outdoor input; the wall block is unchanged.
        A_eff = self._A.copy()
        B_eff = self._B_ext.copy()
        for i in range(n):
            A_eff[i, i] -= delta_ua[i]
            B_eff[i] += delta_ua[i]

        inv_C = 1.0 / self._C
        F = A_eff * inv_C[:, None]
        bias = (B_eff * outdoor_temp + Q) * inv_C

        def rhs(state: np.ndarray) -> np.ndarray:
            return F @ state + bias

        def jacobian(_state: np.ndarray) -> np.ndarray:
            return F

        x_new = implicit_euler_step(rhs, jacobian, x, dt)
        T_air_new = x_new[:n]
        T_wall_new = x_new[n:]

        new_temps: Dict[str, float] = {}
        for i, name in enumerate(self._room_list):
            self._rooms[name].air_temperature = float(T_air_new[i])
            self._rooms[name].wall_temperature = float(T_wall_new[i])
            new_temps[name] = float(T_air_new[i])

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
        # Save both air- and wall-node temperatures, run prediction on a
        # copy, restore state.  The wall state is part of the model so
        # it must be saved alongside the air state — otherwise repeated
        # predict() calls would slowly drift the cached wall temperature.
        saved_air = {n: r.air_temperature for n, r in self._rooms.items()}
        saved_wall = {n: r.wall_temperature for n, r in self._rooms.items()}
        if initial_temps is not None:
            for name, temp in initial_temps.items():
                if name in self._rooms:
                    self._rooms[name].air_temperature = float(temp)
                    # Cold-start wall = air; subsequent steps update it
                    # naturally.
                    self._rooms[name].wall_temperature = float(temp)

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
        for name in self._rooms:
            self._rooms[name].air_temperature = saved_air[name]
            self._rooms[name].wall_temperature = saved_wall[name]

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

            # Heat loss to outdoors.  Reported as the quasi-steady-state
            # equivalent (air → outdoor through the bundled 1/r_external)
            # so the diagnostic stays comparable across the 1R1C → 2R2C
            # transition.  The actual wall-to-outdoor flow at any given
            # instant differs slightly because T_w ≠ T_a — that detail
            # is exposed via the per-room ``wall_temperatures`` view.
            external_loss = (room.air_temperature - outdoor_temp) / room.r_external
            breakdown["external_loss"] = round(external_loss, 2)

            # Heat flow to each connected room.  Inter-room couplings
            # route through wall nodes (mass-to-mass partitions), so we
            # use wall temperatures here.
            total = external_loss
            for conn in room.connections:
                other_wall = self._rooms[conn.connected_room].wall_temperature
                flow = (room.wall_temperature - other_wall) / conn.r_value
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
