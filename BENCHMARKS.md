# Performance Benchmarks

*Generated: 2026-06-12 19:12 UTC*

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
| studio-1room           | IPOPT    | IPOPT          |    3600.6 |      3600.6 |   3600.6 |    1 |
| two-bedroom-2room      | IPOPT    | SLSQP          |   14754.0 |     14754.0 |  14754.0 |    1 |
| full-house-5room       | IPOPT    | IPOPT          |    6530.9 |      6530.9 |   6530.9 |    1 |

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
| KalmanMLEstimator.estimate  | studio-1room           | IPOPT    |      5246.5 |      3600.6 |    -31.4% (faster) |
| KalmanMLEstimator.estimate  | two-bedroom-2room      | IPOPT    |     14869.6 |     14754.0 |     -0.8% (faster) |
| KalmanMLEstimator.estimate  | full-house-5room       | IPOPT    |      6242.4 |      6530.9 |      4.6% (slower) |

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
