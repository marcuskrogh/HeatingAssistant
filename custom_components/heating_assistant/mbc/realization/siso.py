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
        noise_num: np.ndarray | None = None,
    ) -> "SISORealization":
        """
        Realize a SISO transfer function in canonical state-space form.

        The transfer function is given as

            H(z) = (num[0] z^r + … + num[r]) / (den[0] z^n + … + den[n])

        with ``len(den) - 1 = n`` (order) and ``len(num) - 1 ≤ n``.

        For ARMAX models with coloured noise:
            A(z) y[k] = B(z) u[k] + C(z) e[k]

        where C(z) = z^n + noise_num[0] z^{n-1} + … (monic form).

        Parameters
        ----------
        num : array-like, shape (r+1,)
            Numerator coefficients in descending powers of z.
        den : array-like, shape (n+1,)
            Denominator coefficients in descending powers of z.
            ``den[0]`` is normalised to 1.
        form : {"observable", "controllable"}
            Canonical form to use.  Default: ``"observable"``.
        noise_num : array-like, shape (n,), optional
            Noise numerator coefficients (C-polynomial after leading 1).
            If provided, sets the G (noise input) matrix for ARMAX models.

        Returns
        -------
        SISORealization

        Raises
        ------
        ValueError
            If ``den[0]`` is not 1.0 (not monic) or if ``form`` is invalid.
        """
        num = np.asarray(num, dtype=float)
        den = np.asarray(den, dtype=float)

        # Validate denominator is monic
        if not np.isclose(den[0], 1.0):
            raise ValueError(
                f"Denominator must be monic (den[0] = 1.0), got den[0] = {den[0]}"
            )

        # Validate form
        if form not in ("observable", "controllable"):
            raise ValueError(
                f"form must be 'observable' or 'controllable', got '{form}'"
            )

        n = len(den) - 1  # order

        # Zero-pad numerator on the right if needed so len(num) == len(den)
        if len(num) < len(den):
            num = np.pad(num, (0, len(den) - len(num)), mode='constant')
        elif len(num) > len(den):
            raise ValueError(
                f"Numerator degree {len(num)-1} exceeds denominator degree {n}"
            )

        # Extract coefficients
        b = num  # b_0, b_1, ..., b_n
        a = den[1:]  # a_1, a_2, ..., a_n (skip a_0 = 1)

        b0 = b[0]
        D = np.array([[b0]])

        if form == "observable":
            # Observable canonical form
            # A has -a_i in first column, identity shifts
            A = np.zeros((n, n))
            A[:, 0] = -a  # First column: [-a_1, -a_2, ..., -a_n]^T
            if n > 1:
                A[:-1, 1:] = np.eye(n - 1)  # Super-diagonal identity

            # B column: [b_i - b_0 * a_i] for i = 1..n
            B = (b[1:] - b0 * a).reshape(n, 1)

            # C row: [1, 0, ..., 0]
            C = np.zeros((1, n))
            C[0, 0] = 1.0

        else:  # controllable
            # Controllable canonical form
            # A has -a_i in last column (reversed), identity shifts
            A = np.zeros((n, n))
            A[:, -1] = -a[::-1]  # Last column: [-a_n, -a_{n-1}, ..., -a_1]^T
            if n > 1:
                A[1:, :-1] = np.eye(n - 1)  # Sub-diagonal identity

            # C row: [b_i - b_0 * a_i]^T for i = n..1 (reversed)
            C = (b[1:] - b0 * a)[::-1].reshape(1, n)

            # B column: e_n = [0, ..., 0, 1]^T
            B = np.zeros((n, 1))
            B[-1, 0] = 1.0

        sys = cls(A=A, B=B, C=C, D=D)

        # Add noise input matrix if noise_num provided
        if noise_num is not None:
            noise_num = np.asarray(noise_num, dtype=float)
            if len(noise_num) != n:
                raise ValueError(
                    f"noise_num must have length {n} (order), got {len(noise_num)}"
                )

            # Compute G using same formula as B but with c_i coefficients
            # For ARMAX: C(z) = z^n + c_1 z^{n-1} + ... + c_n is monic
            # so b_0 equivalent is 1.0 for the noise polynomial
            c = noise_num  # c_1, c_2, ..., c_n

            if form == "observable":
                # G column: [c_i - a_i] for i = 1..n
                G = (c - a).reshape(n, 1)
            else:  # controllable
                # G column: [c_i - a_i]^T for i = n..1 (reversed)
                G = (c - a)[::-1].reshape(n, 1)

            sys._G = G
        else:
            sys._G = None

        return sys

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

        Uses the Ho-Kalman algorithm restricted to the SISO case:
        - Forms a Hankel matrix from h[1:] (h[0] = D is extracted separately)
        - Computes rank-n truncated SVD
        - Recovers (A, B, C) from observability/controllability factorization

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
        ValueError
            If h is too short for the desired order or if SVD fails.
        """
        h = np.asarray(h, dtype=float)
        T = len(h)

        # Extract D = h[0]
        D = np.array([[h[0]]])

        # Require T >= 2n + 1 for well-determined Hankel
        if T < 2 * n + 1:
            raise ValueError(
                f"Impulse response length {T} is too short for order {n}. "
                f"Require at least {2*n + 1} samples."
            )

        # Form block Hankel matrix from h[1:] with q = T // 2
        q = T // 2
        if q < n:
            raise ValueError(
                f"Block size q={q} < n={n}. Need longer impulse response."
            )

        # Build Hankel matrix: H_blk[i,j] = h[i+j+1] for i,j = 0..q-1
        # This is a q×q Hankel matrix
        H_blk = np.zeros((q, q))
        for i in range(q):
            for j in range(q):
                idx = i + j + 1  # Skip h[0], start from h[1]
                if idx < T:
                    H_blk[i, j] = h[idx]

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
        O_n = U_n @ sqrt_s  # q × n observability matrix
        R_n = sqrt_s @ V_n.T  # n × q controllability matrix

        # Extract system matrices
        # C = first row of observability matrix (ny=1)
        C = O_n[0:1, :]  # Shape (1, n)

        # B = first column of controllability matrix (nu=1)
        B = R_n[:, 0:1]  # Shape (n, 1)

        # A from shift-and-recover: A = O_n[0:q-1]^+ O_n[1:q]
        # For SISO case, O_n has shape (q, n)
        O_upper = O_n[0:q-1, :]  # First q-1 rows
        O_lower = O_n[1:q, :]    # Last q-1 rows (shifted down by 1)

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

    @property
    def G(self) -> np.ndarray | None:
        """Noise input matrix G ∈ ℝⁿˣ¹ (None if no noise model)."""
        return getattr(self, "_G", None)
