"""Heating Assistant sensor platform — model diagnostics sensors."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..coordinator import HeatingAssistantCoordinator
from ..heat_sources import HeatPump
from ..kpi import (
    RoomSnapshot,
    comfort_index_pct,
    mean_tracking_error_c,
    room_comfort_deviation_c,
    room_temperature,
)
from ..naming import slugify
from .base import (
    _LiveValueSensorMixin,
    _ConstraintSensorBase,
    _build_horizon_forecast,
    _closed_loop_fit_for_room,
    _constraint_bound,
    _kpi_room_snapshot,
    _kpi_room_snapshots,
    _room_estimation_provenance,
    _setpoint_value,
)

_LOGGER = logging.getLogger(__name__)

class PredictionErrorSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the current prediction error (residual) for a room.

    The state is the most recent prediction error [°C]:
        error = predicted_temp - measured_temp

    Positive error = model over-predicts (predicts warmer than actual)
    Negative error = model under-predicts (predicts colder than actual)

    Historical errors over the MPC horizon are exposed as attributes for
    visualization of model fit quality.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-bell-curve"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Prediction Error"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_prediction_error"

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent aligned prediction error [°C].

        Uses the ``y_pred`` field which is the prediction made at the previous
        cycle *for* the current cycle — so y_pred and y refer to the same
        timestep and their difference is a genuine forecast error.
        """
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        # Walk backwards to find the latest record with a valid aligned y_pred
        for record in reversed(list(self._coordinator.history_buffer)):
            y_pred = record.get("y_pred")
            y = record.get("y", [])
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                return round(float(y_pred[room_idx]) - float(y[room_idx]), 3)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose historical prediction errors and fit metrics."""
        errors = []
        room_idx = self._coordinator.model.room_names.index(self._room_name)

        for record in list(self._coordinator.history_buffer)[-50:]:  # Last 50 samples
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # aligned: prediction made at k-1 for k

            # Skip records without an aligned prediction (first record after start)
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                error = y_pred[room_idx] - y[room_idx]
                errors.append(round(error, 3))

        # Compute basic statistics
        if errors:
            import numpy as np
            errors_arr = np.array(errors)
            rmse = float(np.sqrt(np.mean(errors_arr ** 2)))
            mae = float(np.mean(np.abs(errors_arr)))
            bias = float(np.mean(errors_arr))
            max_error = float(np.max(np.abs(errors_arr)))
        else:
            rmse = mae = bias = max_error = 0.0

        return {
            "recent_errors": errors,
            "rmse": round(rmse, 3),
            "mae": round(mae, 3),
            "bias": round(bias, 3),
            "max_error": round(max_error, 3),
            "n_samples": len(errors),
        }


# ---------------------------------------------------------------------------
# Model fit quality sensor (per room)
# ---------------------------------------------------------------------------

class ModelFitQualitySensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting overall model fit quality for a room.

    The state is the R² (coefficient of determination) score [0-1]:
        1.0 = perfect fit
        0.0 = no better than mean prediction
        < 0 = worse than mean prediction

    Additional fit metrics (RMSE, MAE, autocorrelation) are exposed as
    attributes for detailed diagnostics.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:poll"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Model Fit Quality"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_model_fit_quality"

    @property
    def native_value(self) -> Optional[float]:
        """Return R² score as the main quality metric."""
        from .model_diagnostics import compute_model_fit_metrics

        # Extract predictions and measurements from history
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        predictions = []
        measurements = []

        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            # Skip records where no aligned prediction was stored yet
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                predictions.append(y_pred[room_idx])
                measurements.append(y[room_idx])

        if len(predictions) < 2:
            return None

        try:
            metrics = compute_model_fit_metrics(predictions, measurements, self._room_name)
            return round(metrics.r_squared, 4)
        except Exception as exc:
            _LOGGER.warning("Failed to compute model fit quality for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose detailed fit metrics."""
        from .model_diagnostics import compute_model_fit_metrics

        # Extract predictions and measurements from history
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        predictions = []
        measurements = []

        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            # Skip records where no aligned prediction was stored yet
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                predictions.append(y_pred[room_idx])
                measurements.append(y[room_idx])

        if len(predictions) < 2:
            return {
                "error": "Insufficient data",
                "n_samples": len(predictions),
            }

        try:
            metrics = compute_model_fit_metrics(predictions, measurements, self._room_name)
            return {
                "r_squared": round(metrics.r_squared, 4),
                "rmse": round(metrics.rmse, 3),
                "mae": round(metrics.mae, 3),
                "bias": round(metrics.bias, 3),
                "max_error": round(metrics.max_error, 2),
                "residual_std": round(metrics.residual_std, 3),
                "residual_autocorr_lag1": (
                    round(metrics.residual_autocorr_lag1, 3)
                    if metrics.residual_autocorr_lag1 is not None
                    else None
                ),
                "n_samples": metrics.n_samples,
            }
        except Exception as exc:
            _LOGGER.warning("Failed to compute fit metrics for %s: %s", self._room_name, exc)
            return {
                "error": str(exc),
                "n_samples": len(predictions),
            }

class ParameterConfidenceSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting confidence/validity of thermal parameters for a room.

    The state is a confidence score [0-100]:
        100 = all parameters in valid range
        0 = parameters outside valid range or no data

    Detailed parameter validation warnings are exposed as attributes.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:shield-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Parameter Confidence"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_parameter_confidence"

    @property
    def native_value(self) -> Optional[float]:
        """Return confidence score [0-100]."""
        from .model_diagnostics import validate_parameters

        room = self._coordinator.model.rooms[self._room_name]

        try:
            fit_r2, fit_rmse, _ = _closed_loop_fit_for_room(
                self._coordinator, self._room_name,
            )
            validation = validate_parameters(
                self._room_name,
                room.thermal_mass,
                room.r_external,
                model_r_squared=fit_r2,
                model_rmse=fit_rmse,
            )

            # Compute confidence score
            score = 0.0
            if validation.mass_valid:
                score += 33.3
            if validation.r_external_valid:
                score += 33.3
            if validation.time_constant_valid:
                score += 33.4

            return round(score, 1)
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose detailed parameter validation."""
        from .model_diagnostics import (
            build_identification_warnings,
            validate_parameters,
        )

        room = self._coordinator.model.rooms[self._room_name]

        try:
            fit_r2, fit_rmse, n_samples = _closed_loop_fit_for_room(
                self._coordinator, self._room_name,
            )
            validation = validate_parameters(
                self._room_name,
                room.thermal_mass,
                room.r_external,
                model_r_squared=fit_r2,
                model_rmse=fit_rmse,
            )

            snapshot = None
            try:
                snapshot = self._coordinator.estimated_params_snapshot
            except Exception:
                pass
            is_estimated, estimated_at = _room_estimation_provenance(
                snapshot, self._room_name,
            )

            ol_rmse = getattr(self._coordinator, "open_loop_results", {}).get(
                self._room_name, {},
            ).get("rmse")
            card_warnings = build_identification_warnings(
                self._room_name,
                validation,
                model_r_squared=fit_r2,
                model_rmse=fit_rmse,
                open_loop_rmse=ol_rmse,
                n_samples=n_samples,
            )

            return {
                "thermal_mass": validation.thermal_mass,
                "r_external": validation.r_external,
                "internal_gain": round(
                    float(getattr(room, "internal_gain", 0.0)), 2
                ),
                "time_constant_hours": round(validation.time_constant_hours, 2),
                "mass_valid": validation.mass_valid,
                "r_external_valid": validation.r_external_valid,
                "time_constant_valid": validation.time_constant_valid,
                "warnings": validation.warnings,
                "card_warnings": [
                    {"code": w.code, "message": w.message, "severity": w.severity}
                    for w in card_warnings
                ],
                "is_estimated": is_estimated,
                "estimated_at": estimated_at,
            }
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", self._room_name, exc)
            return {
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# Open-loop RMSE sensor (per room) – direct model quality indicator
# ---------------------------------------------------------------------------

class OpenLoopRMSESensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the open-loop prediction RMSE for a room.

    Values come from a continuous open-loop simulation over the history
    buffer (no Kalman correction, no artificial segment restarts).  The
    ``rmse_by_horizon`` attribute reports segmented RMSE at ~4 h / 12 h /
    24 h lookahead horizons.  Together these show how much the thermal
    model drifts from reality, which is the root cause of MPC overshoot.

    Rule of thumb (continuous RMSE):
        < 0.2 °C: excellent – MPC predictions are reliable
        0.2–0.5 °C: acceptable
        > 0.5 °C: likely contributing to overshoot; re-run parameter estimation
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Open Loop RMSE"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_open_loop_rmse"

    @property
    def native_value(self) -> Optional[float]:
        """Return open-loop RMSE [°C] for this room, from coordinator cache."""
        room_data = self._coordinator.open_loop_results.get(self._room_name, {})
        return room_data.get("rmse")

    @property
    def extra_state_attributes(self) -> dict:
        """Expose open-loop simulation data for Apex Charts, from coordinator cache."""
        room_data = self._coordinator.open_loop_results.get(self._room_name, {})
        sim = room_data.get("simulation", [])
        from datetime import datetime, timezone
        formatted_sim = []
        for entry in sim:
            ts = entry.get("time", 0.0)
            dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            ol_entry: dict = {
                "time": dt_iso,
                "measured": entry.get("measured"),
                "predicted": entry.get("predicted"),
            }
            if entry.get("predicted_wall") is not None:
                ol_entry["predicted_wall"] = entry["predicted_wall"]
            formatted_sim.append(ol_entry)
        attrs: dict = {
            "open_loop_rmse": room_data.get("rmse"),
            "open_loop_mae": room_data.get("mae"),
            # RMSE at ~4 h / 12 h / 24 h open-loop horizons — how the
            # prediction error grows with lookahead, which is what matters
            # for price-driven anticipatory heating.
            "rmse_by_horizon": room_data.get("rmse_by_horizon"),
            "simulation": formatted_sim,
        }
        if "error" in room_data:
            attrs["error"] = room_data["error"]
        return attrs


# ---------------------------------------------------------------------------
# Kalman innovation sensor (per room)
# ---------------------------------------------------------------------------

class KalmanInnovationSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the most recent Kalman filter innovation ν = y − C x̂⁻.

    A well-tuned model/filter should have innovations that are:
        - Zero-mean (no systematic bias)
        - White noise (no autocorrelation)
        - Consistent with the innovation covariance

    Persistent non-zero mean or high autocorrelation indicates that the
    thermal model is missing dynamics (e.g. inter-room coupling, solar,
    or heat source dynamics).
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:waveform"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Kalman Innovation"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_kalman_innovation"

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent Kalman innovation [°C] for this room."""
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        for record in reversed(list(self._coordinator.history_buffer)):
            innov = record.get("kalman_innovation")
            if innov is not None and room_idx < len(innov):
                return round(float(innov[room_idx]), 4)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose innovation time series and statistics."""
        from datetime import datetime, timezone
        import numpy as np

        room_idx = self._coordinator.model.room_names.index(self._room_name)
        innovations = []

        for record in self._coordinator.history_buffer:
            innov = record.get("kalman_innovation")
            ts = record.get("timestamp", 0.0)
            if innov is not None and room_idx < len(innov):
                dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                innovations.append({
                    "time": dt_iso,
                    "value": round(float(innov[room_idx]), 4),
                })

        if not innovations:
            return {"innovations": [], "n_samples": 0}

        vals = np.array([e["value"] for e in innovations])
        mean_v = float(np.mean(vals))
        std_v = float(np.std(vals))

        # Lag-1 autocorrelation
        autocorr_lag1: Optional[float] = None
        if len(vals) >= 4:
            n_v = len(vals)
            c0 = float(np.dot(vals - mean_v, vals - mean_v))
            if c0 > 0:
                c1 = float(np.dot((vals - mean_v)[1:], (vals - mean_v)[:-1]))
                autocorr_lag1 = round(c1 / c0, 4)

        # Consistency: |mean| < 2 * std/sqrt(n) (approximate 95% test)
        n_samples = len(vals)
        is_consistent = abs(mean_v) < 2.0 * (std_v / max(1.0, float(np.sqrt(n_samples))))

        return {
            "innovations": innovations[-100:],  # keep last 100 for attribute size
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "autocorr_lag1": autocorr_lag1,
            "is_consistent": is_consistent,
            "n_samples": n_samples,
        }


# ---------------------------------------------------------------------------
# Residual ACF sensor (per room)
# ---------------------------------------------------------------------------

class ResidualACFSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the lag-1 autocorrelation of 1-step prediction residuals.

    A lag-1 autocorrelation close to 0 indicates that the residuals are
    white noise (good model).  High autocorrelation (> 0.3) means the model
    is systematically missing dynamics — a signal to re-run parameter
    estimation or check the model configuration.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-bar"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Residual ACF"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_residual_acf"

    @property
    def native_value(self) -> Optional[float]:
        """Return lag-1 residual autocorrelation."""
        try:
            acf_result = self._compute_acf()
            acf = acf_result.get("acf", [])
            return round(acf[1], 4) if len(acf) > 1 else None
        except Exception:
            return None

    def _compute_acf(self) -> dict:
        from .model_diagnostics import compute_autocorrelation_function

        room_idx = self._coordinator.model.room_names.index(self._room_name)
        residuals = []
        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                residuals.append(float(y_pred[room_idx]) - float(y[room_idx]))
        return compute_autocorrelation_function(residuals)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose full ACF, confidence bounds, and Ljung-Box statistic."""
        try:
            return self._compute_acf()
        except Exception as exc:
            _LOGGER.debug("ResidualACFSensor error for %s: %s", self._room_name, exc)
            return {"error": str(exc)}

class HeaterScaleSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the current power-scale factor for a heat source.

    The state is the scale as a percentage (100 % = nominal rated power).
    A value above 100 % means the estimator found that the source delivers
    more heat than its nominal rating predicts; below 100 % means it
    delivers less (e.g. due to duct losses or a degraded heat pump).

    The factor is 1.0 (= 100 %) when no estimation has been run yet.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:tune-vertical"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – Heater Scale"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_heater_scale"

    def _source(self):
        return next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the power-scale factor as a percentage."""
        src = self._source()
        if src is None:
            return None
        return round(float(getattr(src, "power_scale", 1.0)) * 100.0, 1)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose raw scale factor and estimation provenance."""
        src = self._source()
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        is_estimated = (
            self._source_name in snapshot.get("sources", {})
            if snapshot else False
        )
        return {
            "source_name": self._source_name,
            "power_scale": round(float(getattr(src, "power_scale", 1.0)), 4) if src else None,
            "max_power": float(src.max_power) if src else None,
            "is_estimated": is_estimated,
            "estimated_at": snapshot.get("estimated_at") if snapshot else None,
        }


# ---------------------------------------------------------------------------
# Log-likelihood slice sensor (per room)
# ---------------------------------------------------------------------------


class LoglikSliceSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Most recent log-likelihood slice computed for a room.

    Populated by the :meth:`HeatingAssistantCoordinator.async_compute_loglik_slice`
    call (triggered by the ``compute_loglik_slice`` service / dashboard
    button). The state is the ISO timestamp of the last successful run –
    set to ``unknown`` until the user requests a slice for the room. The
    attributes carry the full grid so a Plotly card or template sensor can
    visualise the likelihood landscape without recomputing.
    """

    _attr_icon = "mdi:chart-bell-curve"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Loglik Slice"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_loglik_slice"

    def _slice(self) -> Optional[Dict[str, Any]]:
        slices = getattr(self._coordinator, "_loglik_slices", {}) or {}
        return slices.get(self._room_name)

    @property
    def native_value(self) -> Optional[str]:
        sl = self._slice()
        return None if sl is None else sl.get("computed_at")

    @property
    def extra_state_attributes(self) -> dict:
        sl = self._slice()
        if sl is None:
            return {
                "room": self._room_name,
                "status": "not_computed",
            }
        # Avoid copying the (potentially large) grid until the user opens
        # the entity – it lives in coordinator state so this is a view.
        return dict(sl)

class EstimatedParametersStatusSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    System-wide sensor summarising all currently active thermal parameters.

    State
    -----
    * ``"estimated"`` — at least one parameter was set by the ML estimator
      and the snapshot has been persisted to ``entry.data``.
    * ``"default"`` — no estimation has been run yet (or it was reset).

    Attributes
    ----------
    The attributes expose the full parameter set as a machine-readable dict
    that dashboards and automations can consume directly without querying
    individual per-room sensors:

    * ``rooms``       — per-room ``{thermal_mass, r_external, internal_gain, is_estimated}``
    * ``sources``     — per-source ``{power_scale, is_estimated}``
    * ``connections`` — per-pair ``{r_value, is_estimated}``
    * ``estimated_at``        — ISO-8601 timestamp of the last successful run
    * ``log_likelihood``      — log-likelihood value at the optimal solution
    * ``n_rooms_estimated``   — number of rooms whose parameters were estimated
    * ``n_sources_estimated`` — number of sources whose scale was estimated
    """

    _attr_icon = "mdi:database-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Estimated Parameters Status"
        self._attr_unique_id = f"{DOMAIN}_estimated_parameters_status"

    @property
    def native_value(self) -> str:
        """Return ``"estimated"`` if a persisted snapshot exists, else ``"default"``."""
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        return "estimated" if snapshot else "default"

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the full parameter set with estimation provenance flags."""
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        snap = snapshot or {}
        rooms_snap = snap.get("rooms", {})
        sources_snap = snap.get("sources", {})
        connections_snap = snap.get("connections", {})

        rooms_data: Dict[str, Any] = {}
        for name, room in self._coordinator.model.rooms.items():
            room_snap = rooms_snap.get(name, {}) if isinstance(rooms_snap.get(name), dict) else {}
            is_estimated = bool(room_snap.get("is_estimated", False)) if room_snap else False
            rooms_data[name] = {
                "thermal_mass": round(room.thermal_mass, 0),
                "r_external": round(room.r_external, 6),
                "internal_gain": round(
                    float(getattr(room, "internal_gain", 0.0)), 2
                ),
                "is_estimated": is_estimated,
            }

        sources_data: Dict[str, Any] = {}
        for src in self._coordinator.heat_sources:
            sources_data[src.name] = {
                "power_scale": round(float(getattr(src, "power_scale", 1.0)), 4),
                "is_estimated": src.name in sources_snap,
            }

        connections_data: Dict[str, Any] = {
            key: {"r_value": r_val, "is_estimated": True}
            for key, r_val in connections_snap.items()
        }

        history = list(
            getattr(self._coordinator, "_estimation_history", []) or []
        )
        return {
            "rooms": rooms_data,
            "sources": sources_data,
            "connections": connections_data,
            "estimated_at": snap.get("estimated_at"),
            "log_likelihood": snap.get("log_likelihood"),
            "n_rooms_estimated": len(rooms_snap),
            "n_sources_estimated": len(sources_snap),
            "estimation_history": history,
        }


# ---------------------------------------------------------------------------
# MPC performance sensor (system-wide)
# ---------------------------------------------------------------------------
class MPCPerformanceSensor(CoordinatorEntity, SensorEntity):
    """
    System-wide sensor reporting MPC solver performance statistics.

    The state is the most recent OCP wall-clock solve duration [s].  Detailed
    statistics (solve times, tracking errors) are exposed as state attributes.

    Some callers may provide legacy ``datetime.timedelta`` solve-time values.
    Those are normalized to raw seconds so Home Assistant always receives a
    plain numeric state.
    """

    _attr_state_class = None
    _attr_native_unit_of_measurement = "s"
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – MPC Performance"
        self._attr_unique_id = f"{DOMAIN}_mpc_performance"

    @staticmethod
    def _solve_time_seconds(value: timedelta | float | int | None) -> Optional[float]:
        """Convert a solve-time value in seconds to ``float`` or return ``None``."""
        if value is None:
            return None
        if isinstance(value, timedelta):
            return float(value.total_seconds())
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring non-numeric MPC solve time value: %r", value)
            return None

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent OCP solve duration in seconds."""
        return self._solve_time_seconds(self._coordinator.controller.last_solve_time)

    @property
    def available(self) -> bool:
        """Keep the latest cached solver stats visible across update failures."""
        return getattr(self._coordinator, "controller", None) is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose rolling solve-time statistics and recent history."""
        import numpy as np

        controller = self._coordinator.controller
        solve_times = []
        for sample in controller.solve_times:
            seconds = self._solve_time_seconds(sample)
            if seconds is not None:
                solve_times.append(seconds)

        last_t = self._solve_time_seconds(controller.last_solve_time)
        mean_t = self._solve_time_seconds(controller.mean_solve_time)
        max_t = self._solve_time_seconds(controller.max_solve_time)
        n = controller.n_solves

        attrs: Dict[str, Any] = {
            "total_computes": controller.total_computes,
            "last_solve_time_s": round(last_t, 4) if last_t is not None else None,
            "mean_solve_time_s": round(mean_t, 4) if mean_t is not None else None,
            "max_solve_time_s": round(max_t, 4) if max_t is not None else None,
            "n_solves": n,
            "horizon": self._coordinator.controller.horizon,
            "dt_s": self._coordinator.dt,
        }

        # Explicit MPC schedule timestamps so the dashboard countdown is anchored
        # to the actual internal solve cadence rather than the entity's HA
        # last_updated (which the fast UI refresh also bumps between solves).
        last_run_ts = getattr(self._coordinator, "_last_mpc_run_ts", None)
        attrs["last_run_ts"] = last_run_ts
        attrs["next_run_ts"] = (
            last_run_ts + self._coordinator.dt if last_run_ts else None
        )

        # Tracking error per room (|T − setpoint|; mean excludes inactive rooms)
        room_names = self._coordinator.model.room_names
        room_snapshots = _kpi_room_snapshots(self._coordinator)
        tracking_errors: Dict[str, float] = {}
        for name, snap in zip(room_names, room_snapshots):
            temp = room_temperature(snap)
            setpoint = snap.setpoint
            if temp is not None and setpoint is not None:
                tracking_errors[name] = round(abs(temp - setpoint), 3)
        attrs["current_tracking_errors"] = tracking_errors
        mean_err = mean_tracking_error_c(room_snapshots)
        attrs["mean_tracking_error"] = (
            round(mean_err, 2) if mean_err is not None else None
        )
        active_errors = [
            tracking_errors[name]
            for name, snap in zip(room_names, room_snapshots)
            if snap.room_active and name in tracking_errors
        ]
        attrs["max_tracking_error"] = (
            round(max(active_errors), 2) if active_errors else None
        )

        # Terminal-weight in effect (for reference)
        attrs["terminal_weight"] = (
            controller.terminal_weight
            if hasattr(controller, "terminal_weight")
            else None
        )

        # Rolling solve time history (last 50 samples) for sparkline charts
        attrs["recent_solve_times_s"] = [round(t, 4) for t in solve_times[-50:]]

        return attrs


# ---------------------------------------------------------------------------
# Weather forecast status sensor (system-wide, diagnostic)
# ---------------------------------------------------------------------------

class WeatherForecastStatusSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting the health of the weather-forecast fetch.

    The state is one of:

    * ``"ok"``        — the most recent fetch succeeded
    * ``"failing"``   — at least one consecutive fetch has failed
    * ``"disabled"``  — no weather entity is configured

    Attributes expose the last error message, the timestamps of the most
    recent success and failure, and the consecutive-failure counter so users
    can diagnose persistent breakages without having to grep the HA log.
    """

    _attr_icon = "mdi:weather-cloudy-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Weather Forecast Status"
        self._attr_unique_id = f"{DOMAIN}_weather_forecast_status"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not getattr(self._coordinator, "_weather_entity", None):
            return "disabled"
        if getattr(self._coordinator, "weather_consecutive_failures", 0) > 0:
            return "failing"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        last_err_at = getattr(self._coordinator, "weather_last_error_at", None)
        last_ok_at = getattr(self._coordinator, "weather_last_success_at", None)
        return {
            "weather_entity": getattr(self._coordinator, "_weather_entity", None),
            "last_error": getattr(self._coordinator, "weather_last_error", None),
            "last_error_at": last_err_at.isoformat() if last_err_at else None,
            "last_success_at": last_ok_at.isoformat() if last_ok_at else None,
            "consecutive_failures": getattr(
                self._coordinator, "weather_consecutive_failures", 0
            ),
        }


class SolarRadiationStatusSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting the health of the solar-radiation forecast.

    The state is one of:

    * ``"ok"``        — the most recent read succeeded and drives the gains
    * ``"failing"``   — at least one consecutive read has failed
    * ``"disabled"``  — no solar-radiation entity is configured

    Attributes expose which solar source is active (the irradiance forecast vs
    the analytical clear-sky model), the current GHI and its horizon series
    [W/m²] (kept here, off the history buffer), and the usual error / timestamp
    diagnostics.
    """

    _attr_icon = "mdi:weather-sunny"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Solar Radiation Forecast Status"
        self._attr_unique_id = f"{DOMAIN}_solar_radiation_status"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not getattr(self._coordinator, "_solar_radiation_entity", None):
            return "disabled"
        if getattr(self._coordinator, "solar_fc_consecutive_failures", 0) > 0:
            return "failing"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        last_err_at = getattr(self._coordinator, "solar_fc_last_error_at", None)
        last_ok_at = getattr(self._coordinator, "solar_fc_last_success_at", None)
        return {
            "solar_radiation_entity": getattr(
                self._coordinator, "_solar_radiation_entity", None
            ),
            "active_source": getattr(self._coordinator, "solar_source", "analytical"),
            "ghi_now": getattr(self._coordinator, "ghi_now", None),
            # Effective GHI used by the overview's solar-irradiance KPI: the
            # measured/forecast value when present, else the modeled clear-sky
            # value. Always populated when a site location is known.
            "ghi_now_effective": getattr(
                self._coordinator, "ghi_now_effective", None
            ),
            "ghi_forecast": list(
                getattr(self._coordinator, "ghi_forecast", []) or []
            ),
            "last_error": getattr(self._coordinator, "solar_fc_last_error", None),
            "last_error_at": last_err_at.isoformat() if last_err_at else None,
            "last_success_at": last_ok_at.isoformat() if last_ok_at else None,
            "consecutive_failures": getattr(
                self._coordinator, "solar_fc_consecutive_failures", 0
            ),
        }


# ---------------------------------------------------------------------------
# System identification simulation sensor (per room)
# ---------------------------------------------------------------------------


class SysIdSimulationSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor exposing the most recent system-identification EKF reconstruction.

    State value: one-step-ahead RMSE [°C] (``None`` until the first run).

    Attributes:
        ``simulation``   – list of {time (ISO-8601), measured, predicted,
                                     cov_upper, cov_lower} for Apex Charts
        ``thermal_mass`` – J/K used for this run
        ``r_external``   – K/W used for this run
        ``sigma_w``      – process noise std used for this run
        ``sigma_v``       – measurement noise std used for this run
        ``rmse``         – same as state value [°C]
        ``mae``          – mean absolute error [°C]
        ``horizon_hours``– reconstructed horizon in hours
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-scatter-plot"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – SysID Simulation"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_sysid_simulation"

    @property
    def native_value(self) -> Optional[float]:
        room_data = getattr(self._coordinator, "sysid_results", {}).get(
            self._room_name, {}
        )
        return room_data.get("rmse")

    @property
    def extra_state_attributes(self) -> dict:
        room_data = getattr(self._coordinator, "sysid_results", {}).get(
            self._room_name, {}
        )
        sim_raw = room_data.get("simulation", [])
        formatted: list = []
        for entry in sim_raw:
            ts = entry.get("time", 0.0)
            dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            sim_entry: dict = {
                "time": dt_iso,
                "measured": entry.get("measured"),
                "predicted": entry.get("predicted"),
                "cov_upper": entry.get("cov_upper"),
                "cov_lower": entry.get("cov_lower"),
            }
            if entry.get("predicted_wall") is not None:
                sim_entry["predicted_wall"] = entry["predicted_wall"]
                sim_entry["wall_cov_upper"] = entry.get("wall_cov_upper")
                sim_entry["wall_cov_lower"] = entry.get("wall_cov_lower")
            formatted.append(sim_entry)
        dt_val = self._coordinator.dt
        horizon_steps = room_data.get("horizon_steps", 0)
        horizon_hours = round(horizon_steps * dt_val / 3600.0, 2) if horizon_steps else None
        return {
            "simulation": formatted,
            "thermal_mass": room_data.get("thermal_mass"),
            "r_external": room_data.get("r_external"),
            # Full identified parameter set surfaced after an ML dry-run so the
            # room-level identification page can review/apply every parameter.
            "internal_gain": room_data.get("internal_gain"),
            "solar_scale": room_data.get("solar_scale"),
            "c_air_fraction": room_data.get("c_air_fraction"),
            "r_aw_fraction": room_data.get("r_aw_fraction"),
            "t_wall_initial": room_data.get("t_wall_initial"),
            "estimated_inter_room_r": room_data.get("estimated_inter_room_r"),
            "heater_scales": room_data.get("heater_scales"),
            "sigma_w": room_data.get("sigma_w"),
            "sigma_v": room_data.get("sigma_v"),
            "rmse": room_data.get("rmse"),
            "mae": room_data.get("mae"),
            "horizon_hours": horizon_hours,
            "window_start": room_data.get("window_start"),
            "window_end": room_data.get("window_end"),
        }
