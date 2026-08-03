"""MPC controller package — SDE model, EKF, and MPC facade."""

from .facade import (
    HeatingLinearisedMPC,
    HeatingMPCController,
    HeatingNonlinearMPC,
    HouseThermalSDE,
    _InnovationEKF,
)
from .factory import ControllerBuildConfig, build_mpc_controller
from ..const import MPC_STATS_BUFFER_SIZE

__all__ = [
    "ControllerBuildConfig",
    "HeatingLinearisedMPC",
    "HeatingMPCController",
    "HeatingNonlinearMPC",
    "HouseThermalSDE",
    "MPC_STATS_BUFFER_SIZE",
    "_InnovationEKF",
    "build_mpc_controller",
]
