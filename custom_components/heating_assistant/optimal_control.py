"""
Optimal control problem re-exports.

CDTrackingOptimalControlProblem is the nonlinear NLP-based OCP used by
HeatingMPCController.  The linear OptimalControlProblem is also
re-exported for reference.
"""

from mbc.control import CDTrackingOptimalControlProblem, OptimalControlProblem

__all__ = ["CDTrackingOptimalControlProblem", "OptimalControlProblem"]
