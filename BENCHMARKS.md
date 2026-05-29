# Performance Benchmarks

*Generated: 2026-05-29 18:27 UTC*

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
| studio-1room           | SLSQP    | qp             |       5.6 |         5.5 |      6.5 |   15 |
| studio-1room           | IPOPT    | qp             |       5.2 |         5.2 |      5.7 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       5.7 |         5.7 |      6.4 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       5.7 |         5.6 |      5.8 |   15 |
| full-house-5room       | SLSQP    | qp             |      17.7 |        17.4 |     20.3 |   15 |
| full-house-5room       | IPOPT    | qp             |      17.3 |        17.4 |     18.0 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      48.4 |        48.3 |     48.8 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      48.9 |        48.7 |     49.6 |   15 |

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
| studio-1room           | IPOPT    | IPOPT          |    2382.8 |      2382.8 |   2382.8 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |   14251.1 |     14251.1 |  14251.1 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |   22929.7 |     22929.7 |  22929.7 |    1 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         7.8 |         5.5 |    -30.1% (faster) |
| MPC.compute                 | studio-1room           | IPOPT    |         8.0 |         5.2 |    -35.4% (faster) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         9.0 |         5.7 |    -36.7% (faster) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         9.9 |         5.6 |    -42.9% (faster) |
| MPC.compute                 | full-house-5room       | SLSQP    |        27.5 |        17.4 |    -36.8% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |        28.3 |        17.4 |    -38.6% (faster) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        68.0 |        48.3 |    -28.9% (faster) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        67.2 |        48.7 |    -27.5% (faster) |
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      4423.8 |      2382.8 |    -46.1% (faster) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |     25259.4 |     14251.1 |    -43.6% (faster) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |     44780.5 |     22929.7 |    -48.8% (faster) |

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
