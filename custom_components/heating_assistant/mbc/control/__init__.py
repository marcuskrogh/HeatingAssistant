"""Optimal control sub-package for mbc."""

from .ocp import (
    DiscreteOptimalControlProblem,
    StandardDiscreteOCP,
    OptimalControlProblem,  # deprecated alias
)
from .mpc import MPCController
from .cd_ocp import (
    LinearContinuousOCP,
    StandardContinuousOCP,
    CDOptimalControlProblem,  # deprecated alias
    CDTrackingOptimalControlProblem,  # deprecated alias
)
from .cd_mpc import LinearContinuousMPCController, CDMPCController  # deprecated
from .cd_linearized_ocp import (
    LinearizedContinuousOCP,
    CDLinearizedOptimalControlProblem,  # deprecated alias
)
from .cd_linearized_mpc import (
    LinearizedContinuousMPCController,
    CDLinearizedMPCController,  # deprecated alias
    linearize_cd_model,
    discretize_cd_linearization,
)
from .enmpc import (
    ContinuousOptimalControlProblem,
    ContinuousNMPCController,
    EconomicOptimalControlProblem,  # deprecated alias
    CDNMPCController,  # deprecated alias
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
    # Discrete OCP
    "DiscreteOptimalControlProblem",
    "StandardDiscreteOCP",
    # Continuous OCP
    "ContinuousOptimalControlProblem",
    "StandardContinuousOCP",
    "LinearContinuousOCP",
    "LinearizedContinuousOCP",
    # MPC controllers
    "MPCController",
    "LinearContinuousMPCController",
    "LinearizedContinuousMPCController",
    "ContinuousNMPCController",
    # Linearisation helpers
    "linearize_cd_model",
    "discretize_cd_linearization",
    # NLP / QP solvers
    "EconomicOptimalControlProblem",
    "CDNMPCController",
    "CDOptimalControlProblem",
    "CDTrackingOptimalControlProblem",
    "CDLinearizedOptimalControlProblem",
    "CDMPCController",
    "CDLinearizedMPCController",
    "OptimalControlProblem",
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
]
