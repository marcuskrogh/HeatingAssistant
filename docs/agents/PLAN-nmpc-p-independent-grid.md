# Implementation plan: Independent NMPC / P grid + KPI loading

## Summary
- NEXT CONTROL and NEXT NMPC countdown rings must share the Start epoch so
  they line up at every slow slot (fast update is substepping of the NMPC
  period).
- Nonlinear model predictive control (NMPC) and the proportional (P)
  controller run independently on that grid. While NMPC is solving, P keeps
  the previous plan. P does not run an extra cycle when the NLP finishes, so
  the first 15-minute slot of a new NMPC period uses the old solution.
- Compute KPI cards show a loading animation while a solve is in progress,
  so the operator sees why values have not updated after the timers reset.

## Scope / Decisions / Constraints
**In**
- Both rings use `last_nmpc_ts` as the wall-clock origin. Publish derived
  `dt_s` and `nmpc_period_s`. Do not fall back to entity `last_updated`.
- On load, coerce `last_control_ts` / `last_run_ts` to the same epoch so
  persisted stamps cannot diverge.
- Ticker starts NMPC on the slow grid without waiting for P. P ticks on
  the fast grid and uses the last accepted `U*` / `T_ref` even when the
  NLP worker is busy.
- Do not request a control cycle when NMPC apply succeeds (reverses the
  extra P from SWD-411). Comfort fallback still runs on regular P ticks.
- Fast-step index follows wall-clock substeps of the current plan origin
  (slow slot start at accept), not NLP finish time.
- Publish `nmpc_computing` and `control_computing` on
  `mpc_performance`. Room and overview compute KPI gauges shimmer while
  either flag is true.
- Tests, CalVer, changelog, App package sync.

**Out**
- Changing NMPC period / substeps / horizon defaults.
- NLP solver, accept/reject bar, P gain, or watchdog duration.
- Redesigning countdown ring artwork.
- Faster than 5 s Ingress poll for the loading flag.

**Decisions**
- Class is a **tweak**: intentional two-rate scheduling + loading UX on
  the existing NMPC+P path; timer desync is the same grid contract.
- Extra post-accept P is removed even though heaters wait until the next
  15-minute tick for the new plan.
- Idle-zero NMPC retries stay on the fast grid without moving the epoch.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: tweak
- Confidence: high
- Why: bounded behaviour change to existing two-rate control and KPI
  cards; expected grid contract is known

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
  - sandbox: none
- Chain: implement → review-fix → ship
- Rationale: localized schedule + overlay; unit tests catch remaining
  times, independent dispatch, and computing flags

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Prior: SWD-418 drift-free epoch; SWD-411 extra P after apply; SWD-395
  two-rate timing; SWD-400 countdown attrs

## Acceptance criteria
- At every NMPC due instant both rings have just reset (control remaining
  equals `dt_s`, NMPC remaining equals `nmpc_period_s`). When NMPC
  remaining is less than `dt_s`, both rings show the same remaining time.
- NMPC finishing does not restamp either origin and does not run P.
- While NMPC is busy, a fast tick still publishes P commands from the
  previous plan.
- Compute KPI cards on Overview and room view show a loading animation
  while `nmpc_computing` or `control_computing` is true, and clear it
  when the snapshot updates after the solve.
- Stop then Start sets a new shared epoch. Restart while enabled keeps it.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. Shared grid + independent NMPC and P (no extra P after NLP) — [SWD-427](https://marcusknielsen.atlassian.net/browse/SWD-427)
2. Loading animation on compute KPI cards while solving — [SWD-428](https://marcusknielsen.atlassian.net/browse/SWD-428)
3. Tests, CalVer, changelog, App sync — [SWD-429](https://marcusknielsen.atlassian.net/browse/SWD-429)

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-426](https://marcusknielsen.atlassian.net/browse/SWD-426)
- Sub-tasks: [SWD-427](https://marcusknielsen.atlassian.net/browse/SWD-427),
  [SWD-428](https://marcusknielsen.atlassian.net/browse/SWD-428),
  [SWD-429](https://marcusknielsen.atlassian.net/browse/SWD-429)
- Relates: [SWD-418](https://marcusknielsen.atlassian.net/browse/SWD-418),
  [SWD-411](https://marcusknielsen.atlassian.net/browse/SWD-411)
- Branch: `cursor/swd-426-nmpc-p-grid-7e18`
- PR: —
- Classification: tweak
- Workflow: delta-fast

## Next
`/implement SWD-426` — Build per PLAN.md workflow binding (same branch/PR)
