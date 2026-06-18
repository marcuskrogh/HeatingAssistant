"""Optimal control sub-package for mbc."""

from .ocp import (
    DiscreteOptimalControlProblem,
    StandardLinearDiscreteOCP,
    StandardDiscreteOCP,  # deprecated alias
    OptimalControlProblem,  # deprecated alias
)
from .mpc_forecast import (
    HorizonProfileMPC,
    MPCHorizonProfile,
    MPCLinearisationPoint,
    ForecastAwareMPC,  # deprecated alias
    MPCForecast,  # deprecated alias
    MPCOperatingPoint,  # deprecated alias
)
from .mpc import (
    LinearDiscreteMPC,
    StandardLinearDiscreteMPC,
    MPCController,  # deprecated
)
from .linearised_discrete_mpc import (
    LinearisedDiscreteMPC,
    StandardLinearisedDiscreteMPC,
    linearize_discrete_model,
)
from .cd_ocp import (
    StandardLinearContinuousDiscreteOCP,
    StandardContinuousOCP,
    CDOptimalControlProblem,  # deprecated alias
    LinearContinuousOCP,  # deprecated alias
    CDTrackingOptimalControlProblem,  # deprecated alias
)
from .cd_mpc import (
    LinearContinuousMPC,
    StandardLinearContinuousMPC,
    CDMPCController,  # deprecated alias
    LinearContinuousMPCController,  # deprecated alias
)
from .cd_linearized_ocp import (
    StandardLinearizedContinuousDiscreteOCP,
    CDLinearizedOptimalControlProblem,  # deprecated alias
    LinearizedContinuousOCP,  # deprecated alias
)
from .cd_linearized_mpc import (
    LinearisedContinuousMPC,
    StandardLinearisedContinuousMPC,
    CDLinearizedMPCController,  # deprecated alias
    LinearizedContinuousMPCController,  # deprecated alias
    linearize_cd_model,
    discretize_cd_linearization,
)
from .enmpc import (
    ContinuousOptimalControlProblem,
    GeneralContinuousOCP,
    NonlinearContinuousMPC,
    StandardNonlinearContinuousMPC,
    EconomicOptimalControlProblem,  # deprecated alias
    CDNMPCController,  # deprecated alias
    ContinuousNMPCController,  # deprecated alias
)
from .nlp_solver import (
    NLPConstraint,
    NLPProblem,
    NLPScalingPolicy,
    NLPSolverBackend,
    ScipyNLPBackend,
    IpoptNLPBackend,
)
from .qp_solver import (
    QPProblem,
    QPResult,
    QPSolverBackend,
    HighsQPBackend,
    OSQPBackend,
    make_qp_backend,
)

__all__ = [
    # OCP bases
    "DiscreteOptimalControlProblem",
    "ContinuousOptimalControlProblem",
    # Standard OCP implementations
    "StandardLinearDiscreteOCP",
    "StandardLinearContinuousDiscreteOCP",
    "StandardLinearizedContinuousDiscreteOCP",
    "StandardContinuousOCP",
    "GeneralContinuousOCP",
    # MPC abstractions
    "LinearDiscreteMPC",
    "LinearisedDiscreteMPC",
    "LinearContinuousMPC",
    "LinearisedContinuousMPC",
    "NonlinearContinuousMPC",
    # Standard MPC implementations
    "StandardLinearDiscreteMPC",
    "StandardLinearisedDiscreteMPC",
    "StandardLinearContinuousMPC",
    "StandardLinearisedContinuousMPC",
    "StandardNonlinearContinuousMPC",
    # MPC horizon-profile helpers
    "HorizonProfileMPC",
    "MPCHorizonProfile",
    "MPCLinearisationPoint",
    "linearize_discrete_model",
    "linearize_cd_model",
    "discretize_cd_linearization",
    # NLP / QP solvers
    "NLPConstraint",
    "NLPProblem",
    "NLPScalingPolicy",
    "NLPSolverBackend",
    "ScipyNLPBackend",
    "IpoptNLPBackend",
    "QPProblem",
    "QPResult",
    "QPSolverBackend",
    "HighsQPBackend",
    "OSQPBackend",
    "make_qp_backend",
    # Deprecated aliases
    "MPCController",
    "StandardDiscreteOCP",
    "OptimalControlProblem",
    "CDOptimalControlProblem",
    "LinearContinuousOCP",
    "CDTrackingOptimalControlProblem",
    "CDLinearizedOptimalControlProblem",
    "LinearizedContinuousOCP",
    "CDMPCController",
    "LinearContinuousMPCController",
    "CDLinearizedMPCController",
    "LinearizedContinuousMPCController",
    "EconomicOptimalControlProblem",
    "CDNMPCController",
    "ContinuousNMPCController",
    # Deprecated horizon-profile aliases
    "ForecastAwareMPC",
    "MPCForecast",
    "MPCOperatingPoint",
]
