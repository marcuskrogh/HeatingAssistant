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
        N_mc: int = 100,
        seed: int | None = None,
    ) -> None:
        raise NotImplementedError(
            "MonteCarloSimulation.__init__ is not yet implemented."
        )

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

        Raises
        ------
        NotImplementedError
            Always — to be implemented in a future commit.
        """
        raise NotImplementedError(
            "MonteCarloSimulation.run is not yet implemented."
        )
