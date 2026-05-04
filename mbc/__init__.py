"""
mbc – model-based control library.

Provides generic, reusable algorithms for linear and nonlinear
model-based control, estimation, identification, and simulation.

  mbc.models
      Abstract model interfaces:

      * ``LinearDiscreteModel``              – linear discrete-time system (ZOH).
      * ``LinearContinuousDiscreteModel``    – linear continuous-discrete system
                                               (M.Sc. Ch. 5).
      * ``ContinuousDiscreteModel``          – nonlinear SDE (Ph.D. Ch. 5).
      * ``ContinuousDiscreteDAEModel``       – nonlinear SDAE (Ph.D. Ch. 6).

  mbc.estimation
      State-estimation algorithms:

      * ``KalmanFilter``                    – discrete-time KF (Joseph form).
        Supports noise-separated covariance (M.Sc. Ch. 5.4) and missing
        observations (M.Sc. Ch. 5.5).
      * ``CDKalmanFilter``                  – KF for linear continuous-discrete
                                              systems; ZOH + Van Loan Q_d (M.Sc. Ch. 5).
      * ``ContinuousDiscreteEKF``           – CD-EKF (Ph.D. Ch. 7.1).
      * ``ContinuousDiscreteUKF``           – CD-UKF (Ph.D. Ch. 7.2).
      * ``ContinuousDiscreteEnKF``          – CD-EnKF (Ph.D. Ch. 7.3).
      * ``ContinuousDiscreteParticleFilter``– CD-PF  (Ph.D. Ch. 7.4).
      * ``ContinuousDiscreteDAEEKF``        – CD-EKF for SDAE (Ph.D. Ch. 8).

  mbc.control
      Optimal control algorithms:

      * ``OptimalControlProblem``         – receding-horizon QP (tracking MPC, discrete).
      * ``MPCController``                 – KalmanFilter + OptimalControlProblem.
      * ``CDOptimalControlProblem``       – receding-horizon QP for linear CD systems.
      * ``CDMPCController``               – CDKalmanFilter + CDOptimalControlProblem.
      * ``CDTrackingOptimalControlProblem`` – nonlinear tracking OCP for CD systems
                                             (NLP; input/state/output constraints,
                                             ROM penalty + constraints, linear input
                                             penalty).
      * ``EconomicOptimalControlProblem`` – economic nonlinear OCP (Ph.D. Ch. 9).
                                           Accepts Mayer + Lagrange functions and
                                           the same constraint set as the tracking OCP.
      * ``CDNMPCController``              – generic estimator + OCP controller
                                           (works with any CD estimator and any OCP).

  mbc.identification
      System-identification / parameter-estimation utilities:

      * ``ped_neg_log_likelihood``          – PED Kalman log-likelihood.
      * ``ped_neg_log_likelihood_gradient`` – finite-difference gradient.
      * ``ParameterEstimator``              – multi-start optimiser.
      * ``EstimationResult``                – result dataclass.

  mbc.realization
      State-space realization from I/O data (M.Sc. Ch. 2–4):

      * ``SISORealization`` – from transfer function or impulse response.
      * ``MIMORealization`` – Ho–Kalman from Markov parameters.

  mbc.simulation
      Numerical integration of SDE/SDAE models (Ph.D. Ch. 5–6):

      * ``SDESimulator``  – Euler-Maruyama for SDE systems.
      * ``SDAESimulator`` – Euler-Maruyama for SDAE systems.

  mbc.monte_carlo
      Closed-loop Monte Carlo simulation framework (Ph.D. Ch. 12):

      * ``MonteCarloSimulation`` – N_mc independent closed-loop trials.
      * ``MonteCarloResult``     – results container.
"""

from .models import (
    LinearDiscreteModel,
    LinearContinuousDiscreteModel,
    ContinuousDiscreteModel,
    ContinuousDiscreteDAEModel,
)
from .estimation import (
    KalmanFilter,
    CDKalmanFilter,
    ContinuousDiscreteEKF,
    ContinuousDiscreteUKF,
    ContinuousDiscreteEnKF,
    ContinuousDiscreteParticleFilter,
    ContinuousDiscreteDAEEKF,
)
from .control import (
    OptimalControlProblem,
    MPCController,
    CDOptimalControlProblem,
    CDTrackingOptimalControlProblem,
    CDMPCController,
    EconomicOptimalControlProblem,
    CDNMPCController,
)
from .identification.estimator import ParameterEstimator, EstimationResult
from .identification.likelihood import (
    ped_neg_log_likelihood,
    ped_neg_log_likelihood_gradient,
)
from .realization import SISORealization, MIMORealization
from .simulation import SDESimulator, SDAESimulator
from .monte_carlo import MonteCarloSimulation, MonteCarloResult

__all__ = [
    # Models
    "LinearDiscreteModel",
    "LinearContinuousDiscreteModel",
    "ContinuousDiscreteModel",
    "ContinuousDiscreteDAEModel",
    # Estimation
    "KalmanFilter",
    "CDKalmanFilter",
    "ContinuousDiscreteEKF",
    "ContinuousDiscreteUKF",
    "ContinuousDiscreteEnKF",
    "ContinuousDiscreteParticleFilter",
    "ContinuousDiscreteDAEEKF",
    # Control
    "OptimalControlProblem",
    "MPCController",
    "CDOptimalControlProblem",
    "CDTrackingOptimalControlProblem",
    "CDMPCController",
    "EconomicOptimalControlProblem",
    "CDNMPCController",
    # Identification
    "ParameterEstimator",
    "EstimationResult",
    "ped_neg_log_likelihood",
    "ped_neg_log_likelihood_gradient",
    # Realization
    "SISORealization",
    "MIMORealization",
    # Simulation
    "SDESimulator",
    "SDAESimulator",
    # Monte Carlo
    "MonteCarloSimulation",
    "MonteCarloResult",
]
