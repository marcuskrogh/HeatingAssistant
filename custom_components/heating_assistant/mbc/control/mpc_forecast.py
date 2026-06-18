"""
Horizon profile state and setter API shared by standard MPC implementations.

Known future profiles over the prediction horizon — disturbances, references,
cost weights, constraint bounds, linear input penalties, and so on — are
configured via :meth:`set_horizon_profile` or the individual ``set_*`` helpers
before each :meth:`step` call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class MPCHorizonProfile:
    """Horizon profile bundle consumed by standard MPC ``step`` implementations."""

    disturbance_profile: np.ndarray | None = None
    output_reference_profile: np.ndarray | None = None
    soft_output_band_half_width_profile: np.ndarray | None = None
    output_tracking_weight_scale_profile: np.ndarray | None = None
    input_regularisation_weight_scale_profile: np.ndarray | None = None
    input_lower_bound_profile: np.ndarray | None = None
    input_upper_bound_profile: np.ndarray | None = None
    input_linear_cost_coefficient_profile: np.ndarray | None = None
    input_linear_cost_slack_input_indices: np.ndarray | None = None
    input_linear_cost_positive_slack_coefficient_profile: np.ndarray | None = None
    input_linear_cost_negative_slack_coefficient_profile: np.ndarray | None = None


@dataclass
class MPCLinearisationPoint:
    """Optional linearisation / deviation origin for successive-linearisation MPC."""

    state: np.ndarray
    input: np.ndarray
    disturbance: np.ndarray


class HorizonProfileMPC:
    """
    Mixin providing horizon-profile state and setter methods for standard MPC.

    Subclasses must call ``HorizonProfileMPC.__init__(self, N, nd)`` from their
    ``__init__`` once the horizon and disturbance dimension are known.
    """

    def __init__(self, N: int, nd: int = 0) -> None:
        self._N = int(N)
        self._nd = int(nd)
        self._horizon_profile = MPCHorizonProfile()
        self._linearisation_point: MPCLinearisationPoint | None = None

    @property
    def horizon_profile(self) -> MPCHorizonProfile:
        """Current horizon profile (read-only copy)."""
        hp = self._horizon_profile
        return MPCHorizonProfile(
            disturbance_profile=_copy_optional(hp.disturbance_profile),
            output_reference_profile=_copy_optional(hp.output_reference_profile),
            soft_output_band_half_width_profile=_copy_optional(
                hp.soft_output_band_half_width_profile
            ),
            output_tracking_weight_scale_profile=_copy_optional(
                hp.output_tracking_weight_scale_profile
            ),
            input_regularisation_weight_scale_profile=_copy_optional(
                hp.input_regularisation_weight_scale_profile
            ),
            input_lower_bound_profile=_copy_optional(hp.input_lower_bound_profile),
            input_upper_bound_profile=_copy_optional(hp.input_upper_bound_profile),
            input_linear_cost_coefficient_profile=_copy_optional(
                hp.input_linear_cost_coefficient_profile
            ),
            input_linear_cost_slack_input_indices=(
                None if hp.input_linear_cost_slack_input_indices is None
                else np.asarray(hp.input_linear_cost_slack_input_indices, dtype=int).copy()
            ),
            input_linear_cost_positive_slack_coefficient_profile=_copy_optional(
                hp.input_linear_cost_positive_slack_coefficient_profile
            ),
            input_linear_cost_negative_slack_coefficient_profile=_copy_optional(
                hp.input_linear_cost_negative_slack_coefficient_profile
            ),
        )

    def clear_horizon_profile(self) -> None:
        """Reset all horizon profiles to their defaults."""
        self._horizon_profile = MPCHorizonProfile()

    def set_disturbance_profile(self, profile: Any | None) -> None:
        """Set the disturbance profile ``(N, nd)`` over the prediction horizon."""
        if profile is None:
            self._horizon_profile.disturbance_profile = None
            return
        profile_np = np.asarray(profile, dtype=float)
        if profile_np.ndim == 1 and self._nd > 0:
            profile_np = profile_np.reshape(self._N, self._nd)
        self._horizon_profile.disturbance_profile = profile_np

    def set_output_reference_profile(self, profile: Any | None) -> None:
        """Set the absolute output reference profile ``(N, nz)``."""
        self._horizon_profile.output_reference_profile = _coerce_optional(profile)

    def set_soft_output_band_half_width_profile(self, profile: Any | None) -> None:
        """Set the soft-output corridor half-width profile ``(N, nz)``."""
        self._horizon_profile.soft_output_band_half_width_profile = _coerce_optional(profile)

    def set_output_tracking_weight_scale_profile(self, profile: Any | None) -> None:
        """Set per-step output-tracking weight multipliers ``(N, nz)``."""
        self._horizon_profile.output_tracking_weight_scale_profile = _coerce_optional(profile)

    def set_input_regularisation_weight_scale_profile(self, profile: Any | None) -> None:
        """Set per-step input-regularisation weight multipliers ``(N, nu)``."""
        self._horizon_profile.input_regularisation_weight_scale_profile = _coerce_optional(profile)

    def set_input_lower_bound_profile(self, profile: Any | None) -> None:
        """Set per-step absolute input lower-bound profile ``(N, nu)``."""
        self._horizon_profile.input_lower_bound_profile = _coerce_optional(profile)

    def set_input_upper_bound_profile(self, profile: Any | None) -> None:
        """Set per-step absolute input upper-bound profile ``(N, nu)``."""
        self._horizon_profile.input_upper_bound_profile = _coerce_optional(profile)

    def set_input_bound_profiles(
        self,
        lower: Any | None = None,
        upper: Any | None = None,
    ) -> None:
        """Set per-step absolute input box-bound profiles ``(N, nu)``."""
        self.set_input_lower_bound_profile(lower)
        self.set_input_upper_bound_profile(upper)

    def set_input_linear_cost_profile(
        self,
        coefficient_profile: Any | None,
        *,
        slack_input_indices: Any | None = None,
        positive_slack_coefficient_profile: Any | None = None,
        negative_slack_coefficient_profile: Any | None = None,
    ) -> None:
        """
        Set the horizon linear (Mayer) input-cost profile.

        Parameters
        ----------
        coefficient_profile : (N, nu) array-like or None
            Linear cost coefficient added to the QP gradient on each input
            ``u[k, i]`` at horizon step ``k``.
        slack_input_indices : (n_slack,) int array-like or None
            Input indices whose linear cost is enforced through nonnegative
            slack variables ``s⁺ − s⁻ = u`` (for signed-magnitude penalties).
        positive_slack_coefficient_profile : (N, n_slack) array-like or None
            Linear cost on the positive slack branch at each horizon step.
        negative_slack_coefficient_profile : (N, n_slack) array-like or None
            Linear cost on the negative slack branch at each horizon step.
        """
        self._horizon_profile.input_linear_cost_coefficient_profile = _coerce_optional(
            coefficient_profile
        )
        self._horizon_profile.input_linear_cost_slack_input_indices = (
            None if slack_input_indices is None
            else np.asarray(slack_input_indices, dtype=int).reshape(-1)
        )
        self._horizon_profile.input_linear_cost_positive_slack_coefficient_profile = (
            _coerce_optional(positive_slack_coefficient_profile)
        )
        self._horizon_profile.input_linear_cost_negative_slack_coefficient_profile = (
            _coerce_optional(negative_slack_coefficient_profile)
        )

    def set_horizon_profile(self, **kwargs: Any) -> None:
        """Bulk-update any subset of horizon profile fields."""
        mapping = {
            "disturbance_profile": self.set_disturbance_profile,
            "output_reference_profile": self.set_output_reference_profile,
            "soft_output_band_half_width_profile": (
                self.set_soft_output_band_half_width_profile
            ),
            "output_tracking_weight_scale_profile": (
                self.set_output_tracking_weight_scale_profile
            ),
            "input_regularisation_weight_scale_profile": (
                self.set_input_regularisation_weight_scale_profile
            ),
            "input_lower_bound_profile": self.set_input_lower_bound_profile,
            "input_upper_bound_profile": self.set_input_upper_bound_profile,
        }
        for key, setter in mapping.items():
            if key in kwargs:
                setter(kwargs[key])

        linear_cost_keys = {
            "input_linear_cost_coefficient_profile",
            "input_linear_cost_slack_input_indices",
            "input_linear_cost_positive_slack_coefficient_profile",
            "input_linear_cost_negative_slack_coefficient_profile",
            "slack_input_indices",
            "positive_slack_coefficient_profile",
            "negative_slack_coefficient_profile",
        }
        if linear_cost_keys & kwargs.keys():
            hp = self._horizon_profile
            self.set_input_linear_cost_profile(
                kwargs.get(
                    "input_linear_cost_coefficient_profile",
                    hp.input_linear_cost_coefficient_profile,
                ),
                slack_input_indices=kwargs.get(
                    "input_linear_cost_slack_input_indices",
                    kwargs.get("slack_input_indices", hp.input_linear_cost_slack_input_indices),
                ),
                positive_slack_coefficient_profile=kwargs.get(
                    "input_linear_cost_positive_slack_coefficient_profile",
                    kwargs.get(
                        "positive_slack_coefficient_profile",
                        hp.input_linear_cost_positive_slack_coefficient_profile,
                    ),
                ),
                negative_slack_coefficient_profile=kwargs.get(
                    "input_linear_cost_negative_slack_coefficient_profile",
                    kwargs.get(
                        "negative_slack_coefficient_profile",
                        hp.input_linear_cost_negative_slack_coefficient_profile,
                    ),
                ),
            )

    def set_linearisation_point(
        self,
        state: Any,
        input: Any,
        disturbance: Any | None = None,
    ) -> None:
        """
        Pin the successive-linearisation origin for the next ``step``.

        When set, the local model is linearised at ``(state, input, disturbance)``
        and the QP is solved in deviation coordinates about that point.  Call
        :meth:`clear_linearisation_point` to revert to estimate-based linearisation.
        """
        state_np = np.asarray(state, dtype=float).reshape(-1)
        input_np = np.asarray(input, dtype=float).reshape(-1)
        if disturbance is None:
            disturbance_np = np.zeros(self._nd, dtype=float)
        else:
            disturbance_np = np.asarray(disturbance, dtype=float).reshape(-1)
        self._linearisation_point = MPCLinearisationPoint(
            state_np, input_np, disturbance_np
        )

    def clear_linearisation_point(self) -> None:
        """Revert to estimate-based linearisation at the next ``step``."""
        self._linearisation_point = None

    def _disturbance_deviation_trajectory(self, d_ss: np.ndarray) -> np.ndarray:
        """Build ``(N, nd)`` deviation disturbance from the stored profile."""
        if self._horizon_profile.disturbance_profile is None:
            return np.zeros((self._N, self._nd), dtype=float)
        D_abs = np.asarray(
            self._horizon_profile.disturbance_profile, dtype=float,
        ).reshape(self._N, self._nd)
        return D_abs - d_ss.reshape(1, -1)

    def _has_input_linear_cost(self) -> bool:
        hp = self._horizon_profile
        if hp.input_linear_cost_coefficient_profile is not None:
            return True
        return (
            hp.input_linear_cost_positive_slack_coefficient_profile is not None
            or hp.input_linear_cost_negative_slack_coefficient_profile is not None
        )


# Backward-compatible aliases (deprecated).
MPCForecast = MPCHorizonProfile
MPCOperatingPoint = MPCLinearisationPoint
ForecastAwareMPC = HorizonProfileMPC


def _copy_optional(arr: np.ndarray | None) -> np.ndarray | None:
    return None if arr is None else np.asarray(arr, dtype=float).copy()


def _coerce_optional(arr: Any | None) -> np.ndarray | None:
    return None if arr is None else np.asarray(arr, dtype=float)
