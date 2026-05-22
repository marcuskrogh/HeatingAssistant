# Performance Benchmarks

*Generated: 2026-05-22 16:59 UTC*

All timings are wall-clock milliseconds measured on the CI runner (single
process, single thread).  Each cell shows the result of running the
function the listed number of times (`n`) and computing the statistic
over those samples.  The first call of every MPC scenario is treated as a
warm-up and is **not** included in the timing samples.

---

## MPC active control step — `HeatingMPCController.compute()`

One control step consists of:
1. CD-EKF predict-update (integrate nonlinear drift + Riccati ODE, then Kalman gain)
2. Successive linearisation + ZOH discretisation of the nonlinear SDE model around the current operating point
3. Convex QP solve via `cvxopt` (deviation coordinates, `CDLinearizedMPCController`)

| Scenario               | Solver req | Solver active  |  mean (ms) | median (ms) | p95 (ms) |   n |
|------------------------|------------|----------------|------------|-------------|----------|-----|
| studio-1room           | SLSQP    | qp             |       2.0 |         1.9 |      2.0 |   15 |
| studio-1room           | IPOPT    | qp             |       2.0 |         2.0 |      2.0 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       2.4 |         2.3 |      2.8 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       2.4 |         2.3 |      2.9 |   15 |
| full-house-5room       | SLSQP    | qp             |       7.6 |         7.3 |     10.5 |   15 |
| full-house-5room       | IPOPT    | qp             |       7.4 |         7.4 |      7.9 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      26.6 |        26.6 |     28.2 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      26.4 |        26.0 |     27.8 |   15 |

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
| studio-1room           | IPOPT    | IPOPT          |    2572.5 |      2572.5 |   2572.5 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |    3742.2 |      3742.2 |   3742.2 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |    9318.2 |      9318.2 |   9318.2 |    1 |

**Configurations:**

| Scenario | Rooms | Sources | Parameters estimated |
|----------|-------|---------|---------------------|
| `studio-1room` | 1 | 1 | C₁, R₁, Q_int₁ (3) |
| `two-bedroom-2room` | 2 | 2 | C₁₋₂, R₁₋₂, Q_int₁₋₂, R₁₂ (7) |
| `full-house-5room` | 5 | 5 | C₁₋₅, R₁₋₅, Q_int₁₋₅, R_ij (≥15) |

---

## Comparison vs previous `BENCHMARKS.md`

Baseline is the original NLP-based controller (SLSQP, pre-optimization model).
Current figures are QP on the optimized model.

| Routine                     | Scenario               | Solver req | old median (ms) | new median (ms) | Δ median |
|-----------------------------|------------------------|------------|-----------------|-----------------|----------|
| MPC.compute                 | studio-1room           | SLSQP    |        38.6 |         1.9 |    -95.1% (faster) |
| MPC.compute                 | studio-1room           | IPOPT    |        40.5 |         2.0 |    -95.1% (faster) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |       148.3 |         2.3 |    -98.4% (faster) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |       136.0 |         2.3 |    -98.3% (faster) |
| MPC.compute                 | full-house-5room       | SLSQP    |      1754.2 |         7.3 |    -99.6% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |      1866.8 |         7.4 |    -99.6% (faster) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |      6789.9 |        26.6 |    -99.6% (faster) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |      6728.4 |        26.0 |    -99.6% (faster) |

---

## Notes

- Timings include Python runtime overhead (numpy, scipy) but not module
  import time (the module is already loaded).
- Solver convergence time depends on the warm-start; the
  first call (warm-up) is typically the slowest and is excluded.
- Parameter estimation timing depends heavily on the number of identifiable
  parameters (which the estimator detects automatically from the data).
- Parameter estimation tests are marked `slow` and can be skipped in
  quick CI passes with `pytest -m "not slow"`.
- Run all benchmarks yourself:
  ```bash
  python -m pytest tests/test_performance.py -v -s
  ```
