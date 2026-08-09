# Iterate: Incomplete plot forecasts (outdoor / solar / price / linearised)

## Prior work
- Task: SWD-277
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/566 (v2.0.16)
- Spec context: docs/agents/ITERATE.md

## Problem
After SWD-277, temperature Forecast appears, but Linearised looks flat/useless,
Planned Power sits at 0, Price Forecast is a flat scalar hold, and the
Disturbances plot stays empty (no outdoor / solar forecasts).

Root causes:
1. `ControlEngine.compute_actions` forces `solar_gains={room: 0}` and never
   passes outdoor / cloud / GHI / price forecasts into `controller.compute()`.
2. Thin MQTT bridge publishes only scalar tag values — Nord Pool `raw_today` /
   weather `forecast` attributes never reach the App.
3. Controller `price_forecast` is not cached; App payload only holds the current
   scalar price across the horizon.

## Acceptance criteria
1. After a control cycle with weather (+ optional solar/price) wired,
   `/api/forecasts` room steps include non-null `outdoor_temp` and `solar_gain`
   over the horizon (geometry and/or GHI; outdoor may be persistence when no
   weather forecast attrs).
2. `price_forecast` uses day-ahead attribute series when present; scalar hold
   remains the fallback when only a current price is available.
3. Linearised temperatures use the same room-name keys as nonlinear predictions
   and are present when the QP trajectory exists.
4. Regression tests; version **2.0.17**.

## Out of scope
- Polling `weather.get_forecasts` beyond attribute / Core state attrs.
- History persistence across App restart.
- MODEL FIT / solar KPI completeness.

## Work packages
1. MQTT tag `attributes` + thin integration publish for weather/price/solar.
2. Runtime builds outdoor/cloud/GHI/price series; ControlEngine stops zeroing
   solar and forwards forecasts; cache + payload for price series.
3. Version 2.0.17 + tests + tracker.

## Tracker
- Task: SWD-278
- Relates: SWD-277
- Branch: `cursor/swd-278-forecast-disturbances-f56e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/567

## Next
`/review-fix SWD-278` — Review and auto-fix (single pass)
