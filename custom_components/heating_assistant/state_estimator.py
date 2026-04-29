"""
Discrete-time Kalman Filter — backward-compatibility shim.

The KalmanFilter implementation has moved to ``mbc.estimation``.
This module re-exports it for backward compatibility.
"""

from mbc.estimation import KalmanFilter

__all__ = ["KalmanFilter"]
