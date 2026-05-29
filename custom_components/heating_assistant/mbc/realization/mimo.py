"""
MIMO state-space realization (M.Sc. Ch. 4).

Provides ``MIMORealization`` for computing a minimal state-space
representation of a MIMO system from its Markov parameters (impulse-response
matrices) using the Ho–Kalman algorithm.

Reference:  M.Sc. thesis, Ch. 4.
"""

from __future__ import annotations

import numpy as np


class MIMORealization:
    """
    MIMO state-space realization via the Ho–Kalman algorithm (M.Sc. Ch. 4).

    The realized system has the form:

        x[k+1] = A x[k] + B u[k]
        y[k]   = C x[k] + D u[k]

    Instances are constructed via :meth:`from_markov_parameters`.

    Attributes
    ----------
    A : (n, n) ndarray  — state-transition matrix.
    B : (n, nu) ndarray — input matrix.
    C : (ny, n) ndarray — output matrix.
    D : (ny, nu) ndarray — feed-through matrix.
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
    def from_markov_parameters(
        cls,
        H: list[np.ndarray],
        n: int,
    ) -> "MIMORealization":
        """
        Realize a MIMO system from its Markov parameters via Ho–Kalman
        (M.Sc. Ch. 4).

        The Markov parameters are the impulse-response matrices:

            H[0] = D,  H[1] = C B,  H[2] = C A B,  …,  H[k] = C A^{k-1} B

        The algorithm forms a block Hankel matrix from ``H[1:]``, computes
        a rank-``n`` truncated SVD, and recovers ``(A, B, C)`` from the
        factorisation.

        Parameters
        ----------
        H : list of (ny, nu) ndarray, length >= 2*n + 1
            Markov parameters H[0], H[1], …  The first entry H[0] = D is
            the direct feed-through term.
        n : int
            Desired model order (number of states).

        Returns
        -------
        MIMORealization

        Raises
        ------
        ValueError
            If H is too short or dimensions are inconsistent.
        """
        if len(H) < 2 * n + 1:
            raise ValueError(
                f"Need at least {2*n + 1} Markov parameters for order {n}, "
                f"got {len(H)}"
            )

        # Extract D = H[0] and validate dimensions
        D = np.asarray(H[0], dtype=float)
        if D.ndim != 2:
            raise ValueError(f"H[0] must be 2D, got shape {D.shape}")

        ny, nu = D.shape

        # Validate all H[k] have consistent shape
        for k, Hk in enumerate(H):
            Hk = np.asarray(Hk, dtype=float)
            if Hk.shape != (ny, nu):
                raise ValueError(
                    f"H[{k}] has shape {Hk.shape}, expected ({ny}, {nu})"
                )

        # Determine q (block rows/cols for Hankel matrix)
        # We have H[1], H[2], ..., H[T-1] available
        # Need H[1] through H[2q] for block Hankel, so 2q <= T-1
        T = len(H)
        q = (T - 1) // 2
        if q < n:
            raise ValueError(
                f"Block size q={q} < n={n}. Need more Markov parameters."
            )

        # Validate observability and controllability conditions
        if q * ny < n:
            raise ValueError(
                f"Observability condition violated: q*ny={q*ny} < n={n}. "
                f"Need more outputs or larger q."
            )
        if q * nu < n:
            raise ValueError(
                f"Controllability condition violated: q*nu={q*nu} < n={n}. "
                f"Need more inputs or larger q."
            )

        # Build block Hankel matrix H_blk ∈ ℝ^(q·ny × q·nu)
        # H_blk[i*ny:(i+1)*ny, j*nu:(j+1)*nu] = H[i+j+1] for i,j = 0..q-1
        H_blk = np.zeros((q * ny, q * nu))
        for i in range(q):
            for j in range(q):
                idx = i + j + 1  # Skip H[0], use H[1] onwards
                if idx < len(H):
                    H_blk[i*ny:(i+1)*ny, j*nu:(j+1)*nu] = H[idx]

        # Compute rank-n truncated SVD
        try:
            U, s, Vt = np.linalg.svd(H_blk, full_matrices=False)
        except np.linalg.LinAlgError as e:
            raise ValueError(f"SVD failed: {e}")

        # Truncate to rank n
        U_n = U[:, :n]
        s_n = s[:n]
        V_n = Vt[:n, :].T  # Transpose to get V from V^T

        # Form observability and controllability matrices
        sqrt_s = np.diag(np.sqrt(s_n))
        O_n = U_n @ sqrt_s  # (q·ny) × n observability matrix
        R_n = sqrt_s @ V_n.T  # n × (q·nu) controllability matrix

        # Extract system matrices
        # C = first ny rows of observability matrix
        C = O_n[0:ny, :]  # Shape (ny, n)

        # B = first nu columns of controllability matrix
        B = R_n[:, 0:nu]  # Shape (n, nu)

        # A from shift-and-recover: A = O_n[0:(q-1)·ny]^+ O_n[ny:q·ny]
        O_upper = O_n[0:(q-1)*ny, :]  # First (q-1)·ny rows
        O_lower = O_n[ny:q*ny, :]      # Rows shifted down by ny

        # Compute A using pseudo-inverse
        try:
            A = np.linalg.lstsq(O_upper, O_lower, rcond=None)[0].T
        except np.linalg.LinAlgError as e:
            raise ValueError(f"Failed to recover A matrix: {e}")

        return cls(A=A, B=B, C=C, D=D)

    # ── System matrices ──────────────────────────────────────────────────

    @property
    def A(self) -> np.ndarray:
        """State-transition matrix A ∈ ℝⁿˣⁿ."""
        return self._A

    @property
    def B(self) -> np.ndarray:
        """Input matrix B ∈ ℝⁿˣⁿᵘ."""
        return self._B

    @property
    def C(self) -> np.ndarray:
        """Output matrix C ∈ ℝⁿʸˣⁿ."""
        return self._C

    @property
    def D(self) -> np.ndarray:
        """Feed-through matrix D ∈ ℝⁿʸˣⁿᵘ."""
        return self._D
