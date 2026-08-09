# Bug: Room view Price plot missing historical data

## Summary
- On the room view **HEATING POWER & PRICE** chart, the solid green **Price** series (left of NOW) is empty.
- Dashed **Price Forecast** (right of NOW) renders correctly from `/api/forecasts`.
- Root cause: after the HAOS App / thin-bridge cutover, the UI still requests history for `sensor.heating_assistant_electricity_price`, but `HeatingRuntime.hass_states()` never publishes that synthetic sensor, so plot history never records it and `/api/history` returns nothing.

## Repro
1. Wire an electricity price entity (e.g. Nord Pool) under Environment.
2. Open a room view with MQTT connected and history/forecast hours > 0.
3. Inspect **HEATING POWER & PRICE**.

## Expected
- Solid **Price** line shows historical / current price left of NOW (from day-ahead attrs when available, otherwise sampled tag value).
- Price is recorded into plot history like outdoor temperature.

## Actual
- Historical Price series missing; only Price Forecast is visible.

## Impact
- Users cannot compare planned power against recent electricity prices on the room chart.

## Suspected area
- `heatingassistant/app/runtime.py` — `hass_states()` / `history()` (missing `electricity_price` synthetic; no day-ahead backfill).
- UI already correct: `room-detail.js` + `room-charts.js` request and plot `systemEntity('electricity_price')`.

## Acceptance criteria
- [x] `hass_states()` exposes `sensor.heating_assistant_electricity_price` from the configured price tag.
- [x] `/api/history` for that entity returns points for the plot window (sampled and/or synthesized from day-ahead price attrs when present).
- [x] Room power chart can show a solid Price series left of NOW when a price entity is wired.
- [x] Regression tests; version bump to **2.0.23**; App package synced via `scripts/sync-ha-app-package.sh`.

## Out of scope
- Measured Power flat-at-bottom from pre-SWD-280 fraction history (separate unit/history migration).
- Changing the Price Forecast `/api/forecasts` path.
- Scalar forecast fallback tariff adder parity (deferred note).

## Tracker
- Task: SWD-284
- Branch: `cursor/swd-284-price-history-a08d`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/578
- Merge: `afcbea7`

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/578
- Merge: `afcbea7`
- Version: **2.0.23**

## Next
Done — rebuild App on HAOS to v2.0.23; confirm room Price line left of NOW.
