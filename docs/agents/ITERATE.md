# Iterate: MQTT rc=5 — SUPERVISOR_TOKEN missing without with-contenv entrypoint

## Prior work
- Task: SWD-274
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/563 (v2.0.13)
- Spec context: docs/agents/ITERATE.md

## Problem
After v2.0.13 (`hassio_api: true` / `homeassistant_api: true`), the App still
logs `SUPERVISOR_TOKEN missing` and Mosquitto `rc=5 (not authorised)`.

Root cause: `heating_assistant/run.sh` starts with `#!/usr/bin/env sh`.
Home Assistant base images store Supervisor secrets (including
`SUPERVISOR_TOKEN`) in the s6 container environment; they are only exported
into the App process when the entrypoint is wrapped with
`/usr/bin/with-contenv` (official tutorial: `#!/usr/bin/with-contenv bashio`).
Declaring `hassio_api` alone does not put the token in a bare `sh` process.

## Acceptance criteria
1. `heating_assistant/run.sh` shebang is `#!/usr/bin/with-contenv bashio`.
2. Regression test asserts the with-contenv entrypoint (and keeps hassio_api).
3. Version bump to **2.0.14**.
4. Missing-token log mentions with-contenv / rebuild guidance.

## Out of scope
- Changing Mosquitto addon config.
- Manual credential UX redesign.

## Work packages
1. Fix `run.sh` shebang + missing-token message.
2. Version 2.0.14 across App/package/integration mirrors.
3. Packaging regression + tracker.

## Tracker
- Task: SWD-275
- Relates: SWD-274
- Branch: `cursor/swd-275-with-contenv-token-f56e`

## Next
`/review-fix SWD-275` — Review and auto-fix (single pass)
