"""
Continuous-discrete SDE model interface.

``ContinuousDiscreteModel`` — abstract base for continuous-discrete
stochastic systems (ControlToolbox §SDE):

    dx(t)   = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
    z(t)    = gm(x, u, d, p, t)
    ym(tk)  = hm(x, u, d, p, tk) + v(tk),                         v(tk) ~ N(0, Rm)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from .._utils import _fd_jacobian


class ContinuousDiscreteModel(ABC):
    """
    Abstract interface for a continuous-discrete stochastic system
    (ControlToolbox §SDE).

    The system is governed by the Itô SDE

        dx(t)  = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
        z(t)   = gm(x, u, d, p, t)                                  (continuous output)
        ym(tk) = hm(x, u, d, p, tk) + v(tk),                         v(tk) ~ N(0, Rm)

    Subclasses must implement the drift ``f``, diffusion ``sigma``, output
    function ``gm``, measurement function ``hm``, and the noise covariance
    property ``Rm``.

    Dimensions
    ----------
        nx   – state dimension              x ∈ ℝⁿˣ
        nu   – input dimension              u ∈ ℝⁿᵘ
        nd   – disturbance dimension        d ∈ ℝⁿᵈ
        nw   – process-noise dimension      w ∈ ℝⁿʷ  (columns of sigma's output)
        nz   – output dimension             z ∈ ℝⁿᶻ
        nym  – measurement output dimension ym ∈ ℝⁿʸᵐ
    """

    @abstractmethod
    def f(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Drift function f(x, u, d, p, t).

        Parameters
        ----------
        x : (nx,) state vector.
        u : (nu,) input vector.
        d : (nd,) disturbance vector.
        p : (nparams,) parameter vector.
        t : current time.

        Returns
        -------
        (nx,) drift value.
        """

    @abstractmethod
    def sigma(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Diffusion function sigma(x, u, d, p, t).

        Returns
        -------
        (nx, nw) diffusion matrix such that dx = f dt + sigma dw, w ~ N(0, I dt).
        """

    @abstractmethod
    def gm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Continuous output function gm(x, u, d, p, t)  (ControlToolbox ``g^m``).

        Parameters
        ----------
        x : (nx,) state vector.
        u : (nu,) input vector.
        d : (nd,) disturbance vector.
        p : (nparams,) parameter vector.
        t : current time.

        Returns
        -------
        (nz,) continuous output vector.
        """

    @abstractmethod
    def hm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Measurement function hm(x, u, d, p, t)  (ControlToolbox ``h^m``)
        → (nym,) predicted observation.
        """

    @property
    @abstractmethod
    def Rm(self) -> np.ndarray:
        """Measurement noise covariance Rm ∈ ℝⁿʸᵐˣⁿʸᵐ."""

    @property
    @abstractmethod
    def nym(self) -> int:
        """Measurement output dimension."""

    @property
    @abstractmethod
    def nz(self) -> int:
        """Output dimension."""

    @property
    @abstractmethod
    def nx(self) -> int:
        """State dimension."""

    @property
    @abstractmethod
    def nu(self) -> int:
        """Input dimension."""

    @property
    @abstractmethod
    def nd(self) -> int:
        """Disturbance dimension."""

    @property
    @abstractmethod
    def nw(self) -> int:
        """Process-noise / diffusion dimension nw (columns of sigma's output)."""

    # ── Jacobian methods ──────────────────────────────────────────────────
    #
    # All defaults delegate to :func:`mbc._utils._fd_jacobian`, which
    # implements the single forward-FD kernel used throughout the toolbox.
    # Subclasses may override any method with an analytic Jacobian.

    def dfdx(self, x, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂x at (x, u, d, p, t)  →  (nx, nx) ndarray."""
        return _fd_jacobian(lambda v: self.f(v, u, d, p, t), x)

    def dfdu(self, x, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂u at (x, u, d, p, t)  →  (nx, nu) ndarray."""
        return _fd_jacobian(lambda v: self.f(x, v, d, p, t), u)

    def dfdd(self, x, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂d at (x, u, d, p, t)  →  (nx, nd) ndarray."""
        return _fd_jacobian(lambda v: self.f(x, u, v, p, t), d)

    def dfdp(self, x, u, d, p, t) -> np.ndarray:
        """
        Jacobian ∂f/∂p at (x, u, d, p, t)  →  (nx, nparams) ndarray.

        Returns an empty ``(nx, 0)`` array when ``p`` is empty.
        """
        return _fd_jacobian(lambda v: self.f(x, u, d, v, t), p, m_out=self.nx)

    def dhmdx(self, x, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂x at (x, u, d, p, t)  →  (nym, nx) ndarray."""
        return _fd_jacobian(lambda v: self.hm(v, u, d, p, t), x)

    def dhmdu(self, x, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂u at (x, u, d, p, t)  →  (nym, nu) ndarray."""
        return _fd_jacobian(lambda v: self.hm(x, v, d, p, t), u)

    def dhmdd(self, x, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂d at (x, u, d, p, t)  →  (nym, nd) ndarray."""
        return _fd_jacobian(lambda v: self.hm(x, u, v, p, t), d)

    def dhmdp(self, x, u, d, p, t=0.0) -> np.ndarray:
        """
        Jacobian ∂hm/∂p at (x, u, d, p, t)  →  (nym, nparams) ndarray.

        Returns an empty ``(nym, 0)`` array when ``p`` is empty.
        """
        return _fd_jacobian(lambda v: self.hm(x, u, d, v, t), p, m_out=self.nym)

    def dgmdx(self, x, u, d, p, t) -> np.ndarray:
        """Jacobian ∂gm/∂x at (x, u, d, p, t)  →  (nz, nx) ndarray."""
        return _fd_jacobian(lambda v: self.gm(v, u, d, p, t), x)

    def dgmdu(self, x, u, d, p, t) -> np.ndarray:
        """Jacobian ∂gm/∂u at (x, u, d, p, t)  →  (nz, nu) ndarray."""
        return _fd_jacobian(lambda v: self.gm(x, v, d, p, t), u)

    # ── Parameters ────────────────────────────────────────────────────────

    @property
    def params(self) -> np.ndarray:
        """
        Default parameter vector θ as a flat numpy array.

        Default: empty.  Subclasses should override to return the current
        parameter vector, which callers may use as the default ``p``.
        """
        return np.array([], dtype=float)
