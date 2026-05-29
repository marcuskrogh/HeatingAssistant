"""
Continuous-discrete SDAE model interface.

``ContinuousDiscreteDAEModel`` — extends ``ContinuousDiscreteModel`` with an
algebraic constraint and algebraic states y (ControlToolbox §SDAE):

    dx(t)   = f(x, y, u, d, p, t) dt + sigma(x, y, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
    0       = g(x, y, u, d, p, t)
    z(t)    = gm(x, y, u, d, p, t)
    ym(tk)  = hm(x, y, u, d, p, tk) + v(tk),                            v(tk) ~ N(0, Rm)
"""

from __future__ import annotations

from abc import abstractmethod

import numpy as np

from .._utils import _fd_jacobian
from .continuous_discrete import ContinuousDiscreteModel


class ContinuousDiscreteDAEModel(ContinuousDiscreteModel):
    """
    Abstract interface for a continuous-discrete stochastic DAE
    (ControlToolbox §SDAE).

    Extends ``ContinuousDiscreteModel`` with algebraic states y and constraint:

        dx(t)  = f(x, y, u, d, p, t) dt + sigma(x, y, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
        0      = g(x, y, u, d, p, t)
        z(t)   = gm(x, y, u, d, p, t)
        ym(tk) = hm(x, y, u, d, p, tk) + v(tk),                            v(tk) ~ N(0, Rm)

    At each integration step the algebraic constraint is enforced by solving
    ``g = 0`` for y (via Newton iteration in the simulator/estimator).

    Subclasses must additionally implement ``g``, ``ny``, and override
    ``gm`` and ``hm`` with the DAE-specific signature (which now depends on
    the algebraic state ``y``).  The ``nz`` abstract property (output dimension)
    is inherited from ``ContinuousDiscreteModel`` and must also be implemented
    by concrete subclasses.

    The ``nw`` abstract property is inherited from ``ContinuousDiscreteModel``
    and must be implemented by concrete subclasses.
    """

    @abstractmethod
    def g(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Algebraic constraint residual g(x, y, u, d, p, t)
        (ControlToolbox §SDAE).

        The constraint is satisfied when ``g(x, y, u, d, p, t) = 0``.

        Parameters
        ----------
        x : (nx,) differential state vector.
        y : (ny,) algebraic state vector.
        u : (nu,) input vector.
        d : (nd,) disturbance vector.
        p : (nparams,) parameter vector.
        t : current time.

        Returns
        -------
        (ny,) residual vector.
        """

    @property
    @abstractmethod
    def ny(self) -> int:
        """Algebraic state dimension."""

    @abstractmethod
    def gm(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Continuous output function gm(x, y, u, d, p, t).

        Parameters
        ----------
        x : (nx,) differential state vector.
        y : (ny,) algebraic state vector.
        u : (nu,) input vector.
        d : (nd,) disturbance vector.
        p : (nparams,) parameter vector.
        t : current time.

        Returns
        -------
        (nz,) output vector.
        """

    # ── Jacobian methods for DAE systems ─────────────────────────────────
    #
    # All defaults delegate to :func:`mbc._utils._fd_jacobian`.  These
    # override the base-class methods to include the algebraic state ``y``
    # in their signatures.  Subclasses may override any method with an
    # analytic Jacobian.

    def dfdx(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂x at (x, y, u, d, p, t)  →  (nx, nx) ndarray."""
        return _fd_jacobian(lambda v: self.f(v, y, u, d, p, t), x)

    def dfdy(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂y at (x, y, u, d, p, t)  →  (nx, ny) ndarray."""
        return _fd_jacobian(lambda v: self.f(x, v, u, d, p, t), y)

    def dfdu(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂u at (x, y, u, d, p, t)  →  (nx, nu) ndarray."""
        return _fd_jacobian(lambda v: self.f(x, y, v, d, p, t), u)

    def dfdd(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂f/∂d at (x, y, u, d, p, t)  →  (nx, nd) ndarray."""
        return _fd_jacobian(lambda v: self.f(x, y, u, v, p, t), d)

    def dfdp(self, x, y, u, d, p, t) -> np.ndarray:
        """
        Jacobian ∂f/∂p at (x, y, u, d, p, t)  →  (nx, nparams) ndarray.

        Returns an empty ``(nx, 0)`` array when ``p`` is empty.
        """
        return _fd_jacobian(lambda v: self.f(x, y, u, d, v, t), p, m_out=self.nx)

    def dhmdx(self, x, y, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂x at (x, y, u, d, p, t)  →  (nym, nx) ndarray."""
        return _fd_jacobian(lambda v: self.hm(v, y, u, d, p, t), x)

    def dhmdy(self, x, y, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂y at (x, y, u, d, p, t)  →  (nym, ny) ndarray."""
        return _fd_jacobian(lambda v: self.hm(x, v, u, d, p, t), y)

    def dhmdu(self, x, y, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂u at (x, y, u, d, p, t)  →  (nym, nu) ndarray."""
        return _fd_jacobian(lambda v: self.hm(x, y, v, d, p, t), u)

    def dhmdd(self, x, y, u, d, p, t=0.0) -> np.ndarray:
        """Jacobian ∂hm/∂d at (x, y, u, d, p, t)  →  (nym, nd) ndarray."""
        return _fd_jacobian(lambda v: self.hm(x, y, u, v, p, t), d)

    def dhmdp(self, x, y, u, d, p, t=0.0) -> np.ndarray:
        """
        Jacobian ∂hm/∂p at (x, y, u, d, p, t)  →  (nym, nparams) ndarray.

        Returns an empty ``(nym, 0)`` array when ``p`` is empty.
        """
        return _fd_jacobian(lambda v: self.hm(x, y, u, d, v, t), p, m_out=self.nym)

    def dgdx(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂g/∂x at (x, y, u, d, p, t)  →  (ny, nx) ndarray."""
        return _fd_jacobian(lambda v: self.g(v, y, u, d, p, t), x)

    def dgdy(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂g/∂y at (x, y, u, d, p, t)  →  (ny, ny) ndarray."""
        return _fd_jacobian(lambda v: self.g(x, v, u, d, p, t), y)

    def dgdu(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂g/∂u at (x, y, u, d, p, t)  →  (ny, nu) ndarray."""
        return _fd_jacobian(lambda v: self.g(x, y, v, d, p, t), u)

    def dgdd(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂g/∂d at (x, y, u, d, p, t)  →  (ny, nd) ndarray."""
        return _fd_jacobian(lambda v: self.g(x, y, u, v, p, t), d)

    def dgdp(self, x, y, u, d, p, t) -> np.ndarray:
        """
        Jacobian ∂g/∂p at (x, y, u, d, p, t)  →  (ny, nparams) ndarray.

        Returns an empty ``(ny, 0)`` array when ``p`` is empty.
        """
        return _fd_jacobian(lambda v: self.g(x, y, u, d, v, t), p, m_out=self.ny)

    def dgmdx(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂gm/∂x at (x, y, u, d, p, t)  →  (nz, nx) ndarray."""
        return _fd_jacobian(lambda v: self.gm(v, y, u, d, p, t), x)

    def dgmdy(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂gm/∂y at (x, y, u, d, p, t)  →  (nz, ny) ndarray."""
        return _fd_jacobian(lambda v: self.gm(x, v, u, d, p, t), y)

    def dgmdu(self, x, y, u, d, p, t) -> np.ndarray:
        """Jacobian ∂gm/∂u at (x, y, u, d, p, t)  →  (nz, nu) ndarray."""
        return _fd_jacobian(lambda v: self.gm(x, y, v, d, p, t), u)
