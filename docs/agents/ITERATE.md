# Iterate: Heat and cool on the fast loop when comfort bounds are already violated

## Prior work
- Task: [SWD-405](https://marcusknielsen.atlassian.net/browse/SWD-405)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/624
- Spec context: `docs/agents/PLAN-nmpc-p-ff.md`, `docs/agents/ITERATE.md` (SWD-400 / SWD-405)

## Problem
- Live heating and cooling stay at `u = 0` while rooms already violate the
  comfort band (too cold and too hot). Isolated production-horizon SLSQP
  *does* choose signed `U*` and accept succeeds.
- The fast loop tracks `T_ref` from NMPC only. With no accepted path it
  commands `u = 0`, so it never acts on a live band violation. Climate
  writes then set the unit to the current temperature and hold the
  violation.
- The NLP worker installs a plan on the forecast but does not re-run P or
  publish actuators, so commands stay at 0 until the next 15 min tick.

## Clarifications
- Keep SLSQP and the accept ratio vs `J(u=0)`.
- Fast fallback is zone-gated: P toward the setpoint only while air is
  outside the band. Inside the band with no path stays `u = 0`.
- Watchdog still forces `u = 0`.
- After an accepted plan, request a control cycle immediately so climate
  / number / switch writes follow `U*`.

## Acceptance criteria
- No NMPC path, room below the lower band: heat (`u > 0`) on the fast tick.
- No NMPC path, room above the upper band, cooling-capable source: cool
  (`u < 0`) on the fast tick.
- Inside the band with no path: `u = 0`.
- Watchdog still forces `u = 0`.
- Accepted NMPC result schedules a control cycle (actuator publish) without
  waiting for the next period.
- Tests, CalVer 2026.08.22, changelog, App sync.

## Out of scope
- Switching the NLP backend away from SLSQP.
- Changing the accept ratio vs `J(u=0)`.
- I-term / D-term or QP fallback.

## Work packages
1. Fast P comfort fallback and publish actuators on NMPC apply (SWD-412)
2. Tests, CalVer, App sync for comfort fallback (SWD-413)

## Tracker
- Task: [SWD-411](https://marcusknielsen.atlassian.net/browse/SWD-411)
- Relates: [SWD-405](https://marcusknielsen.atlassian.net/browse/SWD-405)
- Sub-tasks: [SWD-412](https://marcusknielsen.atlassian.net/browse/SWD-412),
  [SWD-413](https://marcusknielsen.atlassian.net/browse/SWD-413)
- Branch: `cursor/swd-411-nmpc-comfort-fallback-8856`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/626

## Next
`/review-fix SWD-411` — Review and auto-fix on the new delivery PR
