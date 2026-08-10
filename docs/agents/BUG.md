# Bug: Climate card setpoints reset to default (overview + room view)

## Summary
- Changing TARGET or COMFORT BAND on climate cards (overview tiles and room-view card) snaps back to the previous/default values after the debounce commit. Users cannot change setpoints.

## Repro
1. Open Overview or a room detail climate card (e.g. Living Room).
2. Use +/- to change TARGET temperature.
3. Use +/- to change COMFORT BAND.
4. Wait ~700 ms for the debounced commit.

## Expected
- New target and comfort-band values persist in config and remain visible after commit / live refresh.

## Actual
- Values reset to the prior/default values and stay unchanged.

## Impact
- Panel climate controls are unusable for retargeting rooms.

## Suspected area
- After SWD-262, `HeatingRuntime.apply_service` accepted `climate.set_temperature` / `turn_on` / `turn_off` as no-ops (`{"accepted": True}`) without writing room config.
- Overview + room climate cards called `setClimateTemperature` (climate domain) instead of `set_room_setpoint`.
- Comfort-band commits cleared optimistic edit guards before the async service settled, so live refreshes could overwrite the optimistic value.

## Acceptance criteria
- [x] `climate.set_temperature` persists room setpoint via config update.
- [x] `climate.turn_on` / `turn_off` persist room enablement.
- [x] Overview and room climate cards commit target via `set_room_setpoint` and power via `set_room_enabled`.
- [x] Optimistic edit guards stay active until the commit promise settles.
- [x] Regression tests; version bump; App package synced.

## Out of scope
- Schedule-period setpoint / comfort overrides (editing a period on the Schedules page).
- Reintroducing fat HA climate entities outside the App Ingress shim.

## Tracker
- Task: SWD-288
- Branch: `cursor/swd-288-climate-setpoint-reset-d3ac`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/584

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/584
- Version: **2.0.26**
- review-fix: CLEAN

## Next
Done — rebuild App on HAOS to v2.0.26; confirm TARGET and COMFORT BAND stick on Overview and room view after debounce.
