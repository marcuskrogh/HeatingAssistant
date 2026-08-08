# Iterate: MQTT rc=5 — SUPERVISOR_TOKEN missing without hassio_api

## Prior work
- Task: SWD-273
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/560 (v2.0.12)
- Spec context: docs/agents/ITERATE.md

## Problem
After v2.0.12, App logs show:

```
HeatingAssistant: SUPERVISOR_TOKEN missing; MQTT discovery unavailable.
MQTT connect to core-mosquitto:1883 failed with rc=5 (not authorised)
```

Discovery/retry from SWD-270/273 never runs because the App container has no
`SUPERVISOR_TOKEN`. Without credentials, Mosquitto rejects anonymous clients
(CONNACK rc=5).

Root cause: App `config.yaml` declares `services: [mqtt:need]` but **not**
`hassio_api: true`. Supervisor only injects `SUPERVISOR_TOKEN` when
`hassio_api` / `homeassistant_api` is enabled (same pattern zigbee2mqtt used
for mqtt:need + Services API).

## Acceptance criteria
1. App `config.yaml` sets `hassio_api: true` and `hassio_role: default` so
   Supervisor injects `SUPERVISOR_TOKEN`.
2. Set `homeassistant_api: true` so existing `run.sh` Core-restart /
   notification paths can authenticate.
3. Correct the outdated comment that claimed `/services/mqtt` needs no token.
4. Regression test asserts these flags are present in App packaging.
5. Version bump to **2.0.13**.

## Out of scope
- Manual Mosquitto user creation UX.
- Changing Mosquitto addon config.

## Work packages
1. Update `heating_assistant/config.yaml` flags + comment.
2. Version 2.0.13 across App/package/integration mirrors.
3. Packaging regression assertions + tracker.

## Tracker
- Task: SWD-274
- Relates: SWD-273
- Branch: `cursor/swd-274-hassio-api-token-f56e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/563

## Next
`/review-fix SWD-274` — Review and auto-fix (single pass)
