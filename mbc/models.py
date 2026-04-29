"""
Abstract linear discrete-time model interface.

Defines the LinearDiscreteModel ABC that all model-based control algorithms
in mbc operate on:

    x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k] + offset,   y[k] = C x[k]

    x ∈ ℝⁿ  state,  u ∈ ℝᵐ  input (ZOH over each dt),
    d ∈ ℝᵖ  disturbance,    y ∈ ℝˡ  output.

    Matrices A, B, E may depend on d (LPV); C is time-invariant.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from cvxopt import matrix


class LinearDiscreteModel(ABC):
    """
    Abstract interface for a linear discrete-time system:

      x[k+1] = A x[k] + B u[k] + E d[k] + offset,   y[k] = C x[k]

    C is time-invariant.  Subclasses implement ``discretize(d)`` to return
    the ZOH-discretised matrices as cvxopt dense matrices.  The ``d``
    argument is passed as operating-point context: LTI implementations may
    ignore it (A, B, E are constant), while LPV implementations use it to
    schedule the matrices (e.g. when actuator gain depends on an exogenous
    signal such as outdoor temperature).

    Parameter-identification interface
    -----------------------------------
    Subclasses that support system identification should additionally
    implement ``params`` and ``with_params``.  The concrete
    ``discretize_jacobian`` method provides finite-difference Jacobians by
    default; subclasses may override it with an analytic implementation for
    improved efficiency.
    """

    @property
    @abstractmethod
    def n_x(self) -> int:
        """State dimension n."""

    @property
    @abstractmethod
    def n_u(self) -> int:
        """Input dimension m."""

    @property
    @abstractmethod
    def n_d(self) -> int:
        """Disturbance dimension p."""

    @property
    @abstractmethod
    def C(self) -> "matrix":
        """Output matrix C ∈ ℝˡˣⁿ (cvxopt dense)."""

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
    def x_ref(self) -> "matrix":
        """Reference / setpoint x_ref ∈ ℝⁿ (cvxopt column vector)."""

    @property
    @abstractmethod
    def u_bounds(self) -> Tuple["matrix", "matrix"]:
        """Box constraint on inputs (u_min, u_max), each (m, 1) cvxopt."""

    @abstractmethod
    def discretize(self, d: "matrix") -> Tuple["matrix", "matrix", "matrix"]:
        """
        Return ZOH-discretised matrices (A_d, B_d, E_d) as cvxopt dense
        matrices.

        The ``d`` argument is passed as operating-point context so that
        LPV implementations can schedule matrices on the current disturbance
        (e.g. heat-pump COP that varies with outdoor temperature).  Pure
        LTI implementations may ignore ``d`` and always return constant
        matrices.
        """

    # ── Parameter-identification interface (non-abstract, overridable) ────

    def predict_offset(self, d_np: np.ndarray) -> np.ndarray:
        """
        Additive constant term for the one-step prediction:

            x_pred = A x + B u + E d + predict_offset(d)

        The default implementation returns a zero vector.  Subclasses that
        model a known constant disturbance or an estimated bias term
        (e.g. internal heat gains that are not captured in *d*) should
        override this method.

        Parameters
        ----------
        d_np : (p,) ndarray  — current disturbance vector (numpy).

        Returns
        -------
        offset : (n,) ndarray
        """
        return np.zeros(self.n_x)

    @property
    def params(self) -> np.ndarray:
        """
        Current parameter vector *θ* as a flat numpy array.

        The default implementation returns an empty array, indicating that
        this model does not expose identifiable parameters via this
        interface.  Subclasses should override to return the natural
        parameter vector for system identification.
        """
        return np.array([], dtype=float)

    def with_params(self, theta: np.ndarray) -> "LinearDiscreteModel":
        """
        Return a **new** model instance constructed from parameter vector *θ*.

        This is the model factory used by
        ``discretize_jacobian`` for finite-difference perturbation.

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

    def discretize_jacobian(
        self,
        d: np.ndarray,
        h: float = 1e-5,
    ) -> Tuple[List[np.ndarray], List[np.ndarray], List[np.ndarray]]:
        """
        Jacobians of the discretised matrices w.r.t. the parameter vector.

        Returns lists ``(dA, dB, dE)`` each of length ``len(self.params)``,
        where ``dA[i]``, ``dB[i]``, ``dE[i]`` are (n×n), (n×m), (n×p)
        numpy arrays representing

            ∂A_d/∂θ_i,  ∂B_d/∂θ_i,  ∂E_d/∂θ_i

        evaluated at the current ``self.params``.

        The default implementation uses forward finite differences via
        ``with_params``.  If ``params`` is empty or ``with_params`` is not
        implemented, empty lists are returned.  Subclasses may override
        with an analytic implementation.

        Parameters
        ----------
        d : (p,) ndarray — disturbance vector (numpy).
        h : float        — finite-difference step size.

        Returns
        -------
        dA : list of (n, n) ndarray
        dB : list of (n, m) ndarray
        dE : list of (n, p) ndarray
        """
        from cvxopt import matrix as cvx_matrix

        theta0 = self.params
        if len(theta0) == 0:
            return [], [], []

        def _to_np(m: "matrix") -> np.ndarray:
            rows, cols = m.size
            return np.array(list(m), dtype=float).reshape(
                (rows, cols), order="F"
            )

        d_cvx = cvx_matrix(d.tolist(), (len(d), 1), tc="d")
        A0, B0, E0 = self.discretize(d_cvx)
        A0_np, B0_np, E0_np = _to_np(A0), _to_np(B0), _to_np(E0)

        dA: List[np.ndarray] = []
        dB: List[np.ndarray] = []
        dE: List[np.ndarray] = []
        for i in range(len(theta0)):
            theta_h = theta0.copy()
            theta_h[i] += h
            try:
                m_h = self.with_params(theta_h)
            except NotImplementedError:
                return [], [], []
            Ah, Bh, Eh = m_h.discretize(d_cvx)
            dA.append((_to_np(Ah) - A0_np) / h)
            dB.append((_to_np(Bh) - B0_np) / h)
            dE.append((_to_np(Eh) - E0_np) / h)
        return dA, dB, dE
