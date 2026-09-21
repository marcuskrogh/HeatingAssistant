"""
Thermal model for the Heating Assistant integration.

Each room is modelled as a lumped-parameter **1R1C** thermal circuit: one
air node ``T_a`` (measured and controlled):

    C_i dT_a,i/dt = Q_heater_i + Q_int_i + s_i Q_solar_i
                  + (T_out − T_a,i) · g_out,i
                  + Σ_{j adj i} (T_a,j − T_a,i) / R_ij

where

    C_i      – room thermal mass [J/K]  (user-facing ``thermal_mass``)
    R_ext,i  – total steady-state resistance to outdoors [K/W]
    g_inf,i  – infiltration_fraction / R_ext,i   (air → outdoor, wind-modulated)
    g_cond,i – (1 − infiltration_fraction) / R_ext,i
    g_out,i  – g_inf,i + g_cond,i + sky_radiative_ua + thermal_bridge_psi_l
    s_i      – per-room solar-gain scale (identified from data; default 1)

At steady state with constant heat ``Q``, ``T_a → T_out + Q · R_ext``
when sky/bridge UA is zero.  Split fractions and a hidden wall node are
not part of the live plant (legacy kwargs still load and are ignored).
"""

from __future__ import annotations

from dataclasses import dataclass
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
    DEFAULT_SOLAR_FACING,
    DEFAULT_SOLAR_SCALE,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    MAX_INFILTRATION_FRACTION,
    SHERMAN_GRIMSRUD_DT_TYPICAL,
    SHERMAN_GRIMSRUD_STACK_COEF,
    SHERMAN_GRIMSRUD_V_TYPICAL,
    SHERMAN_GRIMSRUD_WIND_COEF,
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
    """Lumped-parameter 1R1C thermal model of a single room.

    ``temperature`` is the measured/controlled air node.  Legacy
    ``wall_temperature``, ``c_air_fraction``, and ``r_aw_fraction``
    kwargs still load (configs from the 2R2C era) and are ignored.

    ``thermal_mass`` and ``r_external`` are total heat capacity and
    total steady-state resistance to outdoors.
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
        ua_open: float = 0.0,
        infiltration_fraction: float = DEFAULT_INFILTRATION_FRACTION,
        sky_radiative_ua: float = DEFAULT_SKY_RADIATIVE_UA,
        facade_absorptance: float = DEFAULT_FACADE_ABSORPTANCE,
        facade_solar_share: float = DEFAULT_FACADE_SOLAR_SHARE,
        thermal_bridge_psi_l: float = DEFAULT_THERMAL_BRIDGE_PSI_L,
        solar_exposure_aperture: float = 0.0,
        solar_facing: float = DEFAULT_SOLAR_FACING,
        solar_scale: float = DEFAULT_SOLAR_SCALE,
        temperature: Optional[float] = None,
        c_air_fraction: float = 0.05,
        r_aw_fraction: float = 0.5,
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
        # Extra outdoor conductance [W/K] applied on the air node while the
        # room's window/door override contact is open.  Identified by PE;
        # 0 means closed-window SWD-322 exclusion still applies.
        self.ua_open = max(0.0, float(ua_open))
        self.infiltration_fraction = float(infiltration_fraction)

        # Legacy 2R2C split fractions — accepted, unused.
        self.c_air_fraction = float(c_air_fraction)
        self.r_aw_fraction = float(r_aw_fraction)

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

        # Air node.  Legacy wall_temperature aliases air (1R1C).
        if temperature is not None:
            self.temperature: float = float(temperature)
        elif air_temperature is not None:
            self.temperature = float(air_temperature)
        else:
            self.temperature = 20.0
        self.wall_temperature: float = self.temperature

    # ── Derived split quantities ───────────────────────────────────────

    @property
    def c_air(self) -> float:
        """Air-node heat capacity [J/K] — the full room mass."""
        return self.thermal_mass

    @property
    def c_wall(self) -> float:
        """Unused (1R1C). Kept so older callers do not AttributeError."""
        return self.thermal_mass

    def conductances(self) -> Tuple[float, float, float]:
        """Outdoor UA split as ``(g_inf, g_out, g_out)``.

        ``g_inf`` is the infiltration share of ``1/r_external``.
        ``g_out`` is the rest of the outdoor UA including sky and
        thermal-bridge terms.  The third value duplicates ``g_out`` so
        call sites that still unpack three names keep working.
        """
        ua_tot = 1.0 / self.r_external
        f_inf = float(np.clip(self.infiltration_fraction, 0.0, MAX_INFILTRATION_FRACTION))
        g_inf = f_inf * ua_tot
        g_cond = (1.0 - f_inf) * ua_tot
        g_out = g_cond + float(self.sky_radiative_ua) + float(self.thermal_bridge_psi_l)
        return g_inf, g_out, g_out

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
    Aggregated 1R1C thermal model of an entire house.

    State ordering: ``x = [T_a,1 … T_a,n]`` (one air node per room).

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

        # Per-room envelope-correction terms (attach to the air node).
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

        # Sky cooling-drift bias (per air row).
        # Magnitude: −sky_radiative_ua · ΔT_sky / C.
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
        offset = np.zeros(n)
        for i in range(n):
            if self._sky_ua[i] > 0.0 and C[i] > 0.0:
                offset[i] = -self._sky_ua[i] * self._delta_t_sky / C[i]
        return offset

    def rebuild_derived_parameters(self) -> None:
        """Recompute all cached derived arrays from the current room attributes.

        Must be called whenever ``room.thermal_mass``, ``room.r_external``,
        ``room.solar_scale``, or any connection's
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
        """Number of rooms (physical state-vector size is n)."""
        return self._n

    @property
    def temperatures(self) -> Dict[str, float]:
        """Per-room air temperatures."""
        return {name: self._rooms[name].temperature for name in self._room_list}

    @property
    def wall_temperatures(self) -> Dict[str, float]:
        """Legacy alias: 1R1C has no wall node, so this returns air temps."""
        return {name: self._rooms[name].temperature for name in self._room_list}

    def set_temperatures(self, temps: Dict[str, float]) -> None:
        """Update the room air temperatures from measurements."""
        for name, temp in temps.items():
            if name in self._rooms:
                self._rooms[name].temperature = float(temp)

    def set_wall_temperatures(self, temps: Dict[str, float]) -> None:
        """No-op on air. 1R1C aliases wall to the current air temperature."""
        for name in temps:
            if name in self._rooms:
                self._rooms[name].wall_temperature = self._rooms[name].temperature

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
        Build the continuous-time state matrices for the 1R1C network.

        State ordering: ``x = T_a (n)``.

        Air row i:

            C_i dT_a,i/dt = Q_i + g_out,i (T_out − T_a,i)
                          + Σ_j g_ij (T_a,j − T_a,i)

        with ``g_out = g_inf + g_cond + sky_radiative_ua + thermal_bridge``.

        Returns
        -------
        C : (n,) thermal capacitance vector.
        A : (n, n) drift matrix (conductances, not yet divided by C).
        B_ext : (n,) outdoor input vector.
        """
        n = self._n
        C = np.zeros(n)
        A = np.zeros((n, n))
        B_ext = np.zeros(n)

        idx = {name: i for i, name in enumerate(self._room_list)}

        for name, room in self._rooms.items():
            i = idx[name]
            g_inf, g_rest, _dup = room.conductances()
            g_out = g_inf + g_rest

            C[i] = room.c_air

            A[i, i] -= g_out
            B_ext[i] = g_out

            for conn in room.connections:
                k = idx[conn.connected_room]
                g = 1.0 / conn.r_value
                A[i, k] += g
                A[i, i] -= g

        return C, A, B_ext

    # ------------------------------------------------------------------
    # Heat dispatch
    # ------------------------------------------------------------------

    def dispatch_heat(
        self,
        heat_inputs: Dict[str, float],
        solar_gains: Dict[str, float],
    ) -> np.ndarray:
        """Map heater/solar/internal gains onto the n air-state rows [W].

        Heaters, internal gains, window solar (scaled by the room's
        identified ``solar_scale``), and the sol-air facade share all
        land on the air node.
        """
        n = self._n
        Q = np.zeros(n)
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
            Q[i] += scaled
            share = self._facade_solar_share[i]
            if share > 0.0:
                Q[i] += self._facade_absorptance[i] * share * scaled
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
        window_open: Optional[Dict[str, bool]] = None,
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
            New room **air** temperatures {name: temp °C}.
        """
        n = self._n
        x = np.array(
            [self._rooms[name].temperature for name in self._room_list],
            dtype=float,
        )

        Q = self.dispatch_heat(heat_inputs, solar_gains)

        # Wind-driven infiltration overlay on the air rows (delta from typical).
        delta_ua = self.infiltration_delta_ua(outdoor_temp, wind_speed, x[:n])

        A_eff = self._A.copy()
        B_eff_ext = self._B_ext.copy()
        for i in range(n):
            A_eff[i, i] -= delta_ua[i]
            B_eff_ext[i] += delta_ua[i]
            if window_open and window_open.get(self._room_list[i], False):
                extra_ua = float(
                    getattr(self._rooms[self._room_list[i]], "ua_open", 0.0) or 0.0
                )
                if extra_ua > 0.0:
                    A_eff[i, i] -= extra_ua
                    B_eff_ext[i] += extra_ua

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
            t = float(x_new[i])
            self._rooms[name].temperature = t
            self._rooms[name].wall_temperature = t
            new_temps[name] = t

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
            state.
        wind_speeds : list of float, optional
            Outdoor wind speed [m/s] per step.

        Returns
        -------
        list of dict
            Predicted air temperatures {name: °C} for each step 1…horizon.
        """
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

        for name, t_air in saved.items():
            self._rooms[name].temperature = t_air
            self._rooms[name].wall_temperature = t_air

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
          heat): air-node outdoor UA.
        * ``<other_room>`` – heat flow to/from each connected room [W]
          (air-to-air; positive = losing heat to that room)
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
            g_inf, g_rest, _dup = room.conductances()
            g_out = g_inf + g_rest

            external_loss = g_out * (room.temperature - outdoor_temp)
            breakdown["external_loss"] = round(external_loss, 2)

            total = external_loss
            for conn in room.connections:
                other = self._rooms[conn.connected_room].temperature
                flow = (room.temperature - other) / conn.r_value
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
        the same aggregate quantity the lumped 1R1C model reports.
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

        Because outdoor UA is ``1/r_external`` plus sky/bridge terms that
        default to zero, this matches ``T_out + Q · R_ext`` for the
        isolated-room pass criterion.
        """
        room = self._rooms[room_name]
        g_total = 1.0 / room.r_external
        for conn in room.connections:
            g_total += 1.0 / conn.r_value
        return outdoor_temp + heating_power / g_total
