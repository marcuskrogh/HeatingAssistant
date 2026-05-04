"""
SISO state-space realization (M.Sc. Ch. 2–3).

Provides ``SISORealization`` for computing a minimal state-space
representation of a SISO system from:

  - numerator / denominator polynomial coefficients (transfer function form)
  - sampled impulse or step response data

Two canonical forms are supported:

  - Observable canonical form  (default)
  - Controllable canonical form

Reference:  M.Sc. thesis, Ch. 2–3.
"""

from __future__ import annotations

import numpy as np


class SISORealization:
    """
    SISO state-space realization from a transfer function or I/O data
    (M.Sc. Ch. 2–3).

    The realized system has the form:

        x[k+1] = A x[k] + B u[k]
        y[k]   = C x[k] + D u[k]

    Instances are constructed via the class-method factories
    :meth:`from_transfer_function` and :meth:`from_impulse_response`.

    Attributes
    ----------
    A : (n, n) ndarray  — state-transition matrix.
    B : (n, 1) ndarray  — input matrix.
    C : (1, n) ndarray  — output matrix.
    D : (1, 1) ndarray  — feed-through term.
    """

    def __init__(
        self,
        A: np.ndarray,
        B: np.ndarray,
        C: np.ndarray,
        D: np.ndarray,
    ) -> None:
        self._A = np.asarray(A, dtype=float)
        self._B = np.asarray(B, dtype=float)
        self._C = np.asarray(C, dtype=float)
        self._D = np.asarray(D, dtype=float)

    # ── Factories ────────────────────────────────────────────────────────

    @classmethod
    def from_transfer_function(
        cls,
        num: np.ndarray,
        den: np.ndarray,
        form: str = "observable",
    ) -> "SISORealization":
        """
        Realize a SISO transfer function in canonical state-space form.

        The transfer function is given as

            H(z) = (num[0] z^r + … + num[r]) / (den[0] z^n + … + den[n])

        with ``len(den) - 1 = n`` (order) and ``len(num) - 1 ≤ n``.

        Parameters
        ----------
        num : array-like, shape (r+1,)
            Numerator coefficients in descending powers of z.
        den : array-like, shape (n+1,)
            Denominator coefficients in descending powers of z.
            ``den[0]`` is normalised to 1.
        form : {"observable", "controllable"}
            Canonical form to use.  Default: ``"observable"``.

        Returns
        -------
        SISORealization

        Raises
        ------
        NotImplementedError
            Always — to be implemented in a future commit.
        """
        raise NotImplementedError(
            "SISORealization.from_transfer_function is not yet implemented."
        )

    @classmethod
    def from_impulse_response(
        cls,
        h: np.ndarray,
        dt: float,
        n: int,
    ) -> "SISORealization":
        """
        Realize a SISO system from a sampled impulse response (M.Sc. Ch. 3).

        Constructs a minimal nth-order state-space model whose impulse
        response best fits the sequence ``h[0], h[1], …, h[T-1]`` (sampled
        at interval ``dt``).

        Parameters
        ----------
        h : (T,) ndarray
            Sampled impulse response values.
        dt : float
            Sampling interval in seconds.
        n : int
            Desired model order.

        Returns
        -------
        SISORealization

        Raises
        ------
        NotImplementedError
            Always — to be implemented in a future commit.
        """
        raise NotImplementedError(
            "SISORealization.from_impulse_response is not yet implemented."
        )

    # ── System matrices ──────────────────────────────────────────────────

    @property
    def A(self) -> np.ndarray:
        """State-transition matrix A ∈ ℝⁿˣⁿ."""
        return self._A

    @property
    def B(self) -> np.ndarray:
        """Input matrix B ∈ ℝⁿˣ¹."""
        return self._B

    @property
    def C(self) -> np.ndarray:
        """Output matrix C ∈ ℝ¹ˣⁿ."""
        return self._C

    @property
    def D(self) -> np.ndarray:
        """Feed-through matrix D ∈ ℝ¹ˣ¹."""
        return self._D
