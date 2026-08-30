"""Shared heat-source ABC and smooth ceiling helper."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Optional

_T_SUPPLY_K: float = 308.15  # Assumed supply temperature 35 °C in Kelvin

# Dimensionless sharpness for the smooth power ceiling (higher → sharper).
# At x = cap the output is cap · (1 − ln 2 / k); with k = 50 the bias is ≈ 1.4 %.
# The derivative is sigmoid(k · (x/cap − 1)), which the L-BFGS-B solver
# can exploit without any non-differentiable kink.
_SOFT_CEIL_K: float = 50.0


def _soft_ceiling(x: float, cap: float) -> float:
    """Smooth C∞ ceiling: differentiable approximation to min(x, cap).

    Uses a normalised softplus so the sharpness is dimensionless:

        f(x) = cap − (cap / k) · log(1 + exp(k · (1 − x / cap)))
        f′(x) = sigmoid(k · (x / cap − 1))

    The function is always ≤ cap, approaches x for x ≪ cap, and approaches
    cap for x ≫ cap.  Numerically stable for all finite x.
    """
    if cap <= 0.0:
        return 0.0
    t = _SOFT_CEIL_K * (1.0 - x / cap)
    # Numerically stable softplus: avoids exp overflow for large positive t.
    sp = t + math.log1p(math.exp(-t)) if t > 0.0 else math.log1p(math.exp(t))
    return cap - (cap / _SOFT_CEIL_K) * sp


class HeatSource(ABC):
    """Abstract base class for a controllable heat source."""

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
        p_gain: float = 0.1,
    ) -> None:
        self.name = name
        self.room = room                     # name of the room this source heats
        self.max_power = max_power           # W (maximum *thermal* output)
        self.heater_entity = heater_entity   # optional HA entity_id
        # Multiplicative correction on actual delivered thermal power.
        # Identified jointly with thermal-mass and resistance parameters.
        # Stored behind a property so assigning a new value re-derives the
        # cached power-dependent gains (``_gain`` / ``_q_heat_base`` …) that the
        # SDE and MPC read; a plain attribute left those caches stale, so
        # identified or manually-entered heater scales had no effect on the
        # model.  The raw private attribute is set here (not via the property)
        # because subclass attributes the recompute hook needs are not assigned
        # until after ``super().__init__`` returns.
        self._power_scale = float(power_scale)
        # Phase 1 B2 (pragmatic emitter filter): first-order time
        # constant [s] for the commanded-fraction → delivered-fraction
        # filter.  Captures TRV / valve / water-loop / metal-mass lag
        # without requiring supply-temperature telemetry.
        #
        # * 0 (default for electric resistive heaters) → no filter,
        #   commanded fraction reaches the thermal node instantly.
        # * 60 s (typical heat pump / fan-coil indoor unit) → fast
        #   filter capturing fan-spin-up and refrigerant-loop response.
        # * 600 s (~10 min, typical hydronic radiator) → slow filter
        #   capturing water-loop and metal-mass dynamics.
        #
        # Values > 0 add one state variable per source to the EKF /
        # OCP, so the controller can anticipate the lag rather than
        # treating commanded power as instantaneous.
        self.emitter_time_constant: float = float(emitter_time_constant)
        # Fast P-law gain [1/K] on (T_ref − T_hat).  One gain for heat and cool.
        self.p_gain: float = float(p_gain)
        self._current_power: float = 0.0    # W

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def current_power(self) -> float:
        """Current thermal output power [W]."""
        return self._current_power

    @property
    def power_scale(self) -> float:
        """Multiplicative correction on delivered thermal power (1.0 = rated)."""
        return self._power_scale

    @power_scale.setter
    def power_scale(self, value: float) -> None:
        self._power_scale = float(value)
        # Re-derive any cached power-dependent gains so a runtime scale change
        # (Apply Parameters, identified scales, or a simulation preview) takes
        # effect.  Subclasses override ``_recompute_power_scaled_gains``.
        self._recompute_power_scaled_gains()

    def _recompute_power_scaled_gains(self) -> None:
        """Recompute cached gains that depend on ``power_scale``.

        No-op in the base class; subclasses that precompute thermal-output
        constants from ``power_scale`` at construction override this so those
        caches stay consistent when ``power_scale`` is reassigned.
        """

    @abstractmethod
    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Compute thermal output [W] for a given fractional set-point.

        Parameters
        ----------
        setpoint_fraction : float
            Control signal in [0, 1] (0 = off, 1 = full power).
        outdoor_temp : float
            Outdoor air temperature [°C].  Used by heat pumps to adjust COP.

        Returns
        -------
        float : thermal power output [W].
        """

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        """Rated heating capacity [W] at ``u = 1`` without identified ``power_scale``.

        Mirrors :meth:`rated_cooling_power` on the heating side: reflects the
        configured datasheet capacity (including outdoor-temperature-dependent
        COP for heat pumps) rather than the runtime ``power_scale`` correction
        identified by sysid.  Use this for plot bounds and
        :meth:`display_thermal_power`; use :meth:`thermal_power` for the MPC
        plant model only.
        """
        return self.max_power

    def display_thermal_power(
        self, setpoint_fraction: float, outdoor_temp: float = 0.0,
    ) -> float:
        """Configured thermal output [W] for sensors and plots.

        Ignores the identified ``power_scale`` factor, which only adjusts how
        strongly the heater couples into the MPC/EKF room model (often
        compensating for room thermal-mass error rather than a true change in
        delivered wattage).
        """
        fraction = max(0.0, min(1.0, float(setpoint_fraction)))
        return self.rated_heating_capacity(outdoor_temp) * fraction

    def set_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Apply a control set-point, update internal state, and return the
        resulting thermal power output [W] shown on sensors and plots.
        """
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        power = self.display_thermal_power(setpoint_fraction, outdoor_temp)
        self._current_power = power
        return power

    def control_for_power_fraction(
        self, power_fraction: float, outdoor_temp: float = 0.0, k_sigmoid: float = 5.0,
    ) -> float:
        """Return the control input ``u`` that delivers ``power_fraction`` of the
        source's max heat (``>= 0``) / cool (``< 0``) capacity.

        For sources whose input→power map is linear (electric heaters, heat-only
        units) the control input *is* the power fraction, so this is the identity
        (clamped to the actuation range).  Sources with a nonlinear map (a
        cooling-capable heat pump's smooth sigmoid) override this to invert that
        map, so a commanded power fraction lands linearly on delivered power —
        used by identification experiments so a step of ``step_pct`` really
        delivers ``step_pct`` of capacity.
        """
        return max(self.u_min, min(self.u_max, float(power_fraction)))

    @property
    def can_cool(self) -> bool:
        """Returns True if this source can actively remove heat from the room."""
        return False

    @property
    def u_min(self) -> float:
        """Lower bound on the control input."""
        return 0.0

    @property
    def u_max(self) -> float:
        """Upper bound on the control input."""
        return 1.0

    @property
    def elec_per_unit_heat(self) -> float:
        """Electrical power [W] drawn per unit of positive control input (heating).

        For heat pumps the COP cancels out: P_elec = thermal / COP is independent
        of outdoor temperature and equals the rated electrical draw at full load.
        """
        return 0.0

    @property
    def elec_per_unit_cool(self) -> float:
        """Electrical power [W] drawn per unit of |negative| control input (cooling).

        Zero for sources that cannot cool.
        """
        return 0.0

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, room={self.room!r}, "
            f"max_power={self.max_power} W, current={self._current_power:.1f} W)"
        )


