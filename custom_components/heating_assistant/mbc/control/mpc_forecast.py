"""
Forecast state and setter API shared by standard MPC implementations.

Horizon forecasts (disturbances, references, cost scales, input bounds,
electricity prices, …) are configured via :meth:`set_forecast` or the
individual ``set_*`` helpers before each :meth:`step` call.  The stored
forecast is reused on subsequent steps until replaced or cleared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class MPCForecast:
    """Horizon forecast bundle consumed by standard MPC ``step`` implementations."""

    disturbance_forecast: np.ndarray | None = None
    reference_sequence: np.ndarray | None = None
    offset_sequence: np.ndarray | None = None
    q_scale_sequence: np.ndarray | None = None
    r_scale_sequence: np.ndarray | None = None
    u_min_sequence: np.ndarray | None = None
    u_max_sequence: np.ndarray | None = None
    price_sequence: np.ndarray | None = None
    price_weight: float = 0.0
    elec_heat: np.ndarray | None = None
    elec_cool: np.ndarray | None = None
    bid_mask: np.ndarray | None = None
    dt_h: float = 0.25


@dataclass
class MPCOperatingPoint:
    """Optional linearisation / deviation origin for successive-linearisation MPC."""

    x_ss: np.ndarray
    u_ss: np.ndarray
    d_ss: np.ndarray


class ForecastAwareMPC:
    """
    Mixin providing forecast state and setter methods for standard MPC classes.

    Subclasses must call ``ForecastAwareMPC.__init__(self, N, nd)`` from their
    ``__init__`` once the horizon and disturbance dimension are known.
    """

    def __init__(self, N: int, nd: int = 0) -> None:
        self._N = int(N)
        self._nd = int(nd)
        self._forecast = MPCForecast()
        self._operating_point: MPCOperatingPoint | None = None

    @property
    def forecast(self) -> MPCForecast:
        """Current horizon forecast (read-only copy)."""
        fc = self._forecast
        return MPCForecast(
            disturbance_forecast=_copy_optional(fc.disturbance_forecast),
            reference_sequence=_copy_optional(fc.reference_sequence),
            offset_sequence=_copy_optional(fc.offset_sequence),
            q_scale_sequence=_copy_optional(fc.q_scale_sequence),
            r_scale_sequence=_copy_optional(fc.r_scale_sequence),
            u_min_sequence=_copy_optional(fc.u_min_sequence),
            u_max_sequence=_copy_optional(fc.u_max_sequence),
            price_sequence=_copy_optional(fc.price_sequence),
            price_weight=float(fc.price_weight),
            elec_heat=_copy_optional(fc.elec_heat),
            elec_cool=_copy_optional(fc.elec_cool),
            bid_mask=(
                None if fc.bid_mask is None
                else np.asarray(fc.bid_mask, dtype=bool).copy()
            ),
            dt_h=float(fc.dt_h),
        )

    def clear_forecast(self) -> None:
        """Reset all horizon forecasts to their defaults."""
        self._forecast = MPCForecast()

    def set_disturbance_forecast(self, D: Any | None) -> None:
        """Set the disturbance forecast ``(N, nd)`` over the prediction horizon."""
        if D is None:
            self._forecast.disturbance_forecast = None
            return
        D_np = np.asarray(D, dtype=float)
        if D_np.ndim == 1 and self._nd > 0:
            D_np = D_np.reshape(self._N, self._nd)
        self._forecast.disturbance_forecast = D_np

    def set_reference_sequence(self, seq: Any | None) -> None:
        """Set the absolute output/state reference sequence ``(N, nz)``."""
        self._forecast.reference_sequence = _coerce_optional(seq)

    def set_offset_sequence(self, seq: Any | None) -> None:
        """Set the soft-output corridor half-width sequence ``(N, nz)``."""
        self._forecast.offset_sequence = _coerce_optional(seq)

    def set_q_scale_sequence(self, seq: Any | None) -> None:
        """Set per-step tracking-cost multipliers ``(N, nz)``."""
        self._forecast.q_scale_sequence = _coerce_optional(seq)

    def set_r_scale_sequence(self, seq: Any | None) -> None:
        """Set per-step input-cost multipliers ``(N, nu)``."""
        self._forecast.r_scale_sequence = _coerce_optional(seq)

    def set_input_bounds_sequence(
        self,
        u_min_seq: Any | None = None,
        u_max_seq: Any | None = None,
    ) -> None:
        """Set per-step *absolute* input box bounds ``(N, nu)``."""
        self._forecast.u_min_sequence = _coerce_optional(u_min_seq)
        self._forecast.u_max_sequence = _coerce_optional(u_max_seq)

    def set_price_forecast(
        self,
        price_seq: Any | None,
        *,
        price_weight: float = 0.0,
        elec_heat: Any | None = None,
        elec_cool: Any | None = None,
        bid_mask: Any | None = None,
        dt_h: float = 0.25,
    ) -> None:
        """Configure the horizon electricity-price forecast and draw coefficients."""
        self._forecast.price_sequence = _coerce_optional(price_seq)
        self._forecast.price_weight = float(price_weight)
        self._forecast.elec_heat = _coerce_optional(elec_heat)
        self._forecast.elec_cool = _coerce_optional(elec_cool)
        self._forecast.bid_mask = (
            None if bid_mask is None else np.asarray(bid_mask, dtype=bool)
        )
        self._forecast.dt_h = float(dt_h)

    def set_forecast(self, **kwargs: Any) -> None:
        """Bulk-update any subset of horizon forecast fields."""
        if "disturbance_forecast" in kwargs:
            self.set_disturbance_forecast(kwargs["disturbance_forecast"])
        if "reference_sequence" in kwargs:
            self.set_reference_sequence(kwargs["reference_sequence"])
        if "offset_sequence" in kwargs:
            self.set_offset_sequence(kwargs["offset_sequence"])
        if "q_scale_sequence" in kwargs:
            self.set_q_scale_sequence(kwargs["q_scale_sequence"])
        if "r_scale_sequence" in kwargs:
            self.set_r_scale_sequence(kwargs["r_scale_sequence"])
        if "u_min_sequence" in kwargs or "u_max_sequence" in kwargs:
            self.set_input_bounds_sequence(
                kwargs.get("u_min_sequence"),
                kwargs.get("u_max_sequence"),
            )
        price_keys = {
            "price_sequence", "price_weight", "elec_heat",
            "elec_cool", "bid_mask", "dt_h",
        }
        if price_keys & kwargs.keys():
            fc = self._forecast
            self.set_price_forecast(
                kwargs.get("price_sequence", fc.price_sequence),
                price_weight=kwargs.get("price_weight", fc.price_weight),
                elec_heat=kwargs.get("elec_heat", fc.elec_heat),
                elec_cool=kwargs.get("elec_cool", fc.elec_cool),
                bid_mask=kwargs.get("bid_mask", fc.bid_mask),
                dt_h=kwargs.get("dt_h", fc.dt_h),
            )

    def set_operating_point(
        self,
        x_ss: Any,
        u_ss: Any,
        d_ss: Any | None = None,
    ) -> None:
        """
        Pin the successive-linearisation origin for the next ``step``.

        When set, the local model is linearised at ``(x_ss, u_ss, d_ss)`` and the
        QP is solved in deviation coordinates about that point.  Call
        :meth:`clear_operating_point` to revert to estimate-based linearisation.
        """
        x_ss_np = np.asarray(x_ss, dtype=float).reshape(-1)
        u_ss_np = np.asarray(u_ss, dtype=float).reshape(-1)
        if d_ss is None:
            d_ss_np = np.zeros(self._nd, dtype=float)
        else:
            d_ss_np = np.asarray(d_ss, dtype=float).reshape(-1)
        self._operating_point = MPCOperatingPoint(x_ss_np, u_ss_np, d_ss_np)

    def clear_operating_point(self) -> None:
        """Revert to estimate-based linearisation at the next ``step``."""
        self._operating_point = None

    def _disturbance_deviation_trajectory(self, d_ss: np.ndarray) -> np.ndarray:
        """Build ``(N, nd)`` deviation disturbance from the stored forecast."""
        if self._forecast.disturbance_forecast is None:
            return np.zeros((self._N, self._nd), dtype=float)
        D_abs = np.asarray(
            self._forecast.disturbance_forecast, dtype=float,
        ).reshape(self._N, self._nd)
        return D_abs - d_ss.reshape(1, -1)


def _copy_optional(arr: np.ndarray | None) -> np.ndarray | None:
    return None if arr is None else np.asarray(arr, dtype=float).copy()


def _coerce_optional(arr: Any | None) -> np.ndarray | None:
    return None if arr is None else np.asarray(arr, dtype=float)
