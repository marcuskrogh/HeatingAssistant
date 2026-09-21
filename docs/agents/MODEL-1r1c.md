# Model: 1R1C plant for control (PE + EKF + NMPC + P)

## Problem statement

Replace the live 2R2C house (hidden wall node) with a 1R1C air-node plant
so parameter estimation, state estimation, and the two-rate controller
share one identifiable, fully observed thermal state. The objective is
control quality (trackable `T_ref`, honest `u_ref`), not physical wall
reconstruction or open-loop 2R2C RMSE.

## Notation

| Symbol | Meaning |
|--------|---------|
| `Ta` | Indoor air temperature (state, measured, controlled) |
| `Tout` | Outdoor air (disturbance) |
| `C` | Room thermal mass [J/K] (user-facing, same as today’s `thermal_mass`) |
| `R` | Steady-state resistance to outdoors [K/W] (`r_external`) |
| `Qa` | Heater/cooler thermal power on air [W] |
| `Qsol` | Window solar gain [W], scaled by identified `s` |
| `q_int` | Internal gain [W] |
| `b` | Slow air bias / disturbance observer [K] (optional offset state) |
| `phi` | Emitter filter state (actuator lag; keep if the source is filtered) |
| `u` | Heater fraction |
| `ym` | Air measurement |

Dropped from the live plant: `Tw`, `c_air_fraction`, `r_aw_fraction`,
`Tw0`, `SOLAR_WALL_FRACTION`, Kalman wall gain.

## Formulation

Plant (one room; house is the stacked rooms plus air–air `R_ij`):

```text
C * dTa/dt = Qa(u, Tout)
           + s * Qsol
           + q_int
           + (Tout - Ta) / R_eff
           + sum_j (Ta_j - Ta) / R_ij
           + Qsky_air

R_eff^{-1} = R^{-1} + sky_ua + thermal_bridge   # outdoor UA on air
Qsky_air   = -sky_ua * dT_sky * clear_fraction  # former wall sky term

ym = Ta + b + v
```

`Qa` stays the existing heat-pump map (`smooth_thermal_power` when the
source can cool). `phi` dynamics unchanged: `dphi/dt = (u - phi) / tau`.

SDE for the CD-Kalman filter:

```text
dx = f(x, u, d) dt + sigma dw
x  = [Ta (n), phi (m), b (n)]     # no Tw block
hm = Ta + b
```

Equal-kelvin `sigma_w` on air only. No wall diffusion row.

PE decision vector (per room, shared structure across windows):

```text
theta = { C, R, s, alpha, q_int, ua_open? }
# not in theta: c_air, r_aw, Tw0, solar_wall split
```

NMPC: same two-rate law. `x0` is the EKF air (+ `phi`, `b`). `T_ref` is
still the mean air path. P-law unchanged:

```text
u = clip(u_ref + Kp * (T_ref - Ta_hat), u_min, u_max)
```

Steady-state invariant (unchanged from the 1R1C era):

```text
Ta_ss = Tout + Q * R     # with sky/bridge folded into R_eff as above
```

## Assumptions

- One thermal state per room is enough for price-aware comfort control.
- Unmodelled envelope lag is absorbed by `b` / `q_int` and by P tracking.
- Inter-room `R_ij` couples air nodes (not a hidden wall mesh).
- Config keys `c_air_fraction`, `r_aw_fraction`, `wall_temperature` load
  and are ignored (same pattern as slab-era kwargs).

## Algorithmic choices

- Live PE, EKF, NMPC, P, and room plots all use this 1R1C SDE. No
  parallel 2R2C “truth” model in production.
- Room Temperature plot drops Wall / Wall Forecast series.
- Do not switch filter class (UKF/MHE). 1R1C air is linear-Gaussian
  given `u`, `d`.

## Numerical considerations

- `HouseModel` state size `n` (was `2n`). Integrator and Jacobians stay
  implicit Euler / analytic `dfdx`.
- Dual package tree: `heatingassistant/` and
  `heating_assistant/heatingassistant/`.

## Open items

- None for the plant class (user chose 1R1C). Display of leftover PE
  split fields in old saved configs is ignored-on-load.

## Role in pipeline

Finding docs for `/define` and `/implement`. Supersedes
`MODEL-state-estimation.md` (`Kw = 0` / small `Q_wall` on 2R2C).

## Tracker

- Task: [SWD-570](https://marcusknielsen.atlassian.net/browse/SWD-570)
- Relates: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564)
- Branch: `cursor/constrained-cdkf-wall-5de1`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/691 (define opened)
- Artifact: `docs/agents/MODEL-1r1c.md`

## Next

`/implement SWD-570` — 1R1C production swap
