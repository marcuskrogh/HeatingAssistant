# Iterate: Persist solar-gain LPF state

## Prior work
- Task: SWD-487
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/658
- Spec context: docs/agents/PLAN-solar-gain-lpf.md

## Problem
- Solar-gain EMA state (`_solar_gain_filt`) and the last applied solar
  schedule lived only in RAM.
- App restart and controller rebuild seeded the filter (and
  `solar_gain_measured` / ID `d_solar`) to instantaneous sky, so room
  DISTURBANCES and PE traces jumped at the restart sample.

## Clarifications
- Restore both the EMA map and `_last_solar_forecast` so samples taken
  before the first post-restart compute stay on the filtered k = 0 watt.

## Pass criteria
- After a persisted overcast lag, a new `HeatingRuntime` on the same data
  dir publishes the saved filtered k = 0, not `_current_solar`.
- The first persisted compute after restore EMAs from that saved k = 0.
- `update_config` rebuild keeps the live EMA (Environment Apply).

## Out of scope
- Persisting the unused cloud-cover smoother.
- Changing τ or the EMA law.

## Work packages
1. Save/restore solar LPF runtime state; tests; CalVer; changelog

## Tracker
- Task: SWD-490
- Relates: SWD-487

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/659
