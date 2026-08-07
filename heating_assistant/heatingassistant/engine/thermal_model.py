"""
Thermal model for the Heating Assistant integration.

Each room is modelled as a lumped-parameter **2R2C** thermal circuit: a
fast *air* node ``T_a`` (room air + light furnishings) and a slow
*wall/mass* node ``T_w`` (walls, floor, ceiling, heavy furniture):

    C_a,i dT_a,i/dt = Q_heater_i + Q_int,i + (1 − w_s) s_i Q_solar_i
                    + (T_w,i − T_a,i) / R_aw,i        # air ↔ wall coupling
                    + (T_out − T_a,i) · g_inf,i        # infiltration (air exchange)

    C_w,i dT_w,i/dt = (T_a,i − T_w,i) / R_aw,i
                    + (T_out − T_w,i) · g_wout,i       # wall conduction + sky + bridge
                    + (w_s + α_i · share_i) s_i Q_solar_i
                    + Σ_{j adj i} (T_w,j − T_w,i) / R_ij   # inter-room (wall-to-wall)

where

    C_i      – total room thermal mass [J/K]  (user-facing, as before)
    C_a,i    – c_air_fraction · C_i            (fast node)
    C_w,i    – (1 − c_air_fraction) · C_i      (slow node)
    R_ext,i  – total steady-state resistance to outdoors [K/W] (user-facing)
    g_inf,i  – infiltration_fraction / R_ext,i   (air → outdoor, wind-modulated)
    g_cond,i – (1 − infiltration_fraction) / R_ext,i  (conductive path)
    R_aw,i   – r_aw_fraction / g_cond,i⁻¹ split  (air ↔ wall share of the path)
    R_we,i   – remainder of the conductive path  (wall ↔ outdoor)
    g_wout,i – 1/R_we,i + sky_radiative_ua + thermal_bridge_psi_l
    s_i      – per-room solar-gain scale (identified from data; default 1)
    w_s      – SOLAR_WALL_FRACTION, share of window solar landing on the mass

The parametrisation is deliberately chosen so that the **user-facing
parameters keep their 1R1C meaning**: at steady state with a constant heat
input ``Q`` the air node settles at ``T_out + Q · R_ext`` exactly as the old
single-node model did, because ``g_inf`` parallel with the series pair
``(R_aw, R_we)`` reproduces ``1/R_ext``.  The two split fractions
(``c_air_fraction``, ``r_aw_fraction``) are the only structural additions;
in the limit ``r_aw_fraction → 0`` the nodes lock together and the model
degenerates to the previous 1R1C with ``(C, R_ext)``.

Measurements observe the **air node only**; the wall node is reconstructed
by the EKF.  See ``model_diagnostics.wall_state_observability`` for the
observability metric that monitors how well-conditioned that
reconstruction is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np

from .const import (
    AIR_RHO_CP,
    DEFAULT_C_AIR_FRACTION,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_DELTA_T_SKY,
    DEFAULT_FACADE_ABSORPTANCE,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_R_AW_FRACTION,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_SOLAR_FACING,
    DEFAULT_SOLAR_SCALE,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    MAX_INFILTRATION_FRACTION,
    SHERMAN_GRIMSRUD_DT_TYPICAL,
    SHERMAN_GRIMSRUD_STACK_COEF,
    SHERMAN_GRIMSRUD_V_TYPICAL,
    SHERMAN_GRIMSRUD_WIND_COEF,
    SOLAR_WALL_FRACTION,
)
from .integrator import implicit_euler_step

#: Numerical floor/ceiling on the air↔wall resistance share.  At 0 the
#: coupling conductance diverges; at 1 the wall loses its outdoor path.
_R_AW_FRACTION_MIN = 0.01
_R_AW_FRACTION_MAX = 0.95

#: Bounds on the air share of the thermal mass — keeps both capacitances
#: strictly positive and the stiffness ratio integrable.
_C_AIR_FRACTION_MIN = 0.01
_C_AIR_FRACTION_MAX = 0.60


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
    """Lumped-parameter 2R2C thermal model of a single room.

    Two temperature nodes: ``temperature`` is the measured/controlled
    **air** node; ``wall_temperature`` is the slow envelope/mass node
    that the EKF reconstructs.  All heat sources and internal gains land
    on the air node; solar gain is split between the nodes with the
    fixed ``SOLAR_WALL_FRACTION``.

    ``thermal_mass`` and ``r_external`` keep their previous (1R1C)
    meaning — total heat capacity and total steady-state resistance to
    outdoors.  ``c_air_fraction`` / ``r_aw_fraction`` describe how that
    total is split between the two nodes; both are bounded and have
    typology defaults, and the parameter estimator refines them per room
    when the data identify them.

    The constructor accepts (and ignores) the slab-era keyword arguments
    ``slab_temperature``, ``floor_type``, ``c_slab_fraction``, ``r_sa``
    and ``r_sg`` so configurations written during the earlier 3-node
    phase keep loading.  A slab node may return later for underfloor
    heating with significant lag.
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
        solar_exposure_aperture: float = 0.0,
        solar_facing: float = DEFAULT_SOLAR_FACING,
        solar_scale: float = DEFAULT_SOLAR_SCALE,
        temperature: Optional[float] = None,
        c_air_fraction: float = DEFAULT_C_AIR_FRACTION,
        r_aw_fraction: float = DEFAULT_R_AW_FRACTION,
        air_temperature: Optional[float] = None,
        wall_temperature: Optional[float] = None,
        # Slab-era parameters — accepted but ignored (no slab node).
        slab_temperature: Optional[float] = None,
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

        # 2R2C split fractions (bounded; see module docstring).
        self.c_air_fraction = float(np.clip(
            c_air_fraction, _C_AIR_FRACTION_MIN, _C_AIR_FRACTION_MAX,
        ))
        self.r_aw_fraction = float(np.clip(
            r_aw_fraction, _R_AW_FRACTION_MIN, _R_AW_FRACTION_MAX,
        ))

        # Long-wave to sky (wall node).
        self.sky_radiative_ua: float = max(0.0, float(sky_radiative_ua))

        # Sol-air on opaque surfaces (wall node).
        self.facade_absorptance: float = float(np.clip(facade_absorptance, 0.0, 1.0))
        self.facade_solar_share: float = max(0.0, float(facade_solar_share))

        # Linear thermal-bridge correction (wall ↔ outdoor conductance).
        self.thermal_bridge_psi_l: float = max(0.0, float(thermal_bridge_psi_l))

        # Optional per-room solar-exposure preset (no-geometry fallback for
        # solar gain when no windows are enumerated).  ``aperture`` is the
        # effective collecting area [m²·SHGC]; 0 disables it.
        self.solar_exposure_aperture: float = max(0.0, float(solar_exposure_aperture))
        self.solar_facing: float = float(solar_facing)

        # Identified multiplicative correction on the modelled solar gain.
        # The geometry pipeline records *unscaled* gains; the scale is
        # applied exactly once, inside the model dynamics.
        self.solar_scale: float = max(0.0, float(solar_scale))

        # Air node — initialise from ``temperature``, falling back to the
        # legacy ``air_temperature`` keyword, then 20 °C.
        if temperature is not None:
            self.temperature: float = float(temperature)
        elif air_temperature is not None:
            self.temperature = float(air_temperature)
        else:
            self.temperature = 20.0

        # Wall node — defaults to the air temperature (thermal equilibrium).
        self.wall_temperature: float = (
            float(wall_temperature) if wall_temperature is not None
            else self.temperature
        )

    # ── Derived split quantities ───────────────────────────────────────

    @property
    def c_air(self) -> float:
        """Air-node heat capacity [J/K]."""
        return self.c_air_fraction * self.thermal_mass

    @property
    def c_wall(self) -> float:
        """Wall-node heat capacity [J/K]."""
        return (1.0 - self.c_air_fraction) * self.thermal_mass

    def conductances(self) -> Tuple[float, float, float]:
        """Split the total UA into the three model conductances.

        Returns ``(g_inf, g_aw, g_we)`` [W/K]:

        * ``g_inf`` — air ↔ outdoor (infiltration share, wind-modulated
          at runtime via the Sherman–Grimsrud overlay),
        * ``g_aw``  — air ↔ wall coupling,
        * ``g_we``  — wall ↔ outdoor conduction (excluding sky/bridge).

        Invariant: ``g_inf + 1/(1/g_aw + 1/g_we) == 1/r_external``, so the
        steady-state heat balance matches the user-facing ``r_external``.
        """
        ua_tot = 1.0 / self.r_external
        f_inf = float(np.clip(self.infiltration_fraction, 0.0, MAX_INFILTRATION_FRACTION))
        g_inf = f_inf * ua_tot
        g_cond = (1.0 - f_inf) * ua_tot
        rf = self.r_aw_fraction
        g_aw = g_cond / rf
        g_we = g_cond / (1.0 - rf)
        return g_inf, g_aw, g_we

    def __repr__(self) -> str:
        return (
            f"Room(name={self.name!r}, thermal_mass={self.thermal_mass}, "
            f"r_external={self.r_external}, "
            f"temperature={self.temperature}, "
            f"wall_temperature={self.wall_temperature}, "
            f"setpoint={self.setpoint})"
        )


