# Iterate: Plot samples too dense + empty forecasts

## Prior work
- Task: SWD-276
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/565 (v2.0.15)
- Spec context: docs/agents/ITERATE.md

## Problem
Overview KPIs populate, but room plots sample many times per minute (not at
`update_interval`, e.g. 15 min) and Forecast / Planned Power / Price Forecast
lines stay empty.

Root causes:
1. SWD-276 capped quiet-period history at ≤60s and still force-recorded on
   every MQTT tag + control cycle.
2. `HeatingRuntime.forecasts()` is still an empty stub; MPC trajectories from
   `ControlEngine` are never exposed to `/api/forecasts`.

## Acceptance criteria
1. With `update_interval=900`, history advances ~once per 900s — not every
   ≤60s / tag spam.
2. After an MPC control cycle, `/api/forecasts` returns non-empty
   `rooms[slug].forecast` with ISO times at `now + k·dt`
   (`dt=update_interval`), including `temperature` and `heating_power` where
   available.
3. Planned Power chart can read `heating_power` from that payload.
4. When `energy_price` tag is present, `price_forecast` is non-empty (current
   price held across horizon); empty OK when unwired.
5. Regression tests for history gate + forecast shape; version **2.0.16**.

## Out of scope
- Full Nord Pool attribute-based day-ahead price series over MQTT.
- History persistence across App restart.
- MODEL FIT / solar KPI completeness.

## Work packages
1. Gate history recording to `update_interval`.
2. Cache MPC trajectories on ControlEngine; build App forecast payload.
3. Version 2.0.16 + tests + tracker.

## Tracker
- Task: SWD-277
- Relates: SWD-276
- Branch: `cursor/swd-277-plot-cadence-forecasts-f56e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/566

## Next
`/ship SWD-277` — review-fix CLEAN (slug keys, price_tag, capacity meta, forecast lock)
