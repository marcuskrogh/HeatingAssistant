# Iterate: NMPC must choose negative heater power when cooling is allowed

## Prior work
- Task: [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/622
- Spec context: `docs/agents/PLAN-nmpc-p-ff.md`, `docs/agents/MODEL-nmpc-p-ff.md`

## Problem
- Heat-pump bounds are `[-1, 1]` and `HouseThermalSDE` cools at `u < 0`.
- SciPy SLSQP from the zero warm-start returns `U* = 0` whenever the
  optimum is negative (comfort cost/gradient ~1e5; SLSQP reports success
  after one evaluation). Heating from the same start still works because
  `u = 0` is the lower bound and the search direction is positive.

## Clarifications
- Confirmed on a cooling-capable heat pump, overheated room (28 °C vs
  21 ± 2 °C): `J(u=-1) ≪ J(0)`, analytic Jacobian matches finite
  differences, but SLSQP stays at `0`. Scaling `J` by `max(1, |J(u0)|)`
  finds `u* ≈ -0.83`. L-BFGS-B also finds cooling without scaling.
- Keep SLSQP (PLAN). Scale the NLP objective and Jacobian only; accept
  still uses unscaled `J`.

## Acceptance criteria
- Overheated room + cooling-capable heat pump: accepted `U*` has negative
  entries (from a zero warm-start).
- Heating-only electric heater: still commands heat when too cold.
- Source `u_min` / `u_max` still set the box bounds.
- Tests, CalVer 2026.08.20, changelog, App sync.

## Out of scope
- Switching the NLP backend away from SLSQP.
- Changing the accept ratio vs `J(u=0)`.
- I-term / D-term or QP fallback.

## Work packages
1. Scale NMPC NLP so SLSQP can choose negative `u` (SWD-401)
2. Tests, CalVer, App sync (SWD-402)

## Tracker
- Task: [SWD-400](https://marcusknielsen.atlassian.net/browse/SWD-400)
- Relates: [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395)
- Sub-tasks: [SWD-401](https://marcusknielsen.atlassian.net/browse/SWD-401),
  [SWD-402](https://marcusknielsen.atlassian.net/browse/SWD-402)
- Branch: `cursor/swd-400-nmpc-signed-u-46be`

## Next
`/implement SWD-400` — scale the NLP and add the cooling regression
