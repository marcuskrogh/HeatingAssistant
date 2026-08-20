# Iteration 03: NMPC solve times

Synthetic two-room 2R2C, production `HouseThermalSDE` + implicit Euler, one heat pump per room. Live traces waived. SciPy SLSQP with analytic `dJ/dU` (`--analytic`). Analytic vs finite-difference Jacobian: max abs 30, relative **1.3e-5**.

One 36 h cost evaluation (1 h grid, u=0): **0.049 s**.

## Linearised QP baseline (`HeatingLinearisedMPC`)

| Horizon N | Cold [s] | Warm [s] |
|-----------|----------|----------|
| 100 | 0.270 | 0.254 |
| 144 | 0.682 | 0.697 |

## Nonlinear OCP (single shooting, 36 h look-ahead)

| T_NMPC | N | decisions | maxiter | cold [s] | warm [s] | cold status | warm status | cold nfev | cold nit | J |
|--------|---|-----------|---------|----------|----------|-------------|-------------|-----------|----------|---|
| 2 h | 18 | 36 | 80 | 22.03 | 7.71 | nonconverged | ok | 461 | 80 | 0.811 |

Plots: `03_solve_times.png`, `03_plan.png`.

Closed-loop P vs QP comfort/cost is not in this iteration.
