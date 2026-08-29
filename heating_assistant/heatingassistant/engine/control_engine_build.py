"""MPC controller construction for ControlEngine."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from . import const
from .nmpc_timing import timing_from_dt_horizon

_LOGGER = logging.getLogger("heatingassistant.engine.control_loop")


class BuildMixin:
    """Construct the live MPC controller from App config."""

    def _try_build_controller(self) -> Any:
        if not self.heat_sources or not self.model.rooms:
            self.mode = "proportional"
            self.fallback_reason = "no heat sources or rooms configured"
            return None

        try:
            controller = self._build_controller_from_config(self.config)
        except Exception as exc:  # pragma: no cover - exercised when optional deps differ
            self.mode = "proportional"
            self.fallback_reason = f"controller unavailable: {exc}"
            _LOGGER.info("HeatingAssistant engine using fallback control: %s", exc)
            return None

        if controller is None:
            self.mode = "proportional"
            self.fallback_reason = "no heat sources or rooms configured"
            return None

        self.mode = "mpc"
        self.fallback_reason = None
        return controller

    def _build_controller_from_config(
        self,
        config: Mapping[str, Any],
        *,
        horizon: int | None = None,
        dt: float | None = None,
        timing: Any | None = None,
    ) -> Any:
        """Construct an MPC controller from a config mapping (may raise)."""

        if not self.heat_sources or not self.model.rooms:
            return None

        from .controller.factory import (  # noqa: PLC0415
            ControllerBuildConfig,
            build_mpc_controller,
        )

        if timing is None:
            if dt is not None or horizon is not None:
                base = self._nmpc_timing(config)
                timing = timing_from_dt_horizon(
                    float(dt if dt is not None else base.dt_s),
                    int(horizon if horizon is not None else base.n_fast),
                )
            else:
                timing = self._nmpc_timing(config)
        preview_dt = float(timing.dt_s)
        preview_horizon = int(timing.n_fast)
        build_config = ControllerBuildConfig(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=preview_horizon,
            dt=preview_dt,
            measurement_dt=float(config.get("measurement_dt", preview_dt)),
            latitude=float(config.get("latitude", 0.0)),
            longitude=float(config.get("longitude", 0.0)),
            tracking_weight=float(
                config.get("tracking_weight", const.DEFAULT_TRACKING_WEIGHT)
            ),
            energy_weight=float(
                config.get("energy_weight", const.DEFAULT_ENERGY_WEIGHT)
            ),
            smoothing_weight=float(
                config.get("smoothing_weight", const.DEFAULT_SMOOTHING_WEIGHT)
            ),
            soft_constraint_weight=float(
                config.get(
                    "soft_constraint_weight",
                    const.DEFAULT_SOFT_CONSTRAINT_WEIGHT,
                )
            ),
            soft_constraint_linear_weight=float(
                config.get(
                    "soft_constraint_linear_weight",
                    const.DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
                )
            ),
            terminal_weight=float(
                config.get("terminal_weight", const.DEFAULT_TERMINAL_WEIGHT)
            ),
            sigma_w=float(config.get("sigma_w", const.DEFAULT_SIGMA_W)),
            sigma_v=float(config.get("sigma_v", const.DEFAULT_SIGMA_V)),
            sigma_b=float(config.get("sigma_b", const.DEFAULT_SIGMA_B)),
            energy_price_weight=float(
                config.get(
                    "energy_price_weight",
                    const.DEFAULT_ENERGY_PRICE_WEIGHT,
                )
            ),
            albedo=float(config.get("ground_albedo", const.DEFAULT_GROUND_ALBEDO)),
            nmpc_period=timing.period_s,
            nmpc_fast_substeps=timing.fast_substeps,
            nmpc_horizon_h=timing.horizon_h,
            p_deadband=float(
                config.get(const.CONF_P_DEADBAND, const.DEFAULT_P_DEADBAND)
            ),
            u_ref_gate=float(
                config.get(const.CONF_U_REF_GATE, const.DEFAULT_U_REF_GATE)
            ),
        )
        return build_mpc_controller(build_config)
