# Performance Benchmarks

*Generated: 2026-06-14 19:35 UTC*

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
| studio-1room           | SLSQP    | qp             |      25.1 |        24.3 |     37.1 |   15 |
| studio-1room           | IPOPT    | qp             |      20.7 |        20.2 |     30.9 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |      21.1 |        23.4 |     29.7 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |      14.6 |        12.0 |     28.1 |   15 |
| full-house-5room       | SLSQP    | qp             |      39.6 |        41.3 |     74.5 |   15 |
| full-house-5room       | IPOPT    | qp             |      31.2 |        32.2 |     44.2 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |     145.0 |       146.0 |    198.9 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |     162.7 |       160.2 |    220.8 |   15 |

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
| studio-1room           | IPOPT    | IPOPT          |    6441.6 |      6441.6 |   6441.6 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |   31299.7 |     31299.7 |  31299.7 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |   12352.6 |     12352.6 |  12352.6 |    1 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         6.4 |        24.3 |    279.5% (slower) |
| MPC.compute                 | studio-1room           | IPOPT    |         6.2 |        20.2 |    226.5% (slower) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         7.0 |        23.4 |    234.9% (slower) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         6.9 |        12.0 |     74.4% (slower) |
| MPC.compute                 | full-house-5room       | SLSQP    |        12.9 |        41.3 |    220.4% (slower) |
| MPC.compute                 | full-house-5room       | IPOPT    |        13.3 |        32.2 |    142.2% (slower) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        98.9 |       146.0 |     47.6% (slower) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        66.2 |       160.2 |    141.9% (slower) |
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      3517.7 |      6441.6 |     83.1% (slower) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |     14524.0 |     31299.7 |    115.5% (slower) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |      6428.1 |     12352.6 |     92.2% (slower) |

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
