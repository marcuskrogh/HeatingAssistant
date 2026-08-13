# Report: Combined vs separated/staged 2R2C parameter recovery

Offline synthetic bake-off for SWD-326. **No product winner is declared.**
Judge whether separated/staged recovery of true θ is worth the extra code.

## Truth

- thermal_mass = 8.000e+06 J/K
- r_external = 0.025 K/W
- solar_scale = 1.5
- power_scale (α) = 1.0
- Prior (wrong on purpose): C=4.000e+06, R=0.05, s=1.0

## Procedures

1. `combined_joint` — one window, shared θ, one T_w(t_0).
2. `separated_joint` — solar-off fragments plus heater-off+solar fragments; shared θ; one T_w(t_0) per fragment.
3. `separated_staged` — best-effort lock C,R from solar-off, solar scale from heater-off+solar, then remaining on the full window.

Estimator: existing open-loop `KalmanMLEstimator` (production PE). Plant: 2R2C `HouseModel`. App / UI unused.

## Relative |error| vs true θ

| Scenario | Procedure | OK | C | R_ext | solar_scale | α | notes |
|----------|-----------|----|---|-------|-------------|---|-------|
| strong_separable | combined_joint | yes | 94.9% | 2918.7% | 58.4% | 54.9% |  |
| strong_separable | separated_joint | yes | 61.8% | 6.9% | 11.7% | 8.8% |  |
| strong_separable | separated_staged | yes | 78.8% | 484.9% | 86.7% | 70.0% | locked=['r_external', 'solar_scale', 'thermal_mass'] |
| weaker | combined_joint | yes | 45.2% | 2279.3% | 48.0% | 18.2% |  |
| weaker | separated_joint | yes | 65.8% | 240.0% | 86.7% | 67.8% |  |
| weaker | separated_staged | yes | 18.5% | 2.4% | 86.7% | 134.0% | locked=['r_external', 'solar_scale', 'thermal_mass'] |
| mixed | combined_joint | yes | 115.1% | 1.2% | 38.1% | 29.0% |  |
| mixed | separated_joint | yes | 8.2% | 44.5% | 33.3% | 70.0% |  |
| mixed | separated_staged | yes | 8.2% | 44.5% | 30.5% | 12.1% | locked=['r_external', 'thermal_mass'] |

## Recovered values

| Scenario | Procedure | C | R_ext | solar_scale | α |
|----------|-----------|---|-------|-------------|---|
| strong_separable | combined_joint | 4.095e+05 | 0.7547 | 0.624 | 0.451 |
| strong_separable | separated_joint | 3.053e+06 | 0.0267 | 1.675 | 0.912 |
| strong_separable | separated_staged | 1.698e+06 | 0.1462 | 0.200 | 0.300 |
| weaker | combined_joint | 4.383e+06 | 0.5948 | 0.779 | 0.818 |
| weaker | separated_joint | 1.326e+07 | 0.0850 | 0.200 | 0.322 |
| weaker | separated_staged | 9.481e+06 | 0.0244 | 0.200 | 2.340 |
| mixed | combined_joint | 1.721e+07 | 0.0247 | 2.072 | 0.710 |
| mixed | separated_joint | 7.347e+06 | 0.0139 | 1.000 | 0.300 |
| mixed | separated_staged | 7.347e+06 | 0.0139 | 1.958 | 1.121 |

## Staging recipe (best-effort)

- Solar-off (`Q_solar` ≤ 1 W): identify envelope `thermal_mass` and `r_external`, then lock them.
- Heater-off and solar on (`u` ≤ 0.02 and `Q_solar` > 1 W): identify `solar_scale`, then lock it.
- Full combined window: remaining parameters (including heater α) with those locks.

## Open

Whether the gain (if any) is large enough to ship auto-separation in Parameter Estimation is a human decision on this report.

