# Iteration 02: NMPC solve times

Synthetic two-room 2R2C, production `HouseThermalSDE` + implicit Euler, one heat pump per room. Live traces waived. SciPy SLSQP.

One 36 h cost evaluation (1 h grid, u=0): **0.051 s**.

## Linearised QP baseline (`HeatingLinearisedMPC`)

| Horizon N | Cold [s] | Warm [s] |
|-----------|----------|----------|
| 100 | 0.264 | 0.294 |
| 144 | 0.721 | 0.741 |

## Nonlinear OCP (single shooting, 36 h look-ahead)

| T_NMPC | N | decisions | maxiter | cold [s] | warm [s] | cold status | warm status | cold nfev | cold nit | J |
|--------|---|-----------|---------|----------|----------|-------------|-------------|-----------|----------|---|
| 2 h | 18 | 36 | 80 | 94.03 | 162.06 | ok | nonconverged | 1903 | 47 | 0.832 |

Plots: `02_solve_times.png`, `02_plan.png`.

Closed-loop P vs QP comfort/cost is not in this iteration.
