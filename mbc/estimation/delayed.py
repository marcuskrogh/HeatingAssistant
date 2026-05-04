"""
Delayed-Observation Filter wrapper (README §1.2).

A transparent wrapper that adds per-channel reporting-delay handling to
**any** supported state estimator.  Its public interface is identical to
the wrapped estimator plus one optional ``delay`` keyword argument on
``update`` / ``step``.

Supported wrapped estimators
-----------------------------
Discrete-time (``record_action`` interface):
  - ``KalmanFilter``
  - ``CDKalmanFilter``

Continuous-discrete (``step`` / ``predict`` / ``update`` interface):
  - ``ContinuousDiscreteEKF``
  - ``ContinuousDiscreteUKF``
  - ``ContinuousDiscreteEnKF``
  - ``ContinuousDiscreteParticleFilter``

Algorithm (README §1.2)
------------------------
At each call to ``update`` / ``step``:

1. **Immediate update** — apply the wrapped estimator with all
   non-delayed channels (or the full mask when ``delay`` is ``None``).
   Append ``{x_hat, P, y, d, mask, u, ...}`` to the internal ring buffer
   (``deque``, ``maxlen = lag_max``).

2. **Delayed corrections** — for each channel ``i`` with ``delay[i] = τ > 0``
   (sorted by τ ascending so shorter lags are corrected first):

   a. Retrieve the posterior stored at buffer position ``-(τ+1)``
      (the state at the time the sample was taken, k−τ).
   b. Apply a **measurement-only** correction (no time-update) for
      channel ``i`` at that prior state.
   c. Re-propagate forward through buffer entries ``-(τ) … -1``,
      updating both the estimator state and the stored posteriors.

3. After all delayed channels are processed the estimator holds the
   fully corrected current estimate, and the buffer stores the updated
   posterior chain.

If ``delay[i] > lag_max`` or ``delay[i] >= buffer depth``, channel ``i``
is dropped and a ``RuntimeWarning`` is issued.
"""

from __future__ import annotations

import warnings
from collections import deque
from typing import Any

import numpy as np

from .._utils import _cvx_to_np


# ── Internal helpers ──────────────────────────────────────────────────────────


def _is_cvxopt(v) -> bool:
    """Return True if *v* is a cvxopt matrix."""
    try:
        from cvxopt import matrix as _cvx
        return isinstance(v, _cvx)
    except ImportError:
        return False


def _as_np1d(v) -> np.ndarray | None:
    """Convert a vector (numpy or cvxopt column) to a 1-D numpy float array."""
    if v is None:
        return None
    if _is_cvxopt(v):
        n = v.size[0] * v.size[1]
        return np.array([float(v[i]) for i in range(n)])
    return np.asarray(v, dtype=float).ravel().copy()


def _as_np2d(M) -> np.ndarray:
    """Convert a matrix (numpy or cvxopt) to a 2-D numpy float array."""
    if _is_cvxopt(M):  # cvxopt matrix stored column-major
        rows, cols = M.size
        return np.array(list(M), dtype=float).reshape((rows, cols), order="F")
    return np.asarray(M, dtype=float).copy()


def _to_cvx_col(arr: np.ndarray):
    """Convert a 1-D numpy array to a cvxopt (n, 1) column vector."""
    from cvxopt import matrix as cvx_matrix

    return cvx_matrix(arr.tolist(), (len(arr), 1))


def _to_cvx_mat(arr: np.ndarray):
    """Convert a 2-D numpy array to a cvxopt matrix (column-major)."""
    from cvxopt import matrix as cvx_matrix

    n, m = arr.shape
    return cvx_matrix(arr.ravel(order="F").tolist(), (n, m))


# Small regularisation added to P when a Cholesky factor is needed but the
# matrix is near-singular.  Used when restoring ensemble / particle state.
_CHOLESKY_REGULARIZATION: float = 1e-10


def _ny_of(y) -> int:
    """Return the number of output channels in a measurement vector."""
    if _is_cvxopt(y):
        return y.size[0]
    return int(np.asarray(y).ravel().shape[0])


# ── DelayedObservationFilter ──────────────────────────────────────────────────


