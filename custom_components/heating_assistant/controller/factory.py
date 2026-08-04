"""Factory for constructing :class:`HeatingMPCController` instances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..const import DEFAULT_GROUND_ALBEDO, DEFAULT_MPC_MODE
from ..heat_sources import HeatSource
from ..mpc_mode_validation import validate_mpc_mode_available
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
    mpc_mode: str = DEFAULT_MPC_MODE
    ipopt_available: bool = True
    nonlinear_available: bool = True
    nonlinear_backend: str = "ipopt"
    ipopt_unavailable_reason: Optional[str] = None
    albedo: float = DEFAULT_GROUND_ALBEDO
    measurement_dt: Optional[float] = None

    @classmethod
    def from_coordinator(
        cls,
        coordinator: Any,
        *,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> "ControllerBuildConfig":
        """Build config from a coordinator, optionally applying tuning overrides."""
        from ..const import (  # noqa: PLC0415
            CONF_COMFORT_OFFSET,
            CONF_ENERGY_PRICE_WEIGHT,
            CONF_ENERGY_WEIGHT,
            CONF_HORIZON,
            CONF_MPC_MODE,
            CONF_SMOOTHING_WEIGHT,
            CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
            CONF_SOFT_CONSTRAINT_WEIGHT,
            CONF_TERMINAL_WEIGHT,
            CONF_TRACKING_WEIGHT,
            CONF_UPDATE_INTERVAL,
        )

        ov = dict(overrides or {})
        dt = float(ov.get(CONF_UPDATE_INTERVAL, coordinator._update_interval_s))
        nonlinear_available = bool(
            getattr(
                coordinator,
                "_nonlinear_available",
                getattr(coordinator, "_ipopt_available", True),
            )
        )
        mpc_mode = validate_mpc_mode_available(
            ov.get(
                CONF_MPC_MODE,
                getattr(
                    coordinator,
                    "_mpc_mode",
                    getattr(coordinator, "_mpc_solver", DEFAULT_MPC_MODE),
                ),
            ),
            nonlinear_available=nonlinear_available,
            unavailable_reason=getattr(
                coordinator, "_ipopt_unavailable_reason", None
            ),
        )
        return cls(
            model=coordinator.model,
            heat_sources=coordinator.heat_sources,
            horizon=int(ov.get(CONF_HORIZON, coordinator._horizon)),
            dt=dt,
            measurement_dt=dt,
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
            mpc_mode=mpc_mode,
            ipopt_available=bool(getattr(coordinator, "_ipopt_available", True)),
            nonlinear_available=nonlinear_available,
            nonlinear_backend=str(
                getattr(coordinator, "_nonlinear_backend", "ipopt") or "ipopt"
            ),
            ipopt_unavailable_reason=getattr(
                coordinator, "_ipopt_unavailable_reason", None
            ),
        )


def build_mpc_controller(config: ControllerBuildConfig) -> HeatingMPCController:
    """Construct an MPC controller from a :class:`ControllerBuildConfig`."""
    mpc_mode = validate_mpc_mode_available(
        config.mpc_mode,
        nonlinear_available=config.nonlinear_available,
        ipopt_available=config.ipopt_available,
        unavailable_reason=config.ipopt_unavailable_reason,
    )
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
        mpc_mode=mpc_mode,
        nonlinear_backend=config.nonlinear_backend,
    )
