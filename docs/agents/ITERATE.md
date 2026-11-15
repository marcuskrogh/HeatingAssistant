# ITERATE — SWD-604

Outcome: ready

## Wrong now

On 2026.11.6, after the price entity refreshes tomorrow's forecast, the planner price series starts at an unrelated value (live sample: 0.854) and no longer follows today's quarter-hour prices into 16 Nov. The same value is written as `price_per_kWh` on that tick. Startup writes two tick rows with one timestamp.

## Pass

- A refresh that changes the forecast list and keeps `raw_today` still feeds the solver today's quarter-hour price, then the 16 Nov forecast.
- The tick row `price_per_kWh` is the live quarter-hour price.
- When the forecast starts tomorrow and today's quarters are absent, the current step holds the sensor price, not the last forecast hour.
- A forecast-only republish does not drop a cached `raw_today`.
- Startup writes one tick row.
- Plan acceptance and the 60 s solver limit stay as they are.

## Cause

`_lookup_price` returned `series[-1]` when the step was before every published stamp. A forecast that starts at tomorrow therefore filled today with the furthest forecast hour. `update_tag` replaced the price attributes wholesale, so a refresh payload that omitted `raw_today` dropped today's quarters and took that path. Startup called `run_control_tick` from `start` and again from the MQTT connect handler, and the ticker was already running.

## Next

/implement built — review-only pull request, version 2026.11.8, do not merge.
