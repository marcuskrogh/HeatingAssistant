"""MPC controller package — SDE model, EKF, and MPC facade."""

from .ekf import _InnovationEKF
from .facade import HeatingMPCController
from .factory import ControllerBuildConfig, build_mpc_controller
from .linearised import HeatingLinearisedMPC
from .sde import HouseThermalSDE
from ..const import MPC_STATS_BUFFER_SIZE

__all__ = [
    "ControllerBuildConfig",
    "HeatingLinearisedMPC",
    "HeatingMPCController",
    "HouseThermalSDE",
    "MPC_STATS_BUFFER_SIZE",
    "_InnovationEKF",
    "build_mpc_controller",
]
