# Iterate: forward tariffs into the price day latch

## Prior work
- Task: SWD-604
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/716 (merged `db82aad`, released 2026.11.8)
- Spec context: prior ITERATE for the day latch

## Problem
- After 2026.11.8 the price day latch stays inert. `FORECAST_ATTR_KEYS` omits `tariffs`, so the bridge never forwards it. `PriceDayLatch.consider` then returns passthrough. Live state: days empty, `eds` false, tariffs null, `awaiting_valid` false, ignored 0. Tick prices follow the live series.
- The 2026.11.8 tests handed tariffs to the App and skipped the bridge.

## Pass criteria
- The bridge forwards `tariffs` (additional tariffs live inside it), `tomorrow_valid`, `today_mean`, and `use_cent`.
- An Energi Data Service payload with missing or empty tariffs is rejected: not latched, counted as ignored. On a cold start with nothing stored, the tick price stays null and the solver adds no electricity price term until a valid load (`price_latch_awaiting_valid`).
- Passthrough remains for a Nord Pool `start` + `value` series.
- End-to-end: a realistic Energi Data Service attribute set goes through `forecast_publish` into the real tick and the real solver. With tariffs it latches. Without tariffs it is rejected. A tariff-less cold start omits the price term, then a valid load is accepted.
- Version 2026.11.9, changelog heading, App mirror in sync. Leave pull request #715 alone.

## Out of scope
- Merging this pull request, or marking SWD-604 or SWD-605 Done.
- Renumbering pull request #715.

## Source classification
- Energi Data Service: a `tariffs` key (an empty map counts), any price row with `hour` and `price`, or `price_source` `energidataservice` stamped by the bridge.
- Nord Pool passthrough: `start` + `value` rows and no Energi Data Service signal.

## Tracker
- Task: SWD-605
- Relates: SWD-604, SWD-595

## Workflow
- iterate, review only, do not merge