class DelayedObservationFilter:
    """
    Transparent wrapper that adds per-channel reporting-delay handling to
    any state estimator (README §1.2).

    Parameters
    ----------
    estimator : any supported estimator
        Wrapped estimator.  Discrete-time estimators must expose
        ``update(y, d, mask)`` and ``record_action(u)``; continuous-discrete
        estimators must expose ``predict(u, d, p, t)``,
        ``update(y, u, d, p, mask)``, and ``step(y, u, d, p, t, mask)``.
    lag_max : int
        Maximum delay in sampling steps the buffer can accommodate.
        Channels with a delay exceeding ``lag_max`` or the current buffer
        depth are silently dropped and a ``RuntimeWarning`` is emitted.
    """

    def __init__(self, estimator: Any, lag_max: int) -> None:
        self._est = estimator
        self._lag_max = lag_max
        # Ring buffer: stores one dict per sampling step.
        self._buf: deque[dict] = deque(maxlen=lag_max)
        # Discrete-time flavour detected by the presence of record_action.
        self._is_discrete: bool = hasattr(estimator, "record_action")

    # ── Properties delegated to the wrapped estimator ─────────────────────────

    @property
    def x_hat(self):
        """Current state estimate (delegated to wrapped estimator)."""
        return self._est.x_hat

    @property
    def P(self):
        """Current covariance (delegated to wrapped estimator)."""
        return self._est.P

    @property
    def last_innovation(self):
        """Most recent innovation (delegated if available)."""
        return getattr(self._est, "last_innovation", None)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _get_state(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(x_hat, P)`` as numpy arrays."""
        return _as_np1d(self._est.x_hat), _as_np2d(self._est.P)

    def _set_state(self, x_np: np.ndarray, P_np: np.ndarray) -> None:
        """Restore the wrapped estimator's internal ``(x_hat, P)`` state."""
        # Local imports avoid circular dependencies at module level.
        from .kalman import KalmanFilter
        from .cd_kalman import CDKalmanFilter
        from .ekf import ContinuousDiscreteEKF
        from .ukf import ContinuousDiscreteUKF
        from .enkf import ContinuousDiscreteEnKF
        from .pf import ContinuousDiscreteParticleFilter

        est = self._est
        if isinstance(est, (KalmanFilter, CDKalmanFilter, ContinuousDiscreteEKF)):
            # All three store state as numpy _x_np / _P_np.
            est._x_np = x_np.copy()
            est._P_np = P_np.copy()
        elif isinstance(est, ContinuousDiscreteUKF):
            est._x = x_np.copy()
            est._P = P_np.copy()
        elif isinstance(est, (ContinuousDiscreteEnKF, ContinuousDiscreteParticleFilter)):
            # Stochastic estimators: reinitialise ensemble / particles from
            # N(x_np, P_np).  This is an approximation but is the best
            # available restoration for sample-based methods.
            # Direct access to private attributes is intentional here:
            # the ensemble/particle state is only exposed through _nx, _N,
            # _rng, _X (and _w for PF), which are the canonical internal
            # fields of both implementations.
            try:
                nx, N = est._nx, est._N
                rng = est._rng
            except AttributeError as exc:
                raise TypeError(
                    f"DelayedObservationFilter: ensemble/particle estimator "
                    f"{type(est)!r} does not expose expected internal fields "
                    f"(_nx, _N, _rng): {exc}"
                ) from exc
            try:
                L = np.linalg.cholesky(P_np)
            except np.linalg.LinAlgError:
                L = np.linalg.cholesky(P_np + _CHOLESKY_REGULARIZATION * np.eye(nx))
            Z = rng.standard_normal((nx, N))
            est._X = x_np[:, None] + L @ Z
            if isinstance(est, ContinuousDiscreteParticleFilter):
                est._w = np.full(N, 1.0 / N)
        else:
            raise TypeError(
                f"DelayedObservationFilter: unsupported estimator type {type(est)!r}"
            )

    def _set_discrete_prev(
        self, u_np: np.ndarray | None, d_np: np.ndarray
    ) -> None:
        """
        Set ``_u_prev_np`` / ``_d_prev_np`` on discrete-time estimators so that
        the next ``update`` call uses the correct prior inputs for its
        internal predict step.
        """
        from .kalman import KalmanFilter
        from .cd_kalman import CDKalmanFilter

        est = self._est
        if isinstance(est, (KalmanFilter, CDKalmanFilter)):
            # Both now store previous inputs as numpy arrays.
            if u_np is not None:
                est._u_prev_np = u_np.copy()
            est._d_prev_np = d_np.copy()

    def _zero_u(self) -> np.ndarray:
        """Return a zero input vector of the appropriate dimension."""
        from .kalman import KalmanFilter
        from .cd_kalman import CDKalmanFilter

        est = self._est
        if isinstance(est, KalmanFilter):
            return np.zeros(est._model.n_u)
        if isinstance(est, CDKalmanFilter):
            return np.zeros(est._model.nu)
        return np.zeros(0)

    # ── Channel partitioning ──────────────────────────────────────────────────

    @staticmethod
    def _partition(
        ny: int,
        mask,
        delay: np.ndarray | None,
    ) -> tuple[list[bool] | None, list[tuple[int, int]]]:
        """
        Split output channels into an *immediate* group and a *delayed* group.

        Returns
        -------
        imm_mask : ``list[bool]`` or ``None``
            Active-channel mask for the immediate update.  ``None`` means
            all channels are immediate (``delay`` was ``None``).
        delayed : ``list[(channel_idx, tau)]``
            Delayed channels sorted ascending by ``tau``.
        """
        if delay is None:
            return mask, []

        delay_arr = np.asarray(delay, dtype=int)
        user_active = (
            np.ones(ny, dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
        )

        imm_mask = [
            bool(user_active[i]) and int(delay_arr[i]) == 0 for i in range(ny)
        ]
        delayed = sorted(
            [
                (i, int(delay_arr[i]))
                for i in range(ny)
                if user_active[i] and delay_arr[i] > 0
            ],
            key=lambda x: x[1],
        )
        return imm_mask, delayed

    # ── Measurement-only correction helpers ───────────────────────────────────

    def _correction_discrete(
        self,
        ch_idx: int,
        y_np: np.ndarray,
        x0_np: np.ndarray,
        P0_np: np.ndarray,
        ny: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Apply a **measurement-update-only** (no time-update / predict) for
        a single output channel on a discrete-time estimator.

        Calls the estimator's own ``filter()`` method directly to avoid
        the time-update that ``update()`` would otherwise perform.

        Parameters
        ----------
        ch_idx : int      — index of the channel being corrected.
        y_np   : (ny,)    — full observation vector (numpy).
        x0_np  : (n,)     — prior state (posterior at the sample time).
        P0_np  : (n, n)   — prior covariance.
        ny     : int      — total number of output channels.

        Returns
        -------
        x_upd : (n,) corrected state estimate.
        P_upd : (n, n) corrected covariance.
        """
        from .kalman import KalmanFilter
        from .cd_kalman import CDKalmanFilter

        est = self._est
        n = len(x0_np)

        if isinstance(est, KalmanFilter):
            C_full_np = est._model.Cm   # (ny, n) numpy ndarray
        elif isinstance(est, CDKalmanFilter):
            C_full_np = _cvx_to_np(est._model.C_cvx)  # (ny, n) from cvxopt
        else:
            raise TypeError(
                f"_correction_discrete: unexpected estimator type {type(est)!r}"
            )

        # Single-row C_i : (1, n) numpy
        C_i_np = C_full_np[[ch_idx], :]
        y_i_np = np.array([y_np[ch_idx]])

        # Temporarily replace R with the 1×1 sub-block R[i, i].
        R_orig = est._R_np
        R_i_np = np.array([[R_orig[ch_idx, ch_idx]]])
        est._R_np = R_i_np
        try:
            x_upd, P_upd = est.filter(y_i_np, x0_np, P0_np, C_i_np)
        finally:
            est._R_np = R_orig

        return x_upd, P_upd

    def _correction_cd(
        self,
        ch_idx: int,
        y_np: np.ndarray,
        x0_np: np.ndarray,
        P0_np: np.ndarray,
        u_np: np.ndarray,
        d_np: np.ndarray,
        p_np: np.ndarray | None,
        ny: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Apply a **measurement-update-only** for a single channel on a
        continuous-discrete estimator.

        Restores the estimator to ``(x0_np, P0_np)`` and then calls the
        estimator's own ``update`` with a single-channel boolean mask.

        Returns
        -------
        x_upd : (nx,) corrected state estimate.
        P_upd : (nx, nx) corrected covariance.
        """
        self._set_state(x0_np, P0_np)

        single_mask = np.zeros(ny, dtype=bool)
        single_mask[ch_idx] = True

        if p_np is not None:
            x_new, P_new = self._est.update(y_np, u_np, d_np, p_np, mask=single_mask)
        else:
            x_new, P_new = self._est.update(y_np, u_np, d_np, mask=single_mask)

        return np.asarray(x_new, dtype=float).ravel().copy(), np.asarray(P_new, dtype=float).copy()

    # ── Forward-replay helpers ────────────────────────────────────────────────

    def _replay_discrete(
        self,
        entries: list[dict],
        prev_u: np.ndarray | None,
        prev_d: np.ndarray,
    ) -> None:
        """
        Re-propagate the discrete-time estimator through *entries*.

        Each entry stores the original ``(y, d, mask, u)`` for that step.
        The estimator must already be at the corrected prior state before
        this method is called.  ``prev_u`` / ``prev_d`` are the inputs
        recorded immediately before the first entry (i.e., the inputs
        from the correction step's time).

        The method updates ``entry["x_hat"]`` and ``entry["P"]`` in-place.
        """
        for entry in entries:
            # Restore u_prev / d_prev so the internal predict step uses the
            # correct inputs for this interval.
            u_for_prev = (
                prev_u if prev_u is not None else self._zero_u()
            )
            self._set_discrete_prev(u_for_prev, prev_d)

            # Full predict + filter step — update() accepts numpy directly.
            self._est.update(entry["y"], entry["d"], mask=entry["mask"])

            # Snapshot the new posterior.
            x_np, P_np = self._get_state()
            entry["x_hat"] = x_np
            entry["P"] = P_np

            # Advance pointers.
            prev_u = entry["u"]
            prev_d = entry["d"]

    def _replay_cd(self, entries: list[dict]) -> None:
        """
        Re-propagate the continuous-discrete estimator through *entries*
        (predict then update for each entry).

        The estimator must already be at the corrected prior state.
        Updates ``entry["x_hat"]`` and ``entry["P"]`` in-place.
        """
        for entry in entries:
            u_e = entry["u"]
            d_e = entry["d"]
            p_e = entry.get("p")
            t_e = entry["t"]

            if p_e is not None:
                self._est.predict(u_e, d_e, p_e, t_e)
                self._est.update(entry["y"], u_e, d_e, p_e, mask=entry["mask"])
            else:
                self._est.predict(u_e, d_e, t_e)
                self._est.update(entry["y"], u_e, d_e, mask=entry["mask"])

            x_np, P_np = self._get_state()
            entry["x_hat"] = x_np
            entry["P"] = P_np

    # ── Core delayed-correction dispatch ─────────────────────────────────────

    def _apply_delayed_discrete(
        self,
        y_np: np.ndarray,
        delayed_chs: list[tuple[int, int]],
    ) -> None:
        """
        Apply delayed corrections for discrete-time estimators and rebuild
        the internal buffer with updated posteriors.
        """
        ny = len(y_np)
        buf = list(self._buf)  # mutable snapshot; dicts are shared by reference
        n_buf = len(buf)

        for ch_idx, tau in delayed_chs:
            if tau >= n_buf:
                warnings.warn(
                    f"DelayedObservationFilter: delay {tau} exceeds buffer "
                    f"depth {n_buf - 1}; dropping channel {ch_idx}.",
                    RuntimeWarning,
                    stacklevel=4,
                )
                continue

            prior = buf[-(tau + 1)]  # posterior at k − τ

            # b/c. Measurement-only correction at the k − τ posterior.
            x_upd, P_upd = self._correction_discrete(
                ch_idx, y_np, prior["x_hat"], prior["P"], ny
            )

            # Restore estimator to the corrected state.
            self._set_state(x_upd, P_upd)

            # d/e/f. Re-propagate forward through buf[−τ … −1].
            prev_u = prior["u"]  # action recorded after step k − τ
            prev_d = prior["d"]  # disturbance at step k − τ
            self._replay_discrete(buf[-tau:], prev_u, prev_d)

        # Rebuild deque from the mutated list.
        self._buf.clear()
        for e in buf:
            self._buf.append(e)

    def _apply_delayed_cd(
        self,
        y_np: np.ndarray,
        u_np: np.ndarray,
        d_np: np.ndarray,
        p_np: np.ndarray | None,
        ny: int,
        delayed_chs: list[tuple[int, int]],
    ) -> None:
        """
        Apply delayed corrections for CD estimators and rebuild the buffer.
        """
        buf = list(self._buf)
        n_buf = len(buf)

        for ch_idx, tau in delayed_chs:
            if tau >= n_buf:
                warnings.warn(
                    f"DelayedObservationFilter: delay {tau} exceeds buffer "
                    f"depth {n_buf - 1}; dropping channel {ch_idx}.",
                    RuntimeWarning,
                    stacklevel=4,
                )
                continue

            prior = buf[-(tau + 1)]  # posterior at k − τ

            # b/c. Measurement-only correction using the state at k − τ.
            # Use the disturbance and parameters stored at k − τ (the time
            # the sample was actually taken).
            self._correction_cd(
                ch_idx,
                y_np,
                prior["x_hat"],
                prior["P"],
                u_np=prior["u"],
                d_np=prior["d"],
                p_np=prior.get("p"),
                ny=ny,
            )

            # d/e/f. Re-propagate forward through buf[−τ … −1].
            self._replay_cd(buf[-tau:])

        # Rebuild deque.
        self._buf.clear()
        for e in buf:
            self._buf.append(e)

    # ── Discrete-time public interface ────────────────────────────────────────

    def update(
        self,
        y,
        d,
        mask=None,
        delay: np.ndarray | None = None,
    ):
        """
        Assimilate measurement ``y[k]`` for discrete-time estimators.

        Parameters
        ----------
        y : (l,) or (l,1) measurement vector (cvxopt column or numpy).
        d : (p,) or (p,1) disturbance vector.
        mask : list[bool], optional
            Active-output mask.  ``None`` activates all channels.
        delay : (ny,) int ndarray, optional
            Per-channel delay in sampling steps.  ``delay[i] = 0`` means
            the measurement arrived on time.  ``None`` is equivalent to
            all zeros.

        Returns
        -------
        x_hat : corrected state estimate (same type as wrapped estimator).
        """
        ny = _ny_of(y)
        imm_mask, delayed_chs = self._partition(ny, mask, delay)

        # 1. Immediate (zero-delay) update.
        x_hat = self._est.update(y, d, mask=imm_mask)
        x_np, P_np = self._get_state()

        entry: dict = {
            "x_hat": x_np,
            "P": P_np,
            "y": _as_np1d(y),
            "d": _as_np1d(d),
            "mask": imm_mask,
            "u": None,  # filled later by record_action
        }
        self._buf.append(entry)

        # 2. Delayed corrections.
        if delayed_chs:
            self._apply_delayed_discrete(_as_np1d(y), delayed_chs)
            x_hat = self._est.x_hat

        return x_hat

    def record_action(self, u) -> None:
        """
        Record the applied control action ``u[k]`` (discrete-time interface).

        Stores ``u`` in the most recent buffer entry so that the correct
        input is available for the predict step during delayed replay.
        """
        if self._buf:
            self._buf[-1]["u"] = _as_np1d(u)
        self._est.record_action(u)

    # ── Continuous-discrete public interface ──────────────────────────────────

    def predict(self, u, d, p=None, t: float = 0.0):
        """
        Prediction step for CD estimators — delegates to wrapped estimator.

        Parameters
        ----------
        u : control input.
        d : disturbance.
        p : parameter vector (pass ``None`` for estimators without parameters).
        t : current time.
        """
        if p is not None:
            return self._est.predict(u, d, p, t)
        return self._est.predict(u, d, t)

    def step(
        self,
        y,
        u,
        d,
        p,
        t: float,
        mask=None,
        delay: np.ndarray | None = None,
    ):
        """
        Combined predict + update step for continuous-discrete estimators.

        Parameters
        ----------
        y : (ny,) observation vector.
        u : (nu,) control input (applied over the previous interval).
        d : (nd,) disturbance.
        p : (nparams,) parameter vector.
        t : float — current time (start of integration interval).
        mask : (ny,) bool array or ``None`` — active output channels.
        delay : (ny,) int ndarray or ``None`` — per-channel delay in steps.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        ny = _ny_of(y)
        imm_mask, delayed_chs = self._partition(ny, mask, delay)

        p_np = np.asarray(p, dtype=float).ravel() if p is not None else None
        u_np = _as_np1d(u)
        d_np = _as_np1d(d)
        y_np = _as_np1d(y)

        # 1. Step with immediate channels.
        result = self._est.step(y, u, d, p_np, float(t), mask=imm_mask)
        x_np, P_np = self._get_state()

        entry: dict = {
            "x_hat": x_np,
            "P": P_np,
            "y": y_np,
            "u": u_np,
            "d": d_np,
            "p": p_np,
            "t": float(t),
            "mask": imm_mask,
        }
        self._buf.append(entry)

        # 2. Delayed corrections.
        if delayed_chs:
            self._apply_delayed_cd(y_np, u_np, d_np, p_np, ny, delayed_chs)
            x_np, P_np = self._get_state()
            result = (x_np.copy(), P_np.copy())

        return result
