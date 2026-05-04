"""
Abstract model interfaces for the mbc toolbox.

Linear discrete-time interface
-------------------------------
``LinearDiscreteModel`` — abstract base for linear discrete-time systems:

    x[k+1] = Ad x[k] + Bd u[k] + Ed d[k] + Gd w[k],   w[k] ~ N(0, Qd)
    z[k]   = Cz x[k] + Dz u[k] + Fz d[k]
    ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],       v[k] ~ N(0, Rm)

    x ∈ ℝⁿˣ state, u ∈ ℝⁿᵘ input, d ∈ ℝⁿᵈ disturbance,
    z ∈ ℝⁿᶻ controlled output, ym ∈ ℝⁿʸᵐ measurement.

Continuous-discrete SDE interface
--------------------------------------------------
``ContinuousDiscreteModel`` — abstract base for continuous-discrete
stochastic systems:

    dx(t)   = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
    z(t)    = g(x, u, d, p, t)
    ym(tk)  = hm(x, u, d, p, tk) + v(tk),                         v(tk) ~ N(0, Rm)

``LinearContinuousDiscreteModel`` — extends ``ContinuousDiscreteModel`` for
linear systems where the drift, diffusion, output, and observation functions
take the specific forms:

    f(x, u, d, p, t)     = A x + B u + E d
    sigma(x, u, d, p, t) = G                          (constant diffusion)
    g(x, u, d, p, t)     = Cz x + Dz u + Fz d
    hm(x, u, d, p, t)    = Cm x + Dm u + Fm d

``ContinuousDiscreteDAEModel`` — extends ``ContinuousDiscreteModel`` with an
algebraic constraint and algebraic states y:

    dx(t)   = f(x, y, u, d, p, t) dt + sigma(x, y, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
    0       = h(x, y, u, d, p, t)
    z(t)    = g(x, y, u, d, p, t)
    ym(tk)  = hm(x, y, u, d, p, tk) + v(tk),                            v(tk) ~ N(0, Rm)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from cvxopt import matrix


_H_FD: float = 1e-5  # default finite-difference step for Jacobians


class LinearDiscreteModel(ABC):
    """
    Abstract interface for a linear discrete-time system:

        x[k+1] = Ad x[k] + Bd u[k] + Ed d[k] + Gd w[k],   w[k] ~ N(0, Qd)
        z[k]   = Cz x[k] + Dz u[k] + Fz d[k]
        ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],       v[k] ~ N(0, Rm)

    The system matrices Ad, Bd, Ed, Gd, Cm, Cz, Dm, Dz, Fm, Fz are constant
    (LTI).  This interface is analogous to
    :class:`LinearContinuousDiscreteModel` but for discrete-time systems.

    Dimensions
    ----------
        nx   – state dimension              x ∈ ℝⁿˣ
        nu   – input dimension              u ∈ ℝⁿᵘ
        nd   – disturbance dimension        d ∈ ℝⁿᵈ
        nym  – measurement output dimension ym ∈ ℝⁿʸᵐ  (derived: Cm.shape[0])
        nz   – controlled output dimension  z ∈ ℝⁿᶻ   (derived: Cz.shape[0])

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
        Controlled output matrix Cz ∈ ℝⁿᶻˣⁿˣ.

        Default: Cm (same output set as measurements).
        Subclasses may override for a different controlled-output selection.
        """
        return self.Cm

    @property
    def Dz(self) -> np.ndarray:
        """
        Controlled output feedthrough Dz ∈ ℝⁿᶻˣⁿᵘ.

        Default: zeros (no direct feedthrough from inputs to controlled outputs).
        """
        return np.zeros((self.nz, self.nu))

    @property
    def Fz(self) -> np.ndarray:
        """
        Controlled output disturbance feedthrough Fz ∈ ℝⁿᶻˣⁿᵈ.

        Default: zeros (no direct feedthrough from disturbances to controlled outputs).
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
        """Controlled output dimension nz = Cz.shape[0]."""
        return self.Cz.shape[0]

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


