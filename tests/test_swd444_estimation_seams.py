"""SWD-444: lock estimation, diagnostics, and PE HTTP seams before splits."""

from __future__ import annotations

from heatingassistant.app import sysid_services
from heatingassistant.engine import model_diagnostics, parameter_lifecycle
from heatingassistant.engine.estimation import KalmanMLEstimator as PkgEstimator
from heatingassistant.engine.parameter_estimator import (
    KalmanMLEstimator,
    MIN_HISTORY_STEPS,
)


def test_kalman_ml_estimator_public_methods_exist() -> None:
    for name in (
        "compute_log_likelihood",
        "compute_loglik_slice",
        "estimate",
        "estimate_wall_initial_only",
    ):
        assert callable(getattr(KalmanMLEstimator, name)), name


def test_kalman_ml_estimator_reexported_from_compat_module() -> None:
    assert PkgEstimator is KalmanMLEstimator
    assert MIN_HISTORY_STEPS > 0


def test_sysid_services_public_handlers_exist() -> None:
    for name in (
        "handle_estimate_parameters_ml",
        "start_estimate_parameters_ml",
        "pe_job_snapshot",
        "handle_get_pe_coverage",
        "handle_get_pe_inputs",
        "handle_run_sysid_simulation",
        "handle_run_open_loop_simulation",
        "handle_store_identified_parameters",
        "handle_update_estimation_params",
        "handle_delete_parameter_history",
        "handle_create_dataset",
        "handle_delete_dataset",
        "annotate_datasets_with_coverage",
        "sysid_sensor_attrs",
        "open_loop_sensor_attrs",
        "model_fit_quality_sensor",
        "parameter_confidence_sensor",
        "closed_loop_fit_for_room",
        "resolve_history",
    ):
        assert callable(getattr(sysid_services, name)), name
        assert name in sysid_services.__all__


def test_runtime_maps_pe_services_to_sysid_handlers() -> None:
    from heatingassistant.app.runtime import HeatingRuntime

    source = HeatingRuntime.apply_service.__code__.co_names
    for handler in (
        "start_estimate_parameters_ml",
        "handle_create_dataset",
        "handle_store_identified_parameters",
        "handle_run_open_loop_simulation",
    ):
        assert handler in source, handler


def test_model_diagnostics_public_api_exists() -> None:
    for name in (
        "ModelFitMetrics",
        "IdentificationWarning",
        "ParameterValidation",
        "compute_model_fit_metrics",
        "analyze_residuals",
        "validate_parameters",
        "build_identification_warnings",
        "compute_open_loop_predictions",
        "is_default_thermal_configuration",
    ):
        assert hasattr(model_diagnostics, name), name


def test_parameter_lifecycle_public_api_exists() -> None:
    for name in (
        "store_identified_parameters",
        "apply_estimated_parameters",
        "restore_estimated_parameters",
        "apply_manual_parameters",
        "revert_parameters",
        "delete_parameter_history",
        "estimated_params_snapshot",
    ):
        assert callable(getattr(parameter_lifecycle, name)), name
