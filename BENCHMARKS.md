# Performance Benchmarks

*Generated: 2026-05-25 14:14 UTC*

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
| studio-1room           | SLSQP    | qp             |       6.5 |         6.5 |      6.7 |   15 |
| studio-1room           | IPOPT    | qp             |       6.4 |         6.4 |      6.5 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       7.3 |         7.3 |      7.4 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       7.7 |         7.4 |      9.4 |   15 |
| full-house-5room       | SLSQP    | qp             |      16.5 |        16.4 |     18.2 |   15 |
| full-house-5room       | IPOPT    | qp             |      16.5 |        16.4 |     17.5 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      43.7 |        43.6 |     44.4 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      43.0 |        43.0 |     43.5 |   15 |

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
| studio-1room           | IPOPT    | IPOPT          |    1122.1 |      1122.1 |   1122.1 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |    1544.4 |      1544.4 |   1544.4 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |    3364.7 |      3364.7 |   3364.7 |    1 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         6.4 |         6.5 |      1.5% (slower) |
| MPC.compute                 | studio-1room           | IPOPT    |         6.4 |         6.4 |      0.5% (slower) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         7.3 |         7.3 |      0.7% (slower) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         7.3 |         7.4 |      0.9% (slower) |
| MPC.compute                 | full-house-5room       | SLSQP    |        16.8 |        16.4 |     -2.6% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |        16.9 |        16.4 |     -2.8% (faster) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        43.7 |        43.6 |     -0.2% (faster) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        42.3 |        43.0 |      1.6% (slower) |
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      1083.2 |      1122.1 |      3.6% (slower) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |      1599.0 |      1544.4 |     -3.4% (faster) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |      3416.8 |      3364.7 |     -1.5% (faster) |

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
