"""
Linear discrete-time model interface.

``LinearDiscreteModel`` — abstract base for linear discrete-time systems:

    x[k+1] = Ad x[k] + Bd u[k] + Ed d[k] + Gd w[k],   w[k] ~ N(0, Qd)
    z[k]   = Cz x[k] + Dz u[k] + Fz d[k]
    ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],       v[k] ~ N(0, Rm)

    x ∈ ℝⁿˣ state, u ∈ ℝⁿᵘ input, d ∈ ℝⁿᵈ disturbance,
    z ∈ ℝⁿᶻ output, ym ∈ ℝⁿʸᵐ measurement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple

import numpy as np


class LinearDiscreteModel(ABC):
    """
    Abstract interface for a linear discrete-time stochastic system
    (ControlToolbox notation, discrete-time specialisation):

        x[k+1] = Ad x[k] + Bd u[k] + Ed d[k] + Gd w[k],   w[k] ~ N(0, Qd)
        z[k]   = Cz x[k] + Dz u[k] + Fz d[k]                      (output ``g^m``)
        ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],       v[k] ~ N(0, Rm)
                                                                  (measurement ``h^m``)

    The system matrices ``Ad, Bd, Ed, Gd, Cm, Cz, Dm, Dz, Fm, Fz`` are
    constant (LTI).  This interface is the discrete-time analogue of
    :class:`LinearContinuousDiscreteModel` and uses the same naming
    conventions (``Cz``/``Cm``/``Qd``/``Rm``/``Gd``) that the
    continuous-discrete state-estimation documents
    (ControlToolbox §SDE / §SDAE) prescribe for the linearised filter.

    Dimensions
    ----------
        nx   – state dimension              x ∈ ℝⁿˣ
        nu   – input dimension              u ∈ ℝⁿᵘ
        nd   – disturbance dimension        d ∈ ℝⁿᵈ
        nw   – process-noise dimension      w ∈ ℝⁿʷ   (derived: Gd.shape[1])
        nym  – measurement output dimension ym ∈ ℝⁿʸᵐ (derived: Cm.shape[0])
        nz   – output dimension             z ∈ ℝⁿᶻ   (derived: Cz.shape[0])

    Parameter-identification interface
    -----------------------------------
    Subclasses that support system identification may additionally implement
    ``params`` and ``with_params``.
    """

    # ── Abstract dimensions ────────────────────────────────────────────────

    @property
    @abstractmethod
    def nx(self) -> int:
        """State dimension nx."""

    @property
    @abstractmethod
    def nu(self) -> int:
        """Input dimension nu."""

    @property
    @abstractmethod
    def nd(self) -> int:
        """Disturbance dimension nd."""

    # ── Abstract discrete-time matrices (numpy) ───────────────────────────

    @property
    @abstractmethod
    def Ad(self) -> np.ndarray:
        """Discrete state-transition matrix Ad ∈ ℝⁿˣˣⁿˣ (numpy ndarray)."""

    @property
    @abstractmethod
    def Bd(self) -> np.ndarray:
        """Discrete input matrix Bd ∈ ℝⁿˣˣⁿᵘ (numpy ndarray)."""

    @property
    @abstractmethod
    def Ed(self) -> np.ndarray:
        """Discrete disturbance matrix Ed ∈ ℝⁿˣˣⁿᵈ (numpy ndarray)."""

    @property
    @abstractmethod
    def Cm(self) -> np.ndarray:
        """Measurement output matrix Cm ∈ ℝⁿʸᵐˣⁿˣ (numpy ndarray)."""

    @property
    @abstractmethod
    def Qd(self) -> np.ndarray:
        """Discrete process-noise covariance Qd ∈ ℝⁿˣˣⁿˣ (numpy ndarray)."""

    @property
    @abstractmethod
    def Rm(self) -> np.ndarray:
        """Measurement noise covariance Rm ∈ ℝⁿʸᵐˣⁿʸᵐ (numpy ndarray)."""

    # ── Abstract control-interface properties ────────────────────────────

    @property
    @abstractmethod
    def x(self) -> list[float]:
        """Current state x as a plain list of floats."""

    @x.setter
    @abstractmethod
    def x(self, val: list[float]) -> None:
        ...

    @property
    @abstractmethod
    def x_ref(self) -> np.ndarray:
        """Reference / setpoint x_ref ∈ ℝⁿˣ (numpy 1-D array, length nx)."""

    @property
    @abstractmethod
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """Box constraint on inputs (u_min, u_max), each a (nu,) ndarray."""

    # ── Derived (non-abstract, overridable) ──────────────────────────────

    @property
    def nym(self) -> int:
        """Measurement output dimension nym = Cm.shape[0]."""
        return self.Cm.shape[0]

    @property
    def Gd(self) -> np.ndarray:
        """
        Discrete noise input matrix Gd ∈ ℝⁿˣˣⁿˣ.

        Default: identity (direct additive noise on all states).
        Subclasses may override to use a different noise structure.
        """
        return np.eye(self.nx)

    @property
    def Cz(self) -> np.ndarray:
        """
        Output matrix Cz ∈ ℝⁿᶻˣⁿˣ — discrete analogue of the continuous
        output function ``g^m`` (ControlToolbox §EMPC).

        Default: Cm (same output set as measurements).
        Subclasses may override for a different output selection.
        """
        return self.Cm

    @property
    def Dz(self) -> np.ndarray:
        """
        Output feedthrough Dz ∈ ℝⁿᶻˣⁿᵘ.

        Default: zeros (no direct feedthrough from inputs to outputs).
        """
        return np.zeros((self.nz, self.nu))

    @property
    def Fz(self) -> np.ndarray:
        """
        Output disturbance feedthrough Fz ∈ ℝⁿᶻˣⁿᵈ.

        Default: zeros (no direct feedthrough from disturbances to outputs).
        """
        return np.zeros((self.nz, self.nd))

    @property
    def Dm(self) -> np.ndarray:
        """
        Measurement input feedthrough Dm ∈ ℝⁿʸᵐˣⁿᵘ.

        Default: zeros (no direct feedthrough from inputs to measurements).
        """
        return np.zeros((self.nym, self.nu))

    @property
    def Fm(self) -> np.ndarray:
        """
        Measurement disturbance feedthrough Fm ∈ ℝⁿʸᵐˣⁿᵈ.

        Default: zeros (no direct feedthrough from disturbances to measurements).
        """
        return np.zeros((self.nym, self.nd))

    @property
    def nz(self) -> int:
        """Output dimension nz = Cz.shape[0]."""
        return self.Cz.shape[0]

    @property
    def nw(self) -> int:
        """Process-noise dimension nw = Gd.shape[1]."""
        return self.Gd.shape[1]

    # ── Parameter-identification interface (non-abstract, overridable) ────

    def predict_offset(self, d_np: np.ndarray) -> np.ndarray:
        """
        Additive constant term for the one-step prediction:

            x_pred = Ad x + Bd u + Ed d + predict_offset(d)

        The default implementation returns a zero vector.  Subclasses that
        model a known constant disturbance or an estimated bias term should
        override this method.

        Parameters
        ----------
        d_np : (nd,) ndarray  — current disturbance vector.

        Returns
        -------
        offset : (nx,) ndarray
        """
        return np.zeros(self.nx)

    @property
    def params(self) -> np.ndarray:
        """
        Current parameter vector *θ* as a flat numpy array.

        Default: empty.  Subclasses should override to return the natural
        parameter vector for system identification.
        """
        return np.array([], dtype=float)

    def with_params(self, theta: np.ndarray) -> "LinearDiscreteModel":
        """
        Return a **new** model instance constructed from parameter vector *θ*.

        The default implementation raises :class:`NotImplementedError`.
        Subclasses that expose ``params`` should override this method.

        Parameters
        ----------
        theta : (p,) ndarray — parameter vector (same layout as ``params``).

        Returns
        -------
        LinearDiscreteModel
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement with_params."
        )