class HouseModel:
    """
    Aggregated 2R2C thermal model of an entire house.

    State ordering: ``x = [T_a,1 … T_a,n, T_w,1 … T_w,n]`` (air block
    first, wall block second).

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

        # Per-room envelope-correction terms (attach to the wall node).
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
        self._solar_scale = np.array(
            [self._rooms[name].solar_scale for name in self._room_list],
            dtype=float,
        )

        # Effective sky-temperature depression below outdoor air [K] and
        # its cloud attenuation (1.0 = clear sky, 0.0 = fully overcast).
        self._delta_t_sky: float = DEFAULT_DELTA_T_SKY
        self._sky_clear_fraction: float = 1.0

        # Build state-space matrices once.
        self._C, self._A, self._B_ext = self._build_matrices()

        # Sky cooling-drift bias (per state row; wall block only).
        # Magnitude: −sky_radiative_ua · ΔT_sky / C_wall.
        self._B_sky_offset = self._build_sky_offset(self._C)

        # Per-room effective leakage area L_i (m²), derived so that the
        # wind-driven UA reduces to the infiltration share of 1/r_external
        # at typical conditions.
        self._leakage_area = self._build_leakage_area()

    def _build_leakage_area(self) -> np.ndarray:
        return np.array(
            [
                self._rooms[name].conductances()[0]
                / (AIR_RHO_CP * _SG_FACTOR_TYPICAL)
                for name in self._room_list
            ],
            dtype=float,
        )

    def _build_sky_offset(self, C: np.ndarray) -> np.ndarray:
        n = self._n
        offset = np.zeros(2 * n)
        for i in range(n):
            if self._sky_ua[i] > 0.0 and C[n + i] > 0.0:
                offset[n + i] = -self._sky_ua[i] * self._delta_t_sky / C[n + i]
        return offset

    def rebuild_derived_parameters(self) -> None:
        """Recompute all cached derived arrays from the current room attributes.

        Must be called whenever ``room.thermal_mass``, ``room.r_external``,
        the split fractions, ``room.solar_scale``, or any connection's
        ``r_value`` is updated (e.g. after parameter estimation).  Follow
        this with ``_build_matrices()`` to refresh the state-space matrices
        and assign the results back to ``_C``, ``_A``, and ``_B_ext``.
        """
        self._solar_scale = np.array(
            [self._rooms[name].solar_scale for name in self._room_list],
            dtype=float,
        )
        self._leakage_area = self._build_leakage_area()
        C_new, _, _ = self._build_matrices()
        self._B_sky_offset = self._build_sky_offset(C_new)

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
        """Number of rooms (the physical state-vector size is 2n)."""
        return self._n

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room air temperatures."""
        return {name: self._rooms[name].temperature for name in self._room_list}

    @property
    def wall_temperatures(self) -> Dict[str, float]:
        """Per-room wall/mass-node temperatures."""
        return {
            name: self._rooms[name].wall_temperature for name in self._room_list
        }

    def set_temperatures(self, temps: Dict[str, float]) -> None:
        """Update the room air temperatures from measurements."""
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].temperature = float(temp)

    def set_wall_temperatures(self, temps: Dict[str, float]) -> None:
        """Update the wall-node temperatures (e.g. from the EKF estimate)."""
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].wall_temperature = float(temp)

    def set_cloud_cover(self, cloud_cover: Optional[float]) -> None:
        """Attenuate the sky cooling drift by the current cloud cover.

        The long-wave sky-temperature depression collapses under an
        overcast sky, so the constant drift is scaled by
        ``1 − cloud_cover``.  ``None`` keeps the previous value.
        """
        if cloud_cover is None:
            return
        self._sky_clear_fraction = float(np.clip(1.0 - cloud_cover, 0.0, 1.0))

    # ------------------------------------------------------------------
    # Wind-driven infiltration overlay (Sherman–Grimsrud)
    # ------------------------------------------------------------------

    def infiltration_delta_ua(
        self,
        outdoor_temp: float,
        wind_speed: Optional[float],
        room_temps: np.ndarray,
    ) -> np.ndarray:
        """
        Per-room *delta* on the air-node external conductance relative to
        the typical-conditions baseline already baked into ``A`` and
        ``B_ext``.  ``room_temps`` are the **air** temperatures (n,).

        Returns ``Δ ∈ ℝⁿ`` such that the effective infiltration
        conductance is ``g_inf,i + Δᵢ`` [W/K].  Zero vector at the
        reference conditions or when ``wind_speed`` is ``None``.
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
        Build the continuous-time state matrices for the 2R2C network.

        State ordering: ``x = [T_a (n), T_w (n)]`` of length ``2n``.

        Air rows (0 … n−1):

            C_a,i dT_a,i/dt = Q_air,i + g_aw,i (T_w,i − T_a,i)
                            + g_inf,i (T_out − T_a,i)

        Wall rows (n … 2n−1):

            C_w,i dT_w,i/dt = Q_wall,i + g_aw,i (T_a,i − T_w,i)
                            + g_wout,i (T_out − T_w,i)
                            + Σ_j g_ij (T_w,j − T_w,i)

        with ``g_wout = g_we + sky_radiative_ua + thermal_bridge_psi_l``.

        Returns
        -------
        C : (2n,) thermal capacitance vector ``[C_a, C_w]``.
        A : (2n, 2n) drift matrix (conductances, not yet divided by C).
        B_ext : (2n,) outdoor input vector.
        """
        n = self._n
        C = np.zeros(2 * n)
        A = np.zeros((2 * n, 2 * n))
        B_ext = np.zeros(2 * n)

        idx = {name: i for i, name in enumerate(self._room_list)}

        for name, room in self._rooms.items():
            i = idx[name]
            g_inf, g_aw, g_we = room.conductances()
            g_wout = g_we + float(self._sky_ua[i]) + float(self._thermal_bridge[i])

            C[i] = room.c_air
            C[n + i] = room.c_wall

            # Air row: infiltration to outdoor + coupling to own wall.
            A[i, i] -= g_inf + g_aw
            A[i, n + i] += g_aw
            B_ext[i] = g_inf

            # Wall row: coupling to own air + conduction/sky/bridge to outdoor.
            A[n + i, i] += g_aw
            A[n + i, n + i] -= g_aw + g_wout
            B_ext[n + i] = g_wout

            # Inter-room conduction: wall-to-wall.
            for conn in room.connections:
                k = idx[conn.connected_room]
                g = 1.0 / conn.r_value
                A[n + i, n + k] += g
                A[n + i, n + i] -= g

        return C, A, B_ext

    # ------------------------------------------------------------------
    # Heat dispatch
    # ------------------------------------------------------------------

    def dispatch_heat(
        self,
        heat_inputs: Dict[str, float],
        solar_gains: Dict[str, float],
    ) -> np.ndarray:
        """Map heater/solar/internal gains onto the 2n state rows [W].

        Heaters and internal gains heat the air node; solar gain (scaled
        by the room's identified ``solar_scale``) splits between air and
        wall with ``SOLAR_WALL_FRACTION``, and the sol-air facade share
        adds to the wall node.
        """
        n = self._n
        Q = np.zeros(2 * n)
        idx = {name: i for i, name in enumerate(self._room_list)}
        for name, power in heat_inputs.items():
            i = idx.get(name)
            if i is not None:
                Q[i] += power
        for name, gain in solar_gains.items():
            i = idx.get(name)
            if i is None:
                continue
            scaled = self._solar_scale[i] * float(gain)
            Q[i] += (1.0 - SOLAR_WALL_FRACTION) * scaled
            Q[n + i] += SOLAR_WALL_FRACTION * scaled
            share = self._facade_solar_share[i]
            if share > 0.0:
                Q[n + i] += self._facade_absorptance[i] * share * scaled
        for i, name in enumerate(self._room_list):
            Q[i] += self._rooms[name].internal_gain
        return Q

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
            Mapping room name → heater power [W] (air node).
        outdoor_temp : float
            Outdoor air temperature [°C].
        solar_gains : dict
            Mapping room name → *unscaled* solar heat gain [W]; the
            identified per-room ``solar_scale`` is applied here.
        wind_speed : float or None, optional
            Outdoor wind speed [m/s] (Sherman–Grimsrud overlay on the air
            node).  ``None`` ⇒ typical-conditions baseline.
        ground_temp : float or None, optional
            Accepted for API compatibility; unused (no slab node).

        Returns
        -------
        dict
            New room **air** temperatures {name: temp °C}.  Wall states
            are updated on the ``Room`` objects.
        """
        n = self._n
        x = np.concatenate([
            [self._rooms[name].temperature for name in self._room_list],
            [self._rooms[name].wall_temperature for name in self._room_list],
        ])

        Q = self.dispatch_heat(heat_inputs, solar_gains)

        # Wind-driven infiltration overlay on the air rows (delta from typical).
        delta_ua = self.infiltration_delta_ua(outdoor_temp, wind_speed, x[:n])

        A_eff = self._A.copy()
        B_eff_ext = self._B_ext.copy()
        for i in range(n):
            A_eff[i, i] -= delta_ua[i]
            B_eff_ext[i] += delta_ua[i]

        inv_C = 1.0 / self._C
        F = A_eff * inv_C[:, None]
        bias = (
            (B_eff_ext * outdoor_temp + Q) * inv_C
            + self._B_sky_offset * self._sky_clear_fraction
        )

        def rhs(state: np.ndarray) -> np.ndarray:
            return F @ state + bias

        def jacobian(_state: np.ndarray) -> np.ndarray:
            return F

        x_new = implicit_euler_step(rhs, jacobian, x, dt)

        new_temps: Dict[str, float] = {}
        for i, name in enumerate(self._room_list):
            self._rooms[name].temperature = float(x_new[i])
            self._rooms[name].wall_temperature = float(x_new[n + i])
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
            Starting room air temperatures; defaults to current model
            state.  Wall nodes start from their current values.
        wind_speeds : list of float, optional
            Outdoor wind speed [m/s] per step.

        Returns
        -------
        list of dict
            Predicted air temperatures {name: °C} for each step 1…horizon.
        """
        # Save both node states, run prediction, restore.
        saved = {
            n: (r.temperature, r.wall_temperature)
            for n, r in self._rooms.items()
        }
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
        for name, (t_air, t_wall) in saved.items():
            self._rooms[name].temperature = t_air
            self._rooms[name].wall_temperature = t_wall

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

        * ``external_loss`` – heat flow to outdoor [W] (positive = losing
          heat): air-node infiltration + wall-node conduction.
        * ``<other_room>`` – heat flow to/from each connected room [W]
          (wall-to-wall; positive = losing heat to that room)
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
        flows: Dict[str, Dict[str, float]] = {}

        for name, room in self._rooms.items():
            breakdown: Dict[str, float] = {}
            g_inf, _g_aw, g_we = room.conductances()

            external_loss = (
                g_inf * (room.temperature - outdoor_temp)
                + g_we * (room.wall_temperature - outdoor_temp)
            )
            breakdown["external_loss"] = round(external_loss, 2)

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
        Return the dominant (slow) thermal time constant τ = C × R_eff
        [seconds] for a room, where C is the **total** room mass and
        R_eff combines the external and inter-room paths in parallel —
        the same aggregate quantity the 1R1C model reported.
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
        Compute the steady-state air temperature a room would reach with a
        constant heating power, assuming all connected rooms are at the
        outdoor temperature (worst case).

        Because the split conductances preserve ``1/r_external`` as the
        total air→outdoor steady-state conductance, this is identical to
        the 1R1C result.
        """
        room = self._rooms[room_name]
        g_total = 1.0 / room.r_external
        for conn in room.connections:
            g_total += 1.0 / conn.r_value
        return outdoor_temp + heating_power / g_total
