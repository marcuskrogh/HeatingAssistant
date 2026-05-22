# Performance Benchmarks

*Generated: 2026-05-22 21:43 UTC*

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
| studio-1room           | SLSQP    | qp             |       2.7 |         2.6 |      2.8 |   15 |
| studio-1room           | IPOPT    | qp             |       2.7 |         2.6 |      3.2 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       3.4 |         3.4 |      3.7 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       3.4 |         3.4 |      3.6 |   15 |
| full-house-5room       | SLSQP    | qp             |      10.0 |        10.0 |     10.3 |   15 |
| full-house-5room       | IPOPT    | qp             |       9.9 |         9.9 |     10.1 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      28.0 |        27.9 |     29.3 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      28.3 |        28.2 |     29.0 |   15 |

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
| studio-1room           | IPOPT    | IPOPT          |    1450.6 |      1450.6 |   1450.6 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |    2092.7 |      2092.7 |   2092.7 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |    4094.8 |      4094.8 |   4094.8 |    1 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         2.1 |         2.6 |     25.3% (slower) |
| MPC.compute                 | studio-1room           | IPOPT    |         2.0 |         2.6 |     30.6% (slower) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         2.5 |         3.4 |     35.5% (slower) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         2.5 |         3.4 |     35.8% (slower) |
| MPC.compute                 | full-house-5room       | SLSQP    |         7.8 |        10.0 |     28.0% (slower) |
| MPC.compute                 | full-house-5room       | IPOPT    |         7.8 |         9.9 |     26.7% (slower) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        21.9 |        27.9 |     27.4% (slower) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        22.2 |        28.2 |     27.0% (slower) |
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      1047.4 |      1450.6 |     38.5% (slower) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |      1485.4 |      2092.7 |     40.9% (slower) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |      2367.2 |      4094.8 |     73.0% (slower) |

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
