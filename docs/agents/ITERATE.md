# Iterate: Ingress LOAD ERROR after PE popup close

## Prior work
- Task: [SWD-504](https://marcusknielsen.atlassian.net/browse/SWD-504)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/666 (`60f325e2`)
- Spec context: docs/agents/PLAN-pe-popup-close-exit.md

## Problem
- After merge, Ingress shows `LOAD ERROR — Unexpected token '}'. Try statements must have at least a catch or finally block.`
- `waitForPeJob` in `sysid-detail.js` dropped `finally { hidePeOverlay(); }` so the popup can stay open, but left a `try { ... }` with neither `catch` nor `finally`.
- Engines that enforce that grammar (WebKit / the HA companion WebView) fail to parse the panel module.

## Pass criteria
- `waitForPeJob` is valid JavaScript (every `try` has `catch` or `finally`).
- The overlay still stays open after a fit: `waitForPeJob` does not hide it in `finally`.
- Close still hides the overlay; while running, close still cancels and does not apply parameters.

## Out of scope
- PE algorithm, `exit_label` wording, overlay layout.

## Work packages
1. Remove the bare `try` in `waitForPeJob` (keep the poll loop and throws).
2. Regression parse test, cache bust, CalVer, changelog, App package sync.

## Tracker
- Task: [SWD-507](https://marcusknielsen.atlassian.net/browse/SWD-507)
- Relates: [SWD-504](https://marcusknielsen.atlassian.net/browse/SWD-504)
- Sub-tasks: [SWD-508](https://marcusknielsen.atlassian.net/browse/SWD-508), [SWD-509](https://marcusknielsen.atlassian.net/browse/SWD-509)
- Branch: `cursor/swd-507-pe-try-syntax-cfe8`

## Next
`/test SWD-507` — Dedicated testing phase, then harden and code review
