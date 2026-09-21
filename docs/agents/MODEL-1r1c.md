# Model: 1R1C plant for control (PE + EKF + NMPC)

## Problem statement

Replace the live 2R2C house (hidden wall node) with a 1R1C air-node plant
so parameter estimation, state estimation, and **receding-horizon MPC**
share one identifiable, fully observed thermal state. The objective is
control quality (honest `U*` on air), not wall reconstruction.

The live controller is **not** two-rate NMPC + P. Each sample: EKF, then
NMPC (or Linear MPC), then `u = U*[k]`. See `docs/agents/CONTROL.md`.

## Notation

| Symbol | Meaning |
|--------|---------|
| `Ta` | Indoor air temperature (state, measured, controlled) |
| `Tout` | Outdoor air (disturbance) |
| `C` | Room thermal mass [J/K] (`thermal_mass`) |
| `R` | Steady-state resistance to outdoors [K/W] (`r_external`) |
| `Qa` | Heater/cooler thermal power on air [W] |
| `Qsol` | Window solar gain [W], scaled by identified `s` |
| `q_int` | Internal gain [W] |
| `b` | Slow air bias [K] |
| `phi` | Emitter filter state (actuator lag) |
| `u` | Heater fraction = current plan sample `U*[k]` |
| `ym` | Air measurement |

Dropped from the live plant: `Tw`, `c_air_fraction`, `r_aw_fraction`,
`Tw0`, `SOLAR_WALL_FRACTION`.

## Formulation

Plant (one room; house stacks rooms plus air–air `R_ij`):

```text
C * dTa/dt = Qa(u, Tout)
           + s * Qsol
           + q_int
           + (Tout - Ta) / R_eff
           + sum_j (Ta_j - Ta) / R_ij
           + Qsky_air

R_eff^{-1} = R^{-1} + sky_ua + thermal_bridge
Qsky_air   = -sky_ua * dT_sky * clear_fraction

ym = Ta + b + v
```

`Qa` is the existing heat-pump map. `dphi/dt = (u - phi) / tau`.

SDE for the CD-Kalman filter:

```text
dx = f(x, u, d) dt + sigma dw
x  = [Ta (n), phi (m), b (n)]     # no Tw
hm = Ta + b
```

PE:

```text
theta = { C, R, s, alpha, q_int, ua_open? }
# not in theta: c_air, r_aw, Tw0
```

Control (live, unchanged by this plant swap):

```text
each sample:
  EKF(x, y, u_applied)
  U* = planner(x_hat, d_forecast)    # nmpc or linear
  u  = clip(U*[k], u_min, u_max)    # NOT u_ref + Kp*(T_ref - Ta)
```

Steady-state:

```text
Ta_ss = Tout + Q * R
```

## Assumptions

- One thermal state per room is enough for price-aware comfort MPC.
- Unmodelled envelope lag is absorbed by `b` / `q_int` and by replanning.
- Inter-room `R_ij` couples air nodes.
- Old split / wall config keys load and are ignored.

## Algorithmic choices

- Live PE, EKF, NMPC/Linear, and room plots use this 1R1C SDE.
- Room Temperature plot drops Wall / Wall Forecast.
- Do not restore a P tracker. Do not switch to UKF/MHE.

## Numerical considerations

- `HouseModel` state size `n` (was `2n`). Implicit Euler / analytic `dfdx`.
- Dual package tree: `heatingassistant/` and
  `heating_assistant/heatingassistant/`.

## Open items

- None for the plant class. Ignored-on-load for leftover split fields.

## Role in pipeline

Finding docs for implement. Supersedes `MODEL-state-estimation.md`.
Control-loop source of truth: `docs/agents/CONTROL.md`.

## Tracker

- Task: [SWD-570](https://marcusknielsen.atlassian.net/browse/SWD-570)
- Relates: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564)
- Branch: `cursor/constrained-cdkf-wall-5de1`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/691
- Artifact: `docs/agents/MODEL-1r1c.md`

## Next

`/implement SWD-570` — 1R1C production swap
