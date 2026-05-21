# Performance Benchmarks

*Generated: 2026-05-21 05:39 UTC*

All timings are wall-clock milliseconds measured on the CI runner (single
process, single thread).  Each cell shows the result of running the
function the listed number of times (`n`) and computing the statistic
over those samples.  The first call of every MPC scenario is treated as a
warm-up and is **not** included in the timing samples.

---

## MPC active control step — `HeatingMPCController.compute()`

One control step consists of:
1. CD-EKF predict-update (integrate nonlinear drift + Riccati ODE, then Kalman gain)
2. CDTrackingOCP NLP solve via configured backend (IPOPT default, deterministic fallback to SLSQP)

| Scenario               | Solver req | Solver active  |  mean (ms) | median (ms) | p95 (ms) |   n |
|------------------------|------------|----------------|------------|-------------|----------|-----|
| studio-1room           | SLSQP    | SLSQP          |      54.1 |        33.0 |    129.0 |   15 |
| studio-1room           | IPOPT    | IPOPT          |     645.0 |       597.9 |   1188.1 |   15 |
| two-bedroom-2room      | SLSQP    | SLSQP          |      82.6 |        50.9 |    263.9 |   15 |
| two-bedroom-2room      | IPOPT    | IPOPT          |    2472.0 |      2393.6 |   3435.8 |   15 |
| full-house-5room       | SLSQP    | SLSQP          |     824.3 |       756.6 |   1206.7 |   15 |
| full-house-5room       | IPOPT    | IPOPT          |   28171.4 |     28087.0 |  29569.7 |   15 |
| full-house-5room-N16   | SLSQP    | SLSQP          |    5812.8 |      5382.8 |   9080.9 |   15 |

**Configurations:**

| Scenario | Rooms | Heat sources | MPC horizon | Inputs (u) |
|----------|-------|--------------|-------------|------------|
| `studio-1room` | 1 | 1 electric heater | 6 | 1 |
| `two-bedroom-2room` | 2 (connected) | 2 electric heaters | 6 | 2 |
| `full-house-5room` | 5 (interconnected) | 1 heat pump + 4 electric heaters | 8 | 5 |
| `full-house-5room-N16` | 5 (interconnected) | 1 heat pump + 4 electric heaters | **16** | 5 |

---

## Parameter estimation — `KalmanMLEstimator.estimate()`

One estimation run consists of:
1. Identifiability analysis over the history buffer
2. Multi-start IPOPT minimisation of negative Kalman prediction-error
   decomposition log-likelihood with analytical gradients (3 restarts)

History buffer: 60 steps (1-minute samples) of synthetic data.

| Scenario               | Solver req | Solver active  |  mean (ms) | median (ms) | p95 (ms) |   n |
|------------------------|------------|----------------|------------|-------------|----------|-----|


**Configurations:**

| Scenario | Rooms | Sources | Parameters estimated |
|----------|-------|---------|---------------------|
| `studio-1room` | 1 | 1 | C₁, R₁, Q_int₁ (3) |
| `two-bedroom-2room` | 2 | 2 | C₁₋₂, R₁₋₂, Q_int₁₋₂, R₁₂ (7) |
| `full-house-5room` | 5 | 5 | C₁₋₅, R₁₋₅, Q_int₁₋₅, R_ij (≥15) |

---

## Comparison vs previous `BENCHMARKS.md`

| Routine                     | Scenario               | Solver req | old median (ms) | new median (ms) | Δ median |
|-----------------------------|------------------------|------------|-----------------|-----------------|----------|
| MPC.compute                 | studio-1room           | SLSQP    |       247.1 |        33.0 |    -86.6% (faster) |
| MPC.compute                 | studio-1room           | IPOPT    |       249.0 |       597.9 |    140.1% (slower) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |       616.8 |        50.9 |    -91.7% (faster) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |       615.7 |      2393.6 |    288.8% (slower) |
| MPC.compute                 | full-house-5room       | SLSQP    |      4012.4 |       756.6 |    -81.1% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |      4012.3 |     28087.0 |    600.0% (slower) |

---

## Analysis: SLSQP vs IPOPT (mbc v0.1 with analytical Jacobians)

### SLSQP — dramatic speedup from analytical Jacobians

The previous baseline (old `BENCHMARKS.md`) was measured with **finite-difference Jacobians** (the
old mbc fallback).  With mbc v0.1 the EOCP calls `model.dfdu`, `model.dgmdx`, and `model.dgmdu`
directly for every NLP iteration, eliminating all FD calls.  Result: **81–92 % reduction** in
SLSQP solve time across all scenarios.

### IPOPT — slower than SLSQP due to convergence issues with L-BFGS

The old IPOPT rows in the previous file showed **IPOPT→SLSQP** (falling back, because cyipopt was
not available).  The new runs use **real IPOPT (cyipopt 1.7.0)** for the first time.

IPOPT is slower than SLSQP here for two reasons:

1. **L-BFGS Hessian + poor NLP scaling** — the soft-output slack penalty (`rho_z = 1e4`) creates
   an objective ≈ 10⁶–10⁷ while the input-energy term is O(10³), a 3–4 order-of-magnitude
   imbalance.  IPOPT's dual infeasibility (KKT conditions) is hard to satisfy in this regime with a
   limited-memory Hessian approximation, leading to 300 iterations at ≈ 2 ms/iter for the 1-room
   case and ≈ 28 s for the 5-room case.
2. **Interior-point method overhead** — for these small NLPs (58–380 decision variables) the
   interior-point overhead per iteration outweighs the benefit of second-order information.

**Consequence:** SLSQP remains the more efficient solver for the current NLP formulation unless
NLP scaling is improved (e.g. `nlp_scaling_method = "user-scaling"`) or the soft-penalty
coefficients are rebalanced.

The `full-house-5room-N16` IPOPT scenario was not measured (estimated > 60 s/call; impractical for
CI).

---

## Notes

- Timings include Python runtime overhead (numpy, scipy) but not module
  import time (the module is already loaded).
- Solver convergence time depends on the warm-start; the
  first call (warm-up) is typically the slowest and is excluded.
- The previous IPOPT rows in the comparison table showed **IPOPT→SLSQP** (fallback); the new
  IPOPT rows show **IPOPT→IPOPT** (real IPOPT, first measurement).
- Parameter estimation timing depends heavily on the number of identifiable
  parameters (which the estimator detects automatically from the data).
- Parameter estimation tests are marked `slow` and can be skipped in
  quick CI passes with `pytest -m "not slow"`.
- Run all benchmarks yourself:
  ```bash
  python -m pytest tests/test_performance.py -v -s
  ```
