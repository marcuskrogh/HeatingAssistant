"""Lock SWD-444 extracts: NLP helpers, PE sensors, open-loop diagnostics."""

from heatingassistant.app import sysid_sensors, sysid_services
from heatingassistant.engine import model_diagnostics, open_loop_predictions
from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator
from heatingassistant.engine.estimation.nlp_eval import (
    RegularizedMseCache,
    WallInitMseCache,
    solve_lbfgs,
)


def test_nlp_eval_helpers_are_importable() -> None:
    assert RegularizedMseCache is not None
    assert WallInitMseCache is not None
    assert callable(solve_lbfgs)
    assert callable(KalmanMLEstimator.estimate)
    assert callable(KalmanMLEstimator.estimate_wall_initial_only)


def test_sysid_sensor_functions_reexported_from_services() -> None:
    from heatingassistant.app import sysid_common

    assert sysid_services.sysid_sensor_attrs is sysid_sensors.sysid_sensor_attrs
    assert sysid_services.open_loop_sensor_attrs is sysid_sensors.open_loop_sensor_attrs
    assert sysid_services.closed_loop_fit_for_room is sysid_sensors.closed_loop_fit_for_room
    assert sysid_services.model_fit_quality_sensor is sysid_sensors.model_fit_quality_sensor
    assert sysid_services.parameter_confidence_sensor is sysid_sensors.parameter_confidence_sensor
    assert sysid_services._iso_time is sysid_common._iso_time
    assert sysid_services._dt is sysid_common._dt


def test_open_loop_predictions_reexported_from_diagnostics() -> None:
    assert (
        model_diagnostics.compute_open_loop_predictions
        is open_loop_predictions.compute_open_loop_predictions
    )
