"""Controller Tuning preview helpers for ControlEngine."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
from typing import Any

from . import const
from .nmpc_timing import timing_from_preview_overrides

_LOGGER = logging.getLogger("heatingassistant.engine.control_loop")

# MPC tuning keys accepted by Controller Tuning preview (exclude window detection).
_PREVIEW_TUNING_KEYS = frozenset(
    {
        const.CONF_COMFORT_OFFSET,
        const.CONF_TRACKING_WEIGHT,
        const.CONF_ENERGY_WEIGHT,
        const.CONF_ENERGY_PRICE_WEIGHT,
        const.CONF_SMOOTHING_WEIGHT,
        const.CONF_SOFT_CONSTRAINT_WEIGHT,
        const.CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
        const.CONF_TERMINAL_WEIGHT,
        const.CONF_UPDATE_INTERVAL,
        const.CONF_HORIZON,
        const.CONF_NMPC_PERIOD,
        const.CONF_NMPC_FAST_SUBSTEPS,
        const.CONF_NMPC_HORIZON_H,
    }
)

_PREVIEW_WEIGHT_DEFAULTS: dict[str, float] = {
    const.CONF_TRACKING_WEIGHT: const.DEFAULT_TRACKING_WEIGHT,
    const.CONF_ENERGY_WEIGHT: const.DEFAULT_ENERGY_WEIGHT,
    const.CONF_ENERGY_PRICE_WEIGHT: const.DEFAULT_ENERGY_PRICE_WEIGHT,
    const.CONF_SMOOTHING_WEIGHT: const.DEFAULT_SMOOTHING_WEIGHT,
    const.CONF_SOFT_CONSTRAINT_WEIGHT: const.DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    const.CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT: const.DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    const.CONF_TERMINAL_WEIGHT: const.DEFAULT_TERMINAL_WEIGHT,
}


def _snapshot_from_controller(
    controller: Any,
    *,
    dt: float,
    horizon: int,
    compute_ts: datetime | None = None,
) -> dict[str, Any]:
    """Build a forecast snapshot dict from a one-off controller solve."""

    filtered: dict[str, float] = {}
    raw_filtered = getattr(controller, "filtered_temperatures", None) or {}
    if isinstance(raw_filtered, Mapping):
        for key, value in raw_filtered.items():
            try:
                filtered[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return {
        "mode": "mpc",
        "compute_ts": compute_ts or datetime.now(timezone.utc),
        "predictions": [dict(item) for item in list(getattr(controller, "predictions", []) or [])],
        "linearised_predictions": [
            dict(item)
            for item in list(getattr(controller, "linearised_predictions", []) or [])
        ],
        "heating_schedule": [
            dict(item) for item in list(getattr(controller, "heating_schedule", []) or [])
        ],
        "outdoor_forecast": [
            float(value) for value in list(getattr(controller, "outdoor_forecast", []) or [])
        ],
        "solar_forecast": [
            dict(item) for item in list(getattr(controller, "solar_forecast", []) or [])
        ],
        "price_forecast": [
            float(value) for value in list(getattr(controller, "price_forecast", []) or [])
        ],
        "filtered_temperatures": filtered,
        "dt": float(dt),
        "horizon": int(horizon),
    }


class PreviewMixin:
    """One-off Controller Tuning forecast without mutating the live plan cache."""

    def _preview_matches_live(self, overrides: Mapping[str, Any], timing) -> bool:
        """True when a Tuning preview would solve the same problem as the live plan."""

        if self._controller is None:
            return False
        try:
            live = self._nmpc_timing()
        except ValueError:
            return False
        if abs(float(timing.dt_s) - float(live.dt_s)) > 1e-6:
            return False
        if int(timing.n_fast) != int(live.n_fast):
            return False
        if abs(float(timing.period_s) - float(live.period_s)) > 1e-6:
            return False
        for key, default in _PREVIEW_WEIGHT_DEFAULTS.items():
            if key not in overrides:
                continue
            try:
                draft = float(overrides[key])
            except (TypeError, ValueError):
                return False
            try:
                live_v = float(self.config.get(key, default))
            except (TypeError, ValueError):
                live_v = float(default)
            if abs(draft - live_v) > 1e-9:
                return False
        comfort = overrides.get(const.CONF_COMFORT_OFFSET)
        if comfort is not None:
            try:
                draft_c = float(comfort)
            except (TypeError, ValueError):
                return False
            for room in self.model.rooms.values():
                live_c = float(
                    getattr(room, "comfort_offset", const.DEFAULT_COMFORT_OFFSET)
                )
                if abs(draft_c - live_c) > 1e-9:
                    return False
        return True

    def preview_tuning_forecast(
        self,
        overrides: Mapping[str, Any] | None,
        room_temps: Mapping[str, float | None],
        outdoor_temp: float,
        setpoints: Mapping[str, float] | None = None,
        *,
        outdoor_forecast: list[float] | None = None,
        cloud_forecast: list[float] | None = None,
        cloud_cover_now: float | None = None,
        ghi_forecast: list[float | None] | None = None,
        ghi_now: float | None = None,
        price_forecast: list[float] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Run a one-off MPC solve with proposed tuning parameters.

        Does not mutate the live forecast caches used by room views. Room
        temperatures / setpoints on the shared model are updated to the supplied
        measurements (same as a normal compute). ``comfort_offset`` overrides are
        applied temporarily and restored afterwards.

        When the draft matches the live controller, return the live remaining
        plan that room view already plots instead of solving a second NLP.
        """

        ov = {
            key: value
            for key, value in dict(overrides or {}).items()
            if key in _PREVIEW_TUNING_KEYS and value is not None
        }
        try:
            timing = timing_from_preview_overrides(
                self.config,
                ov,
                default_period=const.DEFAULT_NMPC_PERIOD,
                default_substeps=const.DEFAULT_NMPC_FAST_SUBSTEPS,
                default_horizon_h=const.DEFAULT_NMPC_HORIZON_H,
            )
            preview_dt = timing.dt_s
            preview_horizon = timing.n_fast
        except ValueError as exc:
            _LOGGER.warning("preview_tuning_forecast: invalid NMPC timing: %s", exc)
            return {"error": "invalid_nmpc_timing"}

        if self._preview_matches_live(ov, timing):
            live = self.forecast_snapshot()
            if live.get("predictions"):
                return live

        comfort_override = ov.get(const.CONF_COMFORT_OFFSET)
        saved_comfort: dict[str, float] = {}
        if comfort_override is not None:
            preview_comfort = float(comfort_override)
            for name, room in self.model.rooms.items():
                saved_comfort[name] = float(
                    getattr(room, "comfort_offset", const.DEFAULT_COMFORT_OFFSET)
                )
                room.comfort_offset = preview_comfort

        try:
            try:
                preview_ctrl = self._build_controller_from_config(
                    {**self.config, **ov},
                    timing=timing,
                    horizon=preview_horizon,
                    dt=preview_dt,
                )
            except Exception as exc:
                _LOGGER.warning(
                    "preview_tuning_forecast: controller build failed: %s", exc
                )
                return {"error": "controller_unavailable"}
            if preview_ctrl is None:
                return {"error": "controller_unavailable"}

            if self._controller is not None:
                try:
                    x_hat, P = self._controller.ekf_state
                    preview_ctrl.restore_ekf_state(x_hat, P)
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.debug(
                        "preview_tuning_forecast: could not copy EKF state",
                        exc_info=True,
                    )

            self._apply_measurements(room_temps, dict(setpoints or {}))
            compute_now = now or datetime.now(timezone.utc)
            try:
                plan = preview_ctrl.solve_nmpc(
                    outdoor_temp,
                    now=compute_now,
                    outdoor_forecast=outdoor_forecast,
                    cloud_forecast=cloud_forecast,
                    cloud_cover_now=cloud_cover_now,
                    ghi_forecast=ghi_forecast,
                    ghi_now=ghi_now,
                    price_forecast=price_forecast,
                )
                preview_ctrl.apply_nmpc_result(plan)
                preview_ctrl.compute(
                    outdoor_temp,
                    solar_gains=None,
                    now=compute_now,
                    outdoor_forecast=outdoor_forecast,
                    cloud_forecast=cloud_forecast,
                    cloud_cover_now=cloud_cover_now,
                    ghi_forecast=ghi_forecast,
                    ghi_now=ghi_now,
                    price_forecast=price_forecast,
                    run_optimization=True,
                )
            except Exception as exc:
                _LOGGER.warning(
                    "preview_tuning_forecast: MPC compute failed: %s", exc
                )
                return {"error": "preview_compute_failed"}
            return _snapshot_from_controller(
                preview_ctrl,
                dt=preview_dt,
                horizon=preview_horizon,
                compute_ts=compute_now,
            )
        finally:
            for name, value in saved_comfort.items():
                room = self.model.rooms.get(name)
                if room is not None:
                    room.comfort_offset = value
