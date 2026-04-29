"""Optimal control sub-package for mbc."""

from .ocp import OptimalControlProblem
from .mpc import MPCController

__all__ = [
    "OptimalControlProblem",
    "MPCController",
]
