# Iterate: Ingress shows MQTT disconnected — App missing Supervisor Mosquitto credentials

## Prior work
- Task: SWD-269
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/557 (v2.0.9 — MQTT status + soft writes)
- Spec context: docs/agents/ITERATE.md, heating_assistant/config.yaml, heatingassistant/mqtt/

## Problem
After updating to v2.0.9, Ingress correctly shows:

`API connected - 19 entities · MQTT disconnected`

Room CURRENT stays `—` and KPIs stay at 0 because the App never has a live MQTT session.

Root cause:

1. Official Mosquitto **rejects anonymous** connections.
2. App defaults / options use empty `mqtt_username` / `mqtt_password`.
3. App `config.yaml` does not declare `services: [mqtt:need]`, so Supervisor never grants/discovers Mosquitto credentials.
4. No code path reads `http://supervisor/services/mqtt` for host/user/password.

## Acceptance criteria
1. App declares MQTT service dependency (`mqtt:need`) so Supervisor exposes Mosquitto to the App.
2. On HAOS start, when options lack MQTT credentials, App discovers host/port/username/password from Supervisor Services API and uses them for `PahoMqttBus`.
3. Explicit user-supplied MQTT options still override discovery.
4. `/api/health` / state expose enough detail to diagnose MQTT (connected flag; optional discovery source / broker host).
5. Regression tests cover discovery merge + credential precedence + broker URL normalization.
6. Version bump to **2.0.10**.

## Out of scope
- Changing Mosquitto itself or requiring users to create custom Mosquitto logins by hand when Supervisor discovery works.
- Full offline message queue persistence.

## Work packages
1. Declare `mqtt:need` (+ hassio_api if needed) in App config.yaml
2. Supervisor MQTT discovery + options merge in Python (and run.sh hint)
3. Broker host normalization (`mqtt://` strip)
4. Tests + version 2.0.10 + sync App package

## Tracker
- Task: SWD-270
- Relates: SWD-269
- Branch: `cursor/swd-270-mqtt-supervisor-creds-65c0`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/558
- Status: review-fix CLEAN (credential pair, durable fallback, honest mqtt_source, password redaction)

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/558
