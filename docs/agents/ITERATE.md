# Iterate: Room view plots 15-minute steps instead of the 2-hour NMPC trajectory

## Prior work
- Task: [SWD-414](https://marcusknielsen.atlassian.net/browse/SWD-414)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/627
- Spec context: `docs/agents/PLAN-nmpc-p-ff.md`, `docs/agents/ITERATE.md` (SWD-414)

## Problem
- Controller Tuning preview shows the slow nonlinear model predictive
  control (NMPC) plan: two-hour input holds and a relatively smooth
  temperature path.
- Room view Forecast / Planned Power instead look like the 15-minute
  fast grid (jittery temperature, short power steps).
- After each 15-minute `compute()`, Ingress caches the fast-grid
  nonlinear rollout and outdoor-dependent display power, which
  overwrites the installed slow plan (`U*` zero-order hold + `T_ref`).

## Clarifications
- Keep the 15-minute EKF then P loop for actuators.
- Do not change NMPC timing defaults (2 h / 8 / 36 h).
- Preview and room view should share the same installed-plan series.

## Acceptance criteria
- After an accepted NMPC plan, a later 15-minute `compute()` still
  plots that plan: Planned Power holds for `nmpc_period` even when
  outdoor temperature varies; Forecast follows `T_ref`.
- Controller Tuning preview and room-view snapshots use the same
  power-run length (slow interval).
- Tests, CalVer 2026.08.25, changelog, App sync.

## Out of scope
- Changing the P gain or NMPC solver.
- Plotting a closed-loop P simulation over the horizon.

## Work packages
1. Keep Ingress plots on the installed 2-hour NMPC plan (SWD-422)
2. Tests, CalVer, changelog, App sync for 2-hour room plots (SWD-423)

## Tracker
- Task: [SWD-421](https://marcusknielsen.atlassian.net/browse/SWD-421)
- Relates: [SWD-414](https://marcusknielsen.atlassian.net/browse/SWD-414)
- Sub-tasks: [SWD-422](https://marcusknielsen.atlassian.net/browse/SWD-422),
  [SWD-423](https://marcusknielsen.atlassian.net/browse/SWD-423)
- Branch: `cursor/swd-421-room-view-nmpc-grid-7742`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/629

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/629
