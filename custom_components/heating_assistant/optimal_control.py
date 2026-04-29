"""
Optimal Control Problem — backward-compatibility shim.

The OptimalControlProblem implementation has moved to ``mbc.control``.
This module re-exports it for backward compatibility.
"""

from mbc.control import OptimalControlProblem

__all__ = ["OptimalControlProblem"]
