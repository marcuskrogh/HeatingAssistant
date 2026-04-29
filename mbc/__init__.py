"""
mbc – model-based control library.

Provides generic, reusable algorithms that operate on any model implementing
the ``LinearDiscreteModel`` interface:

  mbc.models
      Abstract model interface:

      * ``LinearDiscreteModel`` – abstract base class for linear discrete-time
        systems.

  mbc.estimation
      State-estimation algorithms:

      * ``KalmanFilter`` – discrete-time Kalman filter with Joseph stabilised
        covariance update.

  mbc.control
      Optimal control algorithms:

      * ``OptimalControlProblem`` – receding-horizon QP with setpoint tracking,
        Δu penalty, hard input box-constraints, and soft output constraints.
      * ``MPCController`` – generic MPC controller composing a KalmanFilter and
        OptimalControlProblem.

  mbc.identification
      System-identification / parameter-estimation utilities:

      * ``ped_neg_log_likelihood``  – prediction-error decomposition (PED)
        Kalman-filter log-likelihood.
      * ``ped_neg_log_likelihood_gradient`` – finite-difference gradient of
        the above.
      * ``ParameterEstimator``  – multi-start optimiser wrapping the
        likelihood with optional regularisation and gradient-based search.
      * ``EstimationResult``    – lightweight result dataclass.
"""

from .models import LinearDiscreteModel
from .estimation import KalmanFilter
from .control import OptimalControlProblem, MPCController
from .identification.estimator import ParameterEstimator, EstimationResult
from .identification.likelihood import (
    ped_neg_log_likelihood,
    ped_neg_log_likelihood_gradient,
)

__all__ = [
    "LinearDiscreteModel",
    "KalmanFilter",
    "OptimalControlProblem",
    "MPCController",
    "ParameterEstimator",
    "EstimationResult",
    "ped_neg_log_likelihood",
    "ped_neg_log_likelihood_gradient",
]
