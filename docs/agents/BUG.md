# Bug: Schedule comfort_offset ignored in room plot + expanded schedule collapses

## Summary
- After the HAOS App rewrite, comfort schedules with a non-default `comfort_offset` (e.g. Night Mode = **3** while the room default is **1**) do not change the room-view temperature plot constraint bands — historically or predictively.
- Separately, an expanded period on the Schedules detail page collapses on its own during live state refreshes.

## Repro
### SWD-286 — plot constraints
1. Configure Night Mode (Mon–Sun 22:00–05:00) with comfort interval / `comfort_offset` = 3.
2. Open the room view while Night Mode shows the NOW badge.
3. Inspect TEMPERATURE constraint bands around NOW and into the forecast.

### SWD-287 — expand collapse
1. Open Schedules → room detail → expand a period for editing.
2. Leave the panel open while MQTT / control ticks update panel state.
3. Observe the card collapse without user action.

## Expected
- Constraint upper/lower (and setpoint overrides) follow the active schedule both as history samples and across the forecast horizon.
- Expanded periods stay expanded until the user collapses them, starts drag-reorder, or navigates away.

## Actual
- Bands stay at the room default comfort_offset (e.g. ±1) through Night Mode; no step at 22:00.
- Expanded period cards collapse spontaneously on live refresh.

## Impact
- Users cannot verify scheduled comfort corridors on the room plot; controller / sensors also ignored schedule comfort after SWD-262.
- Schedule reconfiguration is interrupted by unexpected collapses.

## Suspected area
- `HeatingRuntime.hass_states()` / `build_app_forecast_payload` / `ControlEngine.compute_actions` never rewired schedule → effective setpoint/comfort after fat coordinator removal (classic `schedule_control.py`).
- `schedules-detail.js` `initLocalPeriods()` clears `expandedSet` on every non-dirty `fetchSchedules()` from `update()`.

## Acceptance criteria
- [x] Live setpoint / constraint sensors reflect `resolve_effective_control_params` when the schedule is enabled.
- [x] `/api/forecasts` per-step setpoint/constraints follow schedule projection.
- [x] Control compute applies schedule effective params + horizon `control_trajectory`.
- [x] Expanded schedule survives live panel state refreshes (dirty edits still block overwrite).
- [x] Regression tests; version bump; App package synced.

## Out of scope
- Rebuilding historical constraint samples recorded before this fix (forward-looking only).
- Full experiment clamp / window-override schedule interaction polish beyond trajectory + disabled sources.

## Tracker
- Tasks: SWD-286, SWD-287
- Branch: `cursor/swd-286-schedule-comfort-constraints-7e7d`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/582

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/582
- Merge: `bc6b090`
- Version: **2.0.25**

## Next
Done — rebuild App on HAOS to v2.0.25; confirm Night Mode ±3 on room plot and that expanded schedules stay open through live ticks.
