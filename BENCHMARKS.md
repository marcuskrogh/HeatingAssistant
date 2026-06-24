# Performance Benchmarks

*Generated: 2026-06-24 04:26 UTC*

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
| studio-1room           | SLSQP    | qp             |       5.0 |         5.0 |      5.3 |   15 |
| studio-1room           | IPOPT    | qp             |       5.1 |         5.0 |      5.2 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       5.7 |         5.6 |      7.8 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       5.6 |         5.6 |      6.1 |   15 |
| full-house-5room       | SLSQP    | qp             |      10.1 |        10.0 |     10.5 |   15 |
| full-house-5room       | IPOPT    | qp             |      10.0 |        10.0 |     10.2 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      86.2 |        87.7 |    132.3 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      66.7 |        72.4 |     94.4 |   15 |

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

History buffer: 90 steps (1-minute samples) of synthetic data.

| Scenario               | Solver req | Solver active  |  mean (ms) | median (ms) | p95 (ms) |   n |
|------------------------|------------|----------------|------------|-------------|----------|-----|
| studio-1room           | IPOPT    | IPOPT          |    2669.6 |      2669.6 |   2669.6 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |   11188.9 |     11188.9 |  11188.9 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |   13996.0 |     13996.0 |  13996.0 |    1 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         6.6 |         5.0 |    -24.1% (faster) |
| MPC.compute                 | studio-1room           | IPOPT    |         6.6 |         5.0 |    -23.7% (faster) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         7.6 |         5.6 |    -26.5% (faster) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         7.9 |         5.6 |    -29.4% (faster) |
| MPC.compute                 | full-house-5room       | SLSQP    |        13.8 |        10.0 |    -27.2% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |        13.7 |        10.0 |    -26.8% (faster) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        70.4 |        87.7 |     24.5% (slower) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        59.3 |        72.4 |     22.2% (slower) |
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      3462.4 |      2669.6 |    -22.9% (faster) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |     14499.4 |     11188.9 |    -22.8% (faster) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |      6593.2 |     13996.0 |    112.3% (slower) |

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
