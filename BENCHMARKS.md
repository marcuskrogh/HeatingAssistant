# Performance Benchmarks

*Generated: 2026-05-20 16:51 UTC*

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
| studio-1room           | SLSQP    | SLSQP          |     352.5 |       247.1 |   1046.3 |   15 |
| studio-1room           | IPOPT    | SLSQP          |     356.7 |       249.0 |   1080.5 |   15 |
| two-bedroom-2room      | SLSQP    | SLSQP          |    1071.3 |       616.8 |   2405.8 |   15 |
| two-bedroom-2room      | IPOPT    | SLSQP          |    1068.4 |       615.7 |   2401.1 |   15 |
| full-house-5room       | SLSQP    | SLSQP          |    4781.5 |      4012.4 |  10137.7 |   15 |
| full-house-5room       | IPOPT    | SLSQP          |    4795.0 |      4012.3 |  10125.2 |   15 |

**Configurations:**

| Scenario | Rooms | Heat sources | MPC horizon | Inputs (u) |
|----------|-------|--------------|-------------|------------|
| `studio-1room` | 1 | 1 electric heater | 6 | 1 |
| `two-bedroom-2room` | 2 (connected) | 2 electric heaters | 6 | 2 |
| `full-house-5room` | 5 (interconnected) | 1 heat pump + 4 electric heaters | 8 | 5 |

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
| MPC.compute                 | studio-1room           | SLSQP    |       249.3 |       247.1 |     -0.9% (faster) |
| MPC.compute                 | studio-1room           | IPOPT    |       248.2 |       249.0 |      0.3% (slower) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |       616.5 |       616.8 |      0.1% (slower) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |       614.6 |       615.7 |      0.2% (slower) |
| MPC.compute                 | full-house-5room       | SLSQP    |      4053.2 |      4012.4 |     -1.0% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |      4039.7 |      4012.3 |     -0.7% (faster) |

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
