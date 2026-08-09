# Iterate: App update clears room-plot / ID history

## Prior work
- Task: SWD-279 (deferred "History persistence across App restart"); SWD-269 (in-memory `/api/history`)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/568 (v2.0.18); latest ship SWD-280 v2.0.19
- Spec context: docs/agents/PLAN-haos-app-mqtt.md (App owns history on data volume)

## Problem
Every App update/restart clears room-view plot history. Ingress `/api/history`
is an in-memory ring only (`HeatingRuntime._history`); `/data` survives updates
but history is never written there. `IdentificationHistoryStore` (JSONL) was
never rewired after the HAOS App cutover.

## Acceptance criteria
1. Plot entity samples used by room-view charts persist under the App data
   volume and restore into `/api/history` after restart/update.
2. Identification observation records (`y`/`u`/`d_outdoor`/`d_solar`/`timestamp`)
   append to an App-owned JSONL store under `/data` and restore into an
   in-memory `history_buffer` on startup (same schema as the original
   integration).
3. Retention respects configured `identification_history_days` for ID history;
   plot history retention covers at least the configured `plot_history_hours`
   window.
4. Regression tests cover round-trip across a new `HeatingRuntime` instance on
   the same `data_dir`.
5. Version bump to **2.0.20**.

## Out of scope
- Full sysid service ownership / ending sysid no-ops (separate iterate).
- HA Recorder rebuild path (no HA-owned synthetic sensors in thin bridge).

## Work packages
1. Adapt `IdentificationHistoryStore` for App `data_dir` (no HA `hass`).
2. Persist + restore plot entity history under `/data`.
3. Append ID observation records on history ticks; restore `history_buffer`.
4. Version 2.0.20 + regression tests + tracker.

## Tracker
- Task: SWD-281
- Relates: SWD-279, SWD-269
- Branch: `cursor/swd-281-history-persistence-32e0`
- PR: (pending)

## Next
`/review-fix SWD-281` — Review and auto-fix (single pass)
