# Bug: Sysid Apply Parameters not restored — defaults on reload + panel jumps to overview

## Summary
- After Apply Parameters on System Identification, thermal/heater params land in `estimated_params` / `parameter_history` but the live `ControlEngine` is rebuilt from base room config without restoring the snapshot.
- On reload the form shows DEFAULTS because `temperature_filtered` never publishes live model attrs.
- Ingress UI periodically remounts and lands on overview when the URL hash is empty.

## Repro
1. Open System Identification → room.
2. Change thermal mass / heater scale → Apply Parameters.
3. Reload the Ingress panel (or wait for a remount with a cleared hash).

## Expected
- Applied params remain in the live model and reappear in the form after reload.
- Navigation stays on the current hash route across remounts.

## Actual
- Params snap back to configured/default room values.
- UI returns to overview.

## Impact
- System identification Apply is ineffective for control and confusing in the UI; users cannot keep identified parameters.

## Suspected area
- `HeatingRuntime.update_config` / `__init__` call `ControlEngine.update_config` without `restore_estimated_parameters`.
- Apply's follow-up `update_estimation_params` triggers that rebuild and wipes the just-applied live model.
- `hass_states()` `temperature_filtered` attrs omit thermal params (docs still claim them).
- Ingress remount with empty hash → `readPanelRoute()` defaults to overview; no sessionStorage route restore.

## Acceptance criteria
- [x] `store_identified_parameters` then `update_estimation_params` leaves live model + heater scales at applied values.
- [x] App restart restores `estimated_params` into `ControlEngine`.
- [x] `temperature_filtered` attrs expose live thermal params; sysid form populates from them (with `parameter_history` fallback).
- [x] Panel remembers last hash across remount/reload when URL hash is empty.
- [x] Regression tests; version bump; App package synced.

## Out of scope
- Scheduled identification experiments.
- Reintroducing fat HA Core diagnostic entities.

## Tracker
- Task: [SWD-296](https://marcusknielsen.atlassian.net/browse/SWD-296)
- Branch: `cursor/swd-296-sysid-params-overview-5009`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/588

## Shipped
- Version: **2.0.28**
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/588

## Next
review-fix CLEAN → merge → rebuild App on HAOS to v2.0.28.
