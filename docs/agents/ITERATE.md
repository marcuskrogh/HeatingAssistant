# Iterate: Ingress UI stuck on "Loading App API..."

## Prior work
- Task: SWD-264
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/548
- Spec context: docs/agents/PLAN-haos-app-mqtt.md

## Problem
After SWD-264 shipped static assets, Ingress still showed a blank panel with **"Loading App API..."**
in the status pill. HA Ingress serves the app under `/api/hassio_ingress/<key>/`, but the UI used
absolute paths (`/api/...`, `/static/...`, `/ha-industrial-panel/...`) that resolve against the HA
host root instead of the Ingress subpath.

## Acceptance criteria
1. `index.html` served through Ingress injects `<base href>` from `X-Ingress-Path`.
2. Static assets, API calls, and dynamic panel imports load under the Ingress prefix.
3. Hash routing works in Ingress mode (no broken `/ha-industrial` replaceState).
4. Version bump so Supervisor offers Update.

## Out of scope
- Full HA custom-panel websocket parity (Ingress App shim only).

## Work packages
1. Ingress base injection + relative URL fixes
2. Version bump 2.0.4 + sync App package
3. PR / ship

## Tracker
- Task: SWD-265
- Relates: SWD-264
- Branch: `cursor/fix-ingress-loading-stuck-7bb9`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/549

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/549
- Version: **2.0.4** — Ingress base href + relative asset/API paths.

## Next
Done
