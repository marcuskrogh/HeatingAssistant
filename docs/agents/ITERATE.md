# Iterate: Plot forecasts still flat (attrs / weather service / estimated bridge)

## Prior work
- Task: SWD-278
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/567 (v2.0.17)
- Spec context: docs/agents/ITERATE.md (prior)

## Problem
After SWD-278, room plots still look wrong: linearised prediction does not
start from the EKF estimated output, Price Forecast remains a flat scalar
hold (not the day-ahead entity series), and outdoor/solar forecasts stay
constant (persistence).

Root causes:
1. Thin bridge `json.dumps` of Nord Pool `raw_today` fails when entries
   contain `datetime` objects — attributes never reach the App.
2. Modern HA weather entities no longer expose `forecast` on state
   attributes; SWD-278 only read attrs and skipped `weather.get_forecasts`.
3. App forecast bridge omits `linearised_temperature` at `now` and uses
   measured room temp instead of EKF `filtered_temperatures`; solar future
   steps index the N+1 series as if it were N-aligned.

## Acceptance criteria
1. Price entity with `raw_today`/`raw_tomorrow` (including datetime-valued
   starts) publishes JSON-safe MQTT attributes; `/api/forecasts`
   `price_forecast` varies with the day-ahead series (not only scalar hold).
2. Weather-bound outdoor forecast uses `weather.get_forecasts` when the
   service is available so outdoor (and cloud-driven solar) vary over the
   horizon when forecast data exists.
3. Forecast bridge at `now` sets `temperature` and `linearised_temperature`
   from EKF estimated/filtered output when available; solar future steps use
   `solar_forecast[i+1]` for the N+1 series.
4. Regression tests; version **2.0.18**.

## Out of scope
- History persistence across App restart.
- MODEL FIT / solar KPI completeness.
- Changing MPC linearisation point away from setpoint equilibrium.

## Work packages
1. Thin bridge: JSON-safe attribute sanitisation + `weather.get_forecasts`.
2. ControlEngine caches filtered temps; forecast payload bridges from
   estimated output and fixes solar N+1 indexing.
3. Version 2.0.18 + tests + tracker.

## Tracker
- Task: SWD-279
- Relates: SWD-278
- Branch: `cursor/swd-279-forecast-bridge-attrs-4b6c`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/568

## Next
`/review-fix SWD-279` — Review and auto-fix (single pass)
