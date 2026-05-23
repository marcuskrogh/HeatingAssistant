# Performance Benchmarks

*Generated: 2026-05-23 17:46 UTC*

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
| studio-1room           | SLSQP    | qp             |       4.1 |         4.0 |      5.1 |   15 |
| studio-1room           | IPOPT    | qp             |       4.1 |         4.0 |      4.9 |   15 |
| two-bedroom-2room      | SLSQP    | qp             |       4.8 |         4.6 |      6.3 |   15 |
| two-bedroom-2room      | IPOPT    | qp             |       5.3 |         5.2 |      6.1 |   15 |
| full-house-5room       | SLSQP    | qp             |      11.7 |        11.4 |     13.4 |   15 |
| full-house-5room       | IPOPT    | qp             |      11.2 |        11.2 |     11.6 |   15 |
| full-house-5room-N16   | SLSQP    | qp             |      36.4 |        36.2 |     38.5 |   15 |
| full-house-5room-N16   | IPOPT    | qp             |      36.7 |        35.8 |     44.1 |   15 |

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
| MPC.compute                 | studio-1room           | SLSQP    |         3.9 |         4.0 |      2.3% (slower) |
| MPC.compute                 | studio-1room           | IPOPT    |         4.0 |         4.0 |      0.6% (slower) |
| MPC.compute                 | two-bedroom-2room      | SLSQP    |         4.5 |         4.6 |      2.5% (slower) |
| MPC.compute                 | two-bedroom-2room      | IPOPT    |         4.7 |         5.2 |     10.6% (slower) |
| MPC.compute                 | full-house-5room       | SLSQP    |        11.4 |        11.4 |     -0.3% (faster) |
| MPC.compute                 | full-house-5room       | IPOPT    |        11.4 |        11.2 |     -2.0% (faster) |
| MPC.compute                 | full-house-5room-N16   | SLSQP    |        36.4 |        36.2 |     -0.4% (faster) |
| MPC.compute                 | full-house-5room-N16   | IPOPT    |        35.6 |        35.8 |      0.6% (slower) |

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
