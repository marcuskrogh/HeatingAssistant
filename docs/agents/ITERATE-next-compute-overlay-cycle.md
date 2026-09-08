# Iterate: Next compute overlay toggles while computing is no

## Prior work
- Task: [SWD-494](https://marcusknielsen.atlassian.net/browse/SWD-494)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/662 (`caab25b`)
- Spec context: docs/agents/PLAN-next-compute-timers-computing.md

## Problem
- NEXT CONTROL / NEXT NMPC rings enter a cycle: the computing animation
  toggles on and off, expanded detail shows Computing: no, and no new
  optimal NMPC result arrives.
- SWD-494 wrap fallback treats the first 90–600 s of an NMPC slot as
  computing whenever the result stamp is not in the current slot, even
  when `nmpc_computing` / `control_computing` are false and no worker ran.
- Overview and room pages then paint overlay from those raw flags on every
  HA state update. The 1 Hz tick turns wrap overlay back on. Detail rows
  read the flags, so they stay no.

## Acceptance criteria
- Overlay is on when the matching runtime flag is true, or only for a short
  post-wrap catch-up (poll gap, a few seconds) until the snapshot can be
  trusted.
- Pages do not paint overlay from raw flags against `countdownIsComputing`.
- With Computing: no and a stale or missing result stamp, overlay is off
  after the catch-up — not for 90+ seconds.
- Flag true still shows overlay independently per ring. Stopped system has
  no overlay.
- Immediate post-wrap overlay (first 1–2 s) remains so a 5 s poll is not
  required to start the chrome.
- Tests, CalVer, changelog, App package sync.

## Out of scope
- NMPC / P periods, NLP, accept/reject, new HTTP/WS endpoints.

## Work packages
1. Single-source overlay + short wrap cap (SWD-514)
2. Tests, CalVer, changelog, App sync (SWD-515)

## Tracker
- Task: [SWD-513](https://marcusknielsen.atlassian.net/browse/SWD-513)
- Relates: [SWD-494](https://marcusknielsen.atlassian.net/browse/SWD-494)
- Sub-tasks: [SWD-514](https://marcusknielsen.atlassian.net/browse/SWD-514), [SWD-515](https://marcusknielsen.atlassian.net/browse/SWD-515)
- Branch: `swd-513-next-compute-overlay-cycle`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/670

## Next
`/review-fix SWD-513` — Review and auto-fix on the new delivery PR
