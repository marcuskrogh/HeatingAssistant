# Bug: Applied / measured solar gain stuck at 0 — App hass_states hardcodes solar_gain_measured

## Summary
- Room DISTURBANCES **Solar Gain** (solid / historical) stays flat at 0 kW while **Solar Gain Forecast** (dashed) shows correct daytime peaks.
- Left-of-NOW history and the live value are always zero even though MPC solar geometry is working (post SWD-282).

## Repro
1. Open a room view with Option A solar exposure and a daytime horizon.
2. Confirm DISTURBANCES: Outdoor + Solar Gain Forecast move; Solar Gain (measured) is a flat zero to NOW.
3. Check `sensor.heating_assistant_<room>_solar_gain_measured` state — always `0.0`.

## Expected
- Current applied solar gain matches the geometric/GHI model used by MPC (`_current_solar` / applied disturbance step).
- Historical Solar Gain left of NOW tracks daytime dynamics (non-zero when sun is up).
- Forecast path remains unchanged.

## Actual
- Measured / historical Solar Gain is constant 0.
- Forecast Solar Gain looks correct.

## Impact
- DISTURBANCES plot misleads operators: solar appears unused historically while the controller is forecasting (and applying) real gains.
- Plot history and KPI solar gauge stay dead; ID history is less affected because `d_solar` already prefers `solar_forecast[0]`.

## Suspected area
- `HeatingRuntime.hass_states()` hardcodes:
  ```python
  states[f"sensor.heating_assistant_{slug}_solar_gain_measured"] = self._ha_state(..., 0.0, ...)
  ```
- `_record_history_samples()` samples `hass_states()`, so every durable plot sample is also 0.
- Forecast plots read `solar_forecast` from the controller cache (working).
- Same App synthetic-stub pattern as SWD-284 (`electricity_price`).

## Acceptance criteria
- [ ] After a control cycle with daytime solar geometry, `hass_states()[…_solar_gain_measured].state` is non-zero and matches the applied current-step solar gain for that room.
- [ ] DISTURBANCES historical Solar Gain left of NOW shows daytime dynamics (not a flat zero); Solar Gain Forecast remains correct.
- [ ] Regression test: `hass_states` / history sample must not hardcode `0.0` when the engine has non-zero solar.
- [ ] Version bump to **2.0.29**; App package synced.

## Out of scope
- `heat_loss` synthetic also stubbed at `0.0` (separate follow-up unless trivial in the same fix).
- Re-estimating solar_scale / aperture.
- Changing forecast solar geometry (SWD-282).

## Relates
- SWD-282 (forecast aperture)
- SWD-278 (stop zeroing MPC solar)
- SWD-284 (similar App synthetic stub)

## Tracker
- Task: [SWD-297](https://marcusknielsen.atlassian.net/browse/SWD-297)
- Branch: `cursor/swd-297-measured-solar-gain-zero-f475`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/590

## Next
`/implement SWD-297` → `/review-fix` → closeout
