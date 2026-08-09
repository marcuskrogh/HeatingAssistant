# Iterate: KPIs/plots flat overnight — App has no wall-clock history/control ticker

## Prior work
- Task: SWD-275
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/564 (v2.0.14)
- Spec context: docs/agents/ITERATE.md

## Problem
MQTT is connected (`API connected · MQTT ok`), but Ingress KPIs/plots stay empty
or flat overnight. Temperature and power charts show samples near session start,
then a long hold until the panel is opened again. Live gauges appear to update
only while Ingress is open on the device.

Root cause: App history recording and control cycles run only on MQTT
`tag/+/in` events (plus config writes / startup). There is **no wall-clock
sampler** driven by `update_interval`. Overnight, stable HA sensors emit no
`state_changed`, so the thin bridge publishes no new `tag/in`, so the in-memory
history ring gets no new points. Ingress polls `api/state` every 5s **only while
the panel is loaded** (client-side), which matches “updates when open.”

Screenshot evidence: overnight flat Filtered/Power lines, then a vertical jump
at open time (~06:44); price/forecast series absent (separate stub gap).

## Acceptance criteria
1. Background ticker records history on a wall-clock cadence (≤60s) without
   Ingress being open.
2. Background ticker runs control cycles on `update_interval` when no recent
   MQTT-driven cycle occurred.
3. With panel closed ≥1 sample period, `/api/history` shows new samples spanning
   that window (even without new tag events).
4. Regression tests cover ticker start/stop and history growth without tags.
5. Version bump to **2.0.15**.

## Out of scope
- Filling stub `/api/forecasts` / price forecast series (follow-up iterate).
- Persisting history ring across App restarts.
- Full MODEL FIT / solar KPI completeness.

## Work packages
1. Add background history + control ticker to `HeatingRuntime`.
2. Version 2.0.15 + packaging sync.
3. Unit tests + tracker.

## Tracker
- Task: SWD-276
- Relates: SWD-275
- Branch: `cursor/swd-276-wall-clock-ticker-f56e`

## Next
`/review-fix SWD-276` — Review and auto-fix (single pass)
