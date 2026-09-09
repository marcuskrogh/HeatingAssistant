# Iterate: Compute overlay and KPIs lag after NMPC/P finish

## Prior work
- Task: [SWD-513](https://marcusknielsen.atlassian.net/browse/SWD-513)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/670 (`16319e7d`)
- Spec context: docs/agents/ITERATE-next-compute-overlay-cycle.md, docs/agents/PLAN-next-compute-timers-computing.md

## Problem
- NEXT CONTROL / NEXT NMPC spin stops after the short wrap catch-up while
  NMPC can still be solving.
- Load KPIs, optimal trajectories, and room plots stay on the previous cycle
  for ~30 s (next P publish / Ingress poll) after the solver has finished.
- The ticker publishes status on the P cycle, then starts the NMPC worker.
  Rejected solves never published idle flags or `nmpc_result_ts`. Accepted
  solves published status while `nmpc_computing` was still true.

## Clarifications
- Overlay must follow the runtime computing flags for the real solve window.
- The short wrap cap from SWD-513 stays: idle skipped workers must not flash
  for 90+ seconds.

## Acceptance criteria
- NMPC start publishes status with `nmpc_computing` true.
- NMPC finish (accept, reject, or error) clears the flag, stamps
  `nmpc_result_ts` / `last_nmpc_duration_s`, then publishes immediately.
- Overlay stays on while the matching flag is true after wrap catch-up.
- Ingress poll tightens while a solver flag is true so KPIs and plots refresh
  on the falling edge instead of waiting for the next 5–30 s cycle.
- Panel state updates when `mpc_performance` attributes change in place.
- Tests, CalVer, changelog, App package sync.

## Out of scope
- NLP, accept/reject policy, NMPC/P period lengths, new HTTP/WS endpoints.

## Work packages
1. Publish compute status at NMPC start/end; App poll + panel attr detect (SWD-520)
2. Tests, CalVer, changelog, App sync (SWD-521)

## Tracker
- Task: [SWD-519](https://marcusknielsen.atlassian.net/browse/SWD-519)
- Relates: [SWD-513](https://marcusknielsen.atlassian.net/browse/SWD-513)
- Sub-tasks: [SWD-520](https://marcusknielsen.atlassian.net/browse/SWD-520), [SWD-521](https://marcusknielsen.atlassian.net/browse/SWD-521)
- Branch: `cursor/swd-519-compute-refresh-b94a`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/671

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/671
