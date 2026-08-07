# Iterate: Finish HAOS App (Ingress parity, thin-only tree, port clash)

## Prior work
- Task: SWD-255
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/545 (merge `973a2c5`)
- Spec context: docs/agents/PLAN-haos-app-mqtt.md

## Problem
1. Ingress UI is a stripped shell; the industrial panel still expects Home Assistant websocket/`hass` APIs.
2. Legacy fat `custom_components/heating_assistant` remains for unit-test imports; App installs already sync the thin bridge.
3. Starting the App fails with `port 8099 is already in use` because PLCAssistant also binds 8099.

## Clarifications
- Port change: HeatingAssistant uses **8100** (ingress + host publish); PLCAssistant keeps 8099.
- Yes — PLCAssistant occupying 8099 is the likely cause of the start failure.

## Acceptance criteria
1. App `config.yaml` / Dockerfile / run.sh / docs / tests use **8100**, not 8099; version bump so Supervisor offers Update.
2. Ingress loads the industrial panel UX backed by App `/api/*` (no live HA websocket required for core navigation/config/status/bindings/schedules surfaces that the App owns).
3. Repo root `custom_components/heating_assistant` is the **thin MQTT bridge only**; compute modules live under `heatingassistant/engine/`; pytest imports retargeted; App sync script continues to version-lock.

## Out of scope
- Changing PLCAssistant ports.
- Official HA Apps store publication.
- Re-introducing Heating Assistant–owned HA climate/diagnostic entities.

## Work packages
1. Port clash fix (8100) + version bump 2.0.1
2. Ingress panel App API shim + expanded `/api/*`
3. Thin-only custom_components + test retarget

## Tracker
- Task: SWD-262
- Relates: SWD-255
- Branch: `cursor/swd-262-finish-haos-app-01f0`
- PR: *(filled after open)*

## Next
`/review-fix SWD-262` — Review and auto-fix (single pass)
