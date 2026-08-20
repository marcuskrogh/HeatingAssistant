# Iterate: NMPC planned power stays at 0 kW when cooling is needed

## Prior work
- Task: [SWD-400](https://marcusknielsen.atlassian.net/browse/SWD-400)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/623
- Spec context: `docs/agents/ITERATE.md` (SWD-400), `docs/agents/PLAN-nmpc-p-ff.md`

## Problem
- Room detail after the signed-u update still shows **Planned Power** flat at
  0 kW while the indoor forecast climbs to ~32 °C (setpoint ~23.5 °C) on a
  heat/cool minisplit.
- Isolated SLSQP on the production 36 h / 18-step grid *does* choose negative
  `U*` when solar drives the free response out of band. The live plot stayed at
  zero because:
  1. `compute()` caches the heating schedule **before** the NLP worker
     finishes, so Ingress keeps the open-loop (`U = 0`) trajectory until the
     next 15-minute control tick.
  2. An accepted all-zero plan blocked re-solves for a full NMPC period
     (2 h), so a later solar/outdoor overheat never got a new path.
  3. Room power meta never read `rated_cooling_power`, so the power chart
     had no cooling capacity span.

## Clarifications
- Keep SciPy SLSQP. Add a Cauchy (projected gradient) warm start and a
  signed mid-bound probe if SLSQP stays at `U ≈ 0`.
- Accept ratio vs `J(u=0)` unchanged.
- Do not switch NLP backend.

## Acceptance criteria
- Production timing (2 h / 8 / 36 h) + heat/cool heat pump + high solar:
  accepted `U*` is negative and Ingress `heating_schedule` is negative **as
  soon as the plan is applied** (no extra `compute()`).
- Indoor forecast after apply stays near the comfort band, not the 32 °C
  open-loop spike.
- An installed all-zero plan is treated as idle: NMPC is due again (rate-limited
  to one fast step).
- Heating-only electric still heats when cold.
- Room power meta exposes rated cooling capacity.
- Tests, CalVer 2026.08.21, changelog, App sync.

## Out of scope
- Switching the NLP backend away from SLSQP.
- Changing the accept ratio vs `J(u=0)`.
- I-term / D-term or QP fallback.

## Work packages
1. Refresh NMPC forecast on apply and retry idle zero plans (SWD-406)
2. Tests, CalVer, App sync for cooling plan plot (SWD-407)

## Tracker
- Task: [SWD-405](https://marcusknielsen.atlassian.net/browse/SWD-405)
- Relates: [SWD-400](https://marcusknielsen.atlassian.net/browse/SWD-400)
- Sub-tasks: [SWD-406](https://marcusknielsen.atlassian.net/browse/SWD-406),
  [SWD-407](https://marcusknielsen.atlassian.net/browse/SWD-407)
- Branch: `cursor/swd-405-nmpc-cooling-plan-46be`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/624

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/624
