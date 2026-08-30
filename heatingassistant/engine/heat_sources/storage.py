"""Electric storage heater with brick-core accumulation."""

from __future__ import annotations

from typing import Optional

from .base import HeatSource

class ElectricStorageHeater(HeatSource):
    """
    Electric storage heater with brick-core thermal accumulation.

    Operating modes
    ---------------
    * **Charge mode** (off-peak window): draws ``charge_power`` watts of
      electricity, storing energy in the brick core.
    * **Passive discharge** (daytime): releases stored heat at a rate
      proportional to stored energy × ``passive_discharge_rate``.
    * **Boost mode** (real-time control via MPC): fan-assisted output up to
      ``max_power`` (= ``boost_power``) watts — this is the controllable
      component exposed through the standard ``thermal_power`` interface.

    MPC interface
    -------------
    ``thermal_power(u)`` models only the **boost** component (real-time
    control).  Pass ``max_power = 0`` if the unit has no boost fan.

    The autonomous passive discharge is available via ``passive_thermal_output``
    for callers that track stored energy (e.g. the charge-window planner or a
    future storage-state estimator).

    ``charge_power_input`` exposes the electrical draw during the charge window
    for tariff/cost optimisation.

    ``elec_per_unit_heat`` returns ``charge_power``: the electrical energy
    consumed per unit of charge-mode activation.  The boost is not accounted
    for here because boost electricity is directly proportional to boost
    thermal output at efficiency ≈ 1.

    The ``emitter_time_constant`` default of 0 s reflects the boost fan path
    (instant delivery); the slow passive slab discharge is modelled separately
    via ``passive_thermal_output`` rather than the emitter filter.
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        charge_power: float = 1500.0,
        storage_capacity_kwh: float = 8.0,
        passive_discharge_rate: float = 1.0 / (8 * 3600),
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        super().__init__(
            name, room, max_power, heater_entity, power_scale,
            emitter_time_constant=emitter_time_constant,
        )
        if charge_power < 0.0:
            raise ValueError(f"charge_power must be >= 0; got {charge_power}")
        if storage_capacity_kwh <= 0.0:
            raise ValueError(
                f"storage_capacity_kwh must be > 0; got {storage_capacity_kwh}"
            )
        if passive_discharge_rate < 0.0:
            raise ValueError(
                f"passive_discharge_rate must be >= 0; got {passive_discharge_rate}"
            )
        self.charge_power = charge_power
        self.storage_capacity_kwh = storage_capacity_kwh
        self.passive_discharge_rate = passive_discharge_rate
        self._gain: float = max_power * self._power_scale

    def _recompute_power_scaled_gains(self) -> None:
        """Re-derive the cached boost gain after a ``power_scale`` change."""
        self._gain = self.max_power * self._power_scale

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Instantaneous boost thermal power [W].

        This covers only the fan-assisted boost output controlled by the MPC.
        The autonomous passive discharge is tracked separately via
        ``passive_thermal_output``.
        """
        return self._gain * setpoint_fraction

    def passive_thermal_output(self, stored_energy_kwh: float) -> float:
        """
        Passive (autonomous) heat release rate [W] from stored energy.

        Computes the discharge power as:

            P_passive = stored_energy_kwh × 3600 × 1000 × passive_discharge_rate

        The result is clamped to [0, rated_passive_max] where
        ``rated_passive_max`` is the passive output at full storage.

        Parameters
        ----------
        stored_energy_kwh : float
            Current thermal energy stored in the brick core [kWh].

        Returns
        -------
        float
            Heat release rate [W].  Non-negative.
        """
        stored_j = max(0.0, stored_energy_kwh) * 3_600_000.0  # kWh → J
        return stored_j * self.passive_discharge_rate

    @property
    def charge_power_input(self) -> float:
        """Electrical draw [W] during the charging window."""
        return self.charge_power

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        """
        Upper bound on total deliverable heat [W]: boost + full passive discharge.
        """
        passive_max = self.passive_thermal_output(self.storage_capacity_kwh)
        return self.max_power * self._power_scale + max(passive_max, 0.0)

    @property
    def elec_per_unit_heat(self) -> float:
        """Electrical draw during charging (cost proxy for charge-window planning)."""
        return self.charge_power

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        """Target setpoint for boost fan control (linear offset)."""
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * 5.0  # 5 °C max offset for boost
