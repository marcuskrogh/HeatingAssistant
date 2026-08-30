# Iterate: Room view still shows idle U=0 / free-response instead of the NMPC plan

## Prior work
- Task: [SWD-450](https://marcusknielsen.atlassian.net/browse/SWD-450)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/646
- Spec context: `docs/agents/PLAN-2026-08-30-swd-450-catalog-forecast-attrs.md`

## Problem
- After SWD-450, room-view Forecast still spikes like a heater-off free
  response (~28 °C) and Planned Power stays at 0 kW for the whole window.
- The heater never receives the planned power either.
- `TickerMixin._slow_slot_start` calls `slow_slot_start_s` but
  `runtime_ticker.py` does not import it. The NMPC worker evaluates
  `plan_epoch=self._slow_slot_start(stamp)` before `apply_nmpc_result`.
- When `last_nmpc_ts` is set, that is a `NameError`. The worker except path
  records a reject, so U*/T* never reach the forecast snapshot, the P command
  is never installed on `actuator_outputs`, and MQTT never publishes to the
  heater.

## Acceptance criteria
- `HeatingRuntime._slow_slot_start` returns the slow-slot origin (no NameError).
- An NMPC worker apply can pass `plan_epoch` into `apply_nmpc_result`.
- When `last_nmpc_ts` is set, an accepted worker result installs the P command
  onto `actuator_outputs` and publishes it on the heater MQTT out topic
  (no reject).
- Tests, CalVer 2026.08.37, changelog, App sync.

## Out of scope
- Catalog overlay (SWD-450).
- NMPC cost weights.
- Plot styling.

## Work packages
1. Import `slow_slot_start_s` so NMPC apply can install the plan (SWD-457)
2. Tests, CalVer, changelog, App sync for NMPC plan plot (SWD-458)

## Tracker
- Task: [SWD-456](https://marcusknielsen.atlassian.net/browse/SWD-456)
- Relates: [SWD-450](https://marcusknielsen.atlassian.net/browse/SWD-450)
- Sub-tasks: [SWD-457](https://marcusknielsen.atlassian.net/browse/SWD-457),
  [SWD-458](https://marcusknielsen.atlassian.net/browse/SWD-458)
- Branch: `cursor/swd-456-nmpc-slow-slot-import-6bcb`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/648

## Next
`/harden SWD-456` — Structure pass (same catalog as implement; small diffs included)
