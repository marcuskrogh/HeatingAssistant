# Bug: Applied / measured solar gain stuck at 0 — App hass_states hardcodes solar_gain_measured

## Summary
- Room DISTURBANCES **Solar Gain** (solid / historical) stayed flat at 0 kW while **Solar Gain Forecast** showed correct daytime peaks.
- Root cause: `HeatingRuntime.hass_states()` hardcoded `…_solar_gain_measured` to `0.0`.

## Fix
- `ControlEngine.applied_solar_gains()` returns `solar_forecast[0]` (applied current step) with live geometric fallback.
- `HeatingRuntime.hass_states()` publishes that value on `…_solar_gain_measured` (plus window attrs).
- Plot history samples the real state; ID history reuses the same helper.
- Version **2.0.29**.

## Acceptance criteria
- [x] After a control cycle with daytime solar geometry, `hass_states()[…_solar_gain_measured].state` is non-zero and matches the applied current-step solar gain for that room.
- [x] DISTURBANCES historical Solar Gain left of NOW shows daytime dynamics (not a flat zero); Solar Gain Forecast remains correct.
- [x] Regression test: `hass_states` / history sample must not hardcode `0.0` when the engine has non-zero solar.
- [x] Version bump to **2.0.29**; App package synced.

## Tracker
- Task: [SWD-297](https://marcusknielsen.atlassian.net/browse/SWD-297)
- Branch: `cursor/swd-297-measured-solar-gain-zero-f475`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/590

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/590
- Merge: `6288e22`
- Version: **2.0.29**
- review-fix: CLEAN

## Next
Done — rebuild App on HAOS to v2.0.29; confirm DISTURBANCES Solar Gain left of NOW tracks daytime dynamics.
