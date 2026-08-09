# Bug: Controller Tuning preview ignores unapplied params

## Summary
- On **Controller Tuning**, **Preview Controller Behaviour** should run a one-off MPC solve with the *configured but unapplied* tuning parameters and show predicted trajectories without touching the live controller.
- After the HAOS App / thin-bridge cutover, `POST /api/forecasts/preview` ignores the posted tuning body and returns the live (already-applied) forecast snapshot.
- Symptom: preview only changes after **Apply Changes**.

## Repro
1. Open Controller Tuning with outdoor temperature available and MPC configured.
2. Change a live weight (e.g. tracking weight) — do **not** click Apply.
3. Click **Preview Controller Behaviour** and inspect room trajectories.

## Expected
- Preview charts reflect the draft (unapplied) MPC parameters.
- Live controller / applied config / persisted options are unchanged by Preview.

## Actual
- Preview matches the currently applied live forecast; draft params are ignored until Apply.

## Impact
- Users cannot evaluate tuning changes before applying them to the live controller, defeating the preview workflow.

## Suspected area
- `heatingassistant/app/__main__.py` — POST `/api/forecasts/preview` calls `runtime.forecasts()` only.
- Missing App-side `preview_tuning_forecast` (fat coordinator path removed in SWD-262; UI still posts tuning params).
- `heatingassistant/app/runtime.py` / `heatingassistant/engine/control_loop.py` — need a one-off solve that does not mutate live forecast caches.

## Acceptance criteria
- [x] `POST /api/forecasts/preview` runs a one-off MPC solve using posted MPC params (not only `plot_forecast_hours`).
- [x] Preview does not persist or apply tuning to live options / controller config.
- [x] Live forecast snapshot used by room views is unchanged by Preview.
- [x] Outdoor temperature unavailable returns `{ "error": "outdoor_temperature_unavailable" }` (UI already handles this).
- [x] Regression tests cover draft-vs-applied behaviour.
- [x] Version bump; App package synced via `scripts/sync-ha-app-package.sh`.

## Out of scope
- Window-detection parameter preview (debounce / settle / Q inflation) — not part of the MPC preview solve.
- Changing Apply / persistence behaviour for controller tuning.
- Restoring the fat HA WebSocket `preview_tuning_forecast` coordinator path.

## Tracker
- Task: SWD-285
- Branch: `cursor/swd-285-tuning-preview-unapplied-ebd3`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/580

## Next
`/review-fix SWD-285` — then `/ship SWD-285`
