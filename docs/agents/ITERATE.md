# Iterate: Room view optimal trajectory still U=0 / 30°C free response

## Prior work
- Task: [SWD-411](https://marcusknielsen.atlassian.net/browse/SWD-411)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/626
- Spec context: `docs/agents/PLAN-nmpc-p-ff.md`, `docs/agents/ITERATE.md` (SWD-400 / SWD-405 / SWD-411)

## Problem
- Room view Forecast still spikes toward 30 °C while Planned Power stays at
  0 kW. The room is inside the comfort band, so the SWD-411 fast fallback
  also stays at `u = 0`.
- `accept_plan` required `J < 1e-3 * J(u=0)` (a 1000× improvement). A useful
  cooling plan that only cuts cost to 5–50% of the zero-heat cost was
  rejected. With no installed path, `compute()` plots the open-loop `U = 0`
  rollout (solar-driven heat spike).
- Room view refreshed forecasts only when `last_run_ts` changed, so an
  applied slow plan could sit unpublished in the snapshot until the next
  15 min tick.

## Clarifications
- Product accept intent is “not near the zero-heat cost”, not “cost near
  zero”. Keep rejecting idle false success at `J ≈ J(u=0)`.
- Do not switch NLP backends. Do not add I/D terms.

## Acceptance criteria
- Accept `U*` when it is in bounds, finite, and strictly better than
  `J(u=0)` by at least `ACCEPT_J_RATIO` (0.1%).
- Reject `J ≈ J(u=0)` (idle false success) and out-of-bounds / NaN plans.
- After accept, Forecast / Planned Power show the installed nonlinear path
  (cooling when a future solar/outdoor spike would leave the band).
- Inside the band with a true zero-heat optimum, planned power may stay 0.
- Room view refetches forecasts when `last_nmpc_ts` or `last_run_ts` changes.
- Tests, CalVer 2026.08.24, changelog, App sync.

## Out of scope
- Switching the NLP backend away from SLSQP.
- I-term / D-term or QP fallback.
- Changing default `K_p`, timing triple, or comfort-zone cost.

## Work packages
1. Accept NMPC plans that beat zero-heat and plot that trajectory (SWD-415)
2. Tests, CalVer, changelog, App sync for trajectory plot (SWD-416)

## Tracker
- Task: [SWD-414](https://marcusknielsen.atlassian.net/browse/SWD-414)
- Relates: [SWD-411](https://marcusknielsen.atlassian.net/browse/SWD-411)
- Sub-tasks: [SWD-415](https://marcusknielsen.atlassian.net/browse/SWD-415),
  [SWD-416](https://marcusknielsen.atlassian.net/browse/SWD-416)
- Branch: `cursor/swd-414-nmpc-optimal-trajectory-ce1e`

## Next
`/implement SWD-414` — Build the accept-bar and room-view forecast refresh
