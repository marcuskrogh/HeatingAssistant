"""Factory for constructing :class:`HeatingMPCController` instances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..const import DEFAULT_GROUND_ALBEDO
from ..heat_sources import HeatSource
from ..thermal_model import HouseModel
from .facade import HeatingMPCController


@dataclass
class ControllerBuildConfig:
    """Parameters required to build an MPC controller."""

    model: HouseModel
    heat_sources: List[HeatSource]
    horizon: int
    dt: float
    latitude: float
    longitude: float
    tracking_weight: float
    energy_weight: float
    smoothing_weight: float
    soft_constraint_weight: float
    soft_constraint_linear_weight: float
    terminal_weight: float
    sigma_w: float
    sigma_v: float
    sigma_b: float
    energy_price_weight: float
    albedo: float = DEFAULT_GROUND_ALBEDO
    measurement_dt: Optional[float] = None
    nmpc_period: Optional[float] = None
    nmpc_fast_substeps: Optional[int] = None
    nmpc_horizon_h: Optional[float] = None

    @classmethod
    def from_coordinator(
        cls,
        coordinator: Any,
        *,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> "ControllerBuildConfig":
        """Build config from a coordinator, optionally applying tuning overrides."""
        from ..const import (  # noqa: PLC0415
            CONF_ENERGY_PRICE_WEIGHT,
            CONF_ENERGY_WEIGHT,
            CONF_NMPC_FAST_SUBSTEPS,
            CONF_NMPC_HORIZON_H,
            CONF_NMPC_PERIOD,
            CONF_SMOOTHING_WEIGHT,
            CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
            CONF_SOFT_CONSTRAINT_WEIGHT,
            CONF_TERMINAL_WEIGHT,
            CONF_TRACKING_WEIGHT,
            DEFAULT_NMPC_FAST_SUBSTEPS,
            DEFAULT_NMPC_HORIZON_H,
            DEFAULT_NMPC_PERIOD,
        )
        from ..nmpc_timing import timing_from_options  # noqa: PLC0415

        ov = dict(overrides or {})
        timing = timing_from_options(
            {
                CONF_NMPC_PERIOD: ov.get(
                    CONF_NMPC_PERIOD,
                    getattr(coordinator, "_nmpc_period", DEFAULT_NMPC_PERIOD),
                ),
                CONF_NMPC_FAST_SUBSTEPS: ov.get(
                    CONF_NMPC_FAST_SUBSTEPS,
                    getattr(
                        coordinator,
                        "_nmpc_fast_substeps",
                        DEFAULT_NMPC_FAST_SUBSTEPS,
                    ),
                ),
                CONF_NMPC_HORIZON_H: ov.get(
                    CONF_NMPC_HORIZON_H,
                    getattr(coordinator, "_nmpc_horizon_h", DEFAULT_NMPC_HORIZON_H),
                ),
            },
            default_period=DEFAULT_NMPC_PERIOD,
            default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
            default_horizon_h=DEFAULT_NMPC_HORIZON_H,
        )
        dt = float(timing.dt_s)
        return cls(
            model=coordinator.model,
            heat_sources=coordinator.heat_sources,
            horizon=int(timing.n_fast),
            dt=dt,
            measurement_dt=dt,
            nmpc_period=timing.period_s,
            nmpc_fast_substeps=timing.fast_substeps,
            nmpc_horizon_h=timing.horizon_h,
            latitude=coordinator._latitude,
            longitude=coordinator._longitude,
            albedo=getattr(coordinator, "_ground_albedo", DEFAULT_GROUND_ALBEDO),
            tracking_weight=float(
                ov.get(CONF_TRACKING_WEIGHT, coordinator._tracking_weight)
            ),
            energy_weight=float(
                ov.get(CONF_ENERGY_WEIGHT, coordinator._energy_weight)
            ),
            smoothing_weight=float(
                ov.get(CONF_SMOOTHING_WEIGHT, coordinator._smoothing_weight)
            ),
            soft_constraint_weight=float(
                ov.get(
                    CONF_SOFT_CONSTRAINT_WEIGHT,
                    coordinator._soft_constraint_weight,
                )
            ),
            soft_constraint_linear_weight=float(
                ov.get(
                    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
                    coordinator._soft_constraint_linear_weight,
                )
            ),
            terminal_weight=float(
                ov.get(CONF_TERMINAL_WEIGHT, coordinator._terminal_weight)
            ),
            sigma_w=coordinator._sigma_w,
            sigma_v=coordinator._sigma_v,
            sigma_b=coordinator._sigma_b,
            energy_price_weight=float(
                ov.get(CONF_ENERGY_PRICE_WEIGHT, coordinator._energy_price_weight)
            ),
        )


def build_mpc_controller(config: ControllerBuildConfig) -> HeatingMPCController:
    """Construct an MPC controller from a :class:`ControllerBuildConfig`."""
    measurement_dt = (
        config.measurement_dt if config.measurement_dt is not None else config.dt
    )
    return HeatingMPCController(
        model=config.model,
        heat_sources=config.heat_sources,
        horizon=config.horizon,
        dt=config.dt,
        measurement_dt=measurement_dt,
        latitude=config.latitude,
        longitude=config.longitude,
        albedo=config.albedo,
        tracking_weight=config.tracking_weight,
        energy_weight=config.energy_weight,
        smoothing_weight=config.smoothing_weight,
        soft_constraint_weight=config.soft_constraint_weight,
        soft_constraint_linear_weight=config.soft_constraint_linear_weight,
        terminal_weight=config.terminal_weight,
        sigma_w=config.sigma_w,
        sigma_v=config.sigma_v,
        sigma_b=config.sigma_b,
        energy_price_weight=config.energy_price_weight,
        nmpc_period=config.nmpc_period,
        nmpc_fast_substeps=config.nmpc_fast_substeps,
        nmpc_horizon_h=config.nmpc_horizon_h,
    )
