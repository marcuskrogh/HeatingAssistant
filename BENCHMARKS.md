# Performance Benchmarks

*Generated: 2026-05-15 10:38 UTC*

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
| studio-1room           | SLSQP    | SLSQP          |      47.2 |        43.6 |    108.2 |   15 |
| studio-1room           | IPOPT    | SLSQP          |      47.3 |        43.6 |    108.9 |   15 |
| two-bedroom-2room      | SLSQP    | SLSQP          |     120.1 |       109.2 |    233.0 |   15 |
| two-bedroom-2room      | IPOPT    | SLSQP          |     120.0 |       108.9 |    233.1 |   15 |
| full-house-5room       | SLSQP    | SLSQP          |    5750.9 |      5630.2 |   6387.3 |   15 |
| full-house-5room       | IPOPT    | SLSQP          |    5743.0 |      5639.7 |   6394.8 |   15 |

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
2. Multi-start Nelder–Mead maximisation of the Kalman prediction-error
   decomposition log-likelihood (3 restarts from the prior + random perturbations)

History buffer: 60 steps (1-minute samples) of synthetic data.

| Scenario               |  mean (ms) | median (ms) | p95 (ms) |   n |
|------------------------|------------|-------------|----------|-----|


**Configurations:**

| Scenario | Rooms | Sources | Parameters estimated |
|----------|-------|---------|---------------------|
| `studio-1room` | 1 | 1 | C₁, R₁, Q_int₁ (3) |
| `two-bedroom-2room` | 2 | 2 | C₁₋₂, R₁₋₂, Q_int₁₋₂, R₁₂ (7) |
| `full-house-5room` | 5 | 5 | C₁₋₅, R₁₋₅, Q_int₁₋₅, R_ij (≥15) |

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
