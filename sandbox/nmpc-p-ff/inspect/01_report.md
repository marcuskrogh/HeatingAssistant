# Iteration 1: approximate NMPC solve times

Synthetic two-room 2R2C, production `HouseThermalSDE` + implicit Euler, one heat pump per room. Live traces waived. SciPy SLSQP.

One 36 h cost evaluation: **0.048 s**.

SLSQP with a finite-difference Jacobian is brittle (some runs exit at u=0 with a false success). The table keeps only solves that cut the cost from about 3.5e6 to about 1 (in-band plan).

## Linearised QP baseline (`HeatingLinearisedMPC`)

| Horizon N | Cold [s] | Warm [s] |
|-----------|----------|----------|
| 100 | 0.269 | 0.298 |
| 144 | 0.687 | 0.753 |

## Nonlinear OCP (single shooting, 36 h look-ahead)

| T_NMPC | N | decisions | maxiter | cold [s] | warm [s] | status | J |
|--------|---|-----------|---------|----------|----------|--------|---|
| 15 min | 144 | 288 | 8 | 112 | 56 | ok | 1.08 |
| 1 h | 36 | 72 | 20 | 77 | 78 | maxiter | 1.03 |
| 2 h | 18 | 36 | 20 | 24 | 5.7 | ok | 1.15 |

All three slow periods finish in well under their own period on this VM. 15 min NMPC uses a large fraction of a 15 min ticker (~2 min cold). 1 h is ~80 s and was still improving at 20 iterations. QP stays under 1 s.

Plots: `01_solve_times.png`, `01_plan_1h.png` (1 h plan after 20 iterations).

Closed-loop P vs QP comfort/cost is **not** in this iteration.
