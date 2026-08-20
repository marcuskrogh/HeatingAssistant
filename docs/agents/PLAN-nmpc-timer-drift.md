# Implementation plan: Drift-free NMPC / control schedule

## Summary
- Pressing Start starts the two-hour NMPC countdown, then the ring resets
  when the slow NLP finishes. `_note_nmpc_cycle_complete` stamps
  `last_nmpc_ts = time.time()` at worker completion, so each solve shifts
  the grid by the NLP duration.
- The two-rate clock must be drift-free: slow solves at `t0 + n * period`,
  fast ticks at `t0 + k * T_s`, independent of how long a cycle takes.

## Scope / Decisions / Constraints
**In**
- Wall-clock epoch `t0` when the operator presses Start (`system_enabled`
  False → True). Publish that stamp as `last_nmpc_ts` and `last_run_ts`.
- Do not overwrite `last_nmpc_ts` when the NMPC worker finishes (accept or
  reject). Do not overwrite `last_run_ts` at the end of a control cycle or
  on the extra post-accept cycle.
- Next slow solve is due at the next `t0 + n * nmpc_period` slot, not at
  finish + period. Drop `_nmpc_k >= M` as the slow-due signal (an extra
  fast cycle currently makes NMPC due one substep early).
- Fast ticker sleeps until the next grid time, not `now + T_s` after work.
- Idle-zero retries stay on the fast grid and must not move the UI epoch
  (separate attempt stamp).
- App restart while enabled keeps `t0`. Stop then Start re-anchors.
- Tests, CalVer, changelog, App package sync.

**Out**
- Changing NMPC period / substeps / horizon defaults.
- NLP solver, accept/reject bar, P-law, or watchdog duration.
- Redesigning the countdown rings (they already modulo from the stamp).

**Decisions**
- `last_nmpc_ts` / `last_run_ts` mean the schedule origin, not “worker
  finished at”. The existing UI `period - (now - stamp) % period` is
  correct once the stamp stays on the grid.
- First NMPC after Start is due immediately; the 2-hour ring still counts
  down to `t0 + period` while that first solve runs.
- Extra post-accept control publishes actuators without shifting the grid.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: bug
- Confidence: high
- Why: countdown and next-due time are wrong after an NLP; expected
  drift-free grid is known

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
  - sandbox: none
- Chain: implement → review-fix → ship
- Rationale: localized schedule stamp; unit tests catch the reset

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Prior: SWD-395 two-rate timing; SWD-400 countdown attrs

## Acceptance criteria
- Start: both rings originate at that wall-clock instant.
- NMPC finishing does not jump the 2-hour remaining time back to a full
  period.
- Slow due times are `t0 + n * nmpc_period` even when a solve lasts tens of
  seconds.
- Fast ticks stay on `t0 + k * T_s`; post-accept extra cycles do not move
  `last_run_ts`.
- Stop then Start sets a new epoch. Restart while enabled restores `t0`.
- Idle-zero retry still runs on the fast interval without moving the epoch.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. Wall-clock epoch: do not restamp timers when a solve finishes — [SWD-419](https://marcusknielsen.atlassian.net/browse/SWD-419)
2. Tests, CalVer, changelog, App sync — [SWD-420](https://marcusknielsen.atlassian.net/browse/SWD-420)

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-418](https://marcusknielsen.atlassian.net/browse/SWD-418)
- Sub-tasks: [SWD-419](https://marcusknielsen.atlassian.net/browse/SWD-419),
  [SWD-420](https://marcusknielsen.atlassian.net/browse/SWD-420)
- Relates: [SWD-400](https://marcusknielsen.atlassian.net/browse/SWD-400),
  [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395)
- Branch: `cursor/swd-418-nmpc-timer-drift-9728`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/628
- Classification: bug
- Workflow: fix-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/628