# ── Continuous-Discrete SDE Model ────────────────────────────────────────────


class ContinuousDiscreteModel(ABC):
    """
    Abstract interface for a continuous-discrete stochastic system (Ph.D. Ch. 5).

    The system is governed by the Itô SDE

        dx(t)  = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
        z(t)   = g(x, u, d, p, t)
        ym(tk) = hm(x, u, d, p, tk) + v(tk),                         v(tk) ~ N(0, Rm)

    Subclasses must implement the drift ``f``, diffusion ``sigma``, controlled
    output ``g``, measurement function ``hm``, and the noise covariance
    property ``Rm``.

    Dimensions
    ----------
        nx   – state dimension              x ∈ ℝⁿˣ
        nu   – input dimension              u ∈ ℝⁿᵘ
        nd   – disturbance dimension        d ∈ ℝⁿᵈ
        nw   – process-noise dimension      w ∈ ℝⁿʷ  (columns of sigma's output)
        nz   – controlled output dimension  z ∈ ℝⁿᶻ
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
    def g(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Controlled output function g(x, u, d, p, t).

        Parameters
        ----------
        x : (nx,) state vector.
        u : (nu,) input vector.
        d : (nd,) disturbance vector.
        p : (nparams,) parameter vector.
        t : current time.

        Returns
        -------
        (nz,) controlled output vector.
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
        Measurement function hm(x, u, d, p, t) → (nym,) predicted observation.
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
        """Controlled output dimension."""

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

    # ── Jacobian methods (default: forward finite differences) ────────────

    def dfdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian F = ∂f/∂x evaluated at (x, u, d, p, t)  →  (nx, nx) ndarray.

        Default implementation uses forward finite differences with step
        ``_H_FD``.  Subclasses may override with an analytic Jacobian.
        """
        f0 = self.f(x, u, d, p, t)
        nx = x.shape[0]
        J = np.empty((nx, nx))
        for k in range(nx):
            x_fwd = x.copy()
            x_fwd[k] += _H_FD
            J[:, k] = (self.f(x_fwd, u, d, p, t) - f0) / _H_FD
        return J

    def dhmdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian H = ∂hm/∂x evaluated at (x, u, d, p, t)  →  (nym, nx) ndarray.

        Default implementation uses forward finite differences with step
        ``_H_FD``.  Subclasses may override with an analytic Jacobian.
        """
        h0 = self.hm(x, u, d, p, t)
        nx = x.shape[0]
        ny = h0.shape[0]
        J = np.empty((ny, nx))
        for k in range(nx):
            x_fwd = x.copy()
            x_fwd[k] += _H_FD
            J[:, k] = (self.hm(x_fwd, u, d, p, t) - h0) / _H_FD
        return J

    def dhmdu(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂u evaluated at (x, u, d, p, t)  →  (nym, nu) ndarray.

        Default: forward finite differences.  Subclasses may override.
        """
        h0 = self.hm(x, u, d, p, t)
        nu = u.shape[0]
        ny = h0.shape[0]
        J = np.empty((ny, nu))
        for k in range(nu):
            u_fwd = u.copy()
            u_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, u_fwd, d, p, t) - h0) / _H_FD
        return J

    def dfdu(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂u evaluated at (x, u, d, p, t)  →  (nx, nu) ndarray.

        Default: forward finite differences.  Subclasses may override.
        """
        f0 = self.f(x, u, d, p, t)
        nu = u.shape[0]
        nx = f0.shape[0]
        J = np.empty((nx, nu))
        for k in range(nu):
            u_fwd = u.copy()
            u_fwd[k] += _H_FD
            J[:, k] = (self.f(x, u_fwd, d, p, t) - f0) / _H_FD
        return J

    def dfdd(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂d evaluated at (x, u, d, p, t)  →  (nx, nd) ndarray.

        Default: forward finite differences.  Subclasses may override.
        """
        f0 = self.f(x, u, d, p, t)
        nd = d.shape[0]
        nx = f0.shape[0]
        J = np.empty((nx, nd))
        for k in range(nd):
            d_fwd = d.copy()
            d_fwd[k] += _H_FD
            J[:, k] = (self.f(x, u, d_fwd, p, t) - f0) / _H_FD
        return J

    def dhmdd(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂d evaluated at (x, u, d, p, t)  →  (nym, nd) ndarray.

        Default: forward finite differences.  Subclasses may override.
        """
        h0 = self.hm(x, u, d, p, t)
        nd = d.shape[0]
        ny = h0.shape[0]
        J = np.empty((ny, nd))
        for k in range(nd):
            d_fwd = d.copy()
            d_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, u, d_fwd, p, t) - h0) / _H_FD
        return J

    def dfdp(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂p evaluated at (x, u, d, p, t)  →  (nx, nparams) ndarray.

        Default: forward finite differences.  Subclasses may override.
        Returns an empty (nx, 0) array when p is empty.
        """
        nparams = p.shape[0]
        nx = self.nx
        if nparams == 0:
            return np.empty((nx, 0))
        f0 = self.f(x, u, d, p, t)
        J = np.empty((nx, nparams))
        for k in range(nparams):
            p_fwd = p.copy()
            p_fwd[k] += _H_FD
            J[:, k] = (self.f(x, u, d, p_fwd, t) - f0) / _H_FD
        return J

    def dhmdp(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂p evaluated at (x, u, d, p, t)  →  (nym, nparams) ndarray.

        Default: forward finite differences.  Subclasses may override.
        Returns an empty (nym, 0) array when p is empty.
        """
        nparams = p.shape[0]
        ny = self.nym
        if nparams == 0:
            return np.empty((ny, 0))
        h0 = self.hm(x, u, d, p, t)
        J = np.empty((ny, nparams))
        for k in range(nparams):
            p_fwd = p.copy()
            p_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, u, d, p_fwd, t) - h0) / _H_FD
        return J

    # ── Parameters ────────────────────────────────────────────────────────

    @property
    def params(self) -> np.ndarray:
        """
        Default parameter vector θ as a flat numpy array.

        Default: empty.  Subclasses should override to return the current
        parameter vector, which callers may use as the default ``p``.
        """
        return np.array([], dtype=float)


# ── Linear Continuous-Discrete Model ─────────────────────────────────────────


class LinearContinuousDiscreteModel(ContinuousDiscreteModel):
    """
    Abstract interface for a linear continuous-discrete stochastic system.

    Extends :class:`ContinuousDiscreteModel` with the specific linear forms:

        dx(t)  = (A x(t) + B u(t) + E d(t)) dt + G dw(t),  dw(t) ~ N(0, I dt)
        z(t)   = Cz x(t) + Dz u(t) + Fz d(t)
        ym(tk) = Cm x(tk) + Dm u(tk) + Fm d(tk) + v(tk),   v(tk) ~ N(0, Rm)

    with zero-order-hold (ZOH) inputs and disturbances over each sampling
    interval [t_k, t_{k+1}].

    The model exposes the ZOH-discretised matrices via ``discretize`` and the
    exact discrete process-noise covariance via ``discretize_noise``.

    Dimensions
    ----------
        nx   – state dimension              x ∈ ℝⁿˣ
        nu   – input dimension              u ∈ ℝⁿᵘ
        nd   – disturbance dimension        d ∈ ℝⁿᵈ
        nw   – process-noise dimension      w ∈ ℝⁿʷ  (derived: G.shape[1])
        nz   – controlled output dimension  z ∈ ℝⁿᶻ  (derived: Cz.shape[0])
        nym  – measurement output dimension ym ∈ ℝⁿʸᵐ (derived: Cm.shape[0])
        A    – continuous state matrix      A ∈ ℝⁿˣˣⁿˣ
        B    – continuous input matrix      B ∈ ℝⁿˣˣⁿᵘ
        E    – continuous disturbance mat.  E ∈ ℝⁿˣˣⁿᵈ
        G    – noise input matrix           G ∈ ℝⁿˣˣⁿʷ
        Cm   – measurement output matrix    Cm ∈ ℝⁿʸᵐˣⁿˣ
        Dm   – measurement input D-term     Dm ∈ ℝⁿʸᵐˣⁿᵘ  (default: 0)
        Fm   – measurement disturbance D    Fm ∈ ℝⁿʸᵐˣⁿᵈ  (default: 0)
        Cz   – controlled output matrix     Cz ∈ ℝⁿᶻˣⁿˣ   (default: Cm)
        Dz   – controlled output input D    Dz ∈ ℝⁿᶻˣⁿᵘ   (default: 0)
        Fz   – controlled output dist D     Fz ∈ ℝⁿᶻˣⁿᵈ   (default: 0)
        Rm   – measurement noise cov.       Rm ∈ ℝⁿʸᵐˣⁿʸᵐ
        dt   – sampling interval

    ZOH discretisation (``discretize``)
    -------------------------------------
    Computed via the augmented-matrix method (no matrix inverse required):

        [Ad | Bd | Ed] = expm([[A, B, E], [0, 0, 0], [0, 0, 0]] · dt)[:nx, :]

    Discrete process noise (``discretize_noise``)
    -----------------------------------------------
    Computed by the Van Loan (1978) method:

        Qd = ∫₀^{dt} expm(A τ) G Gᵀ expm(A τ)ᵀ dτ
    """

    # ── Abstract dimensions (inherited from ContinuousDiscreteModel) ──────
    #   nx, nu, nd are abstract in the parent and must be implemented by
    #   concrete subclasses.  nym and nw are provided as concrete derivations
    #   from Cm and G below.

    # ── Abstract continuous-time matrices (numpy) ─────────────────────────

    @property
    @abstractmethod
    def A(self) -> np.ndarray:
        """Continuous state matrix A ∈ ℝⁿˣˣⁿˣ."""

    @property
    @abstractmethod
    def B(self) -> np.ndarray:
        """Continuous input matrix B ∈ ℝⁿˣˣⁿᵘ."""

    @property
    @abstractmethod
    def E(self) -> np.ndarray:
        """Continuous disturbance matrix E ∈ ℝⁿˣˣⁿᵈ."""

    @property
    @abstractmethod
    def G(self) -> np.ndarray:
        """Noise input matrix G ∈ ℝⁿˣˣⁿʷ."""

    # ── Abstract observation matrices (numpy) ────────────────────────────

    @property
    @abstractmethod
    def Cm(self) -> np.ndarray:
        """Measurement output matrix Cm ∈ ℝⁿʸᵐˣⁿˣ (numpy ndarray)."""

    @property
    @abstractmethod
    def Rm(self) -> np.ndarray:
        """Measurement noise covariance Rm ∈ ℝⁿʸᵐˣⁿʸᵐ (numpy ndarray)."""

    # ── Abstract sampling interval ────────────────────────────────────────

    @property
    @abstractmethod
    def dt(self) -> float:
        """Sampling interval (seconds)."""

    # ── Abstract control-interface properties ────────────────────────────

    @property
    @abstractmethod
    def x(self) -> list[float]:
        """Current state x as a plain list of floats."""

    @x.setter
    @abstractmethod
    def x(self, val: list[float]) -> None: ...

    @property
    @abstractmethod
    def x_ref(self) -> np.ndarray:
        """Reference / setpoint x_ref ∈ ℝⁿˣ (numpy 1-D array, length nx)."""

    @property
    @abstractmethod
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """Box constraint on inputs (u_min, u_max), each a (nu,) ndarray."""

    # ── Concrete implementations of ContinuousDiscreteModel abstracts ─────

    @property
    def nym(self) -> int:
        """Measurement output dimension nym = Cm.shape[0]."""
        return self.Cm.shape[0]

    @property
    def nw(self) -> int:
        """Process-noise dimension nw = G.shape[1]."""
        return self.G.shape[1]

    @property
    def Cz(self) -> np.ndarray:
        """
        Controlled output matrix Cz ∈ ℝⁿᶻˣⁿˣ.

        Default: Cm (same output set as measurements).
        Subclasses may override for a different controlled-output selection.
        """
        return self.Cm

    @property
    def Dz(self) -> np.ndarray:
        """
        Controlled output input feedthrough Dz ∈ ℝⁿᶻˣⁿᵘ.

        Default: zeros.  Subclasses may override.
        """
        return np.zeros((self.nz, self.nu))

    @property
    def Fz(self) -> np.ndarray:
        """
        Controlled output disturbance feedthrough Fz ∈ ℝⁿᶻˣⁿᵈ.

        Default: zeros.  Subclasses may override.
        """
        return np.zeros((self.nz, self.nd))

    @property
    def Dm(self) -> np.ndarray:
        """
        Measurement input feedthrough Dm ∈ ℝⁿʸᵐˣⁿᵘ.

        Default: zeros.  Subclasses may override.
        """
        return np.zeros((self.nym, self.nu))

    @property
    def Fm(self) -> np.ndarray:
        """
        Measurement disturbance feedthrough Fm ∈ ℝⁿʸᵐˣⁿᵈ.

        Default: zeros.  Subclasses may override.
        """
        return np.zeros((self.nym, self.nd))

    @property
    def nz(self) -> int:
        """Controlled output dimension nz = Cz.shape[0]."""
        return self.Cz.shape[0]

    def f(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Drift f(x, u, d, p, t) = A x + B u + E d  (p, t ignored for LTI)."""
        return self.A @ x + self.B @ u + self.E @ d

    def sigma(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Diffusion sigma(x, u, d, p, t) = G  (constant; arguments ignored)."""
        return self.G

    def g(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Controlled output g(x, u, d, p, t) = Cz x + Dz u + Fz d."""
        return self.Cz @ x + self.Dz @ u + self.Fz @ d

    def hm(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Measurement hm(x, u, d, p, t) = Cm x + Dm u + Fm d."""
        return self.Cm @ x + self.Dm @ u + self.Fm @ d

    # ── Analytic Jacobian overrides ───────────────────────────────────────

    def dfdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytic Jacobian ∂f/∂x = A  (arguments ignored)."""
        return self.A.copy()

    def dhmdx(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Analytic Jacobian ∂hm/∂x = Cm  (arguments ignored)."""
        return self.Cm.copy()

    def dhmdu(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Analytic Jacobian ∂hm/∂u = Dm."""
        return self.Dm.copy()

    def dfdu(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytic Jacobian ∂f/∂u = B  (arguments ignored)."""
        return self.B.copy()

    def dfdd(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytic Jacobian ∂f/∂d = E  (arguments ignored)."""
        return self.E.copy()

    def dhmdd(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Analytic Jacobian ∂hm/∂d = Fm."""
        return self.Fm.copy()

    def dfdp(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Analytic Jacobian ∂f/∂p = 0  (f = A x + B u + E d does not depend on p)."""
        return np.zeros((self.nx, p.shape[0]))

    def dhmdp(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """Analytic Jacobian ∂hm/∂p = 0  (hm does not depend on p for LTI)."""
        return np.zeros((self.nym, p.shape[0]))

    # ── Concrete discretisation methods ───────────────────────────────────

    def discretize(self, d: "matrix") -> Tuple["matrix", "matrix", "matrix"]:
        """
        ZOH-discretised matrices (Ad, Bd, Ed) as cvxopt dense matrices.

        Uses the augmented-matrix method so that no matrix inverse is
        required:

            expm([[A, B, E], [0, 0, 0], [0, 0, 0]] · dt)[:nx, :]
            = [Ad | Bd | Ed]

        The ``d`` argument is accepted for interface compatibility with
        ``OptimalControlProblem`` (LPV sub-classes may override this method
        to schedule matrices on the current disturbance); the default LTI
        implementation ignores it.

        Parameters
        ----------
        d : (nd, 1) cvxopt column  — current disturbance (ignored for LTI).

        Returns
        -------
        Ad : (nx, nx) cvxopt dense — discrete state-transition matrix.
        Bd : (nx, nu) cvxopt dense — discrete input matrix.
        Ed : (nx, nd) cvxopt dense — discrete disturbance matrix.

        Note
        ----
        Returns cvxopt.matrix for compatibility with ``OptimalControlProblem``.
        """
        from ._utils import _zoh_full, _np_to_cvx

        A_d_np, B_d_np, E_d_np = _zoh_full(self.A, self.B, self.E, self.dt)
        return _np_to_cvx(A_d_np), _np_to_cvx(B_d_np), _np_to_cvx(E_d_np)

    def discretize_noise(self) -> "matrix":
        """
        Exact discrete process-noise covariance Qd via Van Loan (1978).

        Computes

            Qd = ∫₀^{dt} expm(A τ) G Gᵀ expm(A τ)ᵀ dτ

        using the augmented 2nx×2nx matrix method.  The result is symmetric
        positive semi-definite by construction.

        Returns
        -------
        Qd : (nx, nx) cvxopt dense — discrete process-noise covariance.

        Note
        ----
        Returns cvxopt.matrix for compatibility with ``OptimalControlProblem``.
        """
        from ._utils import _van_loan, _np_to_cvx

        # dw ~ N(0, I dt), so the noise intensity is G G^T.
        # Computed via the Van Loan (1978) augmented matrix method.
        Q_d_np = _van_loan(self.A, self.G, np.eye(self.nw), self.dt)
        return _np_to_cvx(Q_d_np)

    # ── Parameter-identification interface (non-abstract, overridable) ────

    @property
    def params(self) -> np.ndarray:
        """
        Current parameter vector θ as a flat numpy array.

        Default: empty (no identifiable parameters exposed).  Subclasses
        should override to support system identification.
        """
        return np.array([], dtype=float)

# ── Continuous-Discrete SDAE Model ───────────────────────────────────────────


class ContinuousDiscreteDAEModel(ContinuousDiscreteModel):
    """
    Abstract interface for a continuous-discrete stochastic DAE (Ph.D. Ch. 6).

    Extends ``ContinuousDiscreteModel`` with algebraic states y and constraint:

        dx(t)  = f(x, y, u, d, p, t) dt + sigma(x, y, u, d, p, t) dw(t),  dw(t) ~ N(0, I dt)
        0      = h(x, y, u, d, p, t)
        z(t)   = g(x, y, u, d, p, t)
        ym(tk) = hm(x, y, u, d, p, tk) + v(tk),                            v(tk) ~ N(0, Rm)

    At each integration step the algebraic constraint is enforced by solving
    ``h = 0`` for y (typically via Newton iteration in the simulator).

    Subclasses must additionally implement ``h``, ``ny``, and override ``g``
    with the DAE-specific signature.  The ``nz`` abstract property (controlled
    output dimension) is inherited from ``ContinuousDiscreteModel`` and must
    also be implemented by concrete subclasses.

    The ``nw`` abstract property is inherited from ``ContinuousDiscreteModel``
    and must be implemented by concrete subclasses.
    """

    @abstractmethod
    def h(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Algebraic constraint residual h(x, y, u, d, p, t).

        The constraint is satisfied when ``h(x, y, u, d, p, t) = 0``.

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
        Controlled output function g(x, y, u, d, p, t).

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
        (nz,) controlled output vector.
        """

    # ── Jacobian methods for DAE systems (default: forward FD) ───────────
    #
    # These override the base-class methods to include the algebraic state y
    # and parameter vector p in their signatures.

    def dfdx(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂x evaluated at (x, y, u, d, p, t)  →  (nx, nx) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        f0 = self.f(x, y, u, d, p, t)
        nx = x.shape[0]
        J = np.empty((nx, nx))
        for k in range(nx):
            x_fwd = x.copy()
            x_fwd[k] += _H_FD
            J[:, k] = (self.f(x_fwd, y, u, d, p, t) - f0) / _H_FD
        return J

    def dfdy(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂y evaluated at (x, y, u, d, p, t)  →  (nx, ny) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        f0 = self.f(x, y, u, d, p, t)
        ny = y.shape[0]
        nx = f0.shape[0]
        J = np.empty((nx, ny))
        for k in range(ny):
            y_fwd = y.copy()
            y_fwd[k] += _H_FD
            J[:, k] = (self.f(x, y_fwd, u, d, p, t) - f0) / _H_FD
        return J

    def dhmdx(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂x at (x, y, u, d, p, t)  →  (nym, nx) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.hm(x, y, u, d, p, t)
        nx = x.shape[0]
        ny = h0.shape[0]
        J = np.empty((ny, nx))
        for k in range(nx):
            x_fwd = x.copy()
            x_fwd[k] += _H_FD
            J[:, k] = (self.hm(x_fwd, y, u, d, p, t) - h0) / _H_FD
        return J

    def dhmdy(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂y at (x, y, u, d, p, t)  →  (nym, ny) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.hm(x, y, u, d, p, t)
        ny = y.shape[0]
        nym = h0.shape[0]
        J = np.empty((nym, ny))
        for k in range(ny):
            y_fwd = y.copy()
            y_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, y_fwd, u, d, p, t) - h0) / _H_FD
        return J

    def dhmdu(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂u at (x, y, u, d, p, t)  →  (nym, nu) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.hm(x, y, u, d, p, t)
        nu = u.shape[0]
        nym = h0.shape[0]
        J = np.empty((nym, nu))
        for k in range(nu):
            u_fwd = u.copy()
            u_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, y, u_fwd, d, p, t) - h0) / _H_FD
        return J

    def dhdx(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂h/∂x evaluated at (x, y, u, d, p, t)  →  (ny, nx) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.h(x, y, u, d, p, t)
        nx = x.shape[0]
        ny_out = h0.shape[0]
        J = np.empty((ny_out, nx))
        for k in range(nx):
            x_fwd = x.copy()
            x_fwd[k] += _H_FD
            J[:, k] = (self.h(x_fwd, y, u, d, p, t) - h0) / _H_FD
        return J

    def dhdy(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂h/∂y evaluated at (x, y, u, d, p, t)  →  (ny, ny) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.h(x, y, u, d, p, t)
        ny = y.shape[0]
        ny_out = h0.shape[0]
        J = np.empty((ny_out, ny))
        for k in range(ny):
            y_fwd = y.copy()
            y_fwd[k] += _H_FD
            J[:, k] = (self.h(x, y_fwd, u, d, p, t) - h0) / _H_FD
        return J

    def dfdu(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂u evaluated at (x, y, u, d, p, t)  →  (nx, nu) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        f0 = self.f(x, y, u, d, p, t)
        nu = u.shape[0]
        nx = f0.shape[0]
        J = np.empty((nx, nu))
        for k in range(nu):
            u_fwd = u.copy()
            u_fwd[k] += _H_FD
            J[:, k] = (self.f(x, y, u_fwd, d, p, t) - f0) / _H_FD
        return J

    def dfdd(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂d evaluated at (x, y, u, d, p, t)  →  (nx, nd) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        f0 = self.f(x, y, u, d, p, t)
        nd = d.shape[0]
        nx = f0.shape[0]
        J = np.empty((nx, nd))
        for k in range(nd):
            d_fwd = d.copy()
            d_fwd[k] += _H_FD
            J[:, k] = (self.f(x, y, u, d_fwd, p, t) - f0) / _H_FD
        return J

    def dhmdd(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂d at (x, y, u, d, p, t)  →  (nym, nd) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.hm(x, y, u, d, p, t)
        nd = d.shape[0]
        nym = h0.shape[0]
        J = np.empty((nym, nd))
        for k in range(nd):
            d_fwd = d.copy()
            d_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, y, u, d_fwd, p, t) - h0) / _H_FD
        return J

    def dhdu(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂h/∂u evaluated at (x, y, u, d, p, t)  →  (ny, nu) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.h(x, y, u, d, p, t)
        nu = u.shape[0]
        ny_out = h0.shape[0]
        J = np.empty((ny_out, nu))
        for k in range(nu):
            u_fwd = u.copy()
            u_fwd[k] += _H_FD
            J[:, k] = (self.h(x, y, u_fwd, d, p, t) - h0) / _H_FD
        return J

    def dhdd(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂h/∂d evaluated at (x, y, u, d, p, t)  →  (ny, nd) ndarray.

        Default: forward finite differences.  Override with analytic form.
        """
        h0 = self.h(x, y, u, d, p, t)
        nd = d.shape[0]
        ny_out = h0.shape[0]
        J = np.empty((ny_out, nd))
        for k in range(nd):
            d_fwd = d.copy()
            d_fwd[k] += _H_FD
            J[:, k] = (self.h(x, y, u, d_fwd, p, t) - h0) / _H_FD
        return J

    def dfdp(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂f/∂p evaluated at (x, y, u, d, p, t)  →  (nx, nparams) ndarray.

        Default: forward finite differences.  Override with analytic form.
        Returns an empty (nx, 0) array when p is empty.
        """
        nparams = p.shape[0]
        nx = self.nx
        if nparams == 0:
            return np.empty((nx, 0))
        f0 = self.f(x, y, u, d, p, t)
        J = np.empty((nx, nparams))
        for k in range(nparams):
            p_fwd = p.copy()
            p_fwd[k] += _H_FD
            J[:, k] = (self.f(x, y, u, d, p_fwd, t) - f0) / _H_FD
        return J

    def dhmdp(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Jacobian ∂hm/∂p at (x, y, u, d, p, t)  →  (nym, nparams) ndarray.

        Default: forward finite differences.  Override with analytic form.
        Returns an empty (nym, 0) array when p is empty.
        """
        nparams = p.shape[0]
        nym = self.nym
        if nparams == 0:
            return np.empty((nym, 0))
        h0 = self.hm(x, y, u, d, p, t)
        J = np.empty((nym, nparams))
        for k in range(nparams):
            p_fwd = p.copy()
            p_fwd[k] += _H_FD
            J[:, k] = (self.hm(x, y, u, d, p_fwd, t) - h0) / _H_FD
        return J

    def dhdp(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Jacobian ∂h/∂p evaluated at (x, y, u, d, p, t)  →  (ny, nparams) ndarray.

        Default: forward finite differences.  Override with analytic form.
        Returns an empty (ny, 0) array when p is empty.
        """
        nparams = p.shape[0]
        ny_out = self.h(x, y, u, d, p, t).shape[0]
        if nparams == 0:
            return np.empty((ny_out, 0))
        h0 = self.h(x, y, u, d, p, t)
        J = np.empty((ny_out, nparams))
        for k in range(nparams):
            p_fwd = p.copy()
            p_fwd[k] += _H_FD
            J[:, k] = (self.h(x, y, u, d, p_fwd, t) - h0) / _H_FD
        return J
