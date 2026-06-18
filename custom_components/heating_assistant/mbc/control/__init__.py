"""Optimal control sub-package for mbc."""

from .ocp import (
    DiscreteOptimalControlProblem,
    StandardLinearDiscreteOCP,
    StandardDiscreteOCP,  # deprecated alias
    OptimalControlProblem,  # deprecated alias
)
from .mpc import MPCController
from .cd_ocp import (
    StandardLinearContinuousDiscreteOCP,
    StandardContinuousOCP,
    CDOptimalControlProblem,  # deprecated alias
    LinearContinuousOCP,  # deprecated alias
    CDTrackingOptimalControlProblem,  # deprecated alias
)
from .cd_mpc import CDMPCController, LinearContinuousMPCController  # deprecated
from .cd_linearized_ocp import (
    StandardLinearizedContinuousDiscreteOCP,
    CDLinearizedOptimalControlProblem,  # deprecated alias
    LinearizedContinuousOCP,  # deprecated alias
)
from .cd_linearized_mpc import (
    CDLinearizedMPCController,
    LinearizedContinuousMPCController,  # deprecated alias
    linearize_cd_model,
    discretize_cd_linearization,
)
from .enmpc import (
    ContinuousOptimalControlProblem,
    GeneralContinuousOCP,
    CDNMPCController,
    EconomicOptimalControlProblem,  # deprecated alias
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
    # General continuous OCP
    "GeneralContinuousOCP",
    # MPC controllers (estimator pairing reflects model type)
    "MPCController",
    "CDMPCController",
    "CDLinearizedMPCController",
    "CDNMPCController",
    # Linearisation helpers
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
    "StandardDiscreteOCP",
    "OptimalControlProblem",
    "CDOptimalControlProblem",
    "LinearContinuousOCP",
    "CDTrackingOptimalControlProblem",
    "CDLinearizedOptimalControlProblem",
    "LinearizedContinuousOCP",
    "LinearContinuousMPCController",
    "LinearizedContinuousMPCController",
    "EconomicOptimalControlProblem",
    "ContinuousNMPCController",
]
