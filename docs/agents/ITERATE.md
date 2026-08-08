# Iterate: Ingress panel LOAD ERROR — bare module path

## Prior work
- Task: SWD-265
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/549
- Spec context: docs/agents/ITERATE.md (SWD-265 Ingress base href)

## Problem
After SWD-265, Ingress loads past "Loading App API..." and the App shim connects
(API connected — N entities), but the industrial panel shows:

`LOAD ERROR — Module name, 'ha-industrial-panel/js/ha-connection.js?v=114' does not resolve to a valid URL.`

SWD-265 changed `BASE_PATH` from `/ha-industrial-panel` to the bare relative
`ha-industrial-panel`. Dynamic `import()` requires a valid URL or a relative
specifier starting with `./`, `../`, or `/`. Bare paths are treated as package
names and fail module resolution.

## Acceptance criteria
1. Dynamic panel module imports use a relative URL (`./ha-industrial-panel/...`)
   that resolves under Ingress `<base href>` (and direct App port access).
2. Panel boots past LOAD ERROR when App API is connected.
3. Regression test asserts `industrial-dashboard.js` does not use a bare
   `BASE_PATH` for `import()`.
4. Version bump so Supervisor offers Update.

## Out of scope
- Full HA custom-panel websocket parity beyond the Ingress App shim.

## Work packages
1. Fix BASE_PATH + cache-bust / version bump 2.0.5
2. Regression test + sync App package
3. PR / ship handoff

## Tracker
- Task: SWD-266
- Relates: SWD-265
- Branch: `cursor/swd-266-ingress-module-url-f9b0`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/550

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/550
- Version: **2.0.5** — BASE_PATH from classic script URL so dynamic `import()` resolves under Ingress.

## Next
Done
