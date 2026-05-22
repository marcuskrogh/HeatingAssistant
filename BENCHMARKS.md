# Performance Benchmarks

*Generated: 2026-05-22 21:28 UTC*

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
| studio-1room           | SLSQP    | qp             |       2.1 |         2.1 |      2.2 |   15 |
| studio-1room           | IPOPT    | qp             |       2.0 |         2.0 |      2.1 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       2.5 |         2.5 |      2.7 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       2.5 |         2.5 |      2.6 |   15 |
| full-house-5room       | SLSQP    | qp             |       7.9 |         7.8 |      8.6 |   15 |
| full-house-5room       | IPOPT    | qp             |       7.8 |         7.8 |      7.9 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      22.0 |        21.9 |     22.9 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      22.3 |        22.2 |     23.0 |   15 |

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
| studio-1room           | IPOPT    | IPOPT          |    1047.4 |      1047.4 |   1047.4 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |    1485.4 |      1485.4 |   1485.4 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |    2367.2 |      2367.2 |   2367.2 |    1 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         2.1 |         2.1 |     -1.6% (faster) |
| MPC.compute                 | studio-1room           | IPOPT    |         2.1 |         2.0 |     -4.3% (faster) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         2.6 |         2.5 |     -2.4% (faster) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         2.6 |         2.5 |     -3.5% (faster) |
| MPC.compute                 | full-house-5room       | SLSQP    |         7.9 |         7.8 |     -0.7% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |         7.8 |         7.8 |      0.2% (slower) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        22.2 |        21.9 |     -1.3% (faster) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        21.8 |        22.2 |      1.6% (slower) |
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      1052.7 |      1047.4 |     -0.5% (faster) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |      1489.6 |      1485.4 |     -0.3% (faster) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |      2410.7 |      2367.2 |     -1.8% (faster) |

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
