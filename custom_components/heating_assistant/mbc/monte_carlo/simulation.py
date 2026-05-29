"""
Monte Carlo closed-loop simulation framework (Ph.D. Ch. 12).

Provides a structured way to run N_mc independent closed-loop trials of a
continuous-discrete stochastic system under a given controller and (optional)
state estimator.  Each trial proceeds as follows:

  1. Draw an initial state x0 from N(x0_mean, x0_cov).
  2. Loop over T measurement intervals:
     a. Simulate the plant one step forward via the simulator.
     b. Collect the noisy observation y_k = h(x_k, d_k) + v_k.
     c. Update the estimator (if provided) with y_k to obtain x̂_k.
     d. Query the controller for the next input u_k = controller.step(x̂_k, …).
  3. Record the full (x, y, u) trajectories and total cost.

The framework is controller- and estimator-agnostic: any object with a
``.step()`` method that matches the expected signature is accepted.

Reference:  Ph.D. thesis, Ch. 12.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import ContinuousDiscreteModel
from ..simulation.sde import SDESimulator
from ..simulation.sdae import SDAESimulator


@dataclass
class MonteCarloResult:
    """
    Container for results from a Monte Carlo closed-loop simulation.

    Attributes
    ----------
    X : (N_mc, T+1, nx) ndarray
        State trajectories.  X[i, 0] = x0 for trial i.
    Y : (N_mc, T, ny) ndarray
        Noisy output trajectories.
    U : (N_mc, T, nu) ndarray
        Applied input trajectories.
    costs : (N_mc,) ndarray
        Cumulative stage cost for each trial.
    """

    X: np.ndarray
    Y: np.ndarray
    U: np.ndarray
    costs: np.ndarray


class MonteCarloSimulation:
    """
    Closed-loop Monte Carlo simulation framework (Ph.D. Ch. 12).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Plant model (used for generating noisy observations).
    simulator : SDESimulator or SDAESimulator
        Numerical integrator for the plant dynamics.
    controller : object with ``.step(x_hat, d, ...)`` method
        Feedback controller.  Must return a (nu,) input array.
    estimator : object with ``.step(y, u, d, t)`` method or None, optional
        State estimator.  When ``None`` the true state is fed to the
        controller (perfect state information).
    stage_cost : callable (x, u, d) → float or None, optional
        Cost function accumulated at each step.  The cumulative per-trial
        cost is stored in ``MonteCarloResult.costs``.  When ``None``
        (default), all costs are zero.
    N_mc : int, optional
        Number of Monte Carlo trials.  Default: 100.
    seed : int or None, optional
        Base random seed.  Trial i uses seed ``seed + i``.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        simulator: SDESimulator | SDAESimulator,
        controller,
        estimator=None,
        stage_cost=None,
        N_mc: int = 100,
        seed: int | None = None,
    ) -> None:
        self._model = model
        self._simulator = simulator
        self._controller = controller
        self._estimator = estimator
        self._stage_cost = stage_cost
        self._N_mc = N_mc
        self._seed = seed

    def run(
        self,
        x0_mean: np.ndarray,
        x0_cov: np.ndarray,
        D: np.ndarray,
        T: int,
    ) -> MonteCarloResult:
        """
        Execute all N_mc Monte Carlo trials.

        Parameters
        ----------
        x0_mean : (nx,) ndarray
            Mean of the initial state distribution.
        x0_cov : (nx, nx) ndarray
            Covariance of the initial state distribution.
        D : (T, nd) ndarray
            Disturbance trajectory shared across all trials.
        T : int
            Number of measurement intervals (simulation horizon).

        Returns
        -------
        MonteCarloResult
        """
        nx = self._model.nx
        nu = self._model.nu
        nym = self._model.nym
        N_mc = self._N_mc

        # Get parameter vector (empty if not available)
        p = self._model.params

        # Check if simulator is SDAE (needs algebraic state y) or SDE
        is_sdae = isinstance(self._simulator, SDAESimulator)

        # Allocate result arrays
        X = np.zeros((N_mc, T + 1, nx))
        Y = np.zeros((N_mc, T, nym))
        U = np.zeros((N_mc, T, nu))
        costs = np.zeros(N_mc)

        # Create RNG for initial state sampling
        rng = np.random.default_rng(self._seed)

        # Run N_mc Monte Carlo trials
        for i in range(N_mc):
            # 1. Draw initial state from N(x0_mean, x0_cov)
            x0_i = rng.multivariate_normal(x0_mean, x0_cov)
            X[i, 0] = x0_i

            # Set random seed for this trial's simulator
            trial_seed = None if self._seed is None else self._seed + i
            self._simulator._rng = np.random.default_rng(trial_seed)

            # Initialize algebraic state for SDAE if needed
            if is_sdae:
                # For SDAE, we need to initialize the algebraic state y
                # Use zero as initial guess (subclass should handle properly)
                ny = self._simulator._model.ny
                y_i = np.zeros(ny)

            # Initialize state for simulation
            x_i = x0_i.copy()

            # Initialize estimator if provided
            if self._estimator is not None:
                # Reset estimator state to initial estimate
                # Note: This assumes estimator has _x_np and _P_np attributes
                # (standard for CD estimators in mbc.estimation)
                self._estimator._x_np = x0_i.copy()
                self._estimator._P_np = x0_cov.copy()

            # 2. Loop over T measurement intervals
            t = 0.0
            dt = self._simulator._dt

            for k in range(T):
                # a. Simulate the plant one step forward
                d_k = D[k] if D.shape[0] > 0 else np.array([])

                # Get control input first (we need u_k before simulating)
                # Use estimator output if available, otherwise true state
                if self._estimator is not None:
                    x_hat_k = self._estimator._x_np.copy()
                else:
                    x_hat_k = x_i.copy()

                # Query controller for input
                # Controller interface is flexible - try common signatures
                try:
                    # Try full signature: step(x_hat, d_trajectory, p, t)
                    # Build d_trajectory for remaining horizon
                    d_trajectory = D[k:] if k < T else D[-1:].reshape(1, -1)
                    u_k = self._controller.step(x_hat_k, d_trajectory, p=p, t=t)
                except TypeError:
                    try:
                        # Try simpler signature: step(x_hat, d)
                        u_k = self._controller.step(x_hat_k, d_k)
                    except TypeError:
                        # Fallback: just x_hat
                        u_k = self._controller.step(x_hat_k)

                U[i, k] = u_k

                # Simulate plant forward
                if is_sdae:
                    x_i, y_i = self._simulator.step(x_i, y_i, u_k, d_k, p, t)
                else:
                    x_i = self._simulator.step(x_i, u_k, d_k, p, t)

                X[i, k + 1] = x_i

                # b. Collect noisy observation y_k = h(x_k, d_k) + v_k
                # Generate measurement noise v_k ~ N(0, Rm)
                Rm = self._model.Rm
                v_k = rng.multivariate_normal(np.zeros(nym), Rm)

                # Measurement function
                if is_sdae:
                    ym_k = self._simulator._model.hm(x_i, y_i, u_k, d_k, p, t + dt) + v_k
                else:
                    ym_k = self._model.hm(x_i, u_k, d_k, p, t + dt) + v_k

                Y[i, k] = ym_k

                # c. Update estimator (if provided) with y_k to obtain x̂_k
                if self._estimator is not None:
                    # Estimator step signature: step(y, u, d, p, t)
                    self._estimator.step(ym_k, u_k, d_k, p, t + dt)

                # Accumulate stage cost if a cost function was provided
                if self._stage_cost is not None:
                    costs[i] += float(self._stage_cost(x_i, u_k, d_k))

                # Advance time
                t += dt

        return MonteCarloResult(X=X, Y=Y, U=U, costs=costs)
