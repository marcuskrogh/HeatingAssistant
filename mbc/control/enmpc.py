"""
Economic Nonlinear Optimal Control Problem and CD-NMPC Controller (Ph.D. Ch. 9).

Unlike tracking MPC (which minimises a quadratic distance to a setpoint),
the economic optimal control problem minimises an economic objective comprising
a Lagrange (stage) term l_e(x, u, d) and an optional Mayer (terminal) term
V_f(x_N).

``EconomicOptimalControlProblem``
    Solves the finite-horizon NLP at each step:

        min_{u_0, …, u_{N-1}}   Σ_{k=0}^{N-1} l_e(x_k, u_k, d_k) + V_f(x_N)

        subject to:
            x_{k+1} = f̄(x_k, u_k, d_k)    (discretised model dynamics)
            u_min ≤ u[k] ≤ u_max            (hard input box)
            Δu_min ≤ u[k] − u[k−1] ≤ Δu_max (hard input ROM box)

    Soft state and output constraint violations are added as quadratic penalty
    terms in the objective:

        ρ_x (‖max(0, x[k] − x_max)‖² + ‖max(0, x_min − x[k])‖²)
        ρ_z (‖max(0, z[k] − z_max)‖² + ‖max(0, z_min − z[k])‖²)

    Additional terms (ROM penalty, linear input penalty) can be included via
    the ``S`` and ``c_u`` parameters.  The NLP is solved by
    ``scipy.optimize.minimize`` with the SLSQP method (default).

``CDNMPCController``
    Generic closed-loop CD-NMPC controller.  Composes any state estimator with
    any OCP that exposes ``solve(x0, d_trajectory, u_prev, p, t0)``:

      1. **Estimate**   x̂[k] ← estimator.step(y[k], u[k−1], d[k], p, t_k)
      2. **Optimise**   U*   ← ocp.solve(x̂[k], d_trajectory, u_seq_prev, p)
      3. **Apply**      u[k] = U*[0]

Reference:  Ph.D. thesis, Ch. 9.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ..models import ContinuousDiscreteModel


class EconomicOptimalControlProblem:
    """
    Economic nonlinear optimal control problem (Ph.D. Ch. 9).

    Solves the finite-horizon economic NLP from a given initial state.
    The predicted trajectory is computed by explicit Euler integration
    of the mean dynamics (no stochastic noise) over each sampling interval.

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete model used for prediction.
    N : int
        Prediction horizon (number of sampling intervals).
    lagrange : Callable[[np.ndarray, np.ndarray, np.ndarray], float] or None
        Economic stage cost ``l_e(x, u, d)`` — a scalar-valued function of
        state, input, and disturbance (Lagrange term).
    mayer : Callable[[np.ndarray], float] or None, optional
        Terminal cost ``V_f(x_N)`` (Mayer term).  ``None`` means no terminal
        cost.
    u_min : (nu,) ndarray or None, optional
        Hard lower bound on inputs.  ``None`` = unconstrained.
    u_max : (nu,) ndarray or None, optional
        Hard upper bound on inputs.  ``None`` = unconstrained.
    du_min : (nu,) ndarray or None, optional
        Hard lower bound on input rate-of-movement Δu[k] = u[k] − u[k−1].
        ``None`` = unconstrained.
    du_max : (nu,) ndarray or None, optional
        Hard upper bound on input ROM.  ``None`` = unconstrained.
    S : (nu, nu) ndarray or None, optional
        Input rate-of-movement cost matrix for the quadratic ROM penalty
        ‖Δu[k]‖²_S.  ``None`` disables the ROM penalty.
    c_u : (nu,) ndarray or None, optional
        Linear input penalty vector  c_uᵀ u[k].  ``None`` disables the term.
    x_min : (nx,) ndarray or None, optional
        Soft lower bound on state (penalised by ``rho_x``).  ``None`` disables.
    x_max : (nx,) ndarray or None, optional
        Soft upper bound on state.  ``None`` disables.
    rho_x : float, optional
        Quadratic penalty weight on soft state constraint violation.
        Default: 1e4.
    z_min : (nz,) ndarray or None, optional
        Soft lower bound on controlled output ``z = g(x, u, d, p, t)``
        (penalised by ``rho_z``).  ``None`` disables.
    z_max : (nz,) ndarray or None, optional
        Soft upper bound on controlled output.  ``None`` disables.
    rho_z : float, optional
        Quadratic penalty weight on soft output constraint violation.
        Default: 1e4.
    constraints : list of dict or None, optional
        Additional user-defined constraints in ``scipy.optimize.minimize``
        format (``{"type": "ineq"/"eq", "fun": ...}``).  These are combined
        with the built-in input box and ROM constraints.
    n_steps : int, optional
        Explicit-Euler integration sub-steps per sampling interval.
        Default: 10.
    solver : str, optional
        NLP solver passed to ``scipy.optimize.minimize``.  Default: ``"SLSQP"``.
    solver_options : dict or None, optional
        Options forwarded to the solver.  ``None`` uses solver defaults.
    dt : float or None, optional
        Sampling interval.  If ``None``, taken from ``model.dt`` if available,
        else ``1.0``.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        N: int,
        lagrange: Callable[[np.ndarray, np.ndarray, np.ndarray], float] | None = None,
        mayer: Callable[[np.ndarray], float] | None = None,
        *,
        u_min: np.ndarray | None = None,
        u_max: np.ndarray | None = None,
        du_min: np.ndarray | None = None,
        du_max: np.ndarray | None = None,
        S: np.ndarray | None = None,
        c_u: np.ndarray | None = None,
        x_min: np.ndarray | None = None,
        x_max: np.ndarray | None = None,
        rho_x: float = 1e4,
        z_min: np.ndarray | None = None,
        z_max: np.ndarray | None = None,
        rho_z: float = 1e4,
        constraints: list | None = None,
        n_steps: int = 10,
        solver: str = "SLSQP",
        solver_options: dict | None = None,
        dt: float | None = None,
    ) -> None:
        self._lagrange = lagrange
        self._mayer = mayer
        self._model = model
        self._N = N
        self._u_min = np.asarray(u_min, dtype=float) if u_min is not None else None
        self._u_max = np.asarray(u_max, dtype=float) if u_max is not None else None
        self._du_min = np.asarray(du_min, dtype=float) if du_min is not None else None
        self._du_max = np.asarray(du_max, dtype=float) if du_max is not None else None
        self._S = np.asarray(S, dtype=float) if S is not None else None
        self._c_u = np.asarray(c_u, dtype=float) if c_u is not None else None
        self._x_min = np.asarray(x_min, dtype=float) if x_min is not None else None
        self._x_max = np.asarray(x_max, dtype=float) if x_max is not None else None
        self._rho_x = float(rho_x)
        self._z_min = np.asarray(z_min, dtype=float) if z_min is not None else None
        self._z_max = np.asarray(z_max, dtype=float) if z_max is not None else None
        self._rho_z = float(rho_z)
        self._user_constraints = constraints if constraints is not None else []
        self._n_steps = n_steps
        self._solver = solver
        self._solver_options = solver_options
        self._dt: float = (
            float(dt) if dt is not None else float(getattr(model, "dt", 1.0))
        )

    @property
    def N(self) -> int:
        """Prediction horizon (number of sampling intervals)."""
        return self._N

    @property
    def nu(self) -> int:
        """Input dimension."""
        return self._model.nu

    def _predict_mean(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Integrate mean dynamics over one sampling interval (no noise).

        Uses ``n_steps`` explicit Euler sub-steps of size
        ``h = dt / n_steps``.
        """
        h = self._dt / self._n_steps
        x_cur = x.copy()
        t_cur = t
        for _ in range(self._n_steps):
            x_cur = x_cur + self._model.f(x_cur, u, d, p, t_cur) * h
            t_cur += h
        return x_cur

    def solve(
        self,
        x0: np.ndarray,
        d_trajectory: np.ndarray,
        u_prev: np.ndarray | None = None,
        p: np.ndarray | None = None,
        t0: float = 0.0,
    ) -> tuple[np.ndarray, float]:
        """
        Solve the economic OCP from initial state x0.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Current state estimate (initial condition for the NLP).
        d_trajectory : (N, nd) ndarray
            Predicted disturbance trajectory over the horizon.
        u_prev : (N, nu) ndarray or None, optional
            Previous optimal input sequence used as a warm start.
            Shifted by one step (last element repeated).  ``None``
            initialises from zeros.
        p : (nparams,) ndarray or None, optional
            Parameter vector.  ``None`` uses ``model.params``.
        t0 : float, optional
            Start time for the prediction horizon.  Default: 0.

        Returns
        -------
        u_opt : (N, nu) ndarray
            Optimal input sequence.
        cost : float
            Optimal economic cost.
        """
        from scipy.optimize import minimize, Bounds

        N = self._N
        nu = self._model.nu
        p_ = self._model.params if p is None else p

        # ── Previous input for ROM (Δu[0] = u[0] - u_prev[-1]) ──────────────
        u_prev_0 = u_prev[-1] if u_prev is not None else np.zeros(nu)

        # ── Warm start ────────────────────────────────────────────────────────
        if u_prev is not None:
            u0 = np.empty_like(u_prev)
            u0[:-1] = u_prev[1:]
            u0[-1] = u_prev[-1]
        else:
            u0 = np.zeros((N, nu))
        u0_flat = u0.ravel()

        # ── Objective function ─────────────────────────────────────────────────
        def objective(u_flat: np.ndarray) -> float:
            U = u_flat.reshape(N, nu)
            x = x0.copy()
            t = t0
            total = 0.0
            for k in range(N):
                u_k = U[k]
                u_km1 = U[k - 1] if k > 0 else u_prev_0

                # Lagrange (economic stage cost)
                if self._lagrange is not None:
                    total += float(self._lagrange(x, u_k, d_trajectory[k]))

                # ROM penalty
                if self._S is not None:
                    du_k = u_k - u_km1
                    total += 0.5 * float(du_k @ self._S @ du_k)

                # Linear input penalty
                if self._c_u is not None:
                    total += float(self._c_u @ u_k)

                # Propagate state
                x = self._predict_mean(x, u_k, d_trajectory[k], p_, t)
                t += self._dt

                # Soft state constraints
                if self._x_min is not None:
                    viol = np.maximum(0.0, self._x_min - x)
                    total += self._rho_x * float(viol @ viol)
                if self._x_max is not None:
                    viol = np.maximum(0.0, x - self._x_max)
                    total += self._rho_x * float(viol @ viol)

                # Soft output constraints
                if self._z_min is not None or self._z_max is not None:
                    z_k = self._model.g(x, u_k, d_trajectory[k], p_, t)
                    if self._z_min is not None:
                        viol = np.maximum(0.0, self._z_min - z_k)
                        total += self._rho_z * float(viol @ viol)
                    if self._z_max is not None:
                        viol = np.maximum(0.0, z_k - self._z_max)
                        total += self._rho_z * float(viol @ viol)

            # Mayer (terminal cost)
            if self._mayer is not None:
                total += float(self._mayer(x))

            return total

        # ── Build scipy Bounds for hard input box ─────────────────────────────
        if self._u_min is not None or self._u_max is not None:
            lb = (
                np.tile(self._u_min, N)
                if self._u_min is not None
                else np.full(N * nu, -np.inf)
            )
            ub = (
                np.tile(self._u_max, N)
                if self._u_max is not None
                else np.full(N * nu, np.inf)
            )
            bounds = Bounds(lb, ub)
        else:
            bounds = None

        # ── Build scipy constraints for hard ROM bounds ───────────────────────
        all_constraints: list = list(self._user_constraints)
        if self._du_min is not None or self._du_max is not None:
            du_lo = self._du_min
            du_hi = self._du_max

            def _make_rom_con(k_: int, lo: bool) -> Callable:
                def _con(u_flat: np.ndarray) -> np.ndarray:
                    u_k = u_flat[k_ * nu:(k_ + 1) * nu]
                    u_km1 = u_flat[(k_ - 1) * nu:k_ * nu] if k_ > 0 else u_prev_0
                    du = u_k - u_km1
                    return du - du_lo if lo else du_hi - du
                return _con

            for k in range(N):
                if du_lo is not None:
                    all_constraints.append(
                        {"type": "ineq", "fun": _make_rom_con(k, True)}
                    )
                if du_hi is not None:
                    all_constraints.append(
                        {"type": "ineq", "fun": _make_rom_con(k, False)}
                    )

        # ── Solve NLP ─────────────────────────────────────────────────────────
        result = minimize(
            objective,
            u0_flat,
            method=self._solver,
            bounds=bounds,
            constraints=all_constraints if all_constraints else (),
            options=self._solver_options,
        )

        u_opt = result.x.reshape(N, nu)
        cost = float(result.fun)
        return u_opt, cost

    def step(
        self,
        x0: np.ndarray,
        d_trajectory: np.ndarray,
        u_prev: np.ndarray | None = None,
        p: np.ndarray | None = None,
        t0: float = 0.0,
    ) -> np.ndarray:
        """
        Solve and return only the first optimal control action.

        This is the standard receding-horizon call for the OCP.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Current state estimate.
        d_trajectory : (N, nd) ndarray
            Predicted disturbance trajectory.
        u_prev : (N, nu) ndarray or None, optional
            Previous optimal sequence for warm-starting.
        p : (nparams,) ndarray or None, optional
            Parameter vector.  ``None`` uses ``model.params``.
        t0 : float, optional
            Start time.  Default: 0.

        Returns
        -------
        u0 : (nu,) ndarray
            First element of the optimal input sequence.
        """
        u_opt, _ = self.solve(x0, d_trajectory, u_prev, p=p, t0=t0)
        return u_opt[0]


# ── Generic CD-NMPC Controller ────────────────────────────────────────────────


class CDNMPCController:
    """
    Generic Continuous-Discrete Nonlinear MPC controller (Ph.D. Ch. 9).

    Composes **any** continuous-discrete state estimator with **any** OCP
    that exposes ``solve(x0, d_trajectory, u_prev, p, t0) → (u_opt, cost)``
    into a closed-loop receding-horizon controller.

    At each measurement time t_k the following steps are executed:

      1. **Estimate**  x̂[k] ← ``estimator.step(y[k], u[k−1], d[k], p, t_k)``
      2. **Optimise**  U*   ← ``ocp.solve(x̂[k], d_trajectory, u_seq_prev, p, t_k)``
      3. **Apply**     u[k] = U*[0]   (receding horizon)

    The estimator is expected to expose a ``step(ym, u, d, p, t)`` method that
    performs a combined predict-and-update and returns ``(x_hat, P)``.  This
    matches the interface of :class:`~mbc.estimation.ContinuousDiscreteEKF` and
    the other CD estimators in the ``mbc.estimation`` sub-package.

    The OCP must expose:

    - ``solve(x0, d_trajectory, u_prev, p, t0) → (u_opt, cost)``
    - ``N`` property (prediction horizon)
    - ``nu`` property (input dimension)

    This is satisfied by both :class:`CDTrackingOptimalControlProblem` and
    :class:`EconomicOptimalControlProblem`.

    Parameters
    ----------
    estimator : object with ``step(ym, u, d, p, t) → (x_hat, P)``
        State estimator.  Typically a
        :class:`~mbc.estimation.ContinuousDiscreteEKF` or any other CD
        estimator in the ``mbc.estimation`` sub-package.
    ocp : object with ``solve``, ``N``, ``nu``
        Optimal control problem (NLP solver).  Typically an
        :class:`EconomicOptimalControlProblem` or a
        :class:`CDTrackingOptimalControlProblem`.
    """

    def __init__(self, estimator, ocp) -> None:
        self._estimator = estimator
        self._ocp = ocp
        self._u_seq_prev: np.ndarray | None = None
        self._u_prev: np.ndarray = np.zeros(ocp.nu)

    def step(
        self,
        y: np.ndarray,
        d_trajectory: np.ndarray,
        p: np.ndarray | None = None,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Execute one CD-NMPC step.

        Parameters
        ----------
        y : (nym,) ndarray
            Current measurement ym[k].
        d_trajectory : (N, nd) ndarray
            Predicted disturbance trajectory over the horizon.
            ``d_trajectory[0]`` is the current disturbance d[k].
        p : (nparams,) ndarray or None, optional
            Parameter vector.  ``None`` uses an empty array (no parameters).
        t : float, optional
            Current measurement time t_k.  Default: 0.

        Returns
        -------
        u : (nu,) ndarray
            Optimal input u[k] to apply.
        """
        p_ = np.array([], dtype=float) if p is None else p
        d0 = d_trajectory[0]

        # Step 1: estimate
        x_hat, _ = self._estimator.step(y, self._u_prev, d0, p_, t)

        # Step 2: optimise
        u0 = self._ocp.step(x_hat, d_trajectory, self._u_seq_prev, p=p_, t0=t)

        # Update warm-start storage
        nu = self._ocp.nu
        N = self._ocp.N
        if self._u_seq_prev is None:
            self._u_seq_prev = np.zeros((N, nu))
        self._u_seq_prev[:-1] = self._u_seq_prev[1:]
        self._u_seq_prev[-1] = u0

        self._u_prev = u0
        return u0
