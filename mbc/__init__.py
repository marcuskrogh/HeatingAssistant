"""
mbc – model-based control library.

Provides generic, reusable algorithms that operate on any model implementing
the ``LinearDiscreteModel`` interface:

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

from .identification.estimator import ParameterEstimator, EstimationResult
from .identification.likelihood import (
    ped_neg_log_likelihood,
    ped_neg_log_likelihood_gradient,
)

__all__ = [
    "ParameterEstimator",
    "EstimationResult",
    "ped_neg_log_likelihood",
    "ped_neg_log_likelihood_gradient",
]
